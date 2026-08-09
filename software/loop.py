"""
LOOP STATION -- HAcK 2026, Team 13
==================================

A single-track looper with overdub, sitting between the effects stage
and the master volume in main.py's signal chain:

    voices -> mix -> lowpass -> [LOOP TAP] -> master volume -> clip -> I2S

Two consequences of tapping there, both deliberate:

  - Effects BAKE IN. What you hear when you record is what plays back.
    Sweeping the filter afterwards changes the live notes on top but
    leaves the loop alone.
  - The volume knob still rides everything, loop included. Sound-check
    scores live volume from silence to full, and a loop that ignored the
    knob would undercut that.

OVERDUB MODEL  (read this before changing it)
---------------------------------------------
Two buffers: BASE holds the first take, LAYER holds overdubs.

Every overdub pass accumulates into the SAME layer. There is deliberately
no undo -- a per-pass undo stack, like a hardware RC-505, needs the
previous layer merged into base every time you start a new pass, and that
merge is a loop over tens of thousands of samples inside the keypad
handler: roughly 50-100 ms of stall, which is an audible dropout on
stage. This design has no merge step at all. Scrap a bad take with the
reset key instead.

layer_mark is what remains of that machinery, and it earns its keep on
its own: an unmarked block is simply not read back, so the layer never
has to be memset. First pass over a block overwrites, later passes
accumulate.

MEMORY
------
Mono int16 at 12000 Hz costs 24,000 bytes per second, and there are TWO
buffers:

    2 s -> 94 KB       3 s -> 141 KB      4 s -> 188 KB

Allocation needs 3x that figure momentarily unless _alloc_pair's
drop-the-temporary path is preserved -- see the comment there before
changing it.

The Pico 2 has 520 KB SRAM but MicroPython's heap is smaller than that,
and pasting main.py into the REPL instead of running it from flash eats
a large chunk of it. Run gc.mem_free() on YOUR board before raising
`seconds`; the constructor backs off in half-second steps and the
startup banner prints what it actually got.

Length is quantised to whole audio blocks (21.3 ms at 256 samples /
12000 Hz). That lets playback do its wrap check once per block instead
of once per sample, and 21 ms is well below what reads as a timing
error.

STATE MACHINE
-------------
    STOPPED --rec--> RECORDING --rec--> PLAYING <--rec--> OVERDUB
       ^                                   |                |
       +-------------play/pause------------+----------------+

RECORDING ends either on a second record press (which sets the loop
length) or when the buffer fills -- it auto-closes into playback, so a
forgotten press cannot wedge the instrument mid-performance.
"""

import gc
from array import array

# --- states ---
STOPPED = 0
RECORDING = 1
PLAYING = 2
OVERDUB = 3

# Live mix peaks near +-256000 (8 voices * 32000). Stored as int16 with a
# 3-bit shift: 256000 >> 3 = 32000, fitting with a hair to spare.
# Playback shifts back by the same 3 bits, so one recorded layer returns
# at exactly the level it went in at.
_SHIFT = 3

# SSD1306 command bytes, for pushing a single page instead of a frame.
_SET_COL_ADDR = 0x21
_SET_PAGE_ADDR = 0x22

# The progress bar lives in the bottom 8-pixel page (y = 56..63).
_BAR_PAGE = 7
_BAR_Y = 56


def largest_block():
    """Biggest single bytearray the heap can currently hand out.

    gc.mem_free() is the TOTAL free bytes, which says nothing about
    whether any of it is contiguous. A heap with 400 KB free in 20 KB
    scraps cannot allocate a 128 KB buffer. Binary-searches for the real
    ceiling -- call it before constructing a Looper if allocation is
    misbehaving."""
    gc.collect()
    lo = 0
    hi = gc.mem_free()
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            probe = bytearray(mid)
            probe = None
            lo = mid
        except MemoryError:
            hi = mid - 1
        gc.collect()
    return lo


def _alloc_pair(n_samples):
    """Allocate TWO zeroed int16 arrays of n_samples each.

    array('h', bytes_like) hits MicroPython's buffer-protocol fast path:
    it memcpy's the raw bytes, giving n int16 elements rather than
    iterating 2n byte values.

    PEAK MATTERS MORE THAN TOTAL. The scratch bytearray must be alive
    while an array is being copied out of it, so building BOTH arrays
    from one live temporary peaks at 3x the buffer size -- 384 KB for a
    4 s loop, which does not fit and silently costs you a second of loop
    length via the constructor's back-off. Instead: build base from the
    temporary, DROP the temporary, then build layer from base. An array
    supports the buffer protocol just like a bytearray, so
    array('h', base) is the same memcpy fast path and yields n elements,
    not 2n. Peak is 2x, and a 4 s loop fits.

    Do NOT go back to growing these with extend(). Each extend allocates
    a whole new buffer and abandons the old one, so building 128 KB
    churns megabytes of short-lived blocks through the heap and leaves it
    too fragmented to hand out anything large.
    """
    gc.collect()

    raw = bytearray(2 * n_samples)
    base = array("h", raw)

    # Dropped BEFORE layer is allocated -- this line is the whole point
    # of the function; see PEAK MATTERS above.
    raw = None
    gc.collect()

    layer = array("h", base)
    gc.collect()

    # If a firmware build ever iterates instead of memcpy'ing, this
    # returns 2n elements of byte values and every loop would play back
    # as noise. Fail loudly at boot instead.
    if len(base) != n_samples or len(layer) != n_samples:
        raise RuntimeError("array('h', buffer) did not copy raw bytes")

    return base, layer


class Looper:

    __slots__ = (
        "block", "capacity", "n_blocks", "seconds",
        "base", "layer", "layer_mark",
        "state", "pos", "length",
    )

    def __init__(self, sample_rate, block_samples, seconds=4, min_seconds=1.0):
        """Allocate the largest loop up to `seconds` that actually fits.

        On a board that cannot hold the full request, a shorter loop is
        far better than a MemoryError traceback at boot -- so this backs
        off in half-second steps and reports what it got. Read
        looper.seconds afterwards to see the real length.
        """
        self.block = block_samples

        self.base = None
        self.layer = None

        while True:
            # Round capacity DOWN to a whole number of blocks.
            n_blocks = int(seconds * sample_rate / block_samples)
            capacity = n_blocks * block_samples

            try:
                self.base, self.layer = _alloc_pair(capacity)
                break
            except MemoryError:
                # Drop both before retrying. If base succeeded and layer
                # failed, holding base would guarantee the retry fails
                # too -- and it is the bigger of the two live objects.
                self.base = None
                self.layer = None
                gc.collect()

                seconds -= 0.5
                if seconds < min_seconds:
                    raise

        self.n_blocks = n_blocks
        self.capacity = capacity
        self.seconds = capacity / sample_rate

        # Which blocks the overdub layer has actually written. This is
        # how the layer gets "zeroed" for free: an unmarked block is
        # simply not read back, so we never memset 128 KB anywhere.
        self.layer_mark = bytearray(self.n_blocks)

        self.state = STOPPED
        self.pos = 0       # sample index, always a multiple of block
        self.length = 0    # 0 = nothing recorded yet

    # ------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------

    def record_toggle(self):
        """Record key (numpad 5). Empty -> record. Recording -> close the
        loop and play it. Playing <-> overdub."""
        s = self.state

        if s == STOPPED and self.length == 0:
            self.pos = 0
            self.state = RECORDING

        elif s == RECORDING:
            # pos is already block-aligned, so it IS the loop length.
            # Guard a double-tap inside one block, which would otherwise
            # set a zero-length loop and wrap on every sample.
            self.length = self.pos if self.pos >= self.block else self.block
            self.pos = 0
            self.state = PLAYING

        elif s == STOPPED:
            # Paused with content: record means overdub, so start moving.
            self.state = OVERDUB

        elif s == PLAYING:
            self.state = OVERDUB

        elif s == OVERDUB:
            self.state = PLAYING

    def play_toggle(self):
        """Play/pause key (numpad 4). No effect on an empty looper.
        Pausing from overdub drops to paused, not to recording."""
        if self.length == 0:
            return

        if self.state == STOPPED:
            self.state = PLAYING
        else:
            self.state = STOPPED

    def reset(self):
        """Reset key (numpad 1). Wipe everything -- this is the only way
        to scrap a take now that undo is gone.

        The sample buffers are deliberately left alone: length = 0 means
        nothing can be read, and clearing layer_mark means no overdub
        block is read back either, so the next take overwrites from 0.
        Zeroing 128 KB here would stall the audio loop for tens of
        milliseconds."""
        self.state = STOPPED
        self.pos = 0
        self.length = 0
        for i in range(self.n_blocks):
            self.layer_mark[i] = 0

    # ------------------------------------------------------------
    # Status
    # ------------------------------------------------------------

    def state_name(self):
        """Short lowercase name for the website JSON."""
        s = self.state
        if s == RECORDING:
            return "rec"
        if s == PLAYING:
            return "play"
        if s == OVERDUB:
            return "dub"
        return "pause" if self.length else "empty"

    def status_text(self):
        """Line for the OLED. Kept to 8 characters or fewer -- the row
        reads "Loop: <this>" and the screen is 16 characters wide."""
        s = self.state
        if s == RECORDING:
            return "REC"
        if s == PLAYING:
            return "PLAY"
        if s == OVERDUB:
            return "DUB"
        return "PAUSE" if self.length else "EMPTY"

    def is_sounding(self):
        """True when the looper is putting audio into the mix. main.py's
        silence fast path MUST consult this -- skipping the render
        because no key is held would otherwise mute the loop."""
        return self.state == PLAYING or self.state == OVERDUB

    def progress(self):
        """0-100 through the loop, for the visualiser. While recording
        this reports buffer used rather than loop position, since the
        loop has no length yet."""
        if self.state == RECORDING:
            return self.pos * 100 // self.capacity
        if self.length == 0:
            return 0
        return self.pos * 100 // self.length

    # ------------------------------------------------------------
    # OLED progress bar
    # ------------------------------------------------------------

    def update_bar(self, display):
        """Draw the bar and push ONLY the bottom page: 128 bytes instead
        of the 1024 a full show() sends. A full frame at 400 kHz takes
        ~25 ms, which exceeds one audio block's entire 16 ms budget --
        fine occasionally, ruinous at the 20 fps a smooth bar wants.
        This is ~3 ms, so it can run every block if you like.

        Leaves the driver's address window changed, which is harmless:
        ssd1306.show() sets both address registers itself."""
        if self.state == RECORDING:
            # While recording, the bar shows buffer CONSUMED -- the
            # useful number is how much room is left, not position in a
            # loop that does not have a length yet.
            fill = self.pos * 128 // self.capacity
        elif self.length:
            fill = self.pos * 128 // self.length
        else:
            fill = 0

        display.fill_rect(0, _BAR_Y, 128, 8, 0)
        display.hline(0, _BAR_Y + 6, 128, 1)     # baseline, always visible
        if fill > 0:
            display.fill_rect(0, _BAR_Y + 1, fill, 5, 1)

        display.write_cmd(_SET_COL_ADDR)
        display.write_cmd(0)
        display.write_cmd(127)
        display.write_cmd(_SET_PAGE_ADDR)
        display.write_cmd(_BAR_PAGE)
        display.write_cmd(_BAR_PAGE)
        display.write_data(display.buffer[_BAR_PAGE * 128:(_BAR_PAGE + 1) * 128])

    # ------------------------------------------------------------
    # Audio path
    # ------------------------------------------------------------

    @micropython.native
    def process(self, mix, n):
        """Called once per block with the post-effects int32 mix buffer.
        Records from it and/or adds playback into it, in place.

        Returns immediately when stopped, so an unused looper costs one
        comparison per block and nothing else."""
        state = self.state
        if state == STOPPED:
            return

        pos = self.pos
        base = self.base

        # ---- RECORDING ----
        if state == RECORDING:
            i = 0
            while i < n:
                v = mix[i] >> _SHIFT
                # Clamp: 8 voices can just exceed int16 after the shift,
                # and a wrapped sample is a hard click baked into the
                # loop permanently.
                if v > 32767:
                    v = 32767
                elif v < -32768:
                    v = -32768
                base[pos + i] = v
                i += 1

            pos += n
            if pos >= self.capacity:
                # Out of room: close the loop rather than stopping dead
                # or overwriting from the top mid-take.
                self.length = self.capacity
                self.state = PLAYING
                pos = 0
            self.pos = pos
            return

        # ---- PLAYING / OVERDUB ----
        # length and n are both whole blocks, so the wrap check happens
        # once per block instead of once per sample.
        layer = self.layer
        bi = pos // self.block
        marked = self.layer_mark[bi]

        if state == PLAYING:
            if marked:
                i = 0
                while i < n:
                    mix[i] += (base[pos + i] + layer[pos + i]) << _SHIFT
                    i += 1
            else:
                i = 0
                while i < n:
                    mix[i] += base[pos + i] << _SHIFT
                    i += 1

        else:   # OVERDUB
            if marked:
                # Already written on an earlier pass: accumulate.
                i = 0
                while i < n:
                    live = mix[i] >> _SHIFT
                    old = layer[pos + i]

                    s = old + live
                    if s > 32767:
                        s = 32767
                    elif s < -32768:
                        s = -32768
                    layer[pos + i] = s

                    # Add base plus the OLD layer, not the new sum --
                    # the live signal is already sitting in mix[i], and
                    # adding s would double it.
                    mix[i] += (base[pos + i] + old) << _SHIFT
                    i += 1
            else:
                # First time over this block: overwrite rather than
                # accumulate, which is what makes the mark array a free
                # substitute for zeroing the buffer.
                i = 0
                while i < n:
                    live = mix[i] >> _SHIFT
                    if live > 32767:
                        live = 32767
                    elif live < -32768:
                        live = -32768
                    layer[pos + i] = live
                    mix[i] += base[pos + i] << _SHIFT
                    i += 1
                self.layer_mark[bi] = 1

        pos += n
        if pos >= self.length:
            pos = 0
        self.pos = pos