"""
8-NOTE SYNTHESIZER
-------------------
Each of the 8 buttons plays its own fixed note. No shift key, no
octave/instrument switching -- just press a button, hear a note.

Pins (same as your original scripts):
  Buttons -> GPIO15, 14, 13, 12, 11, 10, 9, 8 (active-low, internal pull-ups)
  I2S DAC -> sck=GPIO16, ws=GPIO17, sd=GPIO18

To change the instrument all 8 buttons use, edit INSTRUMENT below to any
key from WAVETABLES: "Sine", "Square", "Sawtooth", "Triangle", "Pulse",
"Organ", "Bell", "Pluck", "Piano", "Guitar", "Bass", "Flute", "Clarinet",
"Brass", "Strings".

To change which notes the buttons play, edit NOTE_FREQS -- it's just a
list of 8 frequencies in Hz, one per button, in the same order as
BUTTON_PINS.

PERFORMANCE NOTES (read this if you still hear crackling):
  - The mixing loop is structured per-voice (not per-sample) so envelope
    and phase state live in local variables instead of instance
    attributes -- attribute lookups (self.x) are one of the slowest
    things you can do per-sample in MicroPython, so this matters a lot.
  - @micropython.native on the hot functions skips some bytecode
    overhead. It's a strict improvement with no behavior change, so
    it's left on unconditionally.
  - If it's STILL choppy on your board, in order of effectiveness:
      1) Lower SAMPLE_RATE to 11025 (or 8000 if desperate).
      2) Raise BUF_SAMPLES further (e.g. 1024) -- fewer I2S.write() calls.
      3) Cap polyphony (e.g. only allow 4 simultaneous voices) if you're
         regularly slamming all 8 buttons at once.
"""

from machine import Pin, I2S
import math
from array import array

# ---------------- Buttons (exact pins from original script) ----------------

BUTTON_PINS = [15, 14, 13, 12, 11, 10, 9, 8]
buttons = [Pin(p, Pin.IN, Pin.PULL_UP) for p in BUTTON_PINS]
previous = [False] * len(buttons)

# One note per button, in the same order as BUTTON_PINS.
# Default: C major scale, C4 through C5 (8 notes, one full octave).
NOTE_FREQS = [
    261.63,  # C4
    293.66,  # D4
    329.63,  # E4
    349.23,  # F4
    392.00,  # G4
    440.00,  # A4
    493.88,  # B4
    523.25,  # C5
]

# ---------------- I2S output (exact pins from original script) ----------------

SAMPLE_RATE = 11025
BUF_SAMPLES = 1024  # bumped up from 256 -- fewer I2S.write() calls, less overhead

audio = I2S(
    0,
    sck=Pin(16),
    ws=Pin(17),
    sd=Pin(18),
    mode=I2S.TX,
    bits=16,
    format=I2S.MONO,
    rate=SAMPLE_RATE,
    ibuf=8192,
)

# ---------------- Wavetables ----------------


# ---------------- Wavetables ----------------

TABLE_LEN = 256
TABLE_AMP = 32000


def make_table(fn):
    return array(
        "h",
        [int(fn(i / TABLE_LEN) * TABLE_AMP) for i in range(TABLE_LEN)]
    )


def sine_fn(t):
    return math.sin(2 * math.pi * t)


# Basic synth waveforms
def square_fn(t):
    return 1.0 if t < 0.5 else -1.0


def saw_fn(t):
    return 2.0 * t - 1.0


def triangle_fn(t):
    return 4.0 * abs(t - 0.5) - 1.0


def pulse25_fn(t):
    return 1.0 if t < 0.25 else -1.0


def organ_fn(t):
    # Fuller principal + mixture chorus: more ranks stacked than a basic
    # 8'+4'+2' combo. Real organ "depth" comes from many simultaneously
    # sounding pipes at different pitches (a chorus), not from one pipe's
    # tone color -- so the fix here is adding more harmonic ranks, not
    # making any single one louder.
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)    # 8'   unison (fundamental)
        + 0.75 * math.sin(2 * math.pi * 2.00 * t)    # 4'   octave
        + 0.50 * math.sin(2 * math.pi * 3.00 * t)    # 2⅔'  twelfth (quint)
        + 0.40 * math.sin(2 * math.pi * 4.00 * t)    # 2'   fifteenth
        + 0.25 * math.sin(2 * math.pi * 5.00 * t)    # 1⅗'  tierce
        + 0.20 * math.sin(2 * math.pi * 6.00 * t)    # 1⅓'  larigot
        + 0.15 * math.sin(2 * math.pi * 8.00 * t)    # 1'   twenty-second
        + 0.10 * math.sin(2 * math.pi * 9.00 * t)    # mixture rank
        + 0.08 * math.sin(2 * math.pi * 10.00 * t)   # mixture rank
        + 0.05 * math.sin(2 * math.pi * 12.00 * t)   # mixture top rank
    )
    return v / 3.48


# -------------------------------------------------
# BELL
# -------------------------------------------------
# Bells contain strong NON-integer partials.
# This is much more important than simply adding
# normal harmonics.

def bell_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.55 * math.sin(2 * math.pi * 2.71 * t)
        + 0.32 * math.sin(2 * math.pi * 4.07 * t)
        + 0.22 * math.sin(2 * math.pi * 5.83 * t)
        + 0.12 * math.sin(2 * math.pi * 7.91 * t)
    )
    return v / 2.21


# -------------------------------------------------
# PLUCK
# -------------------------------------------------
# Harmonic amplitudes here follow the real physics of an ideal plucked
# string (Young's theorem): amplitude of harmonic n is proportional to
# sin(n*pi*p)/n, where p is the fractional pluck position along the
# string. A harpsichord plectrum plucks close to the bridge (p ~ 1/8),
# which is what makes it "tinny" and bright rather than round like a
# guitar (which is typically plucked further out, closer to p ~ 0.23).
# Computed for p = 1/8:
def pluck_fn(t):
    v = (
        1.00  * math.sin(2 * math.pi * 1.00 * t)   # n=1: sin(pi/8)/1
        + 0.92  * math.sin(2 * math.pi * 2.00 * t)   # n=2: sin(2pi/8)/2
        + 0.81  * math.sin(2 * math.pi * 3.00 * t)   # n=3: sin(3pi/8)/3
        + 0.65  * math.sin(2 * math.pi * 4.00 * t)   # n=4: sin(4pi/8)/4
        + 0.48  * math.sin(2 * math.pi * 5.00 * t)   # n=5: sin(5pi/8)/5
        + 0.31  * math.sin(2 * math.pi * 6.00 * t)   # n=6: sin(6pi/8)/6
    )
    return v / 4.17


# -------------------------------------------------
# PIANO
# -------------------------------------------------
# Piano strings have multiple partials and are not
# perfectly harmonic. The slightly shifted upper
# partials help avoid the "organ/synth" sound.

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


# -------------------------------------------------
# GUITAR (classical, nylon-string)
# -------------------------------------------------
# Harmonic amplitudes computed the same way as the Pluck instrument
# (Young's plucked-string theorem: amplitude of harmonic n is
# proportional to sin(n*pi*p)/n for pluck position p), but calibrated
# for classical fingerstyle technique rather than a pick: nylon
# strings are lower-tension and plucked with the fingers closer to
# mid-string (p ~ 1/5, roughly over the soundhole) rather than near
# the bridge like a pick attack. That combination is what physically
# produces the warmer, mellower, less bright tone nylon strings are
# known for, versus a steel-string's brighter pick attack.
# Computed for p = 1/5:
def guitar_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)   # n=1: sin(pi/5)/1
        + 0.81 * math.sin(2 * math.pi * 2.00 * t)   # n=2: sin(2pi/5)/2
        + 0.54 * math.sin(2 * math.pi * 3.00 * t)   # n=3: sin(3pi/5)/3
        + 0.25 * math.sin(2 * math.pi * 4.00 * t)   # n=4: sin(4pi/5)/4
        # n=5 omitted: plucking at exactly the 1/5 point places a node
        # right at the pluck location, so the 5th harmonic physically
        # cannot be excited -- this isn't a simplification, it's what
        # a real p=1/5 pluck does.
        + 0.17 * math.sin(2 * math.pi * 6.00 * t)   # n=6: sin(6pi/5)/6
    )
    return v / 2.77


# -------------------------------------------------
# BASS
# -------------------------------------------------
# Strong fundamental with controlled upper harmonics.
# Keeps the sound thick without becoming buzzy.

def bass_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.48 * math.sin(2 * math.pi * 2.00 * t)
        + 0.22 * math.sin(2 * math.pi * 3.00 * t)
        + 0.10 * math.sin(2 * math.pi * 4.00 * t)
    )
    return v / 1.80

# -------------------------------------------------
# FLUTE
# -------------------------------------------------
# Flute has the "purest" tone of any orchestral instrument -- it's
# genuinely poor in harmonics, just enough above the fundamental to
# avoid sounding like a bare sine wave. Keep this weak; don't be
# tempted to add more/louder harmonics to make it sound "richer" --
# that's not what makes a flute sound like a flute.
def flute_fn(t):
    v = (
        1.00  * math.sin(2 * math.pi * 1.00 * t)
        + 0.15  * math.sin(2 * math.pi * 2.00 * t)
        + 0.07  * math.sin(2 * math.pi * 3.00 * t)
        + 0.03  * math.sin(2 * math.pi * 4.00 * t)
        + 0.015 * math.sin(2 * math.pi * 5.00 * t)
        + 0.008 * math.sin(2 * math.pi * 6.00 * t)
    )
    return v / 1.273

# -------------------------------------------------
# CLARINET
# -------------------------------------------------
# Clarinet is a closed cylindrical pipe, which is why it's dominated
# by ODD harmonics (1, 3, 5...) rather than the full series. Weighted
# here toward the chalumeau (low-register) balance, where the
# fundamental carries the majority of the energy and upper harmonics
# are comparatively weak -- this is what gives the low register its
# characteristic warm, hollow, bass-leaning quality, versus the
# brighter, more even harmonic spread of the upper clarion register.
def clarinet_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)   # fundamental -- dominant, chalumeau-style
        + 0.08 * math.sin(2 * math.pi * 2.00 * t)   # faint even harmonic -- woody warmth
        + 0.55 * math.sin(2 * math.pi * 3.00 * t)   # strong odd -- core color, slightly reduced
        + 0.10 * math.sin(2 * math.pi * 4.00 * t)   # faint even harmonic -- roundness
        + 0.30 * math.sin(2 * math.pi * 5.00 * t)   # odd -- reduced from before
        + 0.14 * math.sin(2 * math.pi * 7.00 * t)   # odd -- de-emphasized
        + 0.08 * math.sin(2 * math.pi * 9.00 * t)   # odd -- de-emphasized upper shimmer
    )
    return v / 2.25

# -------------------------------------------------
# TRUMPET
# -------------------------------------------------
# Trumpets are among the "brightest" brass instruments (alongside
# trombones) because of their cylindrical bore right after the
# mouthpiece -- this permits strong nonlinear wave-steepening as the
# sound travels down the tube, which pushes energy into high
# harmonics far more than a conical-bore instrument like a flugelhorn
# or euphonium. That's why this spectrum extends further and stays
# louder into the upper harmonics than a generic/mellower brass patch
# would.
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


# -------------------------------------------------
# STRINGS
# -------------------------------------------------
# Strings have many harmonics, but less aggressive
# high-frequency content than brass.

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


WAVETABLES = {
    "Sine":     make_table(sine_fn),
    "Square":   make_table(square_fn),
    "Sawtooth": make_table(saw_fn),
    "Triangle": make_table(triangle_fn),
    "Pulse":    make_table(pulse25_fn),

    "Organ":    make_table(organ_fn),
    "Bell":     make_table(bell_fn),
    "Pluck":    make_table(pluck_fn),

    "Piano":    make_table(piano_fn),
    "Guitar":   make_table(guitar_fn),
    "Bass":     make_table(bass_fn),
    "Flute":    make_table(flute_fn),
    "Clarinet": make_table(clarinet_fn),
    "Trumpet":    make_table(trumpet_fn),
    "Strings":  make_table(strings_fn),
}


# ---------------- ADSR ----------------
# (attack_ms, decay_ms, sustain_level, release_ms)

ENVELOPES = {
    # Synth sounds
    "Sine":     (10,  80, 0.85, 150),
    "Square":   (5,   60, 0.80, 100),
    "Sawtooth": (15, 120, 0.75, 200), 
    "Triangle": (10, 100, 0.85, 180),
    "Pulse":    (5,   60, 0.75, 120),

    # Instruments
    "  Organ":    (12, 40, 0.97,  400),
    "Bell":     (1,  400, 0.22, 1900),
    "Pluck":    (2,  200, 0.09, 90), 

    "Piano":    (2,  450, 0.12, 450), 
    "Guitar":   (3,  100, 0.25, 800),#o
    "Bass":     (3,  190, 0.15, 90), 

    "Flute":    (90, 100, 0.92, 240),
    "Clarinet": (20,  45, 0.94, 150),
    "Trumpet":    (25, 50, 0.85, 160), #o
    "Strings":  (140, 100, 0.92, 550),
}

# Which instrument all 8 buttons use. Change this to any key above.
INSTRUMENT = "Organ" 

# ---------------- Voices ----------------

NUM_VOICES = len(BUTTON_PINS)  # one voice per button


class Voice:
    __slots__ = (
        "active", "table", "phase", "phase_inc", "stage", "level",
        "attack_step", "decay_step", "sustain_level", "release_step",
    )

    def __init__(self):
        self.active = False
        self.table = None
        self.phase = 0.0
        self.phase_inc = 0.0
        self.stage = "idle"  # idle, attack, decay, sustain, release
        self.level = 0.0
        self.attack_step = 0.0
        self.decay_step = 0.0
        self.sustain_level = 0.0
        self.release_step = 0.0

    def note_on(self, freq, instrument):
        self.table = WAVETABLES.get(instrument, WAVETABLES["Sine"])
        self.phase = 0.0
        self.phase_inc = freq * TABLE_LEN / SAMPLE_RATE

        a_ms, d_ms, s_lvl, r_ms = ENVELOPES.get(instrument, ENVELOPES["Sine"])
        a_samples = max(1, int(a_ms * SAMPLE_RATE / 1000))
        d_samples = max(1, int(d_ms * SAMPLE_RATE / 1000))
        r_samples = max(1, int(r_ms * SAMPLE_RATE / 1000))

        self.attack_step = 1.0 / a_samples
        self.decay_step = (1.0 - s_lvl) / d_samples
        self.sustain_level = s_lvl
        self.release_step = s_lvl / r_samples

        self.stage = "attack"
        self.level = 0.0
        self.active = True

    def note_off(self):
        if self.active and self.stage != "release":
            self.stage = "release"


voices = [Voice() for _ in range(NUM_VOICES)]

VOLUME = 80  # 0-100, fixed

# ---------------- Button handling ----------------


def poll_buttons():
    global previous

    raw = [b.value() == 0 for b in buttons]  # True = pressed
    if raw == previous:
        return

    for i in range(NUM_VOICES):
        pressed = raw[i]
        was_pressed = previous[i]
        if pressed and not was_pressed:
            voices[i].note_on(NOTE_FREQS[i], INSTRUMENT)
        elif not pressed and was_pressed:
            voices[i].note_off()

    previous = raw


# ---------------- Audio generation ----------------

# mix_buf is a wider int32 accumulator so multiple voices can sum into it
# before we clip once at the end -- avoids per-voice-per-sample clipping.
mix_buf = array("l", [0] * BUF_SAMPLES)
out_buf = array("h", [0] * BUF_SAMPLES)


@micropython.native
def render_voice(v, mix_buf, n_samples):
    """Render one voice's contribution into mix_buf, advancing its
    phase/envelope. All per-sample state lives in locals, not on self,
    which is the single biggest speed win here."""
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
        idx = int(phase) % TABLE_LEN
        raw = table[idx]

        phase += phase_inc
        if phase >= TABLE_LEN:
            phase -= TABLE_LEN

        if stage == "attack":
            level += attack_step
            if level >= 1.0:
                level = 1.0
                stage = "decay"
        elif stage == "decay":
            level -= decay_step
            if level <= sustain_level:
                level = sustain_level
                stage = "sustain"
        elif stage == "release":
            level -= release_step
            if level <= 0.0:
                level = 0.0
                stage = "idle"

        mix_buf[n] += int(raw * level)
        n += 1

        if stage == "idle":
            break  # remaining samples in this block get 0 from this voice

    v.phase = phase
    v.stage = stage
    v.level = level
    if stage == "idle":
        v.active = False


@micropython.native
def generate_block():
    vol = VOLUME / 100.0

    i = 0
    while i < BUF_SAMPLES:
        mix_buf[i] = 0
        i += 1

    for v in voices:
        if v.active:
            render_voice(v, mix_buf, BUF_SAMPLES)

    i = 0
    while i < BUF_SAMPLES:
        total = int(mix_buf[i] * vol)
        if total > 32000:
            total = 32000
        elif total < -32000:
            total = -32000
        out_buf[i] = total
        i += 1


# ---------------- Main loop ----------------

print("Synth ready.")

while True:
    poll_buttons()
    generate_block()
    audio.write(out_buf)


