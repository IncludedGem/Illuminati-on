"""
instruments.py -- HAcK 2026, Team 13
=====================================

TONE DATA ONLY. This module contains every wavetable and ADSR envelope
the synth can play. It contains no engine code at all -- no phase
accumulation, no fixed-point math, no rendering. That lives in main.py.
See the "WHERE THE SOUND LIVES" section of main.py's module docstring
for the full explanation of the split.

Every wavetable and envelope below is carried over VERBATIM from the
standalone synth-v4.py prototype -- same harmonic recipes, same ADSR
numbers. Envelope times are in milliseconds and are sample-rate
independent, so they port unchanged even though main.py runs at
12000 Hz instead of synth-v4's 11025 Hz. Wavetable content is likewise
rate-independent: each table is one cycle of the waveform, generated
from harmonic ratios, and playback pitch is set later by how fast
main.py steps through it (phase_inc), not by anything stored here.

ALLOCATION ORDER WARNING
-------------------------
Building WAVETABLES below allocates 12 int16 arrays of TABLE_LEN
samples each, plus the ENVELOPES tuples. That's small on its own, but
main.py's Looper needs its big loop buffers allocated on a still-clean
heap (see "AUDIO CONSTANTS + LOOPER" at the top of main.py). This
module MUST be imported only after `looper = loop.Looper(...)` has run
-- main.py already does this ("MUST be imported here, after the
Looper"). Importing this module first would let these allocations
fragment the heap before the Looper gets its turn.

NOT BAND-LIMITED, ON PURPOSE
------------------------------
An earlier version of this file band-limited every table down to two
harmonics to stay safely under the 12000 Hz project's 6000 Hz Nyquist
at all pitches. That's what made the ported synth sound thin next to
the standalone -- see main.py's "WHERE THE SOUND LIVES" section. These
tables are the standalone's full recipes, unfiltered: rich instruments
like Organ and Trumpet carry harmonics that can alias above Nyquist on
the very highest notes, exactly like the standalone did. That trade
was made deliberately -- correct, present tone everywhere it's heard
in practice, rather than a permanently duller set to guard against a
top-octave edge case. If aliasing on the highest notes ever becomes
audible in practice, that's the knob to revisit -- not a reason to
quietly re-narrow every instrument again.
"""

import math
from array import array

# ---------------- Wavetable generation ----------------

TABLE_LEN = 256          # main.py's engine hardcodes/asserts this -- see
                          # _PHASE_WRAP in main.py if this ever changes.
TABLE_AMP = 32000         # int16 peak. main.py's render_voice assumes a
                          # full-envelope voice reproduces this amplitude
                          # exactly (see "Peak is 32000 * 32768 >> 15").


def make_table(fn):
    return array(
        "h",
        [int(fn(i / TABLE_LEN) * TABLE_AMP) for i in range(TABLE_LEN)]
    )


# ---------------- Waveform recipes (verbatim from synth-v4.py) ----------

def sine_fn(t):
    # Not part of synth-v4 -- added here because main.py's Voice falls
    # back to WAVETABLES["Sine"] / ENVELOPES["Sine"] whenever a preset
    # names an instrument that no longer exists, and the default preset
    # (preset 0) is "Sine" from boot. A bare, unweighted sine keeps that
    # fallback silent-safe rather than accidentally loud or buzzy.
    return math.sin(2 * math.pi * t)


def saw_fn(t):
    return 2.0 * t - 1.0


def organ_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.75 * math.sin(2 * math.pi * 2.00 * t)
        + 0.50 * math.sin(2 * math.pi * 3.00 * t)
        + 0.40 * math.sin(2 * math.pi * 4.00 * t)
        + 0.25 * math.sin(2 * math.pi * 5.00 * t)
        + 0.20 * math.sin(2 * math.pi * 6.00 * t)
        + 0.15 * math.sin(2 * math.pi * 8.00 * t)
        + 0.10 * math.sin(2 * math.pi * 9.00 * t)
        + 0.08 * math.sin(2 * math.pi * 10.00 * t)
        + 0.05 * math.sin(2 * math.pi * 12.00 * t)
    )
    return v / 3.48


def bell_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.55 * math.sin(2 * math.pi * 2.71 * t)
        + 0.32 * math.sin(2 * math.pi * 4.07 * t)
        + 0.22 * math.sin(2 * math.pi * 5.83 * t)
        + 0.12 * math.sin(2 * math.pi * 7.91 * t)
    )
    return v / 2.21


def pluck_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.92 * math.sin(2 * math.pi * 2.00 * t)
        + 0.81 * math.sin(2 * math.pi * 3.00 * t)
        + 0.65 * math.sin(2 * math.pi * 4.00 * t)
        + 0.48 * math.sin(2 * math.pi * 5.00 * t)
        + 0.31 * math.sin(2 * math.pi * 6.00 * t)
    )
    return v / 4.17


def piano_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.62 * math.sin(2 * math.pi * 2.01 * t)
        + 0.38 * math.sin(2 * math.pi * 3.02 * t)
        + 0.24 * math.sin(2 * math.pi * 4.04 * t)
        + 0.16 * math.sin(2 * math.pi * 5.08 * t)
        + 0.10 * math.sin(2 * math.pi * 6.13 * t)
        + 0.06 * math.sin(2 * math.pi * 7.20 * t)
    )
    return v / 3.15


def guitar_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.81 * math.sin(2 * math.pi * 2.00 * t)
        + 0.54 * math.sin(2 * math.pi * 3.00 * t)
        + 0.25 * math.sin(2 * math.pi * 4.00 * t)
        + 0.17 * math.sin(2 * math.pi * 6.00 * t)
    )
    return v / 2.77


def bass_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.48 * math.sin(2 * math.pi * 2.00 * t)
        + 0.22 * math.sin(2 * math.pi * 3.00 * t)
        + 0.10 * math.sin(2 * math.pi * 4.00 * t)
    )
    return v / 1.80


def flute_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.15 * math.sin(2 * math.pi * 2.00 * t)
        + 0.07 * math.sin(2 * math.pi * 3.00 * t)
        + 0.03 * math.sin(2 * math.pi * 4.00 * t)
        + 0.015 * math.sin(2 * math.pi * 5.00 * t)
        + 0.008 * math.sin(2 * math.pi * 6.00 * t)
    )
    return v / 1.273


def clarinet_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.08 * math.sin(2 * math.pi * 2.00 * t)
        + 0.55 * math.sin(2 * math.pi * 3.00 * t)
        + 0.10 * math.sin(2 * math.pi * 4.00 * t)
        + 0.30 * math.sin(2 * math.pi * 5.00 * t)
        + 0.14 * math.sin(2 * math.pi * 7.00 * t)
        + 0.08 * math.sin(2 * math.pi * 9.00 * t)
    )
    return v / 2.25


def trumpet_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.85 * math.sin(2 * math.pi * 2.00 * t)
        + 0.72 * math.sin(2 * math.pi * 3.00 * t)
        + 0.58 * math.sin(2 * math.pi * 4.00 * t)
        + 0.46 * math.sin(2 * math.pi * 5.00 * t)
        + 0.36 * math.sin(2 * math.pi * 6.00 * t)
        + 0.27 * math.sin(2 * math.pi * 7.00 * t)
        + 0.20 * math.sin(2 * math.pi * 8.00 * t)
        + 0.14 * math.sin(2 * math.pi * 9.00 * t)
        + 0.09 * math.sin(2 * math.pi * 10.00 * t)
    )
    return v / 4.67


def strings_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.72 * math.sin(2 * math.pi * 2.00 * t)
        + 0.50 * math.sin(2 * math.pi * 3.00 * t)
        + 0.35 * math.sin(2 * math.pi * 4.00 * t)
        + 0.24 * math.sin(2 * math.pi * 5.00 * t)
        + 0.16 * math.sin(2 * math.pi * 6.00 * t)
        + 0.10 * math.sin(2 * math.pi * 7.00 * t)
        + 0.06 * math.sin(2 * math.pi * 8.00 * t)
    )
    return v / 3.13


# ---------------- Public tables ----------------
# Built once here at import time (boot). Float math (math.sin) is fine
# for this -- it happens once per instrument at startup, never inside
# the audio loop, which is exactly the float/int boundary main.py's
# render_voice comments draw.

WAVETABLES = {
    "Sine":     make_table(sine_fn),
    "Sawtooth": make_table(saw_fn),
    "Organ":    make_table(organ_fn),
    "Bell":     make_table(bell_fn),
    "Pluck":    make_table(pluck_fn),
    "Piano":    make_table(piano_fn),
    "Guitar":   make_table(guitar_fn),
    "Bass":     make_table(bass_fn),
    "Flute":    make_table(flute_fn),
    "Clarinet": make_table(clarinet_fn),
    "Trumpet":  make_table(trumpet_fn),
    "Strings":  make_table(strings_fn),
}

# (attack_ms, decay_ms, sustain_level, release_ms) -- verbatim from
# synth-v4.py, plus a "Sine" entry (see sine_fn above for why).
ENVELOPES = {
    "Sine":     (5,   30, 0.85, 150),
    "Sawtooth": (15, 120, 0.75, 200),
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


# ---------------- Sample cycling (keys '*' / '7') ----------------
# Drums is a one-shot sample kit, not a tuned wavetable (see "DRUM KIT"
# in main.py) -- it has no entry in WAVETABLES/ENVELOPES and is never
# looked up there; main.py checks preset["sample"] == DRUM_KIT_NAME and
# routes note-on to DrumVoice instead of Voice before either dict is
# touched. It still belongs in the cycle order below since '*'/'7' are
# how a performer reaches it from the keypad.

DRUM_KIT_NAME = "Drums"

SAMPLE_NAMES = (
    "Sine", "Sawtooth", "Organ", "Bell", "Pluck", "Piano", "Guitar",
    "Bass", "Flute", "Clarinet", "Trumpet", "Strings", DRUM_KIT_NAME,
)


def shift_sample(name, direction):
    """Cycle to the next/previous sample name. Falls back to index 0
    (Sine) if `name` isn't recognized, same fail-safe spirit as the
    WAVETABLES/ENVELOPES .get() fallbacks in main.py -- a stale or
    corrupted preset value can't get shift_sample stuck."""
    try:
        idx = SAMPLE_NAMES.index(name)
    except ValueError:
        idx = 0
    idx = (idx + direction) % len(SAMPLE_NAMES)
    return SAMPLE_NAMES[idx]