"""Wavetables and the instrument table (single source of truth).

Importing this module builds 15 wavetables at import time. main.py's
Looper needs an unfragmented heap for its ~188 KB buffers, so this must be
imported AFTER the Looper is constructed, never before.

Pure Python/math otherwise -- no hardware.
"""

import math
from machine import Pin, I2S
from array import array
from micropython import const

TABLE_LEN = const(256)
TABLE_AMP = 32000

# Band limit. A wavetable is sampled once at build time, so any partial
# above Nyquist folds down into the audible band permanently -- no
# downstream lowpass can remove it. Nyquist at 12000 Hz is 6000 Hz, and
# how many harmonics fit depends on the note, so a shared table has to be
# sized for a design pitch:
#
#   octave 4 top (C5, 523 Hz)  -> 11 harmonics fit
#   octave 5 top (C6, 1046 Hz) ->  5 harmonics fit
#   octave 6 top (C7, 2093 Hz) ->  2 harmonics fit
#
# 5 sizes the tables for the top of octave 5. Known limitation: octave 6
# still folds. Sizing for it would reduce every instrument to a near-sine
# to protect a register nobody plays in. The real fix is per-pitch
# mip-mapped tables, which is a rebuild-on-note-on cost we cannot afford.
N_MAX = 5


def make_table(fn):
    """Sample one cycle of fn(t), t in [0,1), into a signed 16-bit table."""
    return array("h", [int(fn(i / TABLE_LEN) * TABLE_AMP)
                       for i in range(TABLE_LEN)])


def harmonic(partials, norm=None):
    """Build a waveform function from (harmonic multiple, amplitude) pairs.

    Partials above N_MAX are dropped here, so every recipe below can be
    written at its musically correct spectrum and still come out band
    limited. norm defaults to the sum of the amplitudes that survived,
    which guarantees |output| <= 1 so the table cannot clip; normalising
    against the untrimmed sum would leave trimmed instruments quiet.
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


# --- Geometric waveforms, built additively so they band-limit ---
# Sampling an ideal square or saw puts a step discontinuity in the table,
# and a step contains harmonics up to the 128th -- nearly all above Nyquist
# here. Building them from their Fourier series instead lets harmonic()
# apply N_MAX to them like any other instrument.

SAW_P = tuple((n, 1.0 / n) for n in range(1, N_MAX + 1))

# Odd harmonics only -- the missing evens are why a square is hollow.
SQUARE_P = tuple((n, 1.0 / n) for n in range(1, N_MAX + 1, 2))

# Amplitude goes as |sin(n*pi*d)|/n for duty d; at d=1/4 every 4th vanishes.
PULSE25_P = tuple((n, abs(math.sin(n * math.pi * 0.25)) / n)
                  for n in range(1, N_MAX + 1))


def triangle_fn(t):
    # Kept geometric: harmonics roll off as 1/n^2, so whatever folds is
    # already 40 dB down by the 9th.
    return 4.0 * abs(t - 0.5) - 1.0


# --- Harmonic recipes: (harmonic multiple, amplitude) ---
# The physics behind each spectrum is the defensible part of this file.

# Organ depth comes from many pipes sounding at once, not one pipe's
# timbre -- the fix for a thin organ is more ranks, not a louder fundamental.
ORGAN_P = ((1, 1.00), (2, 0.75), (3, 0.50), (4, 0.40), (5, 0.25),
           (6, 0.20), (8, 0.15), (9, 0.10), (10, 0.08), (12, 0.05))

# Bells are dominated by non-integer partials; that inharmonicity matters
# more to the identity than any amount of normal harmonics.
BELL_P = ((1, 1.00), (2.71, 0.55), (4.07, 0.32), (5.83, 0.22), (7.91, 0.12))

# Young's plucked-string theorem: harmonic n goes as sin(n*pi*p)/n for
# pluck position p. p = 1/8 is a harpsichord plectrum near the bridge.
PLUCK_P = ((1, 1.00), (2, 0.92), (3, 0.81), (4, 0.65), (5, 0.48), (6, 0.31))

# Same theorem at p = 1/5 (nylon, plucked over the soundhole). n=5 is
# absent because plucking at 1/5 puts a node there, so it cannot be excited.
GUITAR_P = ((1, 1.00), (2, 0.81), (3, 0.54), (4, 0.25), (6, 0.17))

# Piano strings are stiff, so partials sit slightly sharp of integer
# multiples. That detuning is what keeps it from sounding like an organ.
PIANO_P = ((1, 1.00), (2.01, 0.62), (3.02, 0.38), (4.04, 0.24),
           (5.08, 0.16), (6.13, 0.10), (7.20, 0.06))

BASS_P = ((1, 1.00), (2, 0.48), (3, 0.22), (4, 0.10))

# Genuinely harmonic-poor -- the weakness is the flute. Do not enrich it.
FLUTE_P = ((1, 1.00), (2, 0.15), (3, 0.07), (4, 0.03), (5, 0.015), (6, 0.008))

# Closed cylindrical pipe, so odd harmonics dominate. Weighted toward the
# low register where the fundamental carries most of the energy.
CLARINET_P = ((1, 1.00), (2, 0.08), (3, 0.55), (4, 0.10),
              (5, 0.30), (7, 0.14), (9, 0.08))

# Nonlinear wave steepening down the bore pushes energy into high
# harmonics -- why a trumpet is brighter than a conical-bore flugelhorn.
TRUMPET_P = ((1, 1.00), (2, 0.85), (3, 0.72), (4, 0.58), (5, 0.46),
             (6, 0.36), (7, 0.27), (8, 0.20), (9, 0.14), (10, 0.09))

STRINGS_P = ((1, 1.00), (2, 0.72), (3, 0.50), (4, 0.35),
             (5, 0.24), (6, 0.16), (7, 0.10), (8, 0.06))


# --- Instrument table ---
# One ordered tuple defines the name, waveform, envelope, and the keypad
# cycling order. A tuple, not a dict: MicroPython does not preserve dict
# insertion order, which would make the sample keys unrehearsable on stage.
#
# Envelope = (attack_ms, decay_ms, sustain_level, release_ms)

INSTRUMENTS = (
    ("Sine",     harmonic(((1, 1.0),)),  (10,  80, 0.85, 150)),
    ("Square",   harmonic(SQUARE_P),     (5,   60, 0.80, 100)),
    ("Sawtooth", harmonic(SAW_P),        (15, 120, 0.75, 200)),
    ("Triangle", triangle_fn,            (10, 100, 0.85, 180)),
    ("Pulse",    harmonic(PULSE25_P),    (5,   60, 0.75, 120)),

    ("Organ",    harmonic(ORGAN_P),      (12,  40, 0.97, 400)),
    ("Bell",     harmonic(BELL_P),       (1,  400, 0.22, 1900)),
    ("Pluck",    harmonic(PLUCK_P),      (2,  200, 0.09, 90)),
    # norm is deliberately above the amplitude sum: piano's inharmonic
    # partials beat against each other and read louder than the sum
    # predicts. The original 3.15 against an untrimmed sum of 2.56 is a
    # factor of 1.23; N_MAX leaves a sum of 2.40, so 2.95 keeps the ratio.
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

# "Drums", not "Drum Kit": the OLED's "Sample: <name>" row is already at
# its 128px limit for the longest existing names.
DRUM_KIT_NAME = "Drums"

SAMPLE_LIST = tuple(name for name, _, _ in INSTRUMENTS) + (DRUM_KIT_NAME,)


def shift_sample(current_sample, step):
    """Step through SAMPLE_LIST, wrapping. Includes Drums -- what differs
    about drums happens at the note-on dispatch in main.py, not here."""
    idx = SAMPLE_LIST.index(current_sample)
    return SAMPLE_LIST[(idx + step) % len(SAMPLE_LIST)]