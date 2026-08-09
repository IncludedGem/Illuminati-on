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
    the note-on dispatch site in the main loop (see DRUM KIT in
    main.py), not here."""
    idx = SAMPLE_LIST.index(current_sample)
    return SAMPLE_LIST[(idx + step) % len(SAMPLE_LIST)]
