"""
PICO 2 W MUSIC CONTROLLER -- HAcK 2026, Team 13
================================================

  UI     -- 3x4 matrix keypad, OLED status display, two pots, loop
            transport.
  AUDIO  -- real-time I2S wavetable synth, one voice per note button,
            per-voice ADSR envelope, 15 instrument wavetables, one-pole
            lowpass filter, and a single-track overdubbing looper.

SIGNAL CHAIN
------------
    voices -> mix -> lowpass -> [LOOP TAP] -> master volume -> clip -> I2S

The loop tap sits after the filter and before the volume, so effects
bake permanently into a recording while the volume knob still rides
everything including the loop.

PIN MAP
-------
  Keypad rows    GP0, GP1, GP2, GP3      (high-Z except while scanning)
  Keypad cols    GP6, GP7, GP19          (internal pull-ups)
  Note buttons   GP15, 14, 13, 12, 11, 10, 9, 8   (active-low, pull-ups)
  Volume pot     GP26 (ADC0)
  Cutoff pot     GP27 (ADC1)
  OLED I2C       SDA=GP4, SCL=GP5
  I2S DAC        sck=GP16, ws=GP17, sd=GP18

  GP28/ADC2 is the only analog channel still free. ADC3 is NOT usable:
  on the W it is wired to the VSYS divider and shared with the CYW43
  SPI clock. GP20, GP21, GP22 are the only free digital pins.

KEYPAD
------
    1  loop reset       2  (unmapped)       3  cycle preset
    4  play / pause     5  loop record      6  cycle mode
    7  sample -         8  octave -         9  key -
    *  sample +         0  octave +         #  key +

Presets and modes both CYCLE on a single key (3 and 6) rather than
direct-select, unlike the old 1/4 preset scheme -- there are only two
presets but nine modes, and nine direct-select keys do not exist on a
3x4 pad. Cycling means the player has to know where they currently are;
the OLED is the only feedback for that, so it must stay legible.

A preset stores octave, key, sample, AND mode -- everything the keypad
edits. It deliberately does NOT store volume or cutoff: those are
physical pots, and a preset cannot move a knob. Recalling a stored
volume that disagrees with where the knob is sitting means the value
jumps the instant you touch it. The knob's position always wins.

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

  1. button poll interval   = BUF_SAMPLES / SAMPLE_RATE        = 23 ms
  2. audio already queued   = IBUF_BLOCKS * BUF_SAMPLES / RATE = 70 ms

In steady state the I2S buffer stays FULL -- the writer runs ahead until
write() blocks -- so every sample waits behind a full ibuf before it
reaches the DAC. Making the synth compute faster does not change this.
Only shrinking the queue does.

The tradeoff: a smaller ibuf means less slack, so any single loop pass
that overruns 23 ms produces an audible click. A full OLED frame is the
biggest spike (~25 ms), which is why the full redraw is rate-limited and
the loop progress bar pushes only its own 128-byte page. Set
TIMING = True and read the worst-case numbers before going tighter.

MELODIC MINOR
-------------
Ascending gets a raised 6th/7th (strong leading tone into the octave);
descending falls back to the natural minor 6th/7th. Direction is judged
by comparing the just-pressed button's index to the PREVIOUS note's
button index (higher index = ascending). `last_degree_index` remembers
that previous index and is deliberately reset to None -- "no direction
yet, assume ascending" -- on every edit that changes what a button index
even means: key, octave, mode, or preset switch. Skipping that reset
would let a melodic line's direction leak across an edit that changed
the scale underneath it.

SERIAL PROTOCOL
---------------
Every state change prints one line: '#' + JSON. While the looper runs, a
heartbeat line goes out every JSON_LOOP_INTERVAL_MS as well, so the
website can animate loop position. The website reads only lines starting
with '#'; anything else is human debug output. DEBUG = False silences it.

JSON fields: preset, octave, key, sample, mode, volume, cutoff, keys[8],
loop, loop_pos. `mode` is new as of this revision -- a visualizer reading
this feed for the first time should treat its absence as "Major".
"""

import gc
import time
import json
import math
from array import array
from micropython import const
from machine import Pin, I2C, ADC, I2S
import ssd1306
import loop

DEBUG = False     # per-keypress serial chatter
TIMING = False    # per-section worst-case timing report (see MAIN LOOP)


# ============================================================
# AUDIO CONSTANTS + LOOPER   (allocated FIRST, deliberately)
# ============================================================
# The loop buffers are by far the largest allocation in the project and
# they must be contiguous. gc.mem_free() reporting 400 KB means nothing
# if that free space is in scraps -- and the wavetables, OLED
# framebuffer, I2S buffer and cutoff table all carve the heap up as they
# are built. Constructing the Looper after them fails on a heap that has
# plenty free but no single run big enough.
#
# So it goes first, while the heap is still one clean block. Everything
# below is small and fits in whatever is left.

SAMPLE_RATE = 16000
BUF_SAMPLES = const(256)

# Raised from 11025 -> 16000 Hz (Aug 2026 audio-quality pass). Nyquist
# goes from 5512 Hz to 8000 Hz, which matters because the harmonic
# instrument recipes below (Organ, Trumpet, Strings, etc.) stack partials
# up to the 10th harmonic -- at the OLD rate, a played note's high
# partials routinely exceeded Nyquist and aliased into audible garbage,
# permanently baked into the wavetable at build time.
#
# KNOWN REMAINING LIMITATION: at mid-range play (around the default
# preset octave and below) every recipe is comfortably safe under the
# new Nyquist -- checked numerically, huge margin. Aliasing can still
# occur in roughly the TOP 1-2 octaves of the playable range (closer to
# MAX_OCTAVE=6) on the richest recipes (Organ, Trumpet, Clarinet,
# Strings). Deliberately NOT band-limited per-instrument or per-note --
# that would need either a dynamic per-pitch table rebuild (a real
# architecture change) or a conservative static trim of the highest
# partials, and the time was spent on the interpolation fix in
# render_voice() instead, which is the larger contributor to the
# reported bad sound quality. Worth a bench-test check at the top of the
# range if time allows before Sunday.
#
# Cost: block period is BUF_SAMPLES / SAMPLE_RATE, so raising the rate
# SHRINKS the audio timing budget -- from ~23.2 ms/block at 11025 Hz
# down to 16.0 ms/block here. The full OLED redraw (~25 ms even at
# 400 kHz I2C) was ALREADY close to the old budget in the worst case;
# at 16 ms it would overrun by a wider margin every time it fires. See
# OLED_MIN_INTERVAL_MS and the redraw-cost note near the display code
# for how that got addressed in the same pass -- raising the sample
# rate without also touching that would trade one audible click (tone
# aliasing) for a worse one (buffer underrun).

# Size the I2S buffer for double/triple buffering, NOT for a big safety
# margin -- every extra block of ibuf is another block of latency between
# a keypress and the sound. 3 blocks at 16000 Hz = 48 ms queued (was
# 70 ms at 11025 Hz -- the rate change is a latency win here even before
# counting the tone-quality fix). Drop to 2 for less latency if the
# timing harness shows headroom; raise to 4 if you hear clicks. This is
# the single most important number in the file.
IBUF_BLOCKS = 3

# Two int16 buffers (base take + overdub layer) at 32,000 bytes per
# second each at the new rate: 4 s costs 250 KB (was 172 KB at
# 11025 Hz). The Looper backs off in half-second steps if that will not
# fit, so a tight board gets a shorter loop rather than a traceback. The
# startup banner prints what it actually got. Worth checking that banner
# after this rate change -- the auto-shrink margin is smaller now.
LOOP_SECONDS = 4

looper = loop.Looper(SAMPLE_RATE, BUF_SAMPLES, seconds=LOOP_SECONDS)


# ============================================================
# 3x4 KEYPAD
# ============================================================

KEY_MAP = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"]
]

# Rows start as INPUTS. An input pin is high-Z (electrically
# disconnected); we only drive a row for the few microseconds we are
# actually scanning it, so no two rows can ever fight each other.
row_pins = [Pin(n, Pin.IN) for n in (0, 1, 2, 3)]
col_pins = [Pin(n, Pin.IN, Pin.PULL_UP) for n in (6, 7, 19)]

DEBOUNCE_MS = 20          # ignore a key's changes within this of its last
key_down = bytearray(12)  # believed state, index = row * 3 + col
key_time = [0] * 12       # ms timestamp of each key's last accepted change


def scan_keypad():
    """Sample the keypad once, return the key that was JUST pressed, or
    None. Never blocks -- important, because this runs inside the audio
    loop's time budget.

    Note the sampling limit: this runs once per audio block (~23 ms), so
    a tap shorter than that can fall entirely between two scans. Press
    keypad keys deliberately on stage."""
    now = time.ticks_ms()
    just_pressed = None

    for row_idx in range(4):
        row = row_pins[row_idx]
        row.init(Pin.OUT, value=0)

        for col_idx in range(3):
            i = row_idx * 3 + col_idx
            is_down = 1 if col_pins[col_idx].value() == 0 else 0

            if is_down != key_down[i]:
                if time.ticks_diff(now, key_time[i]) > DEBOUNCE_MS:
                    key_down[i] = is_down
                    key_time[i] = now
                    if is_down and just_pressed is None:
                        just_pressed = KEY_MAP[row_idx][col_idx]

        row.init(Pin.IN)

    return just_pressed


# ============================================================
# KEY / OCTAVE / SCALE
# ============================================================

CHROMATIC_SCALE = (
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B"
)

MIN_OCTAVE = 2
MAX_OCTAVE = 6

# Semitone offsets, including the octave on top, so the 8 buttons play
# root-to-root (e.g. C4..C5). Tuple values, not lists -- these are read
# constantly and never mutated.
#
# The 7 diatonic modes (Ionian..Locrian) plus harmonic minor. Each is a
# fixed rotation/alteration of the same 8-degree shape.
MODE_STEPS = {
    "Major":          (0, 2, 4, 5, 7, 9, 11, 12),   # Ionian
    "Dorian":         (0, 2, 3, 5, 7, 9, 10, 12),
    "Phrygian":       (0, 1, 3, 5, 7, 8, 10, 12),
    "Lydian":         (0, 2, 4, 6, 7, 9, 11, 12),
    "Mixolydian":     (0, 2, 4, 5, 7, 9, 10, 12),
    "Natural Minor":  (0, 2, 3, 5, 7, 8, 10, 12),   # Aeolian
    "Locrian":        (0, 1, 3, 5, 6, 8, 10, 12),
    "Harmonic Minor": (0, 2, 3, 5, 7, 8, 11, 12),
}

# Real melodic minor is not one fixed scale -- the 6th and 7th degrees
# depend on melodic direction:
#   ascending:  raised 6th & 7th (strong leading tone into the octave)
#   descending: natural 6th & 7th (same as natural minor)
# Handled separately from MODE_STEPS for that reason; see
# scale_step_for_degree() and the MELODIC MINOR note in the module
# docstring.
MELODIC_MINOR_ASCENDING_STEPS = (0, 2, 3, 5, 7, 9, 11, 12)
MELODIC_MINOR_DESCENDING_STEPS = MODE_STEPS["Natural Minor"]

# Cycling order for keypad key '6'. Tuple, not a dict -- MicroPython
# does not preserve dict insertion order, and deriving cycle order from
# one would scramble it and make the mode key unrehearsable on stage.
MODE_LIST = (
    "Major",
    "Natural Minor",
    "Melodic Minor",
    "Harmonic Minor",
    "Dorian",
    "Phrygian",
    "Lydian",
    "Mixolydian",
    "Locrian",
)

# OLED labels ARE shown (see displayState()'s "Mode: " prefix), but the
# mode VALUE still needs to be short: "Mode: Harmonic Minor" is 160px,
# wider than the 128px screen, so the full mode name literally cannot
# render even with the whole row to itself. 8 chars is the practical
# ceiling for the value at 8px-wide default font, once "Mode: " (6 chars)
# is accounted for.
MODE_DISPLAY_LABEL = {
    "Major":          "Major",
    "Natural Minor":  "NatMin",
    "Melodic Minor":  "MelMin",
    "Harmonic Minor": "HarMin",
    "Dorian":         "Dorian",
    "Phrygian":       "Phryg",
    "Lydian":         "Lydian",
    "Mixolydian":     "Mixo",
    "Locrian":        "Locr",
}


def shift_key(current_key, step):
    """Transpose by `step` half steps, wrapping around the octave."""
    idx = CHROMATIC_SCALE.index(current_key)
    return CHROMATIC_SCALE[(idx + step) % 12]


def shift_octave(current_octave, step):
    """Shift octave by `step`, clamped (no wrap -- wrapping mid-song
    would jump the instrument three octaves on a single keypress)."""
    return max(MIN_OCTAVE, min(MAX_OCTAVE, current_octave + step))


def shift_mode(current_mode, step):
    """Step through MODE_LIST, wrapping. `step` is always +-1 from the
    keypad today, but this takes a step count (not just "next") to
    match shift_key/shift_octave/shift_sample's shape."""
    idx = MODE_LIST.index(current_mode)
    return MODE_LIST[(idx + step) % len(MODE_LIST)]


def scale_step_for_degree(mode, degree_index, ascending):
    """Which semitone step to use for one button/degree (0-7),
    accounting for melodic minor's direction-dependent 6th/7th."""
    if mode == "Melodic Minor":
        return (MELODIC_MINOR_ASCENDING_STEPS if ascending
                else MELODIC_MINOR_DESCENDING_STEPS)[degree_index]
    return MODE_STEPS.get(mode, MODE_STEPS["Major"])[degree_index]


def build_scale_freqs(key, octave, mode, degree_index, ascending):
    """One frequency (Hz) for a single note button, in `key`/`octave`,
    using `mode` (direction-aware for Melodic Minor via `ascending`).
    Equal temperament, A4 = 440 Hz.

    Takes ONE degree, not all 8 -- unlike the old build_scale_freqs,
    which always built and returned a fresh 8-element list regardless
    of how many buttons actually changed. Melodic Minor needs a
    per-note ascending/descending direction that can differ button to
    button within the same keypress batch, so the note-on loop now
    calls this once per newly-pressed button instead of computing the
    whole scale up front."""
    root_idx = CHROMATIC_SCALE.index(key)
    step = scale_step_for_degree(mode, degree_index, ascending)
    total = root_idx + step
    note_octave = octave + total // 12
    note_idx = total % 12
    semitones_from_a4 = (note_octave - 4) * 12 + (note_idx - 9)
    return 440.0 * (2 ** (semitones_from_a4 / 12))


# ============================================================
# WAVETABLES
# ============================================================

TABLE_LEN = const(256)
TABLE_AMP = 32000


def make_table(fn):
    """Sample one cycle of fn(t), t in [0,1), into a signed 16-bit table."""
    return array("h", [int(fn(i / TABLE_LEN) * TABLE_AMP)
                       for i in range(TABLE_LEN)])


def harmonic(partials, norm=None):
    """Build a waveform function from (harmonic multiple, amplitude) pairs.

    norm defaults to the sum of the amplitudes -- the worst case peak of
    the sum, which guarantees |output| <= 1 so the table can never clip.
    Real peaks are lower (the partials don't all crest together), so this
    is conservative, which is what we want with 8 voices mixing.
    """
    if norm is None:
        norm = sum(amp for _, amp in partials)
    two_pi = 2 * math.pi

    def fn(t):
        v = 0.0
        for mult, amp in partials:
            v += amp * math.sin(two_pi * mult * t)
        return v / norm

    return fn


# --- Non-harmonic (geometric) waveforms ---

def square_fn(t):
    return 1.0 if t < 0.5 else -1.0


def saw_fn(t):
    return 2.0 * t - 1.0


def triangle_fn(t):
    return 4.0 * abs(t - 0.5) - 1.0


def pulse25_fn(t):
    return 1.0 if t < 0.25 else -1.0


# --- Harmonic recipes: (harmonic multiple, amplitude) ---
# The physics behind each spectrum is the defensible part of this file;
# these are not arbitrary numbers.

# Principal + mixture chorus. Organ "depth" comes from many pipes
# sounding at once at different pitches, not from one pipe's timbre --
# so the fix for a thin organ is more ranks, not a louder fundamental.
ORGAN_P = ((1, 1.00), (2, 0.75), (3, 0.50), (4, 0.40), (5, 0.25),
           (6, 0.20), (8, 0.15), (9, 0.10), (10, 0.08), (12, 0.05))

# Bells are dominated by NON-integer partials -- that inharmonicity
# matters far more to the "bell" identity than adding normal harmonics.
BELL_P = ((1, 1.00), (2.71, 0.55), (4.07, 0.32), (5.83, 0.22), (7.91, 0.12))

# Young's plucked-string theorem: amplitude of harmonic n goes as
# sin(n*pi*p)/n for fractional pluck position p. p = 1/8 is a
# harpsichord plectrum near the bridge -- bright and tinny.
PLUCK_P = ((1, 1.00), (2, 0.92), (3, 0.81), (4, 0.65), (5, 0.48), (6, 0.31))

# Same theorem at p = 1/5 (classical nylon, plucked over the soundhole)
# -- warmer. n=5 is absent because plucking exactly at 1/5 puts a node
# at the pluck point, so that harmonic physically cannot be excited.
GUITAR_P = ((1, 1.00), (2, 0.81), (3, 0.54), (4, 0.25), (6, 0.17))

# Piano strings are stiff, so partials sit slightly SHARP of integer
# multiples (inharmonicity) -- that detuning is what keeps it from
# sounding like an organ.
PIANO_P = ((1, 1.00), (2.01, 0.62), (3.02, 0.38), (4.04, 0.24),
           (5.08, 0.16), (6.13, 0.10), (7.20, 0.06))

BASS_P = ((1, 1.00), (2, 0.48), (3, 0.22), (4, 0.10))

# Flute is the purest orchestral tone -- genuinely harmonic-poor, just
# enough above the fundamental not to be a bare sine. Do not "enrich"
# this; the weakness IS the flute.
FLUTE_P = ((1, 1.00), (2, 0.15), (3, 0.07), (4, 0.03), (5, 0.015), (6, 0.008))

# Closed cylindrical pipe -> ODD harmonics dominate. Weighted toward the
# low (chalumeau) register, where the fundamental carries most of the
# energy: warm and hollow rather than bright.
CLARINET_P = ((1, 1.00), (2, 0.08), (3, 0.55), (4, 0.10),
              (5, 0.30), (7, 0.14), (9, 0.08))

# Cylindrical bore after the mouthpiece allows nonlinear wave steepening
# down the tube, pushing energy into high harmonics -- why a trumpet is
# brighter than a conical-bore flugelhorn or euphonium.
TRUMPET_P = ((1, 1.00), (2, 0.85), (3, 0.72), (4, 0.58), (5, 0.46),
             (6, 0.36), (7, 0.27), (8, 0.20), (9, 0.14), (10, 0.09))

# Many harmonics, but less aggressive high end than brass.
STRINGS_P = ((1, 1.00), (2, 0.72), (3, 0.50), (4, 0.35),
             (5, 0.24), (6, 0.16), (7, 0.10), (8, 0.06))


# ============================================================
# INSTRUMENT TABLE  (single source of truth)
# ============================================================
# One ordered tuple defines the name, the waveform, the ADSR envelope,
# AND the cycling order for the keypad. Tuple, not a dict, because
# MicroPython does not preserve dict insertion order -- deriving the
# cycle order from a dict would scramble it and make the '*'/'7' sample
# keys unrehearsable on stage.
#
# Envelope = (attack_ms, decay_ms, sustain_level, release_ms)

INSTRUMENTS = (
    # --- synth waveforms ---
    ("Sine",     harmonic(((1, 1.0),)),  (10,  80, 0.85, 150)),
    ("Square",   square_fn,              (5,   60, 0.80, 100)),
    ("Sawtooth", saw_fn,                 (15, 120, 0.75, 200)),
    ("Triangle", triangle_fn,            (10, 100, 0.85, 180)),
    ("Pulse",    pulse25_fn,             (5,   60, 0.75, 120)),

    # --- modelled instruments ---
    ("Organ",    harmonic(ORGAN_P),      (12,  40, 0.97, 400)),
    ("Bell",     harmonic(BELL_P),       (1,  400, 0.22, 1900)),
    ("Pluck",    harmonic(PLUCK_P),      (2,  200, 0.09, 90)),
    # norm=3.15 is deliberate, not the 2.56 amplitude sum -- piano was
    # hand-trimmed down because its inharmonic partials beat against
    # each other and read as louder than the sum predicts.
    ("Piano",    harmonic(PIANO_P, 3.15), (2, 450, 0.12, 450)),
    ("Guitar",   harmonic(GUITAR_P),     (3,  100, 0.25, 800)),
    ("Bass",     harmonic(BASS_P),       (3,  190, 0.15, 90)),
    ("Flute",    harmonic(FLUTE_P),      (90, 100, 0.92, 240)),
    ("Clarinet", harmonic(CLARINET_P),   (20,  45, 0.94, 150)),
    ("Trumpet",  harmonic(TRUMPET_P),    (25,  50, 0.85, 160)),
    ("Strings",  harmonic(STRINGS_P),    (140, 100, 0.92, 550)),
)

WAVETABLES = {name: make_table(fn) for name, fn, _ in INSTRUMENTS}
ENVELOPES = {name: env for name, _, env in INSTRUMENTS}

# "Drums" (not "Drum Kit") deliberately -- checked against the OLED's
# "Sample: <name>" row, which is already at its exact 128px limit for
# the longest existing instrument names (Sawtooth/Triangle/Clarinet, all
# 8 chars). "Drums" (5 chars) keeps margin instead of adding a second
# zero-margin case; "Drum Kit" would have landed at the same 128px limit
# as those three.
DRUM_KIT_NAME = "Drums"

SAMPLE_LIST = tuple(name for name, _, _ in INSTRUMENTS) + (DRUM_KIT_NAME,)


def shift_sample(current_sample, step):
    """Step through SAMPLE_LIST, wrapping. Includes "Drums" -- cycling
    the sample key with '*'/'7' reaches the drum kit exactly like any
    other instrument. What's DIFFERENT about drums happens entirely at
    the note-on dispatch site in the main loop (see DRUM KIT below),
    not here."""
    idx = SAMPLE_LIST.index(current_sample)
    return SAMPLE_LIST[(idx + step) % len(SAMPLE_LIST)]


# ============================================================
# DRUM KIT  (one-shot sample playback -- Aug 2026 audio pass)
# ============================================================
# Structurally different from every instrument above: those are TUNED,
# LOOPING wavetables -- one short cycle (TABLE_LEN samples) repeated at
# a pitch-dependent rate, sustained for as long as the button is held
# (Voice's ADSR has a SUSTAIN stage that holds indefinitely). A drum hit
# is a one-shot: a long (thousands of samples), non-looping recording
# that plays exactly once front-to-back and stops itself when the
# sample data runs out, whether or not the button is still down. There
# is no sustain stage and no meaningful "pitch" to assign per scale
# degree.
#
# Trying to force one-shot playback through Voice's phase/TABLE_LEN-wrap
# machinery would mean either abusing phase_inc=1.0 with a per-voice
# TABLE_LEN (breaking the global TABLE_LEN constant render_voice already
# hardcodes) or adding branches to render_voice's per-sample hot loop for
# a case that does not apply to the other 14 instruments. Cheaper and
# clearer to give drums their own voice type (DrumVoice) and render
# function (render_drum_voice), mixed into the same mix_buf.
#
# Per your call: button 1=Kick, 2=Snare, 3=Hat Closed, 4=Hat Open,
# 5=Clap, 6=Tom Low, 7=Tom Mid, 8=Crash -- FIXED regardless of the
# preset's key/octave/mode. Selecting "Drums" as the sample bypasses
# build_scale_freqs() entirely for note-on; see the main loop's note
# button section.
#
# Samples are PRECOMPUTED offline (generate_drums.py, not shipped to the
# Pico) and loaded here as flat .wav files -- not synthesized live at
# boot. Generating ~30,000 samples of noise/sine-sweep DSP in interpreted
# MicroPython was estimated at 1.5-6+ seconds of boot time depending on
# optimization; a slow, unpredictable boot is a real risk if the board
# ever needs a reset mid-performance. Loading precomputed files is
# effectively instant by comparison.

DRUM_FILES = (
    "kick.wav", "snare.wav", "hat_closed.wav", "hat_open.wav",
    "clap.wav", "tom_low.wav", "tom_mid.wav", "crash.wav",
)


def _load_wav_samples(path):
    """Read a 16-bit mono PCM .wav file's sample data into an array('h').

    Skips the RIFF/fmt header by finding the 'data' chunk rather than
    hardcoding a 44-byte offset -- the files as generated have no extra
    chunks so 44 would work today, but trusting the header's own chunk
    size is one extra line and survives a future regeneration that adds
    metadata chunks.

    Raises (loudly, at boot, not mid-performance) if a file is missing or
    malformed -- same philosophy as the Looper's allocation: fail loud
    and early rather than silently play garbage or silence on stage."""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise RuntimeError(path + ": not a RIFF/WAVE file")

        # Walk chunks until 'data' is found.
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                raise RuntimeError(path + ": no data chunk found")
            chunk_size = int.from_bytes(f.read(4), "little")
            if chunk_id == b"data":
                raw = f.read(chunk_size)
                break
            f.read(chunk_size)   # skip this chunk (e.g. 'fmt ')

    samples = array("h", raw)
    if len(samples) * 2 != len(raw):
        raise RuntimeError(path + ": data chunk size not a multiple of 2 bytes")
    return samples


# DRUM_KIT_DIR: where the .wav files live on the Pico's flash filesystem,
# alongside main.py/loop.py/ssd1306.py. Loaded AFTER the Looper (see the
# AUDIO CONSTANTS + LOOPER section at the top of this file) for the same
# reason WAVETABLES is built after it -- the Looper's ~250 KB allocation
# needs the heap to still be one clean block, and drum samples, while
# individually small, are still real allocations that should happen once
# the big one is safely done.
DRUM_KIT_DIR = "drums/"

DRUM_SAMPLES = tuple(_load_wav_samples(DRUM_KIT_DIR + fname)
                      for fname in DRUM_FILES)


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
#
# It deliberately does NOT store volume or cutoff. Those are physical
# pots, and no preset can move a knob -- recalling a stored volume that
# disagrees with the knob's position means the value snaps the instant
# you touch it mid-song. The knob's position always wins.
#
# `presets[active_preset]` is the single source of truth for pitch and
# timbre. `state` is a flat mirror of everything, rebuilt by sync_state()
# purely so the OLED and the website have one object to read.
#
# PRESET_FIELDS lists every key a preset owns. sync_state() below loops
# over this instead of one hand-written line per field, specifically so
# that adding a field to a preset (like `mode` was just added) cannot
# silently forget to add the matching sync line -- that failure mode is
# invisible in testing (the preset itself is still correct) and only
# shows up on stage as "I changed it but the website/OLED didn't
# update."

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
    """Copy every preset-owned field (PRESET_FIELDS) into the reported
    state. Called once per update rather than at every edit site, so
    there is no way for the mirror to drift out of step with the preset
    behind it -- and looping over PRESET_FIELDS instead of listing each
    field by hand means a future field addition can't be forgotten
    here."""
    p = presets[active_preset]
    state["preset"] = active_preset + 1
    for field in PRESET_FIELDS:
        state[field] = p[field]


# ============================================================
# 8 NOTE BUTTONS
# ============================================================

BUTTON_PINS = (15, 14, 13, 12, 11, 10, 9, 8)
buttons = [Pin(p, Pin.IN, Pin.PULL_UP) for p in BUTTON_PINS]

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


def read_pot(ch):
    """Return 0-100 for ADC channel index `ch`, using that channel's own
    learned calibration. Averages 4 reads: the deadband already swamps
    ADC noise for the reported value, but adc_min/adc_max latch onto
    extremes permanently, so one noise spike would widen a channel's
    learned range for the rest of the set."""
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
    else:
        pct = round(raw / 65535 * 100)   # not swept yet

    return max(0, min(100, pct))


# ============================================================
# OLED
# ============================================================

# freq set explicitly: a full 1 KB frame at the 100 kHz default takes
# ~100 ms, which would blow the entire audio block budget on its own. At
# 400 kHz a full display.show() is still ~23-25 ms of raw I2C transfer
# time -- more than the ENTIRE 16 ms block budget at the current
# SAMPLE_RATE (see the SAMPLE_RATE comment for that history). A full
# redraw was already tight at the old 23.2 ms budget; at 16 ms it would
# overrun by close to its own length every time it fired. Fixed below by
# never calling display.show() from displayState() at all -- see
# PAGE_HEIGHT / _push_page.
i2c = I2C(0, sda=Pin(4), scl=Pin(5), freq=400000)
display = ssd1306.SSD1306_I2C(128, 64, i2c)

OLED_MIN_INTERVAL_MS = 100   # partial redraw is cheap now; can run more often
BAR_INTERVAL_MS = 50         # loop progress bar, one page only (~3 ms)

# SSD1306 command bytes for addressing a single 8px-tall "page" (row),
# same technique loop.py already uses for the progress bar -- see
# Looper.update_bar(). Duplicated here rather than imported from loop.py
# because it is a display-driver detail, not a looper detail; the two
# modules happen to both need it.
_SET_COL_ADDR = 0x21
_SET_PAGE_ADDR = 0x22
PAGE_HEIGHT = const(8)


def _push_page(page):
    """Write ONE 128-byte page from display.buffer to the OLED, instead
    of display.show()'s full 1024-byte frame. ~1/8th the I2C traffic,
    which is the difference between overrunning the audio block budget
    and not. Mirrors loop.py's update_bar() exactly, generalized to any
    page 0-7 instead of hardcoding page 7."""
    display.write_cmd(_SET_COL_ADDR)
    display.write_cmd(0)
    display.write_cmd(127)
    display.write_cmd(_SET_PAGE_ADDR)
    display.write_cmd(page)
    display.write_cmd(page)
    display.write_data(display.buffer[page * 128:(page + 1) * 128])


# Last-drawn content string per page, keyed by page number. displayState()
# skips redrawing/pushing a page whose content matches what is already on
# screen. None (not "") for every entry so the very FIRST call always
# treats every page as changed and does a full initial paint -- an empty
# string would look identical to a page that hasn't been drawn yet if any
# real content ever manages to be genuinely empty, but None can never
# collide with a str.
_page_last = {0: None, 1: None, 2: None, 3: None, 5: None}


def displayState(st):
    """Partial, CHANGE-DETECTED redraw: draws into display.buffer exactly
    like a full redraw would, but pushes only the pages whose CONTENT
    actually changed since the last call, as individual 128-byte page
    writes. Never display.show(), which would push all 1024 bytes
    including blank/unused pages and the looper's progress bar page
    every single time.

    Why change-detection and not just "always push all 5 text pages":
    pushing every text page unconditionally still costs ~15 ms of raw
    I2C transfer (5 pages x ~134 bytes x 9 bits / 400 kHz), against a
    16 ms block budget -- under 1 ms of margin, too tight to trust. The
    common case (only the volume pot moved, say) only needs ONE page
    repainted, not five, so tracking each row's last-drawn string and
    skipping unchanged ones keeps the typical call cheap even though the
    worst case (everything changed at once, e.g. a preset switch) still
    costs close to the full ~15 ms.

    ACCEPTED RISK: a single action that changes all 5 pages at once (the
    preset-cycle key changes key/octave/sample/mode together) still costs
    ~15 ms against the 16 ms budget -- tight, and a genuine overrun is
    possible if it lands on an already-expensive block. Deliberately NOT
    spread across multiple loop passes to avoid a pending-pages queue and
    the extra state that would need testing time this build doesn't have.
    Accepted because it is a single, rare, self-limiting action rather
    than a sustained problem -- worth revisiting if bench testing shows
    an audible click specifically on preset switch.

    Rows 56-63 (page 7) belong to the looper's progress bar, pushed
    separately and far more often by loop.py -- never touched here.

    LAYOUT  (128px wide / 16 chars per row at the default 8px font;
    every row below MUST stay on an 8px page boundary -- y a multiple
    of 8 -- or a partial-page push will only ever move the top half of
    that row's text and leave the bottom half stale on screen. This is
    why the loop-status row moved from y=44, which straddled pages 5/6,
    to y=40, which does not.):

        row 0   (page 0)  P1 Key:C          Oct:4     preset/key/octave
        row 8   (page 1)  Sample: Sawtooth            sample name
        row 16  (page 2)  Mode: HarMin                mode (abbreviated
                                                        value -- see
                                                        MODE_DISPLAY_LABEL,
                                                        "Mode: Harmonic
                                                        Minor" is 160px,
                                                        wider than the
                                                        128px screen)
        row 24  (page 3)  Vol: 75           LP: 100   volume, cutoff
        row 32  (page 4)  -- blank, dropped key-bitmap rows --
        row 40  (page 5)  Loop: EMPTY                 loop status
        row 48  (page 6)  -- blank --
        row 56  (page 7)  -- owned by loop.py's progress bar --

    Adding a new row means adding it to _PAGE_TEXT below AND to its
    page's entry in _page_last (both near the top of the OLED section) --
    forgetting either draws the new text correctly into the buffer but
    never pushes it to the physical screen, which is a silent, confusing
    bug (the OLED would just never show the new field)."""
    page0 = "P" + str(st["preset"]) + " Key:" + st["key"]
    page0b = "Oct:" + str(st["octave"])
    page1 = "Sample: " + st["sample"]
    mode_label = MODE_DISPLAY_LABEL.get(st["mode"], st["mode"])
    page2 = "Mode: " + mode_label
    page3 = "Vol: " + str(st["volume"])
    page3b = "LP: " + str(st["cutoff"])
    # `looper` is defined below but always exists by the time this runs.
    page5 = "Loop: " + looper.status_text()

    # One combined key per page so a change in EITHER string on a shared
    # page (e.g. Vol vs LP, both on page 3) is still detected -- comparing
    # them separately would need two dirty-flags per page for no benefit.
    new_content = {
        0: page0 + "|" + page0b,
        1: page1,
        2: page2,
        3: page3 + "|" + page3b,
        5: page5,
    }

    for page, content in new_content.items():
        if _page_last[page] == content:
            continue   # unchanged since last call -- skip blank/draw/push entirely

        display.fill_rect(0, page * PAGE_HEIGHT, 128, PAGE_HEIGHT, 0)

        if page == 0:
            display.text(page0, 0, 0)
            display.text(page0b, 80, 0)
        elif page == 1:
            display.text(page1, 0, 8)
        elif page == 2:
            display.text(page2, 0, 16)
        elif page == 3:
            display.text(page3, 0, 24)
            display.text(page3b, 64, 24)
        elif page == 5:
            display.text(page5, 0, 40)

        _push_page(page)
        _page_last[page] = content






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

NUM_VOICES = len(BUTTON_PINS)   # one voice per button

# Envelope stages as ints, not strings. const() inlines these as literal
# integers at compile time, so the stage comparisons in render_voice --
# which run once per sample per voice, up to 2048 times per block --
# become integer compares instead of object compares.
_IDLE = const(0)
_ATTACK = const(1)
_DECAY = const(2)
_SUSTAIN = const(3)
_RELEASE = const(4)


class Voice:
    # __slots__ avoids a per-instance dict: less RAM, faster attribute
    # access, and no dict growth to trigger GC mid-note.
    __slots__ = (
        "active", "table", "phase", "phase_inc", "stage", "level",
        "attack_step", "decay_step", "sustain_level", "release_step",
    )

    def __init__(self):
        self.active = False
        self.table = None
        self.phase = 0.0
        self.phase_inc = 0.0
        self.stage = _IDLE
        self.level = 0.0
        self.attack_step = 0.0
        self.decay_step = 0.0
        self.sustain_level = 0.0
        self.release_step = 0.0

    def note_on(self, freq, instrument):
        self.table = WAVETABLES.get(instrument, WAVETABLES["Sine"])
        self.phase = 0.0
        # Table steps per output sample. Stays < TABLE_LEN for every note
        # in our range, which is what lets render_voice wrap with a single
        # subtract instead of a modulo.
        self.phase_inc = freq * TABLE_LEN / SAMPLE_RATE

        a_ms, d_ms, s_lvl, r_ms = ENVELOPES.get(instrument, ENVELOPES["Sine"])
        a_samples = max(1, int(a_ms * SAMPLE_RATE / 1000))
        d_samples = max(1, int(d_ms * SAMPLE_RATE / 1000))
        r_samples = max(1, int(r_ms * SAMPLE_RATE / 1000))

        self.attack_step = 1.0 / a_samples
        self.decay_step = (1.0 - s_lvl) / d_samples
        self.sustain_level = s_lvl
        self.release_step = s_lvl / r_samples

        self.stage = _ATTACK
        self.level = 0.0
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
# 32000 output ceiling, so some division is mandatory; 3.0 targets a
# roughly 3-note chord reaching full scale at volume 100. A loop playing
# underneath adds as much again, so if you clip while looping, raise this
# to 5.0 or 6.0 -- or just back the volume knob off, which is what a real
# looper expects you to do.
MIX_HEADROOM = 3.0


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
# Kept just under Nyquist at the CURRENT SAMPLE_RATE, same margin as
# before (roughly 500 Hz of headroom) -- this constant must move if
# SAMPLE_RATE moves again. At 16000 Hz, Nyquist is 8000 Hz.
CUTOFF_MAX_HZ = 7200


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
    envelope. Every per-sample value is pulled into a local first --
    self.x lookups are among the slowest operations in MicroPython and
    this loop runs 256 times per voice per block.

    LINEAR INTERPOLATION (added in the Aug 2026 audio-quality pass):
    the old code read table[int(phase)] with no interpolation --
    nearest-neighbor / zeroth-order-hold. At the phase increments real
    notes actually use (10-70+ table-steps per output sample across the
    playable range -- a played note this far above the table's own
    256-sample resolution skips large, uneven chunks of the table
    between samples), that produces a harsh, gritty, "clicking/clacking"
    character on EVERY note, worse at higher pitches -- this was very
    likely the dominant contributor to the reported bad sound quality,
    more than the sample-rate/aliasing issue fixed elsewhere in this
    pass. Interpolating between the two nearest table samples smooths
    that step down to what it should sound like: a clean tone at the
    table's actual harmonic content, not extra digital noise on top of
    it.

    Cost: one extra array read, one subtract, one multiply, one add per
    sample -- cheap next to the envelope/mix work already happening
    here, and unlike raising SAMPLE_RATE this does not shrink the block
    time budget at all.

    idx1 wraps to 0 only in the single case idx0 == TABLE_LEN - 1 (right
    before phase itself wraps) -- handled with one comparison rather
    than a modulo every sample, since idx0 is already guaranteed to stay
    in [0, TABLE_LEN) by the existing phase-wrap logic below."""
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
        idx0 = int(phase)
        idx1 = idx0 + 1
        if idx1 >= TABLE_LEN:
            idx1 = 0
        frac = phase - idx0
        s0 = table[idx0]
        raw = s0 + (table[idx1] - s0) * frac

        phase += phase_inc
        if phase >= TABLE_LEN:
            phase -= TABLE_LEN

        if stage == _ATTACK:
            level += attack_step
            if level >= 1.0:
                level = 1.0
                stage = _DECAY
        elif stage == _DECAY:
            level -= decay_step
            if level <= sustain_level:
                level = sustain_level
                stage = _SUSTAIN
        elif stage == _RELEASE:
            level -= release_step
            if level <= 0.0:
                level = 0.0
                stage = _IDLE

        mix_buf[n] += int(raw * level)
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

    x = volume_pct / 100.0
    # Square law approximates how the ear hears loudness, so the pot
    # feels linear across its travel.
    vol = x * x / MIX_HEADROOM

    k = CUTOFF_TABLE[cutoff_pct]
    y = lp_state

    mb[:] = zero_buf   # C-level clear, not 256 interpreted stores

    for voice in voices:
        if voice.active:
            render_voice(voice, mb, n)

    # Drum one-shots mix into the SAME buffer, same signal chain
    # position (before the filter/loop/volume passes below) -- a snare
    # hit gets filtered and can be looped exactly like a melodic note.
    # Independent of `voices` above: a melodic note already triggered
    # keeps playing out on its own schedule even if the sample selector
    # has since switched to Drums (see the DRUM KIT section for why this
    # needs no special-case handling -- Voice.note_on already copies its
    # table/envelope by value, so it never re-reads preset["sample"]).
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
        total = int(mb[i] * vol)
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
displayState(state)
looper.update_bar(display)

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
oled_last_ms = time.ticks_ms()
bar_last_ms = time.ticks_ms()
json_last_ms = time.ticks_ms()

# Heartbeat rate for loop position while the looper runs. The visualiser
# needs a moving loop_pos, but a full OLED redraw at this rate would
# overrun the audio budget -- so the serial line and the screen are
# deliberately on separate clocks.
JSON_LOOP_INTERVAL_MS = 100

# Collect on our own schedule, in the slack right after write() returns,
# rather than letting an allocation trigger one at an arbitrary moment
# mid-block. ~every 0.5 s at these settings.
GC_EVERY_N_BLOCKS = 20
block_count = 0

# Timing harness: worst-case microseconds per section since the last
# report. Only touched when TIMING is True.
t_max = [0, 0, 0, 0, 0]   # buttons, pots, keypad, display+serial, audio
t_report_ms = time.ticks_ms()


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    changed = False
    preset = presets[active_preset]

    if TIMING:
        t0 = time.ticks_us()

    # --- 8 note buttons -> voices ------------------------------------
    for i in range(NUM_VOICES):
        key_bits[i] = 1 if buttons[i].value() == 0 else 0

    if key_bits != prev_key_bits:
        # Drums bypass build_scale_freqs and the melodic Voice[] array
        # entirely -- button index IS the drum index (fixed Kick/Snare/
        # HatClosed/HatOpen/Clap/TomLow/TomMid/Crash mapping), not a
        # scale degree. See the DRUM KIT section for why one-shot
        # playback needs a structurally different voice type.
        is_drums = (preset["sample"] == DRUM_KIT_NAME)

        # One frequency computed per newly-pressed button, not the whole
        # scale up front -- Melodic Minor's ascending/descending 6th &
        # 7th is a per-note decision (see MELODIC MINOR in the module
        # docstring), so it cannot be precomputed for all 8 degrees
        # before knowing which one is about to sound. Skipped entirely
        # for drums, which have no scale degree to compute.
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
                if is_drums:
                    drum_voices[i].note_off()   # no-op; see DrumVoice
                else:
                    voices[i].note_off()

        state["keys"] = [b == 1 for b in key_bits]
        prev_key_bits[:] = key_bits
        changed = True

    if TIMING:
        t1 = time.ticks_us()

    # --- pots: volume (GP26) and filter cutoff (GP27) -----------------
    volume = read_pot(POT_VOLUME)
    if previous_volume == -1 or abs(volume - previous_volume) >= VOLUME_DEADBAND:
        previous_volume = volume
        state["volume"] = volume
        changed = True

    cutoff = read_pot(POT_CUTOFF)
    if previous_cutoff == -1 or abs(cutoff - previous_cutoff) >= CUTOFF_DEADBAND:
        previous_cutoff = cutoff
        state["cutoff"] = cutoff
        changed = True

    if TIMING:
        t2 = time.ticks_us()

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

    if TIMING:
        t3 = time.ticks_us()

    # --- serial (immediate + loop heartbeat), OLED (rate limited) -----
    now_ms = time.ticks_ms()

    loop_running = looper.is_sounding() or looper.state == loop.RECORDING

    if changed or (loop_running and
                   time.ticks_diff(now_ms, json_last_ms) >= JSON_LOOP_INTERVAL_MS):
        sync_state()
        state["loop"] = looper.state_name()
        state["loop_pos"] = looper.progress()
        print("#" + json.dumps(state))
        json_last_ms = now_ms
        if changed:
            oled_dirty = True

    if oled_dirty and time.ticks_diff(now_ms, oled_last_ms) >= OLED_MIN_INTERVAL_MS:
        displayState(state)
        oled_last_ms = now_ms
        oled_dirty = False

    # Progress bar on its own, faster clock. Pushes one 128-byte page
    # rather than a 1 KB frame, so 20 fps costs ~3 ms a tick.
    if loop_running and time.ticks_diff(now_ms, bar_last_ms) >= BAR_INTERVAL_MS:
        looper.update_bar(display)
        bar_last_ms = now_ms

    if TIMING:
        t4 = time.ticks_us()

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
        block_count = 0
        gc.collect()

    if TIMING:
        t5 = time.ticks_us()
        spans = (
            time.ticks_diff(t1, t0),   # buttons
            time.ticks_diff(t2, t1),   # pots
            time.ticks_diff(t3, t2),   # keypad
            time.ticks_diff(t4, t3),   # display + serial
            time.ticks_diff(t5, t4),   # render + write + gc
        )
        for idx in range(5):
            if spans[idx] > t_max[idx]:
                t_max[idx] = spans[idx]

        if time.ticks_diff(time.ticks_ms(), t_report_ms) >= 1000:
            # Budget is per block. The audio span includes the blocking
            # write(), so it sits near budget by definition -- the
            # numbers that matter are the other four, whose sum has to
            # fit in the slack.
            print("[timing] worst us: btn={} pot={} pad={} disp={} audio={}"
                  "  budget={}  free={}".format(
                      t_max[0], t_max[1], t_max[2], t_max[3], t_max[4],
                      BUF_SAMPLES * 1000000 // SAMPLE_RATE, gc.mem_free()))
            t_max = [0, 0, 0, 0, 0]
            t_report_ms = time.ticks_ms()