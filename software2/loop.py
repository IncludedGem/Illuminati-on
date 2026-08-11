"""
Single-track looper with overdub -- HAcK 2026, Team 13.

Sits between the effects stage and the master volume in main.py:

    voices -> mix -> lowpass -> [LOOP TAP] -> master volume -> clip -> I2S

Two consequences of tapping there, both deliberate: effects bake into the
recording, and the volume knob still rides the loop.

Overdub model: BASE holds the first take, LAYER holds overdubs, and every
overdub pass accumulates into the same layer. There is no undo -- a
per-pass undo stack needs the previous layer merged into base on every new
pass, which is a loop over tens of thousands of samples inside the keypad
handler (50-100 ms of stall, an audible dropout). Scrap a bad take with
reset instead. layer_mark survives from that machinery and earns its keep
anyway: an unmarked block is not read back, so the layer never needs to be
memset.

Memory: mono int16 at 12000 Hz costs 24,000 bytes/s, and there are two
buffers -- 2 s -> 94 KB, 3 s -> 141 KB, 4 s -> 188 KB. Run gc.mem_free()
on your board before raising `seconds`; the constructor backs off in
half-second steps and the startup banner prints what it got.

Length is quantised to whole audio blocks, so playback does its wrap check
once per block instead of once per sample.

State machine:
    STOPPED --rec--> RECORDING --rec--> PLAYING <--rec--> OVERDUB
       ^                                   |                |
       +-------------play/pause------------+----------------+

RECORDING ends on a second record press or when the buffer fills, so a
forgotten press cannot wedge the instrument mid-performance.
"""

import gc
from array import array

STOPPED = 0
RECORDING = 1
PLAYING = 2
OVERDUB = 3

# Live mix peaks near +-256000 (8 voices * 32000), stored as int16 with a
# 3-bit shift: 256000 >> 3 = 32000. Playback shifts back by the same
# amount, so a recorded layer returns at the level it went in at.
_SHIFT = 3

_SET_COL_ADDR = 0x21
_SET_PAGE_ADDR = 0x22

# The progress bar owns the bottom 8-pixel page.
_BAR_PAGE = 7
_BAR_Y = 56


def largest_block():
    """Biggest single bytearray the heap can currently hand out.

    gc.mem_free() is total free bytes, which says nothing about whether any
    of it is contiguous. Call this before constructing a Looper if
    allocation is misbehaving.
    """
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
    """Allocate two zeroed int16 arrays of n_samples each.

    array('h', bytes_like) hits the buffer-protocol fast path: it memcpy's
    raw bytes, giving n int16 elements rather than iterating 2n byte values.

    Peak matters more than total. The scratch bytearray must stay alive
    while an array is copied out of it, so building both arrays from one
    live temporary peaks at 3x the buffer size -- 384 KB for a 4 s loop,
    which does not fit. Instead: build base from the temporary, drop the
    temporary, then build layer from base. Peak is 2x and a 4 s loop fits.

    Do not go back to extend(): each call allocates a new buffer and
    abandons the old one, fragmenting the heap past handing out anything
    large.
    """
    gc.collect()

    raw = bytearray(2 * n_samples)
    base = array("h", raw)

    # Dropped before layer is allocated -- this is the whole point.
    raw = None
    gc.collect()

    layer = array("h", base)
    gc.collect()

    # If a build ever iterates instead of memcpy'ing, this returns 2n
    # elements of byte values and every loop plays back as noise.
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
        """Allocate the largest loop up to `seconds` that actually fits,
        backing off in half-second steps rather than raising at boot. Read
        looper.seconds afterwards for the real length."""
        self.block = block_samples

        self.base = None
        self.layer = None

        while True:
            n_blocks = int(seconds * sample_rate / block_samples)
            capacity = n_blocks * block_samples

            try:
                self.base, self.layer = _alloc_pair(capacity)
                break
            except MemoryError:
                # Drop both before retrying: holding base would guarantee
                # the retry fails too, and it is the bigger object.
                self.base = None
                self.layer = None
                gc.collect()

                seconds -= 0.5
                if seconds < min_seconds:
                    raise

        self.n_blocks = n_blocks
        self.capacity = capacity
        self.seconds = capacity / sample_rate

        # Which blocks the overdub layer has written. This is how the layer
        # gets zeroed for free -- unmarked blocks are not read back.
        self.layer_mark = bytearray(self.n_blocks)

        self.state = STOPPED
        self.pos = 0        # sample index, always a multiple of block
        self.length = 0     # 0 = nothing recorded yet

    # ---------------- Transport ----------------

    def record_toggle(self):
        """Record key. Empty -> record. Recording -> close the loop and
        play it. Playing <-> overdub."""
        s = self.state

        if s == STOPPED and self.length == 0:
            self.pos = 0
            self.state = RECORDING

        elif s == RECORDING:
            # pos is block-aligned, so it is the loop length. The guard
            # catches a double-tap inside one block, which would set a
            # zero-length loop and wrap on every sample.
            self.length = self.pos if self.pos >= self.block else self.block
            self.pos = 0
            self.state = PLAYING

        elif s == STOPPED:
            self.state = OVERDUB

        elif s == PLAYING:
            self.state = OVERDUB

        elif s == OVERDUB:
            self.state = PLAYING

    def play_toggle(self):
        """Play/pause. No effect on an empty looper. Pausing from overdub
        drops to paused, not to recording."""
        if self.length == 0:
            return

        if self.state == STOPPED:
            self.state = PLAYING
        else:
            self.state = STOPPED

    def reset(self):
        """Wipe everything -- the only way to scrap a take.

        The sample buffers are left alone: length = 0 means nothing can be
        read, and clearing layer_mark means no overdub block is read back
        either. Zeroing 188 KB here would stall the audio loop.
        """
        self.state = STOPPED
        self.pos = 0
        self.length = 0
        for i in range(self.n_blocks):
            self.layer_mark[i] = 0

    # ---------------- Status ----------------

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
        """Line for the OLED, 8 characters or fewer -- the row reads
        "Loop: <this>" on a 16-character screen."""
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
        silence fast path must consult this."""
        return self.state == PLAYING or self.state == OVERDUB

    def progress(self):
        """0-100 through the loop. While recording this reports buffer
        used, since the loop has no length yet."""
        if self.state == RECORDING:
            return self.pos * 100 // self.capacity
        if self.length == 0:
            return 0
        return self.pos * 100 // self.length

    # ---------------- OLED progress bar ----------------

    def update_bar(self, display):
        """Draw the bar and push only the bottom page: 128 bytes rather
        than the 1024 a full show() sends, so this can run at 20 fps.

        Leaves the driver's address window changed, which is harmless --
        ssd1306.show() sets both address registers itself.
        """
        if self.state == RECORDING:
            # While recording the bar shows buffer consumed: the useful
            # number is how much room is left.
            fill = self.pos * 128 // self.capacity
        elif self.length:
            fill = self.pos * 128 // self.length
        else:
            fill = 0

        display.fill_rect(0, _BAR_Y, 128, 8, 0)
        display.hline(0, _BAR_Y + 6, 128, 1)
        if fill > 0:
            display.fill_rect(0, _BAR_Y + 1, fill, 5, 1)

        display.write_cmd(_SET_COL_ADDR)
        display.write_cmd(0)
        display.write_cmd(127)
        display.write_cmd(_SET_PAGE_ADDR)
        display.write_cmd(_BAR_PAGE)
        display.write_cmd(_BAR_PAGE)
        display.write_data(display.buffer[_BAR_PAGE * 128:(_BAR_PAGE + 1) * 128])

    # ---------------- Audio path ----------------

    @micropython.native
    def process(self, mix, n):
        """Called once per block with the post-effects int32 mix buffer.
        Records from it and/or adds playback into it, in place. Returns
        immediately when stopped, so an unused looper costs one compare."""
        state = self.state
        if state == STOPPED:
            return

        pos = self.pos
        base = self.base

        if state == RECORDING:
            i = 0
            while i < n:
                v = mix[i] >> _SHIFT
                # 8 voices can just exceed int16 after the shift, and a
                # wrapped sample is a click baked in permanently.
                if v > 32767:
                    v = 32767
                elif v < -32768:
                    v = -32768
                base[pos + i] = v
                i += 1

            pos += n
            if pos >= self.capacity:
                # Out of room: close the loop rather than stopping dead or
                # overwriting from the top mid-take.
                self.length = self.capacity
                self.state = PLAYING
                pos = 0
            self.pos = pos
            return

        # length and n are both whole blocks, so the wrap check happens
        # once per block rather than once per sample.
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

                    # Add base plus the OLD layer: the live signal is
                    # already in mix[i], and adding s would double it.
                    mix[i] += (base[pos + i] + old) << _SHIFT
                    i += 1
            else:
                # First pass over this block: overwrite rather than
                # accumulate, which is what makes layer_mark a free
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