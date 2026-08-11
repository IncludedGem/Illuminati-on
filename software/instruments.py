"""
8-NOTE SYNTHESIZER (instruments.py edition)
-------------------------------------------
Same behavior as the working standalone synth -- each of the 8 buttons
plays its own fixed note -- but all wavetable/envelope definitions now
come from instruments.py (the single source of truth) instead of being
duplicated here.

What moved OUT of this file and into instruments.py:
  - make_table / all the *_fn waveform recipes
  - WAVETABLES and ENVELOPES dicts
  - TABLE_LEN

What this file keeps:
  - Buttons, I2S setup, Voice class, mixing/render loop, main loop.

IMPORTANT DIFFERENCES vs. the old standalone script:
  1) SAMPLE_RATE is 12000, not 11025. instruments.py band-limits every
     table with N_MAX = 5, which is computed against a 6000 Hz Nyquist
     (i.e., a 12 kHz sample rate). Running its tables at 11025 would
     quietly shift the band-limit math; if you ever change SAMPLE_RATE
     here, re-tune N_MAX in instruments.py to match.
  2) Instrument names must be keys that exist in instruments.INSTRUMENTS.
     The old script's set is a subset of the new one (you also gain
     Sine, Square, Triangle, Pulse). "Drums" is in SAMPLE_LIST but has
     no wavetable -- don't set INSTRUMENT to "Drums" in this standalone
     synth; drum dispatch lives in the full main.py, not here.
  3) If your full main.py has the Looper, import instruments AFTER the
     Looper allocates its buffers (see the allocation-order warning at
     the top of instruments.py). This standalone script has no Looper,
     so importing at the top is fine.

Pins (unchanged):
  Buttons -> GPIO15, 14, 13, 12, 11, 10, 9, 8 (active-low, internal pull-ups)
  I2S DAC -> sck=GPIO16, ws=GPIO17, sd=GPIO18
"""

from machine import Pin, I2S
from array import array

# No Looper in this standalone script, so importing here is safe.
# In the full main.py this import must come AFTER `looper = loop.Looper(...)`.
from instruments import WAVETABLES, ENVELOPES, TABLE_LEN

# ---------------- Buttons ----------------

BUTTON_PINS = [15, 14, 13, 12, 11, 10, 9, 8]
buttons = [Pin(p, Pin.IN, Pin.PULL_UP) for p in BUTTON_PINS]
previous = [False] * len(buttons)

# One note per button, in the same order as BUTTON_PINS.
# Default: C major scale, C4 through C5.
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

# ---------------- I2S output ----------------

# 12000, NOT 11025: instruments.py's N_MAX = 5 band limit is sized for a
# 6000 Hz Nyquist. See "IMPORTANT DIFFERENCES" in the header docstring.
SAMPLE_RATE = 12000
BUF_SAMPLES = 1024  # fewer I2S.write() calls, less overhead

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

# Which instrument all 8 buttons use. Any name from instruments.INSTRUMENTS:
# "Sine", "Square", "Sawtooth", "Triangle", "Pulse", "Organ", "Bell",
# "Pluck", "Piano", "Guitar", "Bass", "Flute", "Clarinet", "Trumpet",
# "Strings". (NOT "Drums" -- no wavetable; see header.)
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
        self.table = WAVETABLES.get(instrument, WAVETABLES["Piano"])
        self.phase = 0.0
        self.phase_inc = freq * TABLE_LEN / SAMPLE_RATE

        a_ms, d_ms, s_lvl, r_ms = ENVELOPES.get(instrument, ENVELOPES["Piano"])
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