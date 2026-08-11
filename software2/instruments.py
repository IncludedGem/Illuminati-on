"""Wavetables and the instrument table (single source of truth).

Importing this module builds the wavetable bank at import time. main.py's
Looper needs an unfragmented heap for its ~188 KB buffers, so this must be
imported AFTER the Looper is constructed, never before.

Pure Python/math otherwise -- no hardware.


WHY THERE IS MORE THAN ONE TABLE PER INSTRUMENT
-----------------------------------------------
A wavetable is sampled once at build time, so any partial above Nyquist
folds down into the audible band permanently -- no downstream lowpass can
remove it. At 12 kHz, Nyquist is 6 kHz, and how many harmonics fit depends
entirely on the note being played:

    C3  (131 Hz)  -> 45 harmonics fit
    C4  (262 Hz)  -> 22 harmonics fit
    C5  (523 Hz)  -> 11 harmonics fit
    C6  (1047 Hz) ->  5 harmonics fit
    C7  (2093 Hz) ->  2 harmonics fit

The previous version used a single shared table sized for the worst case
it expected to see (5 harmonics). That is safe, and it is also why every
instrument sounded like every other instrument. With the series truncated
to 5 terms and renormalised, the surviving spectra are:

              h1     h2     h3     h4     h5
    Organ   0.345  0.259  0.172  0.138  0.086
    Strings 0.356  0.256  0.178  0.125  0.085     <- same instrument
    Trumpet 0.277  0.235  0.199  0.161  0.127
    Pluck   0.259  0.238  0.210  0.168  0.124     <- same instrument

Organ and Strings differ by under 2% per partial. Trumpet and Pluck by
under 3%. Fifteen instruments collapsed into roughly three timbres,
because everything that distinguishes a trumpet from a violin lives in
harmonics 6 through 12 and all of them were being deleted.

The fix is mip-mapping: build several band-limited versions of each
instrument and pick one at note-on based on the note's actual frequency.
A note at C4 gets 22 harmonics and sounds like the instrument it claims to
be; a note at C6 still gets 5, because that is genuinely all that fits.
Costs memory (see MIP_BANDS) and zero CPU -- the render loop still does one
table lookup per sample.
"""

import math
from array import array
from micropython import const

TABLE_LEN = const(256)

# One voice at full envelope must land BELOW main.py's limiter knee, or
# every single note gets its peaks shaved and picks up distortion that no
# amount of volume knob will remove. 28000 against a knee of 28500 means a
# solo note is mathematically untouched by the limiter, and only chords
# ever lean on it. The 1.2 dB given up against 32767 is inaudible; the
# distortion it buys back is not.
TABLE_AMP = 28000

# Highest partial we are willing to place, in Hz. Sits under Nyquist
# (6000 at 12 kHz) with margin, because the interpolator in render_voice
# is not perfect either.
TOP_PARTIAL_HZ = 5600

# Mip bands: (highest note frequency this band serves, harmonics allowed).
# Each entry costs 15 tables * 512 bytes = 7.7 KB of heap.
#
# n_max is set so band_top_hz * n_max stays under TOP_PARTIAL_HZ.
#
# IF YOU GET A MemoryError AT BOOT: delete the first row. You lose harmonic
# richness below C4 only, and you get 7.7 KB back. Delete the second row
# after that. Do not delete rows from the bottom -- the top row is the one
# doing the most work, since most playing happens in octaves 3 and 4.
MIP_BANDS = (
    (260.0, 21),
    (520.0, 10),
    (1050.0, 5),
    (2100.0, 2),
)

# Above the last band there is room for one partial, i.e. a sine. Every
# instrument shares one table up there rather than storing 15 identical
# sines -- at C7 on a 12 kHz system there is no timbre left to preserve.
_TOP_BAND_MIN_HZ = MIP_BANDS[-1][0]


def make_table(fn, normalise=True):
    """Sample one cycle of fn(t), t in [0,1), into a signed 16-bit table.

    Normalises to true measured peak rather than trusting an analytic
    guess. This matters more than it sounds: the old code divided each
    recipe by the SUM of its harmonic amplitudes, but a sum of sines never
    peaks at the sum of its amplitudes -- the partials do not line up. The
    result was every rich instrument sitting 2-5 dB below the sine, before
    any envelope or volume stage got involved.
    """
    raw = [fn(i / TABLE_LEN) for i in range(TABLE_LEN)]
    if normalise:
        peak = max(abs(v) for v in raw)
        if peak > 0:
            scale = TABLE_AMP / peak
        else:
            scale = 0.0
    else:
        scale = TABLE_AMP

    # Clamp: rounding on the last bit can push a normalised peak to 32001.
    out = array("h", bytearray(2 * TABLE_LEN))
    for i in range(TABLE_LEN):
        v = int(raw[i] * scale)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out[i] = v
    return out


def harmonic(partials, n_max, gain=1.0):
    """Build a waveform function from (harmonic multiple, amplitude) pairs.

    Partials above n_max are dropped, so every recipe below can be written
    at its musically correct spectrum and get band-limited per mip band.
    No normalising divisor here -- make_table measures the real peak.

    `gain` is a per-instrument voicing trim applied before normalisation;
    it only has an effect when the caller passes normalise=False, and is
    kept so the recipes stay self-documenting.
    """
    kept = tuple((m, a) for m, a in partials if m <= n_max)
    if not kept:
        kept = ((1, 1.0),)
    two_pi = 2 * math.pi

    def fn(t):
        v = 0.0
        for mult, amp in kept:
            v += amp * math.sin(two_pi * mult * t)
        return v * gain

    return fn


# --- Geometric waveforms, built additively so they band-limit ---
# Sampling an ideal square or saw puts a step discontinuity in the table,
# and a step contains harmonics up to the 128th -- nearly all above Nyquist
# here. Building them from their Fourier series lets the mip logic apply.

_HARMONIC_CEILING = 48      # no recipe needs more than this at any band

SAW_P = tuple((n, 1.0 / n) for n in range(1, _HARMONIC_CEILING + 1))

# Odd harmonics only -- the missing evens are why a square is hollow.
SQUARE_P = tuple((n, 1.0 / n) for n in range(1, _HARMONIC_CEILING + 1, 2))

# Amplitude goes as |sin(n*pi*d)|/n for duty d; at d=1/4 every 4th vanishes.
PULSE25_P = tuple((n, abs(math.sin(n * math.pi * 0.25)) / n)
                  for n in range(1, _HARMONIC_CEILING + 1))

# Triangle: odd harmonics, alternating sign, rolling off as 1/n^2. Built
# additively rather than geometrically now that it can be band-limited
# properly -- the old abs() version had a corner in it, and a corner is
# harmonics all the way up.
TRIANGLE_P = tuple((n, (1.0 if (n // 2) % 2 == 0 else -1.0) / (n * n))
                   for n in range(1, _HARMONIC_CEILING + 1, 2))


# --- Harmonic recipes: (harmonic multiple, amplitude) ---
# The physics behind each spectrum is the defensible part of this file.

# Organ depth comes from many pipes sounding at once, not one pipe's
# timbre -- the fix for a thin organ is more ranks, not a louder fundamental.
ORGAN_P = ((1, 1.00), (2, 0.75), (3, 0.50), (4, 0.40), (5, 0.25),
           (6, 0.20), (8, 0.15), (9, 0.10), (10, 0.08), (12, 0.05),
           (16, 0.04), (20, 0.02))

# Bells are dominated by non-integer partials; that inharmonicity matters
# more to the identity than any amount of normal harmonics.
BELL_P = ((1, 1.00), (2.71, 0.55), (4.07, 0.32), (5.83, 0.22), (7.91, 0.12),
          (10.4, 0.08), (13.2, 0.05))

# Young's plucked-string theorem: harmonic n goes as sin(n*pi*p)/n for
# pluck position p. p = 1/8 is a harpsichord plectrum near the bridge.
PLUCK_P = tuple((n, abs(math.sin(n * math.pi / 8.0)) / n)
                for n in range(1, 25))

# Same theorem at p = 1/5 (nylon, plucked over the soundhole). Every 5th
# harmonic is absent because plucking at 1/5 puts a node there, so it
# cannot be excited -- that is physics, not a simplification.
GUITAR_P = tuple((n, abs(math.sin(n * math.pi / 5.0)) / n)
                 for n in range(1, 25))

# Piano strings are stiff, so partials sit slightly sharp of integer
# multiples. That detuning is what keeps it from sounding like an organ.
PIANO_P = ((1, 1.00), (2.01, 0.62), (3.02, 0.38), (4.04, 0.24),
           (5.08, 0.16), (6.13, 0.10), (7.20, 0.06), (8.31, 0.04),
           (9.45, 0.03), (10.6, 0.02))

BASS_P = ((1, 1.00), (2, 0.48), (3, 0.22), (4, 0.10), (5, 0.05), (6, 0.03))

# Genuinely harmonic-poor -- the weakness is the flute. Do not enrich it.
FLUTE_P = ((1, 1.00), (2, 0.15), (3, 0.07), (4, 0.03), (5, 0.015), (6, 0.008))

# Closed cylindrical pipe, so odd harmonics dominate. Weighted toward the
# low register where the fundamental carries most of the energy.
CLARINET_P = ((1, 1.00), (2, 0.08), (3, 0.55), (4, 0.10), (5, 0.30),
              (6, 0.05), (7, 0.14), (8, 0.03), (9, 0.08), (11, 0.05),
              (13, 0.03))

# Nonlinear wave steepening down the bore pushes energy into high
# harmonics -- why a trumpet is brighter than a conical-bore flugelhorn.
TRUMPET_P = ((1, 1.00), (2, 0.85), (3, 0.72), (4, 0.58), (5, 0.46),
             (6, 0.36), (7, 0.27), (8, 0.20), (9, 0.14), (10, 0.09),
             (11, 0.06), (12, 0.04), (13, 0.03), (14, 0.02))

STRINGS_P = ((1, 1.00), (2, 0.72), (3, 0.50), (4, 0.35), (5, 0.24),
             (6, 0.16), (7, 0.10), (8, 0.06), (9, 0.04), (10, 0.03),
             (11, 0.02), (12, 0.015))

SINE_P = ((1, 1.0),)


# --- Instrument table ---
# One ordered tuple defines the name, spectrum, envelope, and the keypad
# cycling order. A tuple, not a dict: MicroPython does not preserve dict
# insertion order, which would make the sample keys unrehearsable on stage.
#
# Envelope = (attack_ms, decay_ms, sustain_level, release_ms, sustain_decay_ms)
#
# sustain_decay_ms is new. The old envelopes gave the plucked instruments a
# physically sensible sustain FLOOR -- piano 0.12, pluck 0.09 -- and then
# held them there forever, which is 20 dB below the attack and reads as
# "the synth is broken, it goes quiet". Real strings do not hold at 12%,
# they keep decaying. So the sustain level is now set where the note
# actually sits a moment after the strike, and sustain_decay_ms carries it
# the rest of the way down. 0 means hold forever (organ, flute, strings).

INSTRUMENTS = (
    ("Sine",     SINE_P,      (10,  80, 0.85, 150, 0)),
    ("Square",   SQUARE_P,    (5,   60, 0.80, 100, 0)),
    ("Sawtooth", SAW_P,       (15, 120, 0.78, 200, 0)),
    ("Triangle", TRIANGLE_P,  (10, 100, 0.85, 180, 0)),
    ("Pulse",    PULSE25_P,   (5,   60, 0.78, 120, 0)),

    ("Organ",    ORGAN_P,     (12,  40, 0.97, 400, 0)),
    ("Bell",     BELL_P,      (1,  250, 0.55, 1400, 3000)),
    ("Pluck",    PLUCK_P,     (2,   90, 0.45, 250, 900)),
    ("Piano",    PIANO_P,     (2,  130, 0.55, 400, 2600)),
    ("Guitar",   GUITAR_P,    (3,   90, 0.50, 700, 1800)),
    ("Bass",     BASS_P,      (3,  110, 0.55, 180, 1400)),
    ("Flute",    FLUTE_P,     (60, 100, 0.92, 240, 0)),
    ("Clarinet", CLARINET_P,  (20,  45, 0.94, 150, 0)),
    ("Trumpet",  TRUMPET_P,   (25,  50, 0.88, 160, 0)),
    ("Strings",  STRINGS_P,   (110, 100, 0.92, 550, 0)),
)


# --- Build the bank ---
# WAVETABLES[name] is a tuple of tables, one per MIP_BANDS entry, plus the
# shared sine on the end. pick_table() indexes it by note frequency.

_SINE_TABLE = make_table(harmonic(SINE_P, 1))

WAVETABLES = {}
for _name, _partials, _env in INSTRUMENTS:
    _bank = []
    for _top_hz, _n_max in MIP_BANDS:
        _bank.append(make_table(harmonic(_partials, _n_max)))
    _bank.append(_SINE_TABLE)
    WAVETABLES[_name] = tuple(_bank)

del _name, _partials, _env, _bank, _top_hz, _n_max

ENVELOPES = {name: env for name, _, env in INSTRUMENTS}

# Frequency thresholds as a flat tuple, for a cheap linear scan at note-on.
_BAND_EDGES = tuple(top for top, _ in MIP_BANDS)
_N_BANDS = len(_BAND_EDGES)


def pick_table(name, freq):
    """The band-limited table for this instrument at this pitch.

    Runs once per note-on, not per sample, so a linear scan over four
    entries is free. Falls back to Sine for an unknown instrument name,
    matching the old WAVETABLES.get() behaviour.
    """
    bank = WAVETABLES.get(name)
    if bank is None:
        bank = WAVETABLES["Sine"]
    i = 0
    while i < _N_BANDS:
        if freq <= _BAND_EDGES[i]:
            return bank[i]
        i += 1
    return bank[_N_BANDS]        # the shared sine, above the last band


def band_index(freq):
    """Which mip band a frequency lands in. Diagnostics only."""
    i = 0
    while i < _N_BANDS:
        if freq <= _BAND_EDGES[i]:
            return i
        i += 1
    return _N_BANDS


# "Drums", not "Drum Kit": the OLED's "Sample: <name>" row is already at
# its 128px limit for the longest existing names.
DRUM_KIT_NAME = "Drums"

SAMPLE_LIST = tuple(name for name, _, _ in INSTRUMENTS) + (DRUM_KIT_NAME,)


def shift_sample(current_sample, step):
    """Step through SAMPLE_LIST, wrapping. Includes Drums -- what differs
    about drums happens at the note-on dispatch in main.py, not here."""
    idx = SAMPLE_LIST.index(current_sample)
    return SAMPLE_LIST[(idx + step) % len(SAMPLE_LIST)]


def bank_bytes():
    """Heap cost of the wavetable bank, for the startup banner."""
    n_tables = len(INSTRUMENTS) * len(MIP_BANDS) + 1
    return n_tables * TABLE_LEN * 2
