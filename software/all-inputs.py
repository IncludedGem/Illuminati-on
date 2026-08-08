"""
SYNTH-V2 -- MERGED PICO 2 W MUSIC CONTROLLER
==============================================
This combines two things that used to live in separate scripts:

  1) The UI layer: a 3x4 matrix keypad, an OLED status display, a volume
     potentiometer, and two switchable "states" (patches) that each
     remember their own octave / key / instrument / volume.

  2) The audio layer: a real-time I2S wavetable synth with per-voice
     ADSR envelopes and a library of instrument wavetables (Sine,
     Square, Organ, Bell, Piano, Guitar, Flute, Trumpet, Strings, etc).

The 8 physical note buttons now trigger *real audio* instead of just
updating a boolean array. Whichever state (state1 or state2) is active
determines:
  - the 8-note major scale the buttons play, built live from that
    state's "key" + "octave"
  - which wavetable/ADSR envelope is used (that state's "sample")
  - the overall output volume (that state's "volume")

Press '*' on the keypad to swap which state/patch is currently
controlling the 8 buttons and the potentiometer. The keypad always
edits whichever state is currently active.

PIN MAP
-------
  Keypad rows    -> GP0, GP1, GP2, GP3
  Keypad cols    -> GP6, GP7, GP19
  Note buttons   -> GP15, GP14, GP13, GP12, GP11, GP10, GP9, GP8
                    (active-low, internal pull-ups)
  Volume pot     -> GP26 (ADC0)
  OLED (I2C)     -> SDA=GP4, SCL=GP5
  I2S DAC        -> sck=GP16, ws=GP17, sd=GP18

NOTE ON TIMING
--------------
Audio is generated and written to the I2S peripheral on every single
pass of the main loop, unconditionally -- that has to happen every
loop or you'll hear underruns/crackling. Everything else (keypad scan,
potentiometer read, OLED redraw) is cheap and non-blocking (the
potentiometer read below uses microsecond, not millisecond, delays,
unlike the original UI-only script) so it fits comfortably inside one
audio block's worth of time (BUF_SAMPLES / SAMPLE_RATE ~= 93ms at the
settings below). If you add more UI work later and start hearing
glitches, that's the first place to look.
"""

import time
import json
import math
from array import array
from machine import Pin, I2C, ADC, I2S
import ssd1306

# ============================================================
# 3x4 KEYPAD SETUP
# ============================================================

KEY_MAP = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"]
]

# Rows: GP0, GP1, GP2, GP3
# Start as INPUTS. An input pin is "high-Z" -- electrically
# disconnected. We only turn a row into an output for the
# few microseconds we're actually scanning it.
row_pins = [
    Pin(0, Pin.IN),
    Pin(1, Pin.IN),
    Pin(2, Pin.IN),
    Pin(3, Pin.IN)
]

# Columns: GP6, GP7, GP19
col_pins = [
    Pin(6, Pin.IN, Pin.PULL_UP),
    Pin(7, Pin.IN, Pin.PULL_UP),
    Pin(19, Pin.IN, Pin.PULL_UP)
]

# Ignore any change on a key within this many ms of its last
# change. Mechanical switches "bounce" (rapidly make/break
# contact) for a few ms when pressed.
DEBOUNCE_MS = 20

# What we believe each key's state is right now. 12 keys,
# index = row * 3 + col.  1 = down, 0 = up.
key_down = bytearray(12)

# When each key last changed state (ms).
key_time = [0] * 12


def scan_keypad():
    """Sample the keypad once and return the key that was JUST
    pressed, or None. Never waits for anything."""

    now = time.ticks_ms()
    just_pressed = None

    for row_idx in range(4):
        row = row_pins[row_idx]

        # Make this one row an output, driven LOW
        row.init(Pin.OUT, value=0)

        for col_idx in range(3):
            i = row_idx * 3 + col_idx

            # Column reads LOW = this key is bridging row to column
            is_down = 1 if col_pins[col_idx].value() == 0 else 0

            # Only care when the state is different from what we
            # last recorded
            if is_down != key_down[i]:

                # ...and enough time has passed to trust it
                if time.ticks_diff(now, key_time[i]) > DEBOUNCE_MS:
                    key_down[i] = is_down
                    key_time[i] = now

                    if is_down and just_pressed is None:
                        just_pressed = KEY_MAP[row_idx][col_idx]

        # Put the row back to high-Z before moving on
        row.init(Pin.IN)

    return just_pressed

# ============================================================
# CHROMATIC SCALE / KEY HANDLING
# ============================================================

CHROMATIC_SCALE = [
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B"
]

ENHARMONIC_MAP = {
    "Db": "C#",
    "Eb": "D#",
    "Gb": "F#",
    "Ab": "G#",
    "Bb": "A#",
    "Cb": "B",
    "Fb": "E",
    "E#": "F",
    "B#": "C"
}


def normalize_key(key):
    return ENHARMONIC_MAP.get(key, key)


def increment_key(current_key):
    normalized = normalize_key(current_key)
    idx = CHROMATIC_SCALE.index(normalized)
    next_idx = (idx + 1) % len(CHROMATIC_SCALE)
    return CHROMATIC_SCALE[next_idx]


def decrement_key(current_key):
    normalized = normalize_key(current_key)
    idx = CHROMATIC_SCALE.index(normalized)
    prev_idx = (idx - 1) % len(CHROMATIC_SCALE)
    return CHROMATIC_SCALE[prev_idx]


# ============================================================
# OCTAVE
# ============================================================

MIN_OCTAVE = 1
MAX_OCTAVE = 8


def increment_octave(current_octave):
    return min(current_octave + 1, MAX_OCTAVE)


def decrement_octave(current_octave):
    return max(current_octave - 1, MIN_OCTAVE)


# ============================================================
# NOTE FREQUENCY (key + octave -> 8-note major scale, one per button)
# ============================================================

# Semitone offsets of a major scale, including the octave on top, so
# an 8-button row plays a full root-to-root scale (e.g. C4..C5).
MAJOR_SCALE_STEPS = [0, 2, 4, 5, 7, 9, 11, 12]


def build_scale_freqs(key, octave):
    """Return 8 frequencies (Hz), one per note button, forming a
    major scale starting at `key` in `octave`. Standard equal
    temperament, A4 = 440Hz."""

    root_idx = CHROMATIC_SCALE.index(normalize_key(key))
    freqs = []

    for step in MAJOR_SCALE_STEPS:
        total = root_idx + step
        note_octave = octave + total // 12
        note_idx = total % 12

        semitones_from_a4 = (note_octave - 4) * 12 + (note_idx - 9)
        freq = 440.0 * (2 ** (semitones_from_a4 / 12))
        freqs.append(freq)

    return freqs


# ============================================================
# WAVETABLES
# ============================================================

TABLE_LEN = 256
TABLE_AMP = 32000


def make_table(fn):
    return array(
        "h",
        [int(fn(i / TABLE_LEN) * TABLE_AMP) for i in range(TABLE_LEN)]
    )


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
    # Fuller principal + mixture chorus: more ranks stacked than a basic
    # 8'+4'+2' combo.
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)    # 8'   unison
        + 0.75 * math.sin(2 * math.pi * 2.00 * t)    # 4'   octave
        + 0.50 * math.sin(2 * math.pi * 3.00 * t)    # 2⅔'  twelfth
        + 0.40 * math.sin(2 * math.pi * 4.00 * t)    # 2'   fifteenth
        + 0.25 * math.sin(2 * math.pi * 5.00 * t)    # 1⅗'  tierce
        + 0.20 * math.sin(2 * math.pi * 6.00 * t)    # 1⅓'  larigot
        + 0.15 * math.sin(2 * math.pi * 8.00 * t)    # 1'   twenty-second
        + 0.10 * math.sin(2 * math.pi * 9.00 * t)    # mixture rank
        + 0.08 * math.sin(2 * math.pi * 10.00 * t)   # mixture rank
        + 0.05 * math.sin(2 * math.pi * 12.00 * t)   # mixture top rank
    )
    return v / 3.48


def bell_fn(t):
    # Bells contain strong NON-integer partials.
    v = (
        1.00 * math.sin(2 * math.pi * 1.00 * t)
        + 0.55 * math.sin(2 * math.pi * 2.71 * t)
        + 0.32 * math.sin(2 * math.pi * 4.07 * t)
        + 0.22 * math.sin(2 * math.pi * 5.83 * t)
        + 0.12 * math.sin(2 * math.pi * 7.91 * t)
    )
    return v / 2.21


def pluck_fn(t):
    # Young's plucked-string theorem, p = 1/8 (bright, near the bridge)
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
    # p = 1/5, nylon-string fingerstyle; n=5 physically absent (node at pluck point)
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
    "Trumpet":  make_table(trumpet_fn),
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
    "Organ":    (12, 40, 0.97,  400),
    "Bell":     (1,  400, 0.22, 1900),
    "Pluck":    (2,  200, 0.09, 90),

    "Piano":    (2,  450, 0.12, 450),
    "Guitar":   (3,  100, 0.25, 800),
    "Bass":     (3,  190, 0.15, 90),

    "Flute":    (90, 100, 0.92, 240),
    "Clarinet": (20,  45, 0.94, 150),
    "Trumpet":  (25, 50, 0.85, 160),
    "Strings":  (140, 100, 0.92, 550),
}

SAMPLE_LIST = list(ENVELOPES.keys())


def increment_sample(current_sample):
    idx = SAMPLE_LIST.index(current_sample)
    next_idx = (idx + 1) % len(SAMPLE_LIST)
    return SAMPLE_LIST[next_idx]


def decrement_sample(current_sample):
    idx = SAMPLE_LIST.index(current_sample)
    prev_idx = (idx - 1) % len(SAMPLE_LIST)
    return SAMPLE_LIST[prev_idx]


# ============================================================
# INSTRUMENT STATES (PATCHES)
# ============================================================

state1 = {
    "octave": 4,
    "key": "C",
    "sample": "Sine",
    "volume": 75,
    "keys": [False] * 8
}

state2 = {
    "octave": 5,
    "key": "F",
    "sample": "Sawtooth",
    "volume": 30,
    "keys": [False] * 8
}

states = [state1, state2]

# 0 = state1
# 1 = state2
active_index = 0


# ============================================================
# 8 NOTE BUTTONS
# ============================================================

BUTTON_PINS = [15, 14, 13, 12, 11, 10, 9, 8]
buttons = [Pin(p, Pin.IN, Pin.PULL_UP) for p in BUTTON_PINS]

# Tracks the REAL hardware state of the buttons (not per-state) so the
# audio engine always knows what's actually pressed right now.
previous_keys = [False] * len(buttons)


# ============================================================
# VOLUME ADC
# ============================================================

adc = ADC(26)
previous_volume = -1

# ---- Volume pot calibration ----
# Slide pots rarely actually swing the full 0-65535 ADC range in
# practice (wiring resistance, the pot's own tolerance, etc). If you
# hardcode 0-65535 as the endpoints, whatever the pot's *real* range
# turns out to be gets squashed into a smaller chunk of that -- which
# is exactly the "bottom never reads 0" / "halfway already reads 100"
# symptom.
#
# Instead we self-calibrate: the lowest and highest raw readings ever
# seen become the 0% and 100% endpoints, so the whole physical slide
# length maps to the whole 0-100 range with nothing wasted.
#
# After power-on, slide it all the way down then all the way up once
# to calibrate. Until that first full sweep, readings are
# stretched/clamped from whatever range has been seen so far.
adc_min = 65535
adc_max = 0


# ============================================================
# OLED SETUP
# ============================================================

i2c = I2C(
    0,
    sda=Pin(4),
    scl=Pin(5)
)

display = ssd1306.SSD1306_I2C(
    128,
    64,
    i2c
)


# ============================================================
# I2S AUDIO OUTPUT
# ============================================================

SAMPLE_RATE = 11025
BUF_SAMPLES = 1024

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


# ============================================================
# SYNTH VOICES
# ============================================================

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
def generate_block(volume_pct):
    """Render one audio block using the given 0-100 volume percent
    (comes from whichever state is currently active)."""
    vol = volume_pct / 100.0

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


# ============================================================
# OLED DISPLAY
# ============================================================

def displayState(state):

    display.fill(0)

    # -------------------------
    # State
    # -------------------------

    display.text(
        "State: " + str(active_index + 1),
        0,
        0
    )

    # -------------------------
    # Instrument settings
    # -------------------------

    display.text(
        "Oct: " + str(state["octave"]),
        0,
        8
    )

    display.text(
        "Key: " + state["key"],
        64,
        8
    )

    # -------------------------
    # Sample
    # -------------------------

    sample_name = state["sample"]

    display.text(
        "Sample: " + sample_name,
        0,
        16
    )

    # -------------------------
    # Volume
    # -------------------------

    display.text(
        "Volume: " + str(state["volume"]),
        0,
        24
    )

    # -------------------------
    # Keys
    # -------------------------

    display.text(
        "Keys:",
        0,
        32
    )

    key_string = "".join(
        "1" if key else "0"
        for key in state["keys"]
    )

    display.text(
        key_string,
        40,
        32
    )

    # -------------------------
    # Button labels
    # -------------------------

    display.text(
        "12345678",
        40,
        40
    )

    display.show()


# ============================================================
# INITIAL DISPLAY
# ============================================================

displayState(states[active_index])


# ============================================================
# STARTUP
# ============================================================

print("--- Raspberry Pi Pico 2 W Music Controller (synth-v2) ---")
print("")
print("3x4 keypad:")
print("  1 = Key +")
print("  4 = Key -")
print("  2 = Octave +")
print("  5 = Octave -")
print("  3 = Sample +")
print("  6 = Sample -")
print("  * = Switch state")
print("")
print("Instrument buttons play a live major scale built from the")
print("active state's key + octave, using its selected sample/")
print("instrument and volume:")
print("  GPIO15 -> button 1")
print("  GPIO14 -> button 2")
print("  GPIO13 -> button 3")
print("  GPIO12 -> button 4")
print("  GPIO11 -> button 5")
print("  GPIO10 -> button 6")
print("  GPIO9  -> button 7")
print("  GPIO8  -> button 8")
print("")
print("Volume pot: GPIO26")
print("OLED SDA: GPIO4")
print("OLED SCL: GPIO5")
print("I2S sck/ws/sd: GPIO16/17/18")
print("")
print("Active state: state1")
print(json.dumps(states[active_index]))
print("")


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    changed = False
    active_state = states[active_index]

    # ========================================================
    # 8 NOTE BUTTONS -> TRIGGER VOICES
    # ========================================================

    raw_keys = [button.value() == 0 for button in buttons]

    if raw_keys != previous_keys:

        # Only build the scale when something actually changed, and
        # only once per batch of button changes (cheap either way,
        # but no sense doing it 8 times).
        scale_freqs = build_scale_freqs(
            active_state["key"], active_state["octave"]
        )

        for i in range(NUM_VOICES):
            pressed = raw_keys[i]
            was_pressed = previous_keys[i]

            if pressed and not was_pressed:
                voices[i].note_on(scale_freqs[i], active_state["sample"])
            elif was_pressed and not pressed:
                voices[i].note_off()

        active_state["keys"] = raw_keys[:]
        previous_keys = raw_keys[:]
        changed = True

    # ========================================================
    # READ VOLUME POT
    # ========================================================

    total = 0

    # Average a handful of ADC readings with short microsecond delays
    # (not millisecond sleeps) so this doesn't eat into the audio
    # block's timing budget.
    for _ in range(4):
        total += adc.read_u16()
        time.sleep_us(200)

    raw_volume = total // 4

    # Expand the calibrated range if this reading pushes past what
    # we've seen before (only ever grows, never shrinks).
    if raw_volume < adc_min:
        adc_min = raw_volume
    if raw_volume > adc_max:
        adc_max = raw_volume

    # Map the CALIBRATED range to 0-100, not a hardcoded 0-65535
    if adc_max > adc_min:
        volume = round((raw_volume - adc_min) / (adc_max - adc_min) * 100)
    else:
        volume = 0
    volume = max(0, min(100, volume))

    # ========================================================
    # VOLUME DEADBAND
    # ========================================================

    if previous_volume == -1:
        previous_volume = volume
        active_state["volume"] = volume
        changed = True

    elif abs(volume - previous_volume) >= 2:
        previous_volume = volume
        active_state["volume"] = volume
        changed = True

    # ========================================================
    # READ 3x4 KEYPAD
    # ========================================================

    pressed_key = scan_keypad()

    if pressed_key:

        print("[debug] Raw keypad key: " + pressed_key)

        # ----------------------------------------------------
        # KEY UP
        # ----------------------------------------------------
        if pressed_key == "1":
            active_state["key"] = increment_key(active_state["key"])
            changed = True
            print(
                "state" + str(active_index + 1)
                + " key +: " + active_state["key"]
            )

        # ----------------------------------------------------
        # KEY DOWN
        # ----------------------------------------------------
        elif pressed_key == "4":
            active_state["key"] = decrement_key(active_state["key"])
            changed = True
            print(
                "state" + str(active_index + 1)
                + " key -: " + active_state["key"]
            )

        # ----------------------------------------------------
        # OCTAVE UP
        # ----------------------------------------------------
        elif pressed_key == "2":
            active_state["octave"] = increment_octave(active_state["octave"])
            changed = True
            print(
                "state" + str(active_index + 1)
                + " octave +: " + str(active_state["octave"])
            )

        # ----------------------------------------------------
        # OCTAVE DOWN
        # ----------------------------------------------------
        elif pressed_key == "5":
            active_state["octave"] = decrement_octave(active_state["octave"])
            changed = True
            print(
                "state" + str(active_index + 1)
                + " octave -: " + str(active_state["octave"])
            )

        # ----------------------------------------------------
        # SAMPLE UP
        # ----------------------------------------------------
        elif pressed_key == "3":
            active_state["sample"] = increment_sample(active_state["sample"])
            changed = True
            print(
                "state" + str(active_index + 1)
                + " sample +: " + active_state["sample"]
            )

        # ----------------------------------------------------
        # SAMPLE DOWN
        # ----------------------------------------------------
        elif pressed_key == "6":
            active_state["sample"] = decrement_sample(active_state["sample"])
            changed = True
            print(
                "state" + str(active_index + 1)
                + " sample -: " + active_state["sample"]
            )

        # ----------------------------------------------------
        # SWITCH ACTIVE STATE
        # ----------------------------------------------------
        elif pressed_key == "*":
            active_index = (active_index + 1) % len(states)
            changed = True
            active_state = states[active_index]

            # Reset volume tracking so the newly active state's
            # stored volume isn't immediately overwritten by
            # wherever the pot physically happens to be sitting.
            # NOTE: previous_keys is intentionally NOT reset here --
            # it tracks real hardware button state for the audio
            # engine, and resetting it would risk missing a
            # note-off (or firing a phantom note-on) for a button
            # that's still physically held down across the switch.
            previous_volume = active_state["volume"]

            print("Switched active state to state" + str(active_index + 1))

        # ----------------------------------------------------
        # UNASSIGNED KEYS
        # ----------------------------------------------------
        else:
            print("Key '" + pressed_key + "' has no assigned action.")

    # ========================================================
    # UPDATE OLED + JSON
    # ========================================================

    if changed:
        active_state = states[active_index]
        displayState(active_state)
        print(json.dumps(active_state))
        print("")

    # ========================================================
    # AUDIO RENDER -- runs every loop, unconditionally
    # ========================================================

    generate_block(active_state["volume"])
    audio.write(out_buf)
