"""
PICO 2 W MUSIC CONTROLLER -- HAcK 2026, Team 13
================================================

FILE LAYOUT
-----------
  main.py        this file: hardware setup, voices, filter, main loop,
                  AND the tone data -- see INSTRUMENTS below
  keypad.py       3x4 matrix scan + debounce (no dependency on anything)
  scale.py        key/octave/mode theory, pure functions
  display.py      OLED partial-redraw driver
  loop.py         overdubbing looper (record / overdub / reset)

There is deliberately no instruments.py. The tone data lives inline,
after the Looper is constructed, because the Looper's big contiguous
buffers must land on a still-clean heap -- an import would run at the
top of the file, i.e. before that.

NO DRUM KIT. The sample-playback drum voices, the .wav loader and the
eight one-shot samples have been removed and are not coming back in this
file. They were the project's second-largest allocation and needed ~26 KB
in a single contiguous run, which is what turned every heap hiccup into
a boot failure. With them gone the Looper is the only large allocation
left. If drums return, they need their own rate-matched kit -- the loader
required 12000 Hz mono 16-bit and the old kit was 16000 Hz.

RUN THIS FROM FLASH, NOT BY PASTING IT INTO THE REPL. A paste is held
and recopied as a growing buffer while it streams in, then parsed, all
before line one executes, which leaves the heap in scraps. Less fatal
now that nothing needs a 26 KB run, but the Looper still wants two clean
ones and will quietly hand you a shorter loop than you asked for if it
cannot get them. The banner prints what it actually got.

SIGNAL CHAIN
------------
    voices -> mix -> lowpass -> [LOOP TAP] -> master volume -> clip -> I2S

PIN MAP
-------
  Keypad rows    GP0, GP1, GP2, GP3      (high-Z except while scanning)
  Keypad cols    GP6, GP7, GP19          (internal pull-ups)
  Note buttons   GP15, 14, 13, 12, 11, 10, 9, 8   (active-low, pull-ups)
  Volume pot     GP26 (ADC0)
  Cutoff pot     GP27 (ADC1)
  OLED I2C       SDA=GP4, SCL=GP5
  I2S DAC        sck=GP16, ws=GP17, sd=GP18

KEYPAD
------
    1  loop reset       2  (unmapped)       3  cycle preset
    4  play / pause     5  loop record      6  cycle mode
    7  instrument -     8  octave -         9  key -
    *  instrument +     0  octave +         #  key +

Key 2 is free. It used to be loop undo/redo; that feature was scrapped,
so reset (key 1) is the only way to scrap a take.

WHERE THE SOUND LIVES  (read this before "replacing the synth")
---------------------------------------------------------------
This file contains the ENGINE -- phase accumulation, envelope stepping,
interpolation, mixing -- and the TONE DATA it reads: WAVETABLES and
ENVELOPES, in the INSTRUMENTS block.

The engine is the INTEGER form of the algorithm the standalone prototype
ran in floats: same wavetable-lookup shape, same ADSR shape, plus linear
interpolation. It is strictly cheaper per sample and does not allocate.
The tone data is the standalone's, unchanged in substance -- the same
harmonic recipes and the same ADSR numbers -- with one addition, peak
normalization, documented at make_table().

Two things from the standalone were deliberately NOT carried over:

  the float engine   MicroPython boxes every float on the heap, so the
                     inner loop (256 iterations per voice per block,
                     2048 with 8 voices) allocated constantly. That is
                     what made CPU load track voice count, and voice
                     count is the axis the garbling moved along. See
                     the fixed-point block in SYNTH VOICES.

  NOTE_FREQS /       one fixed note per button, one global instrument.
  INSTRUMENT         Superseded: pitch comes from build_scale_freqs()
                     via the preset's key/octave/mode, and the
                     instrument is per-preset and cycled from the
                     keypad.

The engine does not care how many entries either dict has -- only that
every value preset["sample"] can hold resolves to one, and that
DEFAULT_SAMPLE always exists as a fallback (see the .get() calls in
Voice.note_on).

Three things gate the sound before it reaches the DAC, in order. Check
them in this order if it sounds wrong:

  1. the cutoff knob (GP27). The lowpass sits in front of everything, so
     a low cutoff muffles the whole instrument no matter how rich the
     table is. read_pot() reports FULLY OPEN until that pot has been
     swept far enough to calibrate, so an unwired or untouched GP27
     cannot silently filter the set -- see POTS.
  2. MIX_HEADROOM. The standalone was loud partly because it clipped on
     any two-note chord; this divides down first so chords stay clean.
  3. the volume knob (GP26), square-law, so it feels linear.

INSTRUMENTS
-----------
Eleven tuned wavetables -- Sawtooth, Organ, Bell, Pluck, Piano, Guitar,
Bass, Flute, Clarinet, Trumpet, Strings -- cycled with '*' and '7'.

Two consequences of a real tone set worth knowing before the demo:

  ALIASING. Nyquist is 6000 Hz at this sample rate, and a wavetable's
  harmonics scale with the note, so harmonic n of a note at f lands at
  n*f whether or not that fits. The bright tables (Trumpet, Sawtooth,
  Organ, Pluck) carry content out to the 10th harmonic, which folds back
  as a metallic grit above roughly octave 5. This is inherent to
  single-table synthesis and is not a bug to hunt; the standalone had it
  worse at 11025 Hz. Keep bright patches in octaves 3-5, use the mellow
  ones (Flute, Bass, Clarinet, Piano) up high, or roll the cutoff knob
  down -- the filter sits before everything precisely so it can.

  LONGER TAILS. Bell releases over 1.9 s, Guitar 0.8 s, Strings 0.55 s.
  A voice in release is still rendering, so held-and-released passages
  keep more voices active than a short patch does, and those tails sum
  on top of whatever is being played now. If a busy passage distorts,
  that is MIX_HEADROOM, not an overrun -- raise it to 3.0 or back the
  volume knob off.

MODES
-----
Nine scales per preset: Major, Natural Minor, Melodic Minor, Harmonic
Minor, Dorian, Phrygian, Lydian, Mixolydian, Locrian. Melodic Minor is
direction-sensitive (its 6th and 7th degrees differ ascending vs.
descending) -- see MELODIC MINOR below.

LATENCY BUDGET  (read before touching BUF_SAMPLES or IBUF_BLOCKS)
-----------------------------------------------------------------
audio.write() blocks until the I2S peripheral has room, so the main loop
runs once per audio block. Press-to-sound latency is the sum of two
things, and the second is the one that bites:

  1. button poll interval   = BUF_SAMPLES / SAMPLE_RATE        = 21.3 ms
  2. audio already queued   = IBUF_BLOCKS * BUF_SAMPLES / RATE = 64 ms

In steady state the I2S buffer stays FULL -- the writer runs ahead until
write() blocks -- so every sample waits behind a full ibuf before it
reaches the DAC. Making the synth compute faster does not change this.
Only shrinking the queue does.

The standalone's settings (11025 Hz, BUF_SAMPLES 1024, ibuf 8192) are
NOT portable here: 1024/11025 is a 93 ms block and 8192 bytes is four
more of them, so press-to-sound would be about 460 ms. Fine for a script
that only plays notes, unplayable for an instrument you perform on.
LOOP_SECONDS is also budgeted against this rate. Keep the tone, not the
timing.

SPIKE BUDGET  (why chords used to cut out)
------------------------------------------
Render cost scales with BUF_SAMPLES and so does the budget (BUF_SAMPLES /
SAMPLE_RATE), so their ratio is fixed and A BIGGER BUFFER CANNOT FIX AN
OVERRUN CAUSED BY RENDERING. That it did help was the diagnosis: the
overruns were FIXED per-pass costs, which do not scale with the buffer
and so shrink as a fraction of a longer block. Each is capped at source:

  serial   json.dumps + print allocates ~200 bytes and writes to USB
           CDC, which BLOCKS if the host has the port open but is not
           draining it. Coalesced to one line per JSON_MIN_INTERVAL_MS.
  OLED     a preset cycle dirties all 5 text rows: ~15 ms of I2C.
           display.py queues pages and drains ONE (~3 ms) per pass, and
           a pass that already pushed the progress bar drains none.
  GC       skipped on any pass that already did serial or OLED work.

Worst case per pass is one page of I2C plus at most one JSON line, never
both plus a collection.

MELODIC MINOR
-------------
Ascending gets a raised 6th/7th; descending falls back to the natural
minor 6th/7th. Direction is judged by comparing the just-pressed
button's index to the PREVIOUS note's button index (higher = ascending).
`last_degree_index` remembers that previous index and resets to None
("no direction yet, assume ascending") on any edit that changes what a
button index means -- key, octave, mode, or preset switch -- so a
melodic line's direction cannot leak across an edit that changed the
scale underneath it.

SERIAL PROTOCOL
---------------
State changes print one line: '#' + JSON, coalesced to at most one line
per JSON_MIN_INTERVAL_MS (30 ms) -- quicker than a 30 fps video frame,
so the visualiser cannot perceive the difference, while a fast chord can
no longer queue three blocking USB writes into three consecutive audio
blocks. While the looper runs a heartbeat goes out every
JSON_LOOP_INTERVAL_MS (100 ms) so the website can animate loop position.
The website reads only lines starting with '#'; anything else is human
debug output. DEBUG = False silences it.

Fields: preset, octave, key, sample, mode, volume, cutoff, keys[8],
loop, loop_pos. `sample` is now one of the eleven instrument names --
never "Drums" and never the "--" placeholder the controller-only build
sent, so a visualiser that special-cased either can drop that branch.
"""

import gc
import time
import json
import math
from array import array
from micropython import const
from machine import Pin, I2C, ADC, I2S
import loop
from keypad import scan_keypad
from scale import shift_key, shift_octave, shift_mode, build_scale_freqs

DEBUG = False     # per-keypress serial chatter


# ============================================================
# AUDIO CONSTANTS + LOOPER   (allocated FIRST, deliberately)
# ============================================================
# The loop buffers are the largest allocation in the project and must be
# contiguous. gc.mem_free() reporting plenty free means nothing if it is
# fragmented into scraps -- so the Looper is constructed while the heap
# is still one clean block, before wavetables / OLED / I2S / cutoff-table
# allocations carve it up. Everything below is small and fits in
# whatever heap is left.
#
# It used to compete with a ~150 KB drum bank for that heap. With the
# drums gone it is the only large allocation, which is why this build
# has room the previous ones did not.

SAMPLE_RATE = 12000
BUF_SAMPLES = const(256)

# WHY 12000 AND NOT 16000
# -----------------------
# Block period is BUF_SAMPLES / SAMPLE_RATE, and the render work per
# block is a fixed 256 iterations per active voice regardless of rate.
# So the rate sets the BUDGET without changing the WORK:
#
#     16000 Hz -> 16.0 ms/block        12000 Hz -> 21.3 ms/block
#
# 16000 was a 30% pay cut on the audio budget bought for headroom above
# Nyquist we were not using. It showed up as garbling that got worse the
# more voices were held -- and, diagnostically, as garbling that
# DISAPPEARED on loop playback. The looper records post-filter mix, so a
# recording is a strictly lower-fidelity copy of the live signal; if it
# plays back cleaner, the samples were always fine and the DAC was being
# starved. That is an underrun, not a synthesis problem.
#
# The drum kit used to pin this rate hard (its wavs were rendered at
# 12000 and the loader refused anything else). That constraint is gone,
# but the budget argument above is not, and CUTOFF_MAX_HZ still tracks
# this number.

# Size the I2S buffer for double/triple buffering, NOT a big safety
# margin -- every extra block of ibuf is another block of latency between
# a keypress and the sound. Total latency is
# (1 + IBUF_BLOCKS) * BUF_SAMPLES / SAMPLE_RATE, so at 12000 Hz:
#
#     IBUF 2 -> 64 ms      IBUF 3 -> 85 ms      IBUF 4 -> 107 ms
#
# RAISED TO 4 FOR CLEANLINESS. 3 gives 85 ms of queue against a 21.3 ms
# per-pass budget, and the render fits that budget several times over --
# but the render was never what overran it. The OLED, the serial writes
# and GC are fixed costs that do not shrink with a faster synth, and any
# single pass that blows 21.3 ms starves the DAC and clicks. A fourth
# block is one more pass of slack to absorb that.
#
# The standalone never clicks partly because it queues ~460 ms and does
# nothing but render. This is the same trade at a fraction of the cost:
# 107 ms is still playable, 460 ms is not. If it turns out rock solid,
# 3 buys the latency back and 2 buys more.
IBUF_BLOCKS = 4

# Two int16 buffers (base take + overdub layer): 4 s costs 188 KB at
# this rate. The Looper backs off in half-second steps if that will not
# fit, so a tight board gets a shorter loop rather than a traceback --
# the startup banner prints what it actually got.
LOOP_SECONDS = 4

looper = loop.Looper(SAMPLE_RATE, BUF_SAMPLES, seconds=LOOP_SECONDS)


def _largest_free_run():
    """Biggest single allocation the heap can still serve, by binary
    search on bytearray().

    gc.mem_free() is a TOTAL and says nothing about shape. That
    distinction is the whole story of this project's memory failures --
    197 KB free, and not one 26 KB run in it. Kept now that the drum
    bank is gone because the Looper still needs long runs, and because
    a number in the banner beats guessing.

    ~18 probe allocations, each freed and collected immediately. Called
    once at boot, never in a hot path."""
    gc.collect()
    lo = 0
    hi = gc.mem_free()
    while lo < hi:
        mid = (lo + hi + 1) >> 1
        try:
            _probe = bytearray(mid)
        except MemoryError:
            gc.collect()
            hi = mid - 1
        else:
            _probe = None
            gc.collect()
            lo = mid
    return lo


# ============================================================
# INSTRUMENTS  (wavetables + ADSR envelopes)
# ============================================================
# Built after the Looper and not one line before it: the loop buffers
# need a clean heap, so every smaller allocation waits until they have
# landed. Resident cost is 11 tables x 256 samples x 2 bytes = ~5.6 KB,
# so trimming instruments is never the right answer to a MemoryError
# here -- LOOP_SECONDS is the only lever with real bytes behind it.
#
# Boot cost is ~17k sin() calls in interpreted MicroPython, a second or
# two of dead time before the banner prints. That is the price of
# generating tables rather than shipping them, and it keeps the recipes
# readable and editable in one place.
#
# TABLE_LEN is a local const, not imported from anywhere -- MicroPython's
# const() only folds literals or other consts declared in THIS module,
# which is what lets _PHASE_WRAP down in SYNTH VOICES fold to a
# compile-time literal instead of costing a runtime shift every note-on.
TABLE_LEN = const(256)
TABLE_AMP = 32000   # int16 peak -- see "Peak is 32000 * 32768 >> 15" on
                     # render_voice before changing this number.


# ---------------- Shared sine table ----------------
#
# ONE table of sin() values, built once, and every partial of every
# instrument is read out of it instead of calling math.sin again.
#
# This is what makes the band-limiting below affordable. The standalone
# calls math.sin once per partial per sample, which is ~17k calls for
# one table set; doing that for four octave bands would be ~70k calls
# and six to twelve seconds of dead time at boot. Reading a 256-entry
# table instead costs 256 sin() calls TOTAL and turns the rest into
# integer indexing.
#
# Partial n at position i is sin(2*pi*n*i/TABLE_LEN), i.e. this table
# read with a stride of n -- exact for integer n, and interpolated below
# for the non-integer partials Bell and Piano use.
_SINE = [math.sin(2.0 * math.pi * i / TABLE_LEN) for i in range(TABLE_LEN)]


def _sine_at(x):
    """sin(2*pi*x) for x in table-index units, linearly interpolated.

    Integer strides hit table entries exactly and the interpolation is a
    no-op. Bell's 2.71 and Piano's 5.08 do not, and rounding them to the
    nearest entry would detune those partials by up to half a table step
    -- audible as a slightly sour bell, which is the one instrument
    whose whole character is its inharmonic partials."""
    i = int(x) & (TABLE_LEN - 1)
    frac = x - int(x)
    if frac == 0.0:
        return _SINE[i]
    j = (i + 1) & (TABLE_LEN - 1)
    return _SINE[i] + (_SINE[j] - _SINE[i]) * frac


# ---------------- Instruments as PARTIALS, not functions ----------------
#
# Each entry is a tuple of (harmonic_number, amplitude). Same recipes and
# same numbers as the standalone -- but as DATA rather than baked into a
# function body, which is the whole point: a function can only be sampled
# as-is, whereas a partial list can be truncated, and truncating it is
# the only real cure for aliasing (see BANDS below).
#
# The standalone's /3.48-style divisors are gone. They normalized by the
# sum of coefficients, which is not the true peak anyway, and every table
# is peak-normalized after the fact now. Dropping them also means a
# band-limited version does not have to carry its parent's divisor.
#
# Sawtooth is generated rather than listed: a ramp's Fourier series is
# amplitude 1/n on every harmonic. The standalone drew the ramp
# directly, which is a mathematically infinite harmonic series and so
# the single worst aliaser in the set. Sixteen partials is already
# brighter than anything else here and is band-limitable.
PARTIALS = {
    "Sawtooth": tuple((n, 1.0 / n) for n in range(1, 17)),

    # Fuller principal + mixture chorus. Real organ "depth" comes from
    # many simultaneously sounding pipes at different pitches (a
    # chorus), not from one pipe's tone color.
    "Organ": ((1, 1.00),      # 8'   unison (fundamental)
              (2, 0.75),      # 4'   octave
              (3, 0.50),      # 2 2/3' twelfth (quint)
              (4, 0.40),      # 2'   fifteenth
              (5, 0.25),      # 1 3/5' tierce
              (6, 0.20),      # 1 1/3' larigot
              (8, 0.15),      # 1'   twenty-second
              (9, 0.10),      # mixture rank
              (10, 0.08),     # mixture rank
              (12, 0.05)),    # mixture top rank

    # Bells contain strong NON-INTEGER partials. This matters far more
    # than adding ordinary harmonics, and it is why _sine_at
    # interpolates rather than rounding to the nearest table entry.
    "Bell": ((1, 1.00), (2.71, 0.55), (4.07, 0.32),
             (5.83, 0.22), (7.91, 0.12)),

    # Young's plucked-string theorem: amplitude of harmonic n is
    # proportional to sin(n*pi*p)/n for fractional pluck position p. A
    # harpsichord plectrum plucks close to the bridge (p ~ 1/8), which
    # is what makes it tinny and bright rather than round. For p = 1/8:
    "Pluck": ((1, 1.00), (2, 0.92), (3, 0.81),
              (4, 0.65), (5, 0.48), (6, 0.31)),

    # Piano strings are not perfectly harmonic -- the slightly sharp
    # upper partials are what stop it sounding like an organ.
    "Piano": ((1, 1.00), (2.01, 0.62), (3.02, 0.38), (4.04, 0.24),
              (5.08, 0.16), (6.13, 0.10), (7.20, 0.06)),

    # Same theorem as Pluck, but classical fingerstyle: nylon strings
    # plucked closer to mid-string (p ~ 1/5, over the soundhole) rather
    # than near the bridge. n=5 is absent because plucking at exactly
    # the 1/5 point places a node there, so the 5th harmonic physically
    # cannot be excited -- not a simplification.
    "Guitar": ((1, 1.00), (2, 0.81), (3, 0.54), (4, 0.25), (6, 0.17)),

    # Strong fundamental, controlled upper harmonics: thick without
    # becoming buzzy.
    "Bass": ((1, 1.00), (2, 0.48), (3, 0.22), (4, 0.10)),

    # The purest tone in the orchestra -- genuinely poor in harmonics,
    # just enough above the fundamental to avoid a bare sine. Keep this
    # weak; adding louder harmonics is not what makes a flute a flute.
    "Flute": ((1, 1.00), (2, 0.15), (3, 0.07),
              (4, 0.03), (5, 0.015), (6, 0.008)),

    # A closed cylindrical pipe, hence dominated by ODD harmonics.
    # Weighted toward the chalumeau register, where the fundamental
    # carries most of the energy -- the warm, hollow, bass-leaning low
    # register rather than the brighter clarion.
    "Clarinet": ((1, 1.00), (2, 0.08), (3, 0.55), (4, 0.10),
                 (5, 0.30), (7, 0.14), (9, 0.08)),

    # Among the brightest brass: the cylindrical bore after the
    # mouthpiece permits strong nonlinear wave-steepening down the tube,
    # pushing energy into high harmonics far more than a conical bore
    # like a flugelhorn. Which also makes it the set's worst aliaser
    # after Sawtooth, and the instrument BANDS below helps most.
    "Trumpet": ((1, 1.00), (2, 0.85), (3, 0.72), (4, 0.58), (5, 0.46),
                (6, 0.36), (7, 0.27), (8, 0.20), (9, 0.14), (10, 0.09)),

    # Many harmonics, but less aggressive high-frequency content than
    # brass.
    "Strings": ((1, 1.00), (2, 0.72), (3, 0.50), (4, 0.35),
                (5, 0.24), (6, 0.16), (7, 0.10), (8, 0.06)),
}


# ---------------- BANDS: per-octave band-limited tables ----------------
#
# THE ALIASING FIX. A wavetable's harmonics scale with the note, so
# harmonic n of a note at f lands at n*f whether or not that fits under
# Nyquist. At 12000 Hz Nyquist is 6000, so Trumpet's 10th harmonic
# exceeds it above 600 Hz and folds back as inharmonic grit that gets
# worse the higher you play. One table cannot avoid this: the content
# that aliases up high is the same content that makes it sound right
# down low.
#
# So each instrument gets FOUR tables instead of one, each holding only
# the partials that stay under ANTIALIAS_HZ for the highest note in its
# band. Voice.note_on picks the band from the note's frequency. Low
# notes keep the full spectrum and sound exactly as before; high notes
# lose the partials that would have aliased anyway, so they get duller
# rather than dirtier. Dull is a sound; aliasing is a fault.
#
# Cost is 11 instruments x 4 bands x 512 bytes = ~22 KB, which is
# affordable only because the drum bank is gone -- it alone was ~150 KB.
# Boot cost is near zero thanks to _SINE above.
#
# Band tops are octave boundaries. The last is C7, the top of the
# playable range (octave 6, degree 7), since that band has no natural
# ceiling.
ANTIALIAS_HZ = 5400          # matches CUTOFF_MAX_HZ, ~10% under Nyquist
BAND_TOP_HZ = (262.0, 523.0, 1046.0, 2093.0)
N_BANDS = len(BAND_TOP_HZ)


def make_band(partials, top_hz):
    """One band-limited table: every partial whose frequency stays under
    ANTIALIAS_HZ at `top_hz`, summed and peak-normalized to int16.

    NORMALIZED PER BAND, not once per instrument. Dropping partials
    genuinely lowers a waveform's peak, so scaling every band by the
    parent's factor would make the same instrument quieter as you played
    higher -- a level change riding on top of a timbre change, which
    reads as the keyboard going weak at the top rather than as
    band-limiting. Per-band normalization keeps perceived loudness even
    across the range.

    At least the fundamental always survives: max_n can fall below 1 for
    a high enough band, and a table of zeroes is a silent instrument.

    BUILT IN PLACE, no intermediate lists -- a 256-element list of boxed
    floats is ~5 KB of transient garbage, and this now runs 44 times
    rather than 11. Fill at TABLE_AMP scale tracking the integer peak,
    then rescale in place. The rescale multiply is bounded by
    TABLE_AMP * TABLE_AMP = 1.024e9, just inside MicroPython's 31-bit
    small-int ceiling, so raising TABLE_AMP above 32767 would start
    allocating big ints here."""
    max_n = ANTIALIAS_HZ / top_hz

    kept = []
    for n, amp in partials:
        if n <= max_n:
            kept.append((n, amp))
    if not kept:
        kept = [(partials[0][0], partials[0][1])]

    tbl = array("h", bytearray(TABLE_LEN * 2))

    peak = 0
    for i in range(TABLE_LEN):
        v = 0.0
        for n, amp in kept:
            v += amp * _sine_at(n * i)
        s = int(v * TABLE_AMP)
        # The un-normalized sum can exceed TABLE_AMP before rescaling,
        # so clamp into int16 range on the way into the array rather
        # than letting the assignment overflow.
        if s > 32767:
            s = 32767
        elif s < -32768:
            s = -32768
        tbl[i] = s
        if s < 0:
            s = -s
        if s > peak:
            peak = s

    if peak > 0 and peak != TABLE_AMP:
        for i in range(TABLE_LEN):
            tbl[i] = tbl[i] * TABLE_AMP // peak

    return tbl


# Built one band at a time with a collection between, rather than as one
# nested comprehension: each make_band call churns transient floats, and
# 44 of them back to back would leave that garbage interleaved with the
# tables that survive.
WAVETABLES = {}
for _name in ("Sawtooth", "Organ", "Bell", "Pluck", "Piano", "Guitar",
              "Bass", "Flute", "Clarinet", "Trumpet", "Strings"):
    _bands = []
    for _b in range(N_BANDS):
        try:
            _bands.append(make_band(PARTIALS[_name], BAND_TOP_HZ[_b]))
        except MemoryError:
            # ~22 KB total across the set, so this is never really about
            # the tables -- report the budget rather than the last straw.
            raise MemoryError(
                "out of heap building '" + _name + "' band " + str(_b)
                + " -- free=" + str(gc.mem_free()) + ", largest run="
                + str(_largest_free_run()) + ". Tables are 512 bytes each,"
                + " so this is the Looper: run from FLASH rather than"
                + " pasting into the REPL, and if it is already on flash"
                + " lower LOOP_SECONDS. Dropping N_BANDS to 2 buys back"
                + " ~11 KB at the cost of aliasing in the upper octaves.")
        gc.collect()
    WAVETABLES[_name] = tuple(_bands)
_name = None
_bands = None
_b = None
_SINE = None      # 256 boxed floats, only needed while building
gc.collect()


def band_for(freq):
    """Which band-limited table a note at `freq` should play from.

    Chosen ONCE at note_on and held for the life of the voice. A note
    does not change band while sounding, so a sustained note cannot
    change timbre under you -- and render_voice never has to check."""
    b = 0
    while b < N_BANDS - 1 and freq > BAND_TOP_HZ[b]:
        b += 1
    return b


# (attack_ms, decay_ms, sustain_level, release_ms), straight from the
# standalone. Every key here must exist in PARTIALS and vice versa --
# Voice.note_on looks the same name up in both, and a name present in
# only one silently falls back to DEFAULT_SAMPLE's other half, which
# sounds like the right instrument with the wrong envelope (or the
# reverse) and is miserable to spot by ear.
ENVELOPES = {
    # Synth
    "Sawtooth": (15, 120, 0.75, 200),

    # Instruments
    "Organ":    (12,  40, 0.97, 400),
    "Bell":     (1,  400, 0.22, 1900),
    "Pluck":    (2,  200, 0.09, 90),

    "Piano":    (2,  450, 0.12, 450),
    "Guitar":   (3,  100, 0.25, 800),
    "Bass":     (3,  190, 0.15, 90),

    "Flute":    (90, 100, 0.92, 240),
    "Clarinet": (20,  45, 0.94, 150),
    "Trumpet":  (25,  50, 0.85, 160),
    "Strings":  (140, 100, 0.92, 550),
}

# The one name guaranteed to resolve in both dicts, used by the .get()
# fallbacks in Voice.note_on so a preset naming a sample that no longer
# exists gets a sound instead of a KeyError mid-set. Sawtooth because it
# is also index 0 of SAMPLE_NAMES, so shift_sample's fallback and this
# one agree.
DEFAULT_SAMPLE = "Sawtooth"

# Cycle order for '*' (forward) and '7' (back).
SAMPLE_NAMES = (
    "Sawtooth", "Organ", "Bell", "Pluck", "Piano", "Guitar", "Bass",
    "Flute", "Clarinet", "Trumpet", "Strings",
)


def shift_sample(name, direction):
    """Cycle to the next/previous instrument name. Falls back to index 0
    (DEFAULT_SAMPLE) if `name` is not recognized, so a stale preset value
    -- "Drums", say, from a saved state written by an older build --
    cannot get this stuck."""
    try:
        idx = SAMPLE_NAMES.index(name)
    except ValueError:
        idx = 0
    idx = (idx + direction) % len(SAMPLE_NAMES)
    return SAMPLE_NAMES[idx]


# ============================================================
# 8 NOTE BUTTONS
# ============================================================

BUTTON_PINS = (15, 14, 13, 12, 11, 10, 9, 8)
buttons = [Pin(p, Pin.IN, Pin.PULL_UP) for p in BUTTON_PINS]
NUM_VOICES = len(BUTTON_PINS)   # one voice per button

# bytearrays, not lists of bools: a list comprehension would allocate a
# fresh 8-element list on every loop pass, and allocation is what
# eventually triggers a GC pause in the middle of an audio block. These
# are written in place and never reallocated.
key_bits = bytearray(8)
prev_key_bits = bytearray(8)

# Which button index (0-7) was most recently note-on'd, for Melodic
# Minor's ascending/descending 6th & 7th. None means "no direction yet"
# -- treated as ascending. Reset to None on any edit that changes what a
# button index means: key, octave, mode, or preset switch.
last_degree_index = None


# ============================================================
# PRESETS + REPORTED STATE
# ============================================================
# A preset stores only what the keypad edits: octave, key, sample, mode.
# It deliberately does NOT store volume or cutoff -- those are physical
# pots, and recalling a stored volume that disagrees with the knob's
# position means the value snaps the instant you touch it. The knob
# always wins.
#
# PRESET_FIELDS lists every key a preset owns. sync_state() loops over
# it instead of hand-writing one line per field, so adding a preset
# field cannot silently forget its sync line -- a bug invisible in
# testing that shows up on stage as "I changed it but nothing updated."
#
# The two presets start on DIFFERENT instruments so key '3' is audibly a
# preset switch and not just a transpose. Piano is a safe demo default:
# mellow enough not to alias in the upper octaves, percussive enough to
# hear the envelope.

PRESET_FIELDS = ("octave", "key", "sample", "mode")

presets = [
    {"octave": 4, "key": "C", "sample": "Piano", "mode": "Major"},
    {"octave": 5, "key": "F", "sample": "Organ", "mode": "Major"},
]

active_preset = 0

state = {
    "preset": 1,
    "octave": 4,
    "key": "C",
    "sample": "Piano",
    "mode": "Major",
    "volume": 75,
    "cutoff": 100,          # 100 = filter fully open (bypassed)
    "keys": [False] * 8,
    "loop": "empty",
    "loop_pos": 0,
}


def sync_state():
    """Copy every preset-owned field into the reported state. Called
    once per update rather than at every edit site, so the mirror cannot
    drift out of step with the preset behind it."""
    p = presets[active_preset]
    state["preset"] = active_preset + 1
    for field in PRESET_FIELDS:
        state[field] = p[field]


# ============================================================
# POTS (self-calibrating)
# ============================================================

POT_VOLUME = const(0)
POT_CUTOFF = const(1)

adc_channels = (ADC(26), ADC(27))

# Pots rarely swing the full 0-65535 range (wiring resistance, pot
# tolerance), and hardcoding those endpoints gives the classic "bottom
# never reaches 0, halfway already reads 100" symptom. So we learn the
# real endpoints from the widest travel seen since power-on: sweep each
# pot end to end once after boot to calibrate.
#
# One entry PER CHANNEL. As single globals this was fine with one pot
# and silently wrong with two -- both channels would have shared
# whichever pot happened to swing widest.
adc_min = [65535] * len(adc_channels)
adc_max = [0] * len(adc_channels)

# Require this much travel before trusting the learned endpoints. Below
# it the "range" is just ADC noise a few counts wide, and dividing by it
# makes the reading snap randomly between 0 and 100. 8000 is ~12% of
# full scale: well above noise, well below any real pot's travel.
MIN_ADC_SPAN = 8000

VOLUME_DEADBAND = 2   # percent of change required to report a new value
CUTOFF_DEADBAND = 2

previous_volume = -1
previous_cutoff = -1


def read_pot(ch, unswept_default=-1):
    """Return 0-100 for ADC channel index `ch`, using that channel's own
    learned calibration. Averages 4 reads: the deadband already swamps
    ADC noise for the reported value, but adc_min/adc_max latch onto
    extremes permanently, so one noise spike would widen a channel's
    learned range for the rest of the set.

    `unswept_default`, if given (0-100), is what this channel reports
    until it has been swept far enough to calibrate. Cutoff passes 100,
    and that is not cosmetic: the lowpass sits in front of everything in
    the chain, and an UNWIRED or merely untouched GP27 floats, so the raw
    fallback would scale noise into an arbitrary cutoff and quietly
    muffle the entire instrument. Failing open means a miswired filter
    knob costs you the filter, not the sound. Volume passes nothing and
    keeps the raw fallback, because a wired pot's raw reading is already
    close to its true position and jumping to a default would be the
    worse surprise."""
    a = adc_channels[ch]
    raw = (a.read_u16() + a.read_u16() + a.read_u16() + a.read_u16()) >> 2

    # Learned range only ever grows, never shrinks.
    if raw < adc_min[ch]:
        adc_min[ch] = raw
    if raw > adc_max[ch]:
        adc_max[ch] = raw

    span = adc_max[ch] - adc_min[ch]
    if span >= MIN_ADC_SPAN:
        pct = round((raw - adc_min[ch]) / span * 100)
    elif unswept_default >= 0:
        return unswept_default           # not swept yet, fail safe
    else:
        pct = round(raw / 65535 * 100)   # not swept yet, best guess

    return max(0, min(100, pct))


# ============================================================
# OLED
# ============================================================

import display as oled
i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400000)
oled.init(i2c)


# ============================================================
# I2S AUDIO OUTPUT
# ============================================================

audio = I2S(
    0,
    sck=Pin(16),
    ws=Pin(17),
    sd=Pin(18),
    mode=I2S.TX,
    bits=16,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=BUF_SAMPLES * 2 * IBUF_BLOCKS,   # 2 bytes per 16-bit sample
)


# ============================================================
# SYNTH VOICES
# ============================================================

# Envelope stages as ints, not strings. const() inlines these as literal
# integers at compile time, so the stage comparisons in render_voice --
# which run once per sample per voice, up to 2048 times per block --
# become integer compares instead of object compares. The standalone
# compared strings here.
_IDLE = const(0)
_ATTACK = const(1)
_DECAY = const(2)
_SUSTAIN = const(3)
_RELEASE = const(4)


# ---------------- Fixed-point scales for the voice render ----------------
#
# The standalone carries a float phase, a float envelope level, and does
# `int(phase) % TABLE_LEN` every sample. MicroPython boxes every float on
# the heap, so that inner loop -- 256 iterations per voice per block,
# 2048 with 8 voices held -- allocates constantly and runs the slowest
# arithmetic the interpreter has. That is what made CPU load track voice
# count, and voice count is exactly the axis the garbling moved along.
#
# The standalone gets away with it because it runs 1024-sample blocks at
# 11025 Hz behind a 4-block ibuf: a 93 ms budget and ~460 ms of queue to
# hide any overrun in. This build spends that latency on playability
# instead, which only works if the render is cheap. So the arithmetic
# below is integer -- SAME algorithm, same envelope shape, no allocation,
# no GC pressure -- and the whole loop stays inside MicroPython's 31-bit
# small-int range (+-1.07e9) so nothing silently promotes to a big int.
#
# PHASE -- Q16.16. phase_inc = freq * TABLE_LEN * 65536 / SAMPLE_RATE.
# Top note (octave 6, degree 7 = C7, 2093 Hz) gives ~2.9e6, far below
# _PHASE_WRAP, which is what lets the wrap stay a single subtract rather
# than the standalone's modulo.
_PHASE_BITS = const(16)
#
# TABLE_LEN is a local const (see INSTRUMENTS above), not an imported
# runtime name, so const() can fold this directly instead of needing a
# hardcoded literal plus a boot-time assert to catch drift.
_PHASE_WRAP = const(TABLE_LEN << 16)

# INTERPOLATION FRACTION -- Q8, NOT the full Q16. (s1 - s0) can reach
# 64000, and 64000 * 65535 = 4.2e9 overflows a small int and would start
# allocating big ints inside the audio loop. 64000 * 255 = 1.6e7 is safe,
# and 256 steps between adjacent table entries is already finer than the
# table's own 16-bit resolution can express.
_FRAC_BITS = const(8)
_FRAC_MASK = const(255)

# ENVELOPE LEVEL -- Q23 for the accumulator, used at Q15 for the multiply.
# Q15 alone is too coarse to ACCUMULATE in over a long release -- Q23
# gives the step 256x the resolution, and shifting down by 8 at the point
# of use keeps the multiply in range: 32000 * 32768 = 1.048e9, just under
# the 1.07e9 small-int ceiling.
#
# The long releases in ENVELOPES are exactly the case this was chosen
# for: Bell at 1900 ms is 22800 samples, and at Q15 that release step
# would floor to zero and max(1, ...) in note_on would stretch the tail
# to nonsense. At Q23 it comes out at 80 per sample, a real ramp.
_LVL_BITS = const(23)
_LVL_ONE = const(8388608)                 # 1 << 23
_LVL_TO_Q15 = const(8)                    # >> this before multiplying

# Absolute floor at which a release gives up and snaps to zero: -54 dBFS
# of the envelope, before MIX_HEADROOM and the volume knob touch it. An
# exponential never actually reaches zero, so something has to end it,
# and the alternative -- waiting for the +1 in the step below to walk it
# down one unit at a time -- would leave a Bell inaudibly ringing for
# seconds while its voice stayed allocated.
_ENV_FLOOR = const(16384)                 # 1 << 14


def _env_shifts(n_samples):
    """Two shift counts whose reciprocals sum to the per-sample fraction
    an exponential needs to fall to 1% of its starting gap in n_samples.

    WHY SHIFTS AND NOT A MULTIPLY. Exponential decay is level *= r each
    sample, and r is very close to 1 -- Bell's release needs 0.99980, so
    about 16 fractional bits. level is Q23 and peaks at 8388608, and
    8388608 * 65536 = 5.5e11, which blows straight past MicroPython's
    31-bit small-int ceiling and starts allocating big ints inside the
    audio loop. That is the one thing this whole file is arranged to
    avoid.

    A sum of two reciprocal powers of two -- level -= (level >> a) +
    (level >> b) -- is pure integer shifting, cannot overflow at any
    level, and lands within a few percent of any ratio we need. One
    shift alone would only give ratios a factor of 2 apart, i.e. decay
    times off by up to 100%; the second shift brings that to under 40%,
    which for envelope times that were chosen by ear anyway is well
    inside taste.

    `a` is clamped at 13 so that (level >> a) is still non-zero near
    _ENV_FLOOR -- otherwise a long release would stop moving before it
    reached the floor. `b` needs no clamp: if (level >> b) rounds to
    zero, `a` is still carrying the decay.

    Runs once per keypress, so the float maths here costs nothing."""
    d = 1.0 - 0.01 ** (1.0 / n_samples)

    a = 1
    while a < 13 and (1.0 / (1 << a)) > d:
        a += 1

    rem = d - 1.0 / (1 << a)
    if rem <= 0:
        b = 30            # `a` already overshoots; make `b` contribute nothing
    else:
        b = 1
        while b < 30 and (1.0 / (1 << b)) > rem:
            b += 1

    return a, b


class Voice:
    # __slots__ avoids a per-instance dict: less RAM, faster attribute
    # access, and no dict growth to trigger GC mid-note.
    __slots__ = (
        "active", "table", "phase", "phase_inc", "stage", "level",
        "attack_step", "sustain_level",
        "decay_a", "decay_b", "decay_end",
        "release_a", "release_b", "release_end",
    )

    def __init__(self):
        # All integers -- see the fixed-point scale block above. Seeding
        # these as floats (as the standalone does) would let the very
        # first note run one block of mixed int/float arithmetic before
        # note_on() overwrote them.
        self.active = False
        self.table = None
        self.phase = 0
        self.phase_inc = 0
        self.stage = _IDLE
        self.level = 0
        self.attack_step = 0
        self.sustain_level = 0
        self.decay_a = 1
        self.decay_b = 30
        self.decay_end = 0
        self.release_a = 1
        self.release_b = 30
        self.release_end = _ENV_FLOOR

    def note_on(self, freq, instrument):
        # Float maths is fine HERE -- note_on runs once per keypress, not
        # once per sample. Everything it stores is an integer, so the
        # render loop never sees a float.
        #
        # The two .get() fallbacks land on DEFAULT_SAMPLE, the one entry
        # both dicts are guaranteed to have.
        # Band chosen here, once, from the note's own frequency -- see
        # BANDS above. render_voice is unchanged and never knows there
        # is more than one table.
        self.table = WAVETABLES.get(
            instrument, WAVETABLES[DEFAULT_SAMPLE])[band_for(freq)]

        # CLICK-FREE RETRIGGER. Hitting a button whose voice is still
        # sounding -- a repeated note, or one caught mid-release --  used
        # to slam phase and level both back to 0. Level dropping from
        # wherever it was to zero in one sample is a step discontinuity,
        # and so is the waveform jumping to table index 0 mid-cycle.
        # Two clicks on every repeated note.
        #
        # Restarting from the CURRENT phase and level removes both. The
        # attack then ramps from where the old note was rather than from
        # silence, which is also what a re-struck string actually does.
        # A genuinely new note (voice idle) still starts clean at zero.
        if not self.active or self.stage == _IDLE:
            self.phase = 0
            self.level = 0
        # Q16.16 table steps per output sample. Stays well below
        # _PHASE_WRAP for every note in our range, which is what lets
        # render_voice wrap with a single subtract instead of a modulo.
        self.phase_inc = int(freq * TABLE_LEN * 65536 / SAMPLE_RATE)

        a_ms, d_ms, s_lvl, r_ms = ENVELOPES.get(
            instrument, ENVELOPES[DEFAULT_SAMPLE])
        a_samples = max(1, int(a_ms * SAMPLE_RATE / 1000))
        d_samples = max(1, int(d_ms * SAMPLE_RATE / 1000))
        r_samples = max(1, int(r_ms * SAMPLE_RATE / 1000))

        s_level = int(s_lvl * _LVL_ONE)

        # max(1, ...) on every step: a step that floors to 0 is a voice
        # that never leaves its stage -- a note stuck on forever, which
        # on stage is worse than any amount of envelope inaccuracy.
        # Attack stays LINEAR. It runs from zero, where an exponential
        # has nothing to be proportional to, and a linear ramp up is
        # what a real attack transient looks like anyway. max(1, ...)
        # because a step that floors to 0 is a voice that never leaves
        # its stage -- a note stuck on forever, which on stage is worse
        # than any amount of envelope inaccuracy.
        self.attack_step = max(1, _LVL_ONE // a_samples)
        self.sustain_level = s_level

        # Decay and release are EXPONENTIAL -- see _env_shifts. Each
        # stage also carries the level at which it gives up, set at the
        # design point (1% of the distance it had to travel) rather than
        # at exact arrival, because an exponential's last 1% takes as
        # long again as the whole audible part.
        self.decay_a, self.decay_b = _env_shifts(d_samples)
        self.decay_end = s_level + ((_LVL_ONE - s_level) >> 7)

        self.release_a, self.release_b = _env_shifts(r_samples)
        r_end = s_level >> 7
        if r_end < _ENV_FLOOR:
            r_end = _ENV_FLOOR
        self.release_end = r_end

        self.stage = _ATTACK
        self.active = True

    def note_off(self):
        if self.active and self.stage != _RELEASE:
            self.stage = _RELEASE


voices = [Voice() for _ in range(NUM_VOICES)]

# int32 accumulator so all 8 voices can sum before a single clip at the
# end -- clipping per voice would distort long before the mix is actually
# too loud. Everything here is preallocated: no allocation in the audio
# path, so GC can never pause us mid-block.
mix_buf = array("l", [0] * BUF_SAMPLES)
out_buf = array("h", [0] * BUF_SAMPLES)

# zero_buf exists purely to clear mix_buf with a C-level slice copy
# instead of the standalone's 256 interpreted stores.
zero_buf = array("l", [0] * BUF_SAMPLES)

# Written straight to I2S when nothing is sounding, so silence costs one
# memcpy instead of a full render + filter + scale + clip pass.
silence_buf = array("h", [0] * BUF_SAMPLES)

# Master headroom divisor. 8 voices at full scale sum to 256000 against a
# 32000 output ceiling, so some division is mandatory. The standalone
# used a flat VOLUME = 80 and simply clipped on any two-note chord.
#
# 2.0 splits the difference: one note peaks at 16000 (half scale, plenty
# of room to be heard), a two-note chord at full scale, and three or more
# clip progressively. If it distorts while a loop is playing underneath
# -- the loop adds as much again on top of the live voices -- go to 3.0
# or back the volume knob off, which is what a real looper expects.
#
# The tables are peak-normalized, so "full scale" means full scale for
# every instrument rather than just the sawtooth, and the long-release
# patches keep old notes summing under new ones. 3.0 is a more likely
# landing spot here than the arithmetic alone suggests.
MIX_HEADROOM = 2.0

# Where the soft knee starts, in output units. Below this the output
# stage is bit-identical to a straight wire; above it the excess is
# scaled by a quarter.
#
# 26000 of 32000, chosen by measurement rather than taste. The knee has
# to start below the ceiling to soften the APPROACH to it, but every
# unit below is headroom that gets gently compressed when it did not
# need to be. Measured on the harmonics that actually matter -- the 7th
# and above, which are both what makes clipping sound harsh and what
# folds back as aliasing at this sample rate -- 26000 beats both a hard
# clip and a lower knee at every overload level, while staying perfectly
# linear 6000 units further up than a knee at 20000 would.
_KNEE = const(26000)

# Precomputed Q10 master gain, indexed by volume percent, same shape as
# CUTOFF_TABLE below.
#
# Square law approximates how the ear hears loudness, so the pot feels
# linear across its travel. Doing that as `x * x / MIX_HEADROOM` inside
# generate_block would mean a float multiply per sample in the output
# pass -- 256 boxed floats per block, in the one function most carefully
# written to avoid exactly that. Table it once at boot and the whole
# audio path is integer end to end.
#
# Q10 and not Q12, for the same range reason as the filter coefficient:
# mb[i] reaches ~512000 with 8 voices plus loop playback, and
# 512000 * 512 = 2.6e8 sits well inside the 31-bit small-int ceiling
# while 4096-scale would not.
VOL_TABLE = tuple(
    int((p / 100.0) * (p / 100.0) / MIX_HEADROOM * 1024) for p in range(101)
)


# ---------------- One-pole lowpass ----------------
#
#   y[n] = y[n-1] + k * (x[n] - y[n-1])
#
# k is the per-sample fraction of the way the output moves toward the
# input: k = 1 - exp(-2*pi*fc/fs). Fixed point with a 10-bit scale, so k
# runs 0..1024 and the per-sample cost is a subtract, a multiply, a shift
# and an add -- integer only, no floats in the filter path.
#
# Headroom check: |x - y| peaks near 8 voices * 32000 * 2 = 512000, and
# 512000 * 1024 = 524M, comfortably inside MicroPython's 31-bit small int
# (+-1.07e9). A 12-bit scale would overflow into heap-allocated big ints
# -- allocation inside the audio loop, exactly what everything else here
# is arranged to avoid.

CUTOFF_MIN_HZ = 60
# Kept just under Nyquist at the CURRENT SAMPLE_RATE -- this constant
# must move whenever SAMPLE_RATE does. At 12000 Hz Nyquist is 6000 Hz;
# 5400 keeps a ~10% margin. Fully open (pct 100) bypasses the filter
# outright, so the ceiling only shapes the top of the knob's travel.
CUTOFF_MAX_HZ = 5400


def _cutoff_k(pct):
    """Map pot percent to a filter coefficient, EXPONENTIALLY in
    frequency. Pitch perception is logarithmic, so a linear pot-to-k map
    would put nearly the whole audible sweep in the last 10% of knob
    travel -- you would turn it most of the way hearing nothing, then
    everything at once. This gives even musical travel end to end."""
    if pct >= 100:
        return 1024   # fully open: y tracks x exactly, filter is bypassed
    fc = CUTOFF_MIN_HZ * (CUTOFF_MAX_HZ / CUTOFF_MIN_HZ) ** (pct / 100.0)
    k = 1.0 - math.exp(-2 * math.pi * fc / SAMPLE_RATE)
    return max(1, min(1024, int(k * 1024)))


CUTOFF_TABLE = tuple(_cutoff_k(p) for p in range(101))

# Filter memory, carried across blocks. A per-block reset would put a
# discontinuity at every block boundary -- a 43 Hz buzz under everything.
#
# TWO stages, not one. A single one-pole rolls off at 6 dB/octave, which
# is so gentle that sweeping the cutoff reads as a tone control rather
# than a filter -- there is always plenty of signal left an octave above
# the corner. Cascading two identical one-poles gives 12 dB/octave for
# one extra multiply and shift PER BLOCK SAMPLE, not per voice, so the
# cost does not scale with polyphony.
#
# The pair is -6 dB at the nominal corner rather than -3, so the same
# knob position sounds slightly darker than it did. That is the trade,
# and it is the right way round: the knob now has an audible effect
# across its whole travel instead of only at the very bottom.
lp1 = 0
lp2 = 0

# Smoothed volume and cutoff, moved toward the pot reading a fraction of
# the way each block rather than jumped to it. A pot step of 2% (the
# deadband) applied instantly is a gain discontinuity at a block
# boundary -- the zipper you hear when turning a knob on a cheap synth.
# Converging over a few blocks puts the change below the audible step.
vol_q_now = -1        # -1 = uninitialised, snap to the first reading
k_now = -1


@micropython.native
def render_voice(v, mix_buf, n_samples):
    """Add one voice's contribution to mix_buf, advancing its phase and
    envelope. INTEGER ONLY -- see the fixed-point scale block above for
    why, and for the range analysis on every multiply in here.

    Every per-sample value is pulled into a local first: self.x lookups
    are among the slowest operations in MicroPython and this loop runs
    256 times per voice per block. That much the standalone already got
    right, and it is why its structure survives here.

    LINEAR INTERPOLATION between the two nearest table samples, which the
    standalone does not do. Real notes step 10-70+ table entries per
    output sample, far coarser than the table's 256-entry resolution, so
    nearest-neighbour lookup produces a harsh clicking character that
    worsens with pitch. Interpolating smooths that to the table's actual
    harmonic content for one extra read, subtract, multiply and shift --
    all integer. It matters most on exactly the harmonically rich tables
    people will want to play, whose sample-to-sample slopes are steepest.

    idx1 wraps to 0 only when idx0 == TABLE_LEN - 1 (right before phase
    itself wraps), handled with one comparison rather than a modulo every
    sample, since idx0 is kept in [0, TABLE_LEN) by the phase wrap."""
    table = v.table
    phase = v.phase
    phase_inc = v.phase_inc
    stage = v.stage
    level = v.level
    attack_step = v.attack_step
    sustain_level = v.sustain_level
    decay_a = v.decay_a
    decay_b = v.decay_b
    decay_end = v.decay_end
    release_a = v.release_a
    release_b = v.release_b
    release_end = v.release_end

    n = 0
    while n < n_samples:
        idx0 = phase >> _PHASE_BITS
        s0 = table[idx0]
        idx1 = idx0 + 1
        if idx1 >= TABLE_LEN:
            idx1 = 0
        raw = s0 + (((table[idx1] - s0) * ((phase >> _FRAC_BITS) & _FRAC_MASK))
                    >> _FRAC_BITS)

        phase += phase_inc
        if phase >= _PHASE_WRAP:
            phase -= _PHASE_WRAP

        if stage == _ATTACK:
            level += attack_step
            if level >= _LVL_ONE:
                level = _LVL_ONE
                stage = _DECAY
        elif stage == _DECAY:
            # Exponential toward the sustain floor: the step is a
            # fraction of the REMAINING GAP, not a constant. Same cost
            # as the old linear subtract -- two shifts and two adds, all
            # integer, no overflow possible at any level.
            #
            # The +1 is a stall guard. Near the end the shifts can both
            # round to zero, and without it the stage would sit one unit
            # above its target forever with the voice still allocated.
            gap = level - sustain_level
            level -= (gap >> decay_a) + (gap >> decay_b) + 1
            if level <= decay_end:
                level = sustain_level
                stage = _SUSTAIN
        elif stage == _RELEASE:
            level -= (level >> release_a) + (level >> release_b) + 1
            if level <= release_end:
                level = 0
                stage = _IDLE

        # Q23 level down to Q15 for the multiply, then back out of Q15.
        # Peak is 32000 * 32768 >> 15 = 32000, i.e. one voice at full
        # envelope reproduces the table amplitude exactly.
        mix_buf[n] += (raw * (level >> _LVL_TO_Q15)) >> 15
        n += 1

        if stage == _IDLE:
            break   # voice is done; the rest of the block gets 0 from it

    v.phase = phase
    v.stage = stage
    v.level = level
    if stage == _IDLE:
        v.active = False


@micropython.native
def generate_block(volume_pct, cutoff_pct):
    """Render one audio block.

    Chain: voices -> mix -> lowpass -> loop tap -> volume -> clip.

    The filter sits BEFORE volume so cutoff and loudness stay
    independent; filtering after the volume scale would make quiet
    passages sound differently filtered than loud ones. One filter on the
    summed mix, not one per voice: a static filter per-voice is 8x the
    cost and very nearly the same sound.

    The loop tap sits after the filter so effects bake into a recording,
    and before volume so the knob still rides the loop."""
    global lp1, lp2, vol_q_now, k_now

    # Bind globals to locals once. Even under @native these are dict
    # lookups otherwise, and they would happen on all 256 iterations.
    mb = mix_buf
    ob = out_buf
    n = BUF_SAMPLES

    # Q10 master gain and filter coefficient, both TABLE LOOKUPS -- no
    # float arithmetic anywhere in this function. See VOL_TABLE.
    #
    # Each is then eased toward its target rather than snapped to it,
    # a quarter of the remaining distance per block, closing the last
    # few units exactly so it cannot oscillate by one forever. Three or
    # four blocks is ~70 ms, far quicker than a hand turns a knob and
    # far slower than a step you would hear.
    vol_t = VOL_TABLE[volume_pct]
    if vol_q_now < 0:
        vol_q_now = vol_t
    else:
        d = vol_t - vol_q_now
        if d > 3 or d < -3:
            vol_q_now += d >> 2
        else:
            vol_q_now = vol_t
    vol_q = vol_q_now

    k_t = CUTOFF_TABLE[cutoff_pct]
    if k_now < 0:
        k_now = k_t
    else:
        d = k_t - k_now
        if d > 3 or d < -3:
            k_now += d >> 2
        else:
            k_now = k_t
    k = k_now

    y1 = lp1
    y2 = lp2

    mb[:] = zero_buf   # C-level clear, not 256 interpreted stores

    for voice in voices:
        if voice.active:
            render_voice(voice, mb, n)

    # Pass 1: two cascaded one-poles, in place -- 12 dB/octave.
    # Range: |x - y| peaks near 8 voices * 32000 * 2 = 512000, and
    # 512000 * 1024 = 5.2e8, comfortably inside the 31-bit small-int
    # ceiling. The second stage sees an already-filtered signal, so its
    # range is strictly smaller than the first's.
    i = 0
    while i < n:
        y1 += ((mb[i] - y1) * k) >> 10
        y2 += ((y1 - y2) * k) >> 10
        mb[i] = y2
        i += 1
    lp1 = y1
    lp2 = y2

    # Loop tap: records what you hear, mixes playback back in.
    looper.process(mb, n)

    # Pass 2: master volume, then SOFT-KNEE limit rather than hard clip.
    #
    # A hard clip is a corner in the transfer curve, and a corner
    # generates strong odd harmonics -- which at this sample rate then
    # alias, so a loud chord got both harshness and inharmonic grit at
    # once. Above _KNEE the excess is scaled by a quarter instead of
    # passed straight through, which bends the curve over rather than
    # breaking it. Below _KNEE -- one note, two notes, most of what you
    # actually play -- the path is bit-identical to before.
    #
    # The final clamp still exists because the knee only buys headroom
    # up to 4x the excess; past that there is nowhere left to go.
    i = 0
    while i < n:
        total = (mb[i] * vol_q) >> 10
        if total > _KNEE:
            total = _KNEE + ((total - _KNEE) >> 2)
            if total > 32000:
                total = 32000
        elif total < -_KNEE:
            total = -_KNEE + ((total + _KNEE) >> 2)
            if total < -32000:
                total = -32000
        ob[i] = total
        i += 1


# ============================================================
# STARTUP
# ============================================================

sync_state()
oled.displayState(state, looper)
# displayState only QUEUES pages, so the first paint needs an explicit
# drain. flush() is the ~15 ms spike push_one() exists to avoid, which is
# exactly why it is confined to here -- before the audio loop starts
# there is no block budget to overrun.
oled.flush()
looper.update_bar(oled.display)

print("--- Pico 2 W Music Controller | Team 13 ---")
print("keypad: #/9 key +-  0/8 octave +-  */7 instrument +-  6 mode")
print("        3 preset  5 rec  4 play/pause  1 loop reset")
print("loop: %.2f s | %d blocks | %d KB" % (
    looper.capacity / SAMPLE_RATE,
    looper.n_blocks,
    looper.capacity * 4 // 1024))     # 2 buffers x 2 bytes per sample
print("instruments: %d x %d bands | %d B of tables" % (
    len(WAVETABLES), N_BANDS, len(WAVETABLES) * N_BANDS * TABLE_LEN * 2))
# Both numbers, deliberately. The gap between them is the story of every
# MemoryError this project has hit: a large total with no large run in it
# is a fragmented heap, which is a loading problem (paste vs. flash), not
# a size problem.
print("heap: %d free | %d largest run" % (gc.mem_free(), _largest_free_run()))
print("#" + json.dumps(state))

oled_dirty = False
json_dirty = False
oled_last_ms = time.ticks_ms()
bar_last_ms = time.ticks_ms()
json_last_ms = time.ticks_ms()

# Floor on the gap between change-driven JSON lines. Not a delay -- a
# line still goes out on the very first pass after an edit if this much
# time has already elapsed, which it usually has. What it prevents is a
# burst: pressing three notes for a chord produces three button edges in
# three consecutive blocks, and printing each one meant three blocking
# USB writes. 30 ms is under one frame at 30 fps, so the visualiser
# cannot tell.
JSON_MIN_INTERVAL_MS = 30

# Heartbeat rate for loop position while the looper runs. Slower on
# purpose: this fires continuously for as long as the loop plays, so it
# sets the floor on idle serial traffic, whereas change lines are bursty
# and self-limiting. A full OLED redraw at this rate would overrun the
# audio budget, so the serial line and the screen are on separate clocks.
JSON_LOOP_INTERVAL_MS = 100

# Collect on our own schedule, in the slack right after write() returns,
# rather than letting an allocation trigger one at an arbitrary moment
# mid-block. ~every 0.5 s at these settings.
GC_EVERY_N_BLOCKS = 20

# Hard ceiling on deferring a collection past a busy pass. Without it, a
# sustained run of passes that all print or push would starve GC until an
# allocation forces one mid-block, which is precisely the arbitrary pause
# that scheduling collection here is meant to prevent.
GC_MAX_BLOCKS = 40
block_count = 0

# Pots are polled every Nth pass rather than every pass -- see the pot
# section of the main loop. 4 passes is ~85 ms at this block period.
POT_EVERY_N_BLOCKS = 4
pot_turn = POT_EVERY_N_BLOCKS     # start due, so the first pass reads them


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    changed = False
    preset = presets[active_preset]


    # --- 8 note buttons -> voices ------------------------------------
    for i in range(NUM_VOICES):
        key_bits[i] = 1 if buttons[i].value() == 0 else 0

    if key_bits != prev_key_bits:
        # One frequency computed per newly-pressed button, not the whole
        # scale up front -- Melodic Minor's ascending/descending 6th/7th
        # is a per-note decision (see MELODIC MINOR docstring) that
        # cannot be precomputed before knowing which button is about to
        # sound.
        for i in range(NUM_VOICES):
            if key_bits[i] and not prev_key_bits[i]:
                ascending = (last_degree_index is None
                             or i > last_degree_index)
                freq = build_scale_freqs(
                    preset["key"], preset["octave"], preset["mode"],
                    i, ascending)
                voices[i].note_on(freq, preset["sample"])
                last_degree_index = i
            elif prev_key_bits[i] and not key_bits[i]:
                # Unconditional: an idle Voice.note_off() is a no-op, so
                # there is nothing to guard against. This used to need a
                # careful non-dispatch on the drum branch -- a note held
                # while the sample cycled to Drums would otherwise be
                # stranded in SUSTAIN and drone forever. With one voice
                # type that whole hazard is gone.
                voices[i].note_off()

        # Mutated in place: a list comprehension here would allocate a
        # fresh 8-element list on every button edge, which is exactly
        # what key_bits being a bytearray is meant to avoid. `state` is
        # only read by json.dumps() and the OLED, both of which see the
        # same list object updated.
        state_keys = state["keys"]
        for i in range(NUM_VOICES):
            state_keys[i] = key_bits[i] == 1

        prev_key_bits[:] = key_bits
        changed = True


    # --- pots: volume (GP26) and filter cutoff (GP27) -----------------
    # Read every POT_EVERY_N_BLOCKS passes, not every pass. Each call is
    # four ADC conversions, so both pots was eight per block for a value
    # that then has to move 2% to be reported at all -- work spent
    # inside the one budget that must not overrun. Every 4th pass is
    # ~85 ms, far quicker than a hand can turn a knob.
    pot_turn += 1
    if pot_turn >= POT_EVERY_N_BLOCKS:
        pot_turn = 0
        volume = read_pot(POT_VOLUME)
        if (previous_volume == -1
                or abs(volume - previous_volume) >= VOLUME_DEADBAND):
            previous_volume = volume
            state["volume"] = volume
            changed = True

        # Reports 100 (filter open) until GP27 has actually been swept
        # -- see read_pot. An unwired cutoff knob is inaudible instead
        # of muffling the whole set.
        cutoff = read_pot(POT_CUTOFF, 100)
        if (previous_cutoff == -1
                or abs(cutoff - previous_cutoff) >= CUTOFF_DEADBAND):
            previous_cutoff = cutoff
            state["cutoff"] = cutoff
            changed = True


    # --- keypad -------------------------------------------------------
    pressed_key = scan_keypad()

    if pressed_key:
        if DEBUG:
            print("[debug] keypad:", pressed_key)

        # --- pitch (edits the active preset in place) ---
        # Key and octave both change what a button INDEX means musically
        # (build_scale_freqs maps index -> degree -> frequency using
        # both), so either edit resets last_degree_index -- otherwise a
        # Melodic Minor line's direction state could survive a transpose
        # that changed what "higher" even refers to.
        if pressed_key == "#":
            preset["key"] = shift_key(preset["key"], 1)
            last_degree_index = None
            changed = True

        elif pressed_key == "9":
            preset["key"] = shift_key(preset["key"], -1)
            last_degree_index = None
            changed = True

        elif pressed_key == "0":
            preset["octave"] = shift_octave(preset["octave"], 1)
            last_degree_index = None
            changed = True

        elif pressed_key == "8":
            preset["octave"] = shift_octave(preset["octave"], -1)
            last_degree_index = None
            changed = True

        # --- timbre (does not affect scale degree math -- no reset) ---
        elif pressed_key == "*":
            preset["sample"] = shift_sample(preset["sample"], 1)
            changed = True

        elif pressed_key == "7":
            preset["sample"] = shift_sample(preset["sample"], -1)
            changed = True

        # --- mode ---
        # Changes the step table build_scale_freqs uses for every degree,
        # so it resets direction state for the same reason key/octave do.
        elif pressed_key == "6":
            preset["mode"] = shift_mode(preset["mode"], 1)
            last_degree_index = None
            changed = True

        # --- preset select ---
        # CYCLES (2 presets today), not direct-select, so a single key
        # cannot land on a specific preset by index once there could be
        # more than 2. Notes already sounding keep the frequency and
        # wavetable they were triggered with -- switching preset must not
        # retune or re-voice a held chord underneath you. `preset` is
        # rebound so anything later in this same pass edits the newly
        # selected one. A switch can change key, octave, sample AND mode
        # at once, so this resets direction state too.
        elif pressed_key == "3":
            active_preset = (active_preset + 1) % len(presets)
            preset = presets[active_preset]
            last_degree_index = None
            changed = True

        # --- loop transport ---
        elif pressed_key == "4":
            looper.play_toggle()
            changed = True

        elif pressed_key == "5":
            looper.record_toggle()
            changed = True

        elif pressed_key == "1":
            looper.reset()
            changed = True

        elif DEBUG:
            print("[debug] key", pressed_key, "unassigned")


    # --- serial (coalesced), OLED (queued, one page per pass) ---------
    now_ms = time.ticks_ms()

    loop_running = looper.is_sounding() or looper.state == loop.RECORDING

    if changed:
        # Runs at the edge that caused the change, NOT deferred with the
        # print below. It is four dict copies, so it costs nothing -- and
        # the OLED redraw reads `state`, so deferring it would let the
        # screen paint a stale mirror whenever a redraw landed between an
        # edit and its JSON line.
        sync_state()
        json_dirty = True
        oled_dirty = True

    # Two clocks, one printer. A pending change uses the fast floor; a
    # free-running loop heartbeat uses the slow one. Checked in this
    # order so an edit made mid-loop is not held back to heartbeat rate.
    if json_dirty:
        json_due = JSON_MIN_INTERVAL_MS
    elif loop_running:
        json_due = JSON_LOOP_INTERVAL_MS
    else:
        json_due = -1     # nothing to say

    # json_printed, not json_dirty, is what the GC guard below reads:
    # printing CLEARS json_dirty, so a pass that just paid for a blocking
    # USB write would otherwise look idle and get a collection stacked on
    # top of it -- the exact pairing this is all meant to prevent.
    json_printed = False
    if json_due >= 0 and time.ticks_diff(now_ms, json_last_ms) >= json_due:
        state["loop"] = looper.state_name()
        state["loop_pos"] = looper.progress()
        print("#" + json.dumps(state))
        json_last_ms = now_ms
        json_dirty = False
        json_printed = True

    if oled_dirty and time.ticks_diff(now_ms, oled_last_ms) >= oled.OLED_MIN_INTERVAL_MS:
        # Draws into the framebuffer and queues the changed pages. Sends
        # no I2C itself -- that happens in push_one() below, one page at
        # a time, so a 5-page repaint cannot land inside one block.
        oled.displayState(state, looper)
        oled_last_ms = now_ms
        oled_dirty = False

    # Progress bar on its own, faster clock. Pushes one 128-byte page
    # rather than a 1 KB frame, so 20 fps costs ~3 ms a tick.
    bar_pushed = False
    if loop_running and time.ticks_diff(now_ms, bar_last_ms) >= oled.BAR_INTERVAL_MS:
        looper.update_bar(oled.display)
        bar_last_ms = now_ms
        bar_pushed = True

    # ONE page of I2C per pass, total -- the bar and the status rows share
    # both the bus and the block budget, so a pass that already spent
    # ~3 ms on the bar does not also spend 3 ms here. The queue drains a
    # pass later, which is 21 ms nobody can see.
    oled_pushed = False if bar_pushed else oled.push_one()


    # --- audio: must run every pass, unconditionally -------------------
    # Silence fast path: skip the clear, the voice idle checks, and the
    # filter/loop/scale/clip passes entirely.
    any_active = False
    for voice in voices:
        if voice.active:
            any_active = True
            break

    # Three reasons the output may be non-zero with no key held:
    #   - a voice is still in RELEASE (Bell runs 1.9 s past the release,
    #     which is why voice.active, not the button, gates this)
    #   - the looper is playing back or recording
    #   - the filter still has a tail (both poles have memory, and at a low
    #     cutoff that takes tens of milliseconds to decay)
    # Cutting to silence_buf while any is true is a step discontinuity,
    # i.e. a click at the end of every phrase.
    if (any_active or looper.state != loop.STOPPED
            or lp2 > 16 or lp2 < -16 or lp1 > 16 or lp1 < -16):
        generate_block(state["volume"], state["cutoff"])
        audio.write(out_buf)
    else:
        # Integer floor-shift is asymmetric, so a small negative filter
        # state can converge to -1 and stay there forever. Zero both
        # stages explicitly (inaudible at this magnitude) rather than
        # leaving a permanent DC offset on the DAC.
        lp1 = 0
        lp2 = 0
        audio.write(silence_buf)

    # write() has just returned, so the I2S buffer is as full as it gets
    # -- the moment with the most slack in the whole loop.
    block_count += 1
    if block_count >= GC_EVERY_N_BLOCKS:
        # Hold off if this pass already spent its slack on a JSON line or
        # a page push -- stacking a collection on top of another spike is
        # how a pass overruns. GC_MAX_BLOCKS stops that deferral from
        # running forever.
        if ((not json_printed and not bar_pushed and not oled_pushed)
                or block_count >= GC_MAX_BLOCKS):
            block_count = 0
            gc.collect()