"""Wavetables and the instrument table (single source of truth).

ALLOCATION ORDER WARNING: importing this module builds 15 wavetables
(TABLE_LEN int16 samples each) via WAVETABLES = {...} at import time.
main.py's Looper needs the heap to still be one clean, unfragmented
block when it allocates its ~250 KB loop buffers (see AUDIO CONSTANTS +
LOOPER in main.py) -- so this module must be imported AFTER
`looper = loop.Looper(...)` runs, never before. Moving the import
earlier in main.py can turn "plenty of free heap" into "no single run
big enough" for the Looper.

Pure Python/math otherwise -- no hardware.
"""

import math
from array import array
from micropython import const

TABLE_LEN = const(256)
TABLE_AMP = 32000

# ============================================================
# BAND LIMIT
# ============================================================
# A wavetable is sampled once at build time and then played back at
# whatever rate the note needs, so any partial above Nyquist is folded
# down into the audible band PERMANENTLY, at table-build time. No
# lowpass downstream can remove it -- by the time it is in the table the
# alias is already below Nyquist and looks like signal.
#
# Nyquist at SAMPLE_RATE = 12000 is 6000 Hz. The highest partial that
# survives depends on the note being played, so a single shared table
# has to be sized for a DESIGN PITCH:
#
#     octave 4 top (C5, 523 Hz)   -> 11 harmonics fit
#     octave 5 top (C6, 1046 Hz)  ->  5 harmonics fit
#     octave 6 top (C7, 2093 Hz)  ->  2 harmonics fit
#
# N_MAX = 5 sizes the tables for the top of octave 5, which is the top of
# the range anything is actually played in. KNOWN AND ACCEPTED: the
# octave 6 register still folds. Sizing for it instead would mean two
# harmonics total, which does not sound like a sawtooth, a trumpet, or
# anything else -- it reduces every instrument in the kit to a near-sine
# in order to protect a register nobody plays in. The real fix is
# per-pitch mip-mapped tables, which is a rebuild-on-note-on cost this
# project has no budget for.
N_MAX = 5


def make_table(fn):
    """Sample one cycle of fn(t), t in [0,1), into a signed 16-bit table."""
    return array("h", [int(fn(i / TABLE_LEN) * TABLE_AMP)
                       for i in range(TABLE_LEN)])


def harmonic(partials, norm=None):
    """Build a waveform function from (harmonic multiple, amplitude) pairs.

    Partials above N_MAX are DROPPED HERE, once, so every recipe below
    can be written out in full at its musically correct spectrum and
    still come out band-limited. Keeping the drop in one place means the
    band limit is a single constant to re-tune if SAMPLE_RATE moves
    again, rather than a hand-edit of a dozen tuples.

    norm defaults to the sum of the amplitudes THAT SURVIVED the band
    limit -- the worst case peak of the sum, which guarantees
    |output| <= 1 so the table can never clip. Normalising against the
    full untrimmed sum instead would leave every trimmed instrument
    quieter than it should be, by exactly the amount that was trimmed.
    Real peaks are lower (the partials don't all crest together), so this
    is conservative, which is what we want with 8 voices mixing.
    """
    partials = tuple((m, a) for m, a in partials if m <= N_MAX)
    if norm is None:
        norm = sum(amp for _, amp in partials)
    two_pi = 2 * math.pi

    def fn(t):
        v = 0.0
        for mult, amp in partials:
            v += amp * math.sin(two_pi * mult * t)
        return v / norm

    return fn


# --- Geometric waveforms, built ADDITIVELY so they band-limit ---
#
# Sampling an ideal square or sawtooth shape directly puts a step
# discontinuity in the table, and a step contains every harmonic up to
# the 128th. At 12000 Hz nearly all of them are above Nyquist and fold
# back down as inharmonic noise -- which is what made Sawtooth (the
# default sample on preset 2) the harshest voice in the kit.
#
# Building the same waveforms from their Fourier series instead means
# harmonic() applies N_MAX to them exactly like any modelled instrument.
# The result is a rounded-off square and saw ("Gibbs ears"), which is
# what a band-limited version of these waveforms legitimately looks like
# -- and it is what analogue synths with real filters sound like anyway.

# Sawtooth: all harmonics, amplitude 1/n.
SAW_P = tuple((n, 1.0 / n) for n in range(1, N_MAX + 1))

# Square: ODD harmonics only, amplitude 1/n. The missing even harmonics
# are why a square is hollow where a saw is bright.
SQUARE_P = tuple((n, 1.0 / n) for n in range(1, N_MAX + 1, 2))

# 25% pulse: amplitude of harmonic n goes as |sin(n*pi*d)|/n for duty d.
# At d = 1/4 every 4th harmonic vanishes -- the same nodal argument as
# the plucked-string recipes below.
PULSE25_P = tuple((n, abs(math.sin(n * math.pi * 0.25)) / n)
                  for n in range(1, N_MAX + 1))

# Triangle keeps its direct geometric form. Its harmonics roll off as
# 1/n^2 rather than 1/n, so by the 9th they are already 40 dB down and
# whatever folds is far below the noise floor. Sampling the shape
# directly is cheaper and sounds identical.
def triangle_fn(t):
    return 4.0 * abs(t - 0.5) - 1.0


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
    ("Square",   harmonic(SQUARE_P),     (5,   60, 0.80, 100)),
    ("Sawtooth", harmonic(SAW_P),        (15, 120, 0.75, 200)),
    ("Triangle", triangle_fn,            (10, 100, 0.85, 180)),
    ("Pulse",    harmonic(PULSE25_P),    (5,   60, 0.75, 120)),

    # --- modelled instruments ---
    ("Organ",    harmonic(ORGAN_P),      (12,  40, 0.97, 400)),
    ("Bell",     harmonic(BELL_P),       (1,  400, 0.22, 1900)),
    ("Pluck",    harmonic(PLUCK_P),      (2,  200, 0.09, 90)),
    # norm is deliberately ABOVE the amplitude sum -- piano was hand
    # trimmed down because its inharmonic partials beat against each
    # other and read as louder than the sum predicts. The original pair
    # was 3.15 against an untrimmed sum of 2.56, a factor of 1.23; N_MAX
    # leaves a surviving sum of 2.40, so the same factor gives 2.95.
    # Keeping the literal 3.15 would have quietly dropped piano ~7% in
    # level relative to every other instrument.
    ("Piano",    harmonic(PIANO_P, 2.95), (2, 450, 0.12, 450)),
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
    the note-on dispatch site in the main loop (see DRUM KIT in
    main.py), not here."""
    idx = SAMPLE_LIST.index(current_sample)
    return SAMPLE_LIST[(idx + step) % len(SAMPLE_LIST)]