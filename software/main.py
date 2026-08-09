"""
PICO 2 W MUSIC CONTROLLER -- HAcK 2026, Team 13
================================================

FILE LAYOUT
-----------
  main.py        this file: hardware setup, voices, filter, main loop
  keypad.py       3x4 matrix scan + debounce (no dependency on anything)
  scale.py        key/octave/mode theory, pure functions
  instruments.py  wavetables + instrument table -- MUST be imported
                  after the Looper (see the import site in this file
                  and the warning at the top of instruments.py)
  display.py      OLED partial-redraw driver
  loop.py         overdubbing looper (record / overdub / reset)

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
    7  sample -         8  octave -         9  key -
    *  sample +         0  octave +         #  key +

Key 2 is free. It used to be reserved for loop undo/redo; that feature
was scrapped, so reset (key 1) is now the only way to scrap a take.

WHERE THE SOUND LIVES  (read this before "replacing the synth")
---------------------------------------------------------------
This file contains the ENGINE -- phase accumulation, envelope stepping,
interpolation, mixing. It contains no tone decisions at all. Every
spectrum and every ADSR setting lives in instruments.py, which now
carries the standalone synth's recipes verbatim.

That split matters because the two are independently swappable, and only
one of them was ever the problem. The engine here is the integer form of
the same algorithm the standalone ran in floats: same wavetable lookup,
same ADSR shape, plus linear interpolation. It is strictly cheaper per
sample and does not allocate. What made this build sound thin next to the
standalone was the OLD instruments.py band-limiting every table down to
two harmonics -- a tone problem, fixed in the tone file.

Three things gate the sound before it reaches the DAC, in order. Check
them in this order if it still sounds wrong:

  1. the cutoff knob (GP27). The lowpass sits in front of everything, so
     a low cutoff muffles the whole instrument no matter how rich the
     table is. read_pot() now reports FULLY OPEN until that pot has been
     swept far enough to calibrate, so an unwired or untouched GP27 can
     no longer silently filter the set -- see POTS.
  2. MIX_HEADROOM. The standalone was loud partly because it clipped on
     any two-note chord; this divides down first so chords stay clean.
     See the constant for the exact clip math.
  3. the volume knob (GP26), square-law, so it feels linear.

MODES
-----
Nine scales are available per preset: Major, Natural Minor, Melodic
Minor, Harmonic Minor, Dorian, Phrygian, Lydian, Mixolydian, Locrian.
Melodic Minor is direction-sensitive (its 6th and 7th degrees differ
ascending vs. descending) -- see MELODIC MINOR below.

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

The tradeoff: a smaller ibuf means less slack, so any single loop pass
that overruns 16 ms produces an audible click. See SPIKE BUDGET.

The standalone synth's settings (11025 Hz, BUF_SAMPLES 1024, ibuf 8192)
are NOT portable here, and this is the reason: 1024/11025 is a 93 ms
block and 8192 bytes is four more of them, so press-to-sound would be
about 460 ms. That is fine for a script that only plays notes and
unplayable for an instrument you perform on. It also breaks two things
outright -- the drum .wav files are 12000 Hz and _load_wav_samples()
raises at boot on any mismatch, and LOOP_SECONDS is budgeted against
this rate. Keep the tone, not the timing.

SPIKE BUDGET  (why chords used to cut out)
------------------------------------------
Render cost scales with BUF_SAMPLES and so does the budget (BUF_SAMPLES /
SAMPLE_RATE), so their ratio is fixed and a BIGGER BUFFER CANNOT FIX AN
OVERRUN CAUSED BY RENDERING. That it did help was the diagnosis: the
overruns were FIXED per-pass costs, which do not scale with the buffer
and so shrink as a fraction of a longer block. Rather than pay latency to
hide them, each is now capped at source:

  serial   json.dumps + print allocates ~200 bytes and writes to USB
           CDC, which BLOCKS if the host has the port open but is not
           draining it -- tens of milliseconds, entirely outside our
           control. A chord lands 2-3 button edges in consecutive
           blocks and each one used to print. Now coalesced to one line
           per JSON_MIN_INTERVAL_MS.

  OLED     a preset cycle changes key, octave, sample and mode at once,
           dirtying all 5 text rows: ~15 ms of I2C against a 16 ms
           budget. display.py now queues pages and drains ONE (~3 ms)
           per pass, and a pass that already pushed the loop progress
           bar drains none.

  GC       skipped on any pass that already did serial or OLED work, so
           a collection cannot land on top of another spike.

Worst case per pass is therefore one page of I2C plus at most one JSON
line, never both plus a collection.

MELODIC MINOR
-------------
Ascending gets a raised 6th/7th; descending falls back to the natural
minor 6th/7th. Direction is judged by comparing the just-pressed
button's index to the PREVIOUS note's button index (higher = ascending).
`last_degree_index` remembers that previous index and resets to None
("no direction yet, assume ascending") on any edit that changes what a
button index means -- key, octave, mode, or preset switch -- so a
melodic line's direction can't leak across an edit that changed the
scale underneath it.

SERIAL PROTOCOL
---------------
State changes print one line: '#' + JSON, coalesced to at most one line
per JSON_MIN_INTERVAL_MS (30 ms) -- quicker than a 30 fps video frame, so
the visualiser cannot perceive the difference, while a fast chord can no
longer queue three blocking USB writes into three consecutive audio
blocks. While the looper runs a heartbeat goes out every
JSON_LOOP_INTERVAL_MS (100 ms) so the website can animate loop position;
that stays slower deliberately, since it fires continuously and change
lines do not. The website reads only lines starting with '#'; anything
else is human debug output. DEBUG = False silences it.

JSON fields: preset, octave, key, sample, mode, volume, cutoff, keys[8],
loop, loop_pos. `mode` is new as of this revision -- a visualizer reading
this feed for the first time should treat its absence as "Major".
"""

import gc
import os
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
# contiguous. gc.mem_free() reporting plenty free means nothing if it's
# fragmented into scraps -- so the Looper is constructed while the heap
# is still one clean block, before wavetables/OLED/I2S/cutoff-table
# allocations carve it up. Everything below is small and fits in
# whatever heap is left.

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
# Nyquist here is 6000 Hz. See N_MAX in instruments.py for why the
# wavetables are NO LONGER band-limited to suit it, and CUTOFF_MAX_HZ
# below.

# Size the I2S buffer for double/triple buffering, NOT a big safety
# margin -- every extra block of ibuf is another block of latency between
# a keypress and the sound.
#
# LEFT AT 3 DELIBERATELY. Raising it to 4 was on the table while the rate
# was still 16000, but a longer block period multiplies through here too:
# total latency is (1 + IBUF_BLOCKS) * BUF_SAMPLES / SAMPLE_RATE, so at
# 12000 Hz
#
#     IBUF 2 -> 64 ms      IBUF 3 -> 85 ms      IBUF 4 -> 107 ms
#
# and 4 would be audibly laggy to play. 3 gives 85 ms with a 21.3 ms
# budget that the fixed-point voice render now fits inside several times
# over. If clicks somehow persist, go to 4 before touching anything else
# -- a laggy instrument still scores, one that cuts out does not. If it
# is rock solid, 2 buys back the latency.
IBUF_BLOCKS = 3

# Two int16 buffers (base take + overdub layer): 4 s costs 188 KB at this
# rate (down from 250 KB at 16000 -- the rate drop pays for itself in
# heap as well as CPU). The Looper backs off in half-second steps if that
# won't fit, so a tight board gets a shorter loop rather than a traceback
# -- the startup banner prints what it actually got.
LOOP_SECONDS = 4

looper = loop.Looper(SAMPLE_RATE, BUF_SAMPLES, seconds=LOOP_SECONDS)

# MUST be imported here, after the Looper -- see the allocation-order
# warning at the top of instruments.py. This builds 14 wavetables.
from instruments import (TABLE_LEN, WAVETABLES, ENVELOPES, DRUM_KIT_NAME,
                          shift_sample)


# ============================================================
# 8 NOTE BUTTONS
# ============================================================
# Defined here, ahead of DRUM KIT below, because DrumVoice's pool is
# sized off NUM_VOICES -- definition must precede use.

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
# Minor's ascending/descending 6th & 7th (see MELODIC MINOR in the
# module docstring). None means "no direction yet" -- treated as
# ascending. Reset to None on any edit that changes what a button index
# means: key, octave, mode, or preset switch, so a melodic line's
# direction can't leak across an edit that changed the scale under it.
last_degree_index = None


# ============================================================
# DRUM KIT  (one-shot sample playback)
# ============================================================
# Structurally different from every instrument above: those are TUNED,
# LOOPING wavetables (one short cycle repeated at a pitch-dependent
# rate, sustained while held). A drum hit is a one-shot: a long,
# non-looping recording that plays exactly once and stops itself when
# the sample runs out, regardless of button state. No sustain stage, no
# per-scale-degree pitch.
#
# Forcing one-shot playback through Voice's phase/TABLE_LEN-wrap
# machinery would need per-sample branches in render_voice's hot loop
# for a case that doesn't apply to the other 13 instruments. Cheaper to
# give drums their own voice type (DrumVoice) and render function
# (render_drum_voice), mixed into the same mix_buf.
#
# Fixed mapping regardless of preset key/octave/mode: 1=Kick, 2=Snare,
# 3=Hat Closed, 4=Hat Open, 5=Clap, 6=Tom Low, 7=Tom Mid, 8=Crash.
# Selecting "Drums" as the sample bypasses build_scale_freqs() entirely
# for note-on; see the main loop's note button section.
#
# Samples are PRECOMPUTED offline (generate_drums.py, not shipped to the
# Pico) and loaded as flat .wav files rather than synthesized at boot --
# generating that much noise/sine-sweep DSP in interpreted MicroPython
# would cost seconds of unpredictable boot time; loading files is
# effectively instant.

DRUM_FILES = (
    "kick.wav", "snare.wav", "hat_closed.wav", "hat_open.wav",
    "clap.wav", "tom_low.wav", "tom_mid.wav", "crash.wav",
)


def _load_wav_samples(path):
    """Read a 16-bit mono PCM .wav file's sample data into an array('h').

    Skips the RIFF/fmt header by finding the 'data' chunk rather than
    hardcoding a 44-byte offset, so it survives a future regeneration
    that adds metadata chunks.

    Raises loudly at boot (not mid-performance) if a file is missing or
    malformed -- fail early rather than play garbage or silence on stage.
    The fmt chunk IS checked: render_drum_voice copies samples straight
    into the mix with no rate conversion and no channel handling, so a
    44.1 kHz or stereo file does not error, it just plays at the wrong
    pitch and half speed. That is a miserable thing to debug on stage
    and cheap to catch here.

    Note the rate check is also what makes SAMPLE_RATE non-negotiable
    without regenerating the kit: these wavs were rendered at 12000 Hz,
    so dropping the project to 11025 to match the standalone synth would
    raise here on kick.wav and never reach the main loop."""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise RuntimeError(path + ": not a RIFF/WAVE file")

        raw = None
        fmt_seen = False

        # Walk chunks until 'data' is found. RIFF chunks are word
        # aligned: an odd-sized chunk is followed by one pad byte that
        # is NOT counted in chunk_size. Skipping only chunk_size bytes
        # leaves the reader one byte out of step and every subsequent
        # chunk id reads as garbage.
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                raise RuntimeError(path + ": no data chunk found")
            size_bytes = f.read(4)
            if len(size_bytes) < 4:
                raise RuntimeError(path + ": truncated chunk header")
            chunk_size = int.from_bytes(size_bytes, "little")
            pad = chunk_size & 1

            if chunk_id == b"fmt ":
                fmt = f.read(chunk_size)
                audio_format = int.from_bytes(fmt[0:2], "little")
                channels = int.from_bytes(fmt[2:4], "little")
                rate = int.from_bytes(fmt[4:8], "little")
                bits = int.from_bytes(fmt[14:16], "little")
                if audio_format != 1 or bits != 16 or channels != 1:
                    raise RuntimeError(
                        path + ": need 16-bit mono PCM, got format "
                        + str(audio_format) + "/" + str(bits) + "-bit/"
                        + str(channels) + "ch")
                if rate != SAMPLE_RATE:
                    raise RuntimeError(
                        path + ": sample rate " + str(rate) + " != "
                        + str(SAMPLE_RATE) + " (no resampling on the Pico)")
                fmt_seen = True
                if pad:
                    f.read(1)

            elif chunk_id == b"data":
                if chunk_size & 1:
                    raise RuntimeError(
                        path + ": data chunk size not a multiple of 2 bytes")
                raw = f.read(chunk_size)
                if len(raw) != chunk_size:
                    raise RuntimeError(
                        path + ": data chunk truncated (" + str(len(raw))
                        + " of " + str(chunk_size) + " bytes)")
                break

            else:
                f.read(chunk_size + pad)   # metadata etc., skip it

    if not fmt_seen:
        raise RuntimeError(path + ": no fmt chunk before data")

    return array("h", raw)


# Where the .wav files live on the Pico's flash filesystem. Loaded AFTER
# the Looper (see AUDIO CONSTANTS + LOOPER at the top) for the same
# reason WAVETABLES is: the Looper's big allocation needs the heap still
# clean, so smaller allocations happen once that's safely done.
#
# ABSOLUTE path, leading slash, deliberately. A relative "drums/"
# resolves against the current working directory, which is not the same
# thing depending on how this file got run (pasted into the REPL,
# `mpremote run`, or auto-started from flash). "/drums/" is the same
# directory in all three cases.
DRUM_KIT_DIR = "/"

# LOADED BIGGEST FIRST, then reordered back into button order.
#
# Loading a wav costs 2x its size for a moment -- f.read() hands back the
# raw bytes and array("h", raw) copies them into a second block of the
# same size -- and only 1x once the bytes are dropped. So the whole bank
# fits iff  total + largest <= free, and the ORDER decides whether that
# peak lands on an empty heap or one that seven samples have already
# carved up. Ascending order fails on the last (largest) file with plenty
# of total room left; descending order takes the big hit first and then
# only needs small ones. Same resident memory either way, strictly better
# odds of getting there.
#
# Sizes are read first so the whole set can be checked BEFORE anything is
# allocated -- an up-front number you can act on beats a MemoryError
# seven files deep.

_sizes = []
for _i in range(len(DRUM_FILES)):
    _path = DRUM_KIT_DIR + DRUM_FILES[_i]
    try:
        _sizes.append((os.stat(_path)[6], _i))
    except OSError as e:
        raise OSError("cannot open " + _path + " (errno " + str(e.args[0])
                      + ") -- is " + DRUM_KIT_DIR
                      + " a directory with all 8 wavs in it?")

_sizes.sort()
_sizes.reverse()                      # biggest first

gc.collect()
_need = 0
for _sz, _i in _sizes:
    _need += _sz
_need += _sizes[0][0]                 # the transient peak on the largest
_free = gc.mem_free()

if _need > _free:
    # Every 0.5 s of LOOP_SECONDS is 0.5 * SAMPLE_RATE * 2 bytes * 2
    # buffers = 24000 bytes at 12000 Hz, so the shortfall converts
    # straight into the number of half-seconds of loop to give back.
    _short = _need - _free
    raise MemoryError(
        "drum bank needs ~" + str(_need) + " bytes (" + str(_need - _sizes[0][0])
        + " resident + " + str(_sizes[0][0]) + " peak on "
        + DRUM_FILES[_sizes[0][1]] + "), only " + str(_free)
        + " free -- short by " + str(_short) + ". Trim the longest samples, or"
        + " drop LOOP_SECONDS by " + str((_short + 23999) // 24000 * 0.5) + " s")

_slots = [None] * len(DRUM_FILES)
for _sz, _i in _sizes:
    _path = DRUM_KIT_DIR + DRUM_FILES[_i]
    try:
        _slots[_i] = _load_wav_samples(_path)
    except OSError as e:
        raise OSError("cannot open " + _path + " (errno " + str(e.args[0])
                      + ") -- is " + DRUM_KIT_DIR
                      + " a directory with all 8 wavs in it?")
    except MemoryError:
        # Preflight said it fits, so getting here means fragmentation,
        # not arithmetic: the bytes are free but not in one run.
        raise MemoryError(
            "no contiguous run for " + _path + " (" + str(_sz) + " bytes, "
            + "needs " + str(2 * _sz) + " to load) -- free=" + str(gc.mem_free())
            + "; heap is fragmented, lower LOOP_SECONDS")

DRUM_SAMPLES = tuple(_slots)          # back in button order: 0=Kick .. 7=Crash
_sizes = None
_slots = None
_path = None
gc.collect()


class DrumVoice:
    """One-shot sample playback: no phase-wrap, no ADSR sustain. Plays
    `sample` forward from `pos` until pos reaches the end, then goes
    idle on its own -- unlike Voice, note_off() does nothing, because a
    real drum hit is not "held," it just plays out. __slots__ for the
    same reason as Voice: no per-instance dict, no GC pressure from dict
    growth mid-performance."""
    __slots__ = ("active", "sample", "pos")

    def __init__(self):
        self.active = False
        self.sample = None
        self.pos = 0

    def note_on(self, drum_index):
        """drum_index is the BUTTON index (0-7), used directly as the
        DRUM_SAMPLES index -- fixed mapping, no scale/key/octave
        involved. Re-triggering a still-sounding drum (fast repeated
        hits) restarts it from 0 rather than layering a second copy --
        simpler and avoids needing a drum-specific polyphony scheme on
        top of the existing one-voice-per-button design."""
        self.sample = DRUM_SAMPLES[drum_index]
        self.pos = 0
        self.active = True

    def note_off(self):
        # Deliberately a no-op: a drum one-shot plays out regardless of
        # how long the button is held. Present so the main loop's
        # existing "if pressed: note_on() elif released: note_off()"
        # shape doesn't need a drum-specific special case there.
        pass


drum_voices = [DrumVoice() for _ in range(NUM_VOICES)]


@micropython.native
def render_drum_voice(v, mix_buf, n_samples):
    """Mix one drum voice's contribution into mix_buf. Structurally
    simpler than render_voice: no phase accumulator, no envelope, no
    interpolation (these are already-recorded samples at the project's
    own SAMPLE_RATE, not a pitched lookup table being stepped through at
    an arbitrary rate -- there is nothing to interpolate between). Just
    copy forward from pos, clamping at the sample's own length."""
    sample = v.sample
    pos = v.pos
    sample_len = len(sample)

    n = 0
    while n < n_samples and pos < sample_len:
        mix_buf[n] += sample[pos]
        pos += 1
        n += 1

    v.pos = pos
    if pos >= sample_len:
        v.active = False


# ============================================================
# PRESETS + REPORTED STATE
# ============================================================
# A preset stores only what the keypad edits: octave, key, sample, mode.
# It deliberately does NOT store volume or cutoff -- those are physical
# pots, and recalling a stored volume that disagrees with the knob's
# position means the value snaps the instant you touch it. The knob
# always wins.
#
# `presets[active_preset]` is the source of truth for pitch/timbre.
# `state` is a flat mirror rebuilt by sync_state() so the OLED and the
# website have one object to read.
#
# PRESET_FIELDS lists every key a preset owns. sync_state() loops over
# it instead of hand-writing one line per field, so adding a preset
# field can't silently forget its sync line -- a bug that's invisible in
# testing and only shows up on stage as "I changed it but nothing updated."

PRESET_FIELDS = ("octave", "key", "sample", "mode")

presets = [
    {"octave": 4, "key": "C", "sample": "Sine", "mode": "Major"},
    {"octave": 5, "key": "F", "sample": "Sawtooth", "mode": "Major"},
]

active_preset = 0

state = {
    "preset": 1,
    "octave": 4,
    "key": "C",
    "sample": "Sine",
    "mode": "Major",
    "volume": 75,
    "cutoff": 100,          # 100 = filter fully open (bypassed)
    "keys": [False] * 8,
    "loop": "empty",
    "loop_pos": 0,
}


def sync_state():
    """Copy every preset-owned field into the reported state. Called
    once per update rather than at every edit site, so the mirror can't
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
# One entry PER CHANNEL. As single globals this was fine with one pot and
# silently wrong with two -- both channels would have shared whichever
# pot happened to swing widest.
adc_min = [65535] * len(adc_channels)
adc_max = [0] * len(adc_channels)

# Require this much travel before trusting the learned endpoints. Below
# it the "range" is just ADC noise a few counts wide, and dividing by it
# makes the reading snap randomly between 0 and 100. 8000 is ~12% of full
# scale: well above noise, well below any real pot's travel.
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
    until it has actually been swept far enough to calibrate. Cutoff
    passes 100 for it, and that is not cosmetic: the lowpass sits in
    front of everything in the chain, and an UNWIRED or merely untouched
    GP27 floats, so the raw fallback below would scale noise into an
    arbitrary cutoff and quietly muffle the entire instrument. Failing
    open means a miswired filter knob costs you the filter, not the
    sound. Volume passes nothing and keeps the raw fallback, because for
    volume the raw reading of a wired pot is already close to its true
    position and jumping to a default would be the worse surprise."""
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
# SAMPLE_RATE, BUF_SAMPLES and IBUF_BLOCKS are defined at the top of the
# file, next to the Looper that has to be allocated before anything else.

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
# become integer compares instead of object compares.
_IDLE = const(0)
_ATTACK = const(1)
_DECAY = const(2)
_SUSTAIN = const(3)
_RELEASE = const(4)


# ---------------- Fixed-point scales for the voice render ----------------
#
# render_voice used to carry a float phase, a float envelope level, and a
# float interpolation fraction -- the shape the standalone synth still
# uses. MicroPython boxes every float on the heap, so the inner loop --
# 256 iterations per voice per block, 2048 with 8 voices held -- was
# allocating constantly and running the slowest arithmetic the
# interpreter has. That is what made CPU load track voice count, and
# voice count is exactly the axis the garbling moved along.
#
# The standalone gets away with floats because it runs 1024-sample blocks
# at 11025 Hz behind a 4-block ibuf: a 93 ms budget and ~460 ms of queue
# to hide any overrun in. This build spends that latency on playability
# instead, which only works if the render is cheap. So the arithmetic
# below is integer -- SAME algorithm, same envelope shape, same tables,
# no allocation, no GC pressure -- and the whole loop stays inside
# MicroPython's 31-bit small-int range (+-1.07e9) so nothing silently
# promotes to a heap-allocated big int.
#
# PHASE -- Q16.16. phase_inc = freq * TABLE_LEN * 65536 / SAMPLE_RATE.
# Top note (octave 6, degree 7 = C7, 2093 Hz) gives ~2.9e6, far below
# _PHASE_WRAP, which is what lets the wrap stay a single subtract rather
# than a modulo.
_PHASE_BITS = const(16)
#
# LITERAL, not const(TABLE_LEN << 16). MicroPython's const() folds at
# COMPILE time, so its argument must be a literal or another const
# declared in THIS module -- TABLE_LEN is imported from instruments.py,
# which makes it a runtime name and raises "SyntaxError: not a constant".
# The assert below is the price of hardcoding it: it costs one comparison
# once at boot and makes a TABLE_LEN change fail loudly here instead of
# detuning every note by a factor of two.
_PHASE_WRAP = const(16777216)             # TABLE_LEN(256) << 16
assert TABLE_LEN == 256, "_PHASE_WRAP hardcodes TABLE_LEN=256"

# INTERPOLATION FRACTION -- Q8, NOT the full Q16. (s1 - s0) can reach
# 64000, and 64000 * 65535 = 4.2e9 overflows a small int and would start
# allocating big ints inside the audio loop. 64000 * 255 = 1.6e7 is
# safe, and 256 steps between adjacent table entries is already finer
# than the table's own 16-bit resolution can express.
_FRAC_BITS = const(8)
_FRAC_MASK = const(255)

# ENVELOPE LEVEL -- Q23 for the accumulator, used at Q15 for the multiply.
# Q15 alone is too coarse to ACCUMULATE in: Bell's release is 1900 ms
# from a sustain of 0.22, which at 12000 Hz is 22800 samples of decay --
# a Q15 step would round to 0, get forced to 1, and cut Bell's tail to a
# third of its length. Q23 gives the step 256x the resolution, and
# shifting down by 8 at the point of use keeps the multiply in range:
# 32000 * 32768 = 1.048e9, just under the 1.07e9 small-int ceiling.
_LVL_BITS = const(23)
_LVL_ONE = const(8388608)                 # 1 << 23
_LVL_TO_Q15 = const(8)                    # >> this before multiplying


class Voice:
    # __slots__ avoids a per-instance dict: less RAM, faster attribute
    # access, and no dict growth to trigger GC mid-note.
    __slots__ = (
        "active", "table", "phase", "phase_inc", "stage", "level",
        "attack_step", "decay_step", "sustain_level", "release_step",
    )

    def __init__(self):
        # All integers -- see the fixed-point scale block above. Seeding
        # these as floats would let the very first note run one block of
        # mixed int/float arithmetic before note_on() overwrote them.
        self.active = False
        self.table = None
        self.phase = 0
        self.phase_inc = 0
        self.stage = _IDLE
        self.level = 0
        self.attack_step = 0
        self.decay_step = 0
        self.sustain_level = 0
        self.release_step = 0

    def note_on(self, freq, instrument):
        # Float maths is fine HERE -- note_on runs once per keypress, not
        # once per sample. Everything it stores is an integer, so the
        # render loop never sees a float.
        #
        # The two .get() fallbacks land on "Sine", which is why
        # instruments.py must always define it: a preset naming a sample
        # that no longer exists gets a plain tone instead of a KeyError
        # mid-set.
        self.table = WAVETABLES.get(instrument, WAVETABLES["Sine"])
        self.phase = 0
        # Q16.16 table steps per output sample. Stays well below
        # _PHASE_WRAP for every note in our range, which is what lets
        # render_voice wrap with a single subtract instead of a modulo.
        self.phase_inc = int(freq * TABLE_LEN * 65536 / SAMPLE_RATE)

        a_ms, d_ms, s_lvl, r_ms = ENVELOPES.get(instrument, ENVELOPES["Sine"])
        a_samples = max(1, int(a_ms * SAMPLE_RATE / 1000))
        d_samples = max(1, int(d_ms * SAMPLE_RATE / 1000))
        r_samples = max(1, int(r_ms * SAMPLE_RATE / 1000))

        s_level = int(s_lvl * _LVL_ONE)

        # max(1, ...) on every step: a step that floors to 0 is a voice
        # that never leaves its stage -- a note stuck on forever, which
        # on stage is worse than any amount of envelope inaccuracy.
        self.attack_step = max(1, _LVL_ONE // a_samples)
        self.decay_step = max(1, (_LVL_ONE - s_level) // d_samples)
        self.sustain_level = s_level
        self.release_step = max(1, s_level // r_samples)

        self.stage = _ATTACK
        self.level = 0
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
# instead of 256 interpreted loop iterations doing nothing.
zero_buf = array("l", [0] * BUF_SAMPLES)

# Written straight to I2S when nothing is sounding, so silence costs one
# memcpy instead of a full render + filter + scale + clip pass.
silence_buf = array("h", [0] * BUF_SAMPLES)

# Master headroom divisor. 8 voices at full scale sum to 256000 against a
# 32000 output ceiling, so some division is mandatory.
#
# 2.0, NOT 3.0. This is the knob that made the old build sound weak next
# to the standalone synth, and it is worth being precise about why. The
# standalone ran a flat 0.8 gain with no divisor at all, so ANY two notes
# together clipped -- that hard clipping is a real part of why it sounds
# loud and present rather than polite. 3.0 was set so a three-note chord
# lands exactly at full scale and never clips, which is correct on paper
# and about 7 dB quieter per note in the room, with the volume knob
# already at maximum and nowhere left to go.
#
# 2.0 splits it: one note peaks at 16000 (half scale, plenty of room to
# be heard), a two-note chord at full scale, and three or more clip
# progressively the way the standalone did. If it distorts while a loop
# is playing underneath -- the loop adds as much again on top of the live
# voices -- go back to 3.0 or just back the volume knob off, which is
# what a real looper expects you to do.
MIX_HEADROOM = 2.0

# Precomputed Q10 master gain, indexed by volume percent, same shape as
# CUTOFF_TABLE below.
#
# Square law approximates how the ear hears loudness, so the pot feels
# linear across its travel. Doing that as `x * x / MIX_HEADROOM` inside
# generate_block meant a float multiply per sample in the output pass --
# 256 boxed floats per block, in the one function most carefully written
# to avoid exactly that. Table it once at boot and the whole audio path
# is integer end to end.
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
# runs 0..1024 and the per-sample cost is a subtract, a multiply, a
# shift, and an add -- integer only, no floats in the filter path.
#
# Headroom check: |x - y| peaks near 8 voices * 32000 * 2 = 512000, and
# 512000 * 1024 = 524M, comfortably inside MicroPython's 31-bit small int
# (+-1.07e9). A 12-bit scale would overflow into heap-allocated big ints
# -- allocation inside the audio loop, exactly what everything else here
# is arranged to avoid.

CUTOFF_MIN_HZ = 60
# Kept just under Nyquist at the CURRENT SAMPLE_RATE -- this constant
# must move whenever SAMPLE_RATE does. At 12000 Hz Nyquist is 6000 Hz;
# 5400 keeps the same ~10% margin the 16000/7200 pair had. Fully open
# (pct 100) bypasses the filter outright, so the ceiling only shapes the
# top of the knob's travel.
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
lp_state = 0


@micropython.native
def render_voice(v, mix_buf, n_samples):
    """Add one voice's contribution to mix_buf, advancing its phase and
    envelope. INTEGER ONLY -- see the fixed-point scale block above for
    why, and for the range analysis on every multiply in here.

    Every per-sample value is pulled into a local first: self.x lookups
    are among the slowest operations in MicroPython and this loop runs
    256 times per voice per block.

    LINEAR INTERPOLATION between the two nearest table samples. This is
    the one thing here the standalone does not do -- it takes
    int(phase) % TABLE_LEN and reads the nearest entry. Real notes step
    10-70+ table entries per output sample, far coarser than the table's
    256-entry resolution, so nearest-neighbour lookup produces a harsh
    clicking character that worsens with pitch. Interpolating smooths
    that to the table's actual harmonic content for one extra read,
    subtract, multiply and shift -- all integer. Keep it.

    idx1 wraps to 0 only when idx0 == TABLE_LEN - 1 (right before phase
    itself wraps), handled with one comparison rather than a modulo every
    sample, since idx0 is kept in [0, TABLE_LEN) by the phase wrap
    below."""
    table = v.table
    phase = v.phase
    phase_inc = v.phase_inc
    stage = v.stage
    level = v.level
    attack_step = v.attack_step
    decay_step = v.decay_step
    sustain_level = v.sustain_level
    release_step = v.release_step

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
            level -= decay_step
            if level <= sustain_level:
                level = sustain_level
                stage = _SUSTAIN
        elif stage == _RELEASE:
            level -= release_step
            if level <= 0:
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
    global lp_state

    # Bind globals to locals once. Even under @native these are dict
    # lookups otherwise, and they would happen on all 256 iterations.
    mb = mix_buf
    ob = out_buf
    n = BUF_SAMPLES

    # Q10 master gain, table lookup -- no float arithmetic anywhere in
    # this function. See VOL_TABLE.
    vol_q = VOL_TABLE[volume_pct]

    k = CUTOFF_TABLE[cutoff_pct]
    y = lp_state

    mb[:] = zero_buf   # C-level clear, not 256 interpreted stores

    for voice in voices:
        if voice.active:
            render_voice(voice, mb, n)

    # Drum one-shots mix into the SAME buffer, same signal chain position
    # (before filter/loop/volume) -- a snare hit gets filtered and can be
    # looped exactly like a melodic note.
    for dv in drum_voices:
        if dv.active:
            render_drum_voice(dv, mb, n)

    # Pass 1: lowpass, in place.
    i = 0
    while i < n:
        y += ((mb[i] - y) * k) >> 10
        mb[i] = y
        i += 1
    lp_state = y

    # Loop tap: records what you hear, mixes playback back in.
    looper.process(mb, n)

    # Pass 2: master volume and clip.
    i = 0
    while i < n:
        total = (mb[i] * vol_q) >> 10
        if total > 32000:
            total = 32000
        elif total < -32000:
            total = -32000
        ob[i] = total
        i += 1


# ============================================================
# STARTUP
# ============================================================

sync_state()
oled.displayState(state, looper)
# displayState only QUEUES pages now, so the first paint needs an
# explicit drain. flush() is the ~15 ms spike push_one() exists to avoid,
# which is exactly why it is confined to here -- before the audio loop
# starts there is no block budget to overrun.
oled.flush()
looper.update_bar(oled.display)

print("--- Pico 2 W Music Controller | Team 13 ---")
print("keypad: #/9 key +-  0/8 octave +-  */7 sample +-  6 mode")
print("        3 preset  5 rec  4 play/pause  1 loop reset")
print("loop: %.2f s | %d blocks | %d KB" % (
    looper.capacity / SAMPLE_RATE,
    looper.n_blocks,
    looper.capacity * 4 // 1024))     # 2 buffers x 2 bytes per sample
print("free heap after init:", gc.mem_free())
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
# three consecutive 16 ms blocks, and printing each one meant three
# blocking USB writes inside 48 ms of audio. 30 ms is under one frame at
# 30 fps, so the visualiser cannot tell.
JSON_MIN_INTERVAL_MS = 30

# Heartbeat rate for loop position while the looper runs. Slower than
# JSON_MIN_INTERVAL_MS on purpose: this one fires continuously for as
# long as the loop plays, so it sets the floor on idle serial traffic,
# whereas change lines are bursty and self-limiting. The visualiser
# needs a moving loop_pos, but a full OLED redraw at this rate would
# overrun the audio budget -- so the serial line and the screen are
# deliberately on separate clocks.
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
        # Drums bypass build_scale_freqs entirely -- button index IS the
        # drum index (fixed mapping), not a scale degree. See DRUM KIT.
        is_drums = (preset["sample"] == DRUM_KIT_NAME)

        # One frequency computed per newly-pressed button, not the whole
        # scale up front -- Melodic Minor's ascending/descending 6th/7th
        # is a per-note decision (see MELODIC MINOR docstring) that can't
        # be precomputed before knowing which button is about to sound.
        for i in range(NUM_VOICES):
            if key_bits[i] and not prev_key_bits[i]:
                if is_drums:
                    drum_voices[i].note_on(i)
                else:
                    ascending = (last_degree_index is None
                                 or i > last_degree_index)
                    freq = build_scale_freqs(
                        preset["key"], preset["octave"], preset["mode"],
                        i, ascending)
                    voices[i].note_on(freq, preset["sample"])
                    last_degree_index = i
            elif prev_key_bits[i] and not key_bits[i]:
                # NOT dispatched on is_drums, unlike note-on. is_drums
                # reflects the sample selected RIGHT NOW, but the voice
                # that needs releasing was started under whatever the
                # sample was when the button went down. Hold a note on
                # Strings, press '*' to cycle to Drums, release: the
                # drums branch would fire, DrumVoice.note_off() is a
                # no-op, and voices[i] is stranded in SUSTAIN and drones
                # forever. Preset switching (key '3') can cross the same
                # boundary. Releasing BOTH is unconditionally safe -- a
                # drum one-shot ignores note_off by design and an idle
                # Voice.note_off() is a no-op too -- so there is no
                # reason to branch here at all.
                voices[i].note_off()
                drum_voices[i].note_off()

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
    volume = read_pot(POT_VOLUME)
    if previous_volume == -1 or abs(volume - previous_volume) >= VOLUME_DEADBAND:
        previous_volume = volume
        state["volume"] = volume
        changed = True

    # Reports 100 (filter open) until GP27 has actually been swept -- see
    # read_pot. An unwired cutoff knob is now inaudible instead of
    # muffling the whole set.
    cutoff = read_pot(POT_CUTOFF, 100)
    if previous_cutoff == -1 or abs(cutoff - previous_cutoff) >= CUTOFF_DEADBAND:
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
        # Melodic Minor line's ascending/descending state could survive
        # a transpose that changed what "higher" even refers to.
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
        # Changes the step table build_scale_freqs uses for every
        # degree, so it resets direction state for the same reason
        # key/octave do above.
        elif pressed_key == "6":
            preset["mode"] = shift_mode(preset["mode"], 1)
            last_degree_index = None
            changed = True

        # --- preset select ---
        # CYCLES (2 presets today), not direct-select -- unlike the old
        # 1/4 scheme, a single key can't land on a specific preset by
        # index once there could be more than 2. Notes already sounding
        # keep the frequency and wavetable they were triggered with --
        # switching preset must not retune a held chord underneath you.
        # `preset` is rebound so anything later in this same pass edits
        # the newly selected one. Switching presets can change key,
        # octave, AND mode all at once, so this resets direction state
        # too.
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
    # both the bus and the 16 ms budget, so a pass that already spent
    # ~3 ms on the bar does not also spend 3 ms here. The queue simply
    # drains a pass later, which is 16 ms nobody can see.
    oled_pushed = False if bar_pushed else oled.push_one()


    # --- audio: must run every pass, unconditionally -------------------
    # Silence fast path: skip the clear, the voice/drum idle checks, and
    # the filter/loop/scale/clip passes entirely.
    any_active = False
    for voice in voices:
        if voice.active:
            any_active = True
            break
    if not any_active:
        # A drum one-shot can be the ONLY thing sounding (no melodic
        # buttons held), so this must be checked independently -- an
        # earlier version of this check only scanned `voices` and would
        # have written silence_buf straight over an active drum hit,
        # making every drum sound completely silent whenever no melodic
        # note happened to be held at the same time.
        for dv in drum_voices:
            if dv.active:
                any_active = True
                break

    # Three reasons the output may be non-zero with no key held:
    #   - the looper is playing back or recording
    #   - the filter still has a tail (lp_state has memory, and at a low
    #     cutoff that takes tens of milliseconds to decay)
    # Cutting to silence_buf while either is true is a step
    # discontinuity, i.e. a click at the end of every phrase.
    if (any_active or looper.state != loop.STOPPED
            or lp_state > 16 or lp_state < -16):
        generate_block(state["volume"], state["cutoff"])
        audio.write(out_buf)
    else:
        # Integer floor-shift is asymmetric, so a small negative lp_state
        # can converge to -1 and stay there forever. Zero it explicitly
        # (inaudible at this magnitude) rather than leaving a permanent
        # DC offset on the DAC.
        lp_state = 0
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