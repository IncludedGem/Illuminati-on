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
"Organ", "Bell", "Pluck".

To change which notes the buttons play, edit NOTE_FREQS -- it's just a
list of 8 frequencies in Hz, one per button, in the same order as
BUTTON_PINS.

PERFORMANCE NOTE: real-time wavetable synthesis in plain Python is
CPU-heavy on a microcontroller. SAMPLE_RATE and BUF_SAMPLES below are
tuned conservatively (16 kHz mono). If you hear crackling/underruns,
lower SAMPLE_RATE (e.g. 11025) or increase BUF_SAMPLES.
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

SAMPLE_RATE = 16000
BUF_SAMPLES = 256  # samples generated per I2S.write() call

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

TABLE_LEN = 256
TABLE_AMP = 32000  # headroom below 32767 for mixing multiple voices


def make_table(fn):
    return array("h", [int(fn(i / TABLE_LEN) * TABLE_AMP) for i in range(TABLE_LEN)])


def sine_fn(t):
    return math.sin(2 * math.pi * t)


def square_fn(t):
    return 1.0 if t < 0.5 else -1.0


def saw_fn(t):
    return 2.0 * t - 1.0


def triangle_fn(t):
    return 4.0 * abs(t - 0.5) - 1.0


def pulse25_fn(t):
    return 1.0 if t < 0.25 else -1.0


def organ_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * t)
        + 0.50 * math.sin(2 * math.pi * 2 * t)
        + 0.33 * math.sin(2 * math.pi * 3 * t)
        + 0.25 * math.sin(2 * math.pi * 4 * t)
    )
    return v / 2.08


def bell_fn(t):
    v = (
        1.00 * math.sin(2 * math.pi * t)
        + 0.55 * math.sin(2 * math.pi * 2.76 * t)
        + 0.30 * math.sin(2 * math.pi * 5.4 * t)
    )
    return v / 1.85


def pluck_fn(t):
    return saw_fn(t)  # the "plucked" feel mostly comes from its envelope


WAVETABLES = {
    "Sine": make_table(sine_fn),
    "Square": make_table(square_fn),
    "Sawtooth": make_table(saw_fn),
    "Triangle": make_table(triangle_fn),
    "Pulse": make_table(pulse25_fn),
    "Organ": make_table(organ_fn),
    "Bell": make_table(bell_fn),
    "Pluck": make_table(pluck_fn),
}

# ADSR presets per instrument: (attack_ms, decay_ms, sustain_level 0..1, release_ms)
ENVELOPES = {
    "Sine":     (10, 80, 0.85, 150),
    "Square":   (5, 60, 0.80, 100),
    "Sawtooth": (15, 120, 0.75, 200),
    "Triangle": (10, 100, 0.85, 180),
    "Pulse":    (5, 60, 0.75, 120),
    "Organ":    (5, 30, 0.95, 60),
    "Bell":     (2, 400, 0.20, 600),
    "Pluck":    (2, 250, 0.05, 300),
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

    def next_sample(self):
        if not self.active:
            return 0

        idx = int(self.phase) % TABLE_LEN
        raw = self.table[idx]

        self.phase += self.phase_inc
        if self.phase >= TABLE_LEN:
            self.phase -= TABLE_LEN

        if self.stage == "attack":
            self.level += self.attack_step
            if self.level >= 1.0:
                self.level = 1.0
                self.stage = "decay"
        elif self.stage == "decay":
            self.level -= self.decay_step
            if self.level <= self.sustain_level:
                self.level = self.sustain_level
                self.stage = "sustain"
        elif self.stage == "release":
            self.level -= self.release_step
            if self.level <= 0.0:
                self.level = 0.0
                self.stage = "idle"
                self.active = False

        return int(raw * self.level)


voices = [Voice() for _ in range(NUM_VOICES)]

VOLUME = 30  # 0-100, fixed

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

out_buf = array("h", [0] * BUF_SAMPLES)


def generate_block():
    vol = VOLUME / 100.0
    for n in range(BUF_SAMPLES):
        total = 0
        for v in voices:
            if v.active:
                total += v.next_sample()
        total = int(total * vol)
        if total > 32000:
            total = 32000
        elif total < -32000:
            total = -32000
        out_buf[n] = total


# ---------------- Main loop ----------------

print("Synth ready.")

while True:
    poll_buttons()
    generate_block()
    audio.write(out_buf)
