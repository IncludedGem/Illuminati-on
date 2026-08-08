
import time
import json
from machine import Pin, I2C, ADC
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

# Columns: GP6, GP7, GP19    <-- GP19, not GP16
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
# CHROMATIC SCALE
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
# INSTRUMENT / ENVELOPE SETTINGS
# ============================================================

# (attack_ms, decay_ms, sustain_level, release_ms)

ENVELOPES = {
    "Sine":     (30, 150, 0.75, 350),
    "Square":   (20, 130, 0.65, 300),
    "Sawtooth": (30, 180, 0.70, 350),
    "Triangle": (30, 160, 0.75, 350),
    "Pulse":    (20, 120, 0.65, 300),
    "Organ":    (20, 50, 0.90, 250),
    "Bell":     (10, 600, 0.15, 900),
    "Pluck":    (10, 350, 0.08, 500),
    "Piano":    (5, 500, 0.20, 350),
    "Guitar":  (3, 400, 0.12, 500),
    "Bass":     (5, 200, 0.85, 300),
    "Flute":    (80, 100, 0.85, 200),
    "Clarinet": (60, 80, 0.85, 200),
    "Brass":    (40, 120, 0.80, 250),
    "Strings":  (150, 200, 0.85, 400)
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
# INSTRUMENT STATES
# ============================================================

state1 = {
    "octave": 4,
    "key": "C",
    "sample": "Sine",
    "volume": 75,
    "keys": [True, False, True, False, False, False, True, False]
}

state2 = {
    "octave": 5,
    "key": "F",
    "sample": "Sawtooth",
    "volume": 30,
    "keys": [False, True, False, True, True, False, False, True]
}

states = [state1, state2]

# 0 = state1
# 1 = state2
active_index = 0


# ============================================================
# 8 NOTE BUTTONS
# ============================================================

buttons = [
    Pin(15, Pin.IN, Pin.PULL_UP),
    Pin(14, Pin.IN, Pin.PULL_UP),
    Pin(13, Pin.IN, Pin.PULL_UP),
    Pin(12, Pin.IN, Pin.PULL_UP),
    Pin(11, Pin.IN, Pin.PULL_UP),
    Pin(10, Pin.IN, Pin.PULL_UP),
    Pin(9, Pin.IN, Pin.PULL_UP),
    Pin(8, Pin.IN, Pin.PULL_UP)
]

previous_keys = [False] * len(buttons)


# ============================================================
# VOLUME ADC
# ============================================================

adc = ADC(26)

previous_volume = -1


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

    # OLED can only display 21-ish characters
    # on a 128 pixel wide display.

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

print("--- Raspberry Pi Pico 2 W Music Controller ---")
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
print("Instrument buttons:")
print("  GPIO15 -> key 1")
print("  GPIO14 -> key 2")
print("  GPIO13 -> key 3")
print("  GPIO12 -> key 4")
print("  GPIO11 -> key 5")
print("  GPIO10 -> key 6")
print("  GPIO9  -> key 7")
print("  GPIO8  -> key 8")
print("")
print("Volume ADC: GPIO26")
print("OLED SDA: GPIO4")
print("OLED SCL: GPIO5")
print("")
print("Active state: state1")
print(json.dumps(states[active_index]))
print("")


# ============================================================
# MAIN LOOP
# ============================================================

while True:

    changed = False

    # Current active state
    active_state = states[active_index]


    # ========================================================
    # READ 8 INSTRUMENT BUTTONS
    # ========================================================

    keys = [
        button.value() == 0
        for button in buttons
    ]

    if keys != previous_keys:

        active_state["keys"] = keys[:]

        previous_keys = keys[:]

        changed = True


    # ========================================================
    # READ VOLUME
    # ========================================================

    total = 0

    # Average 10 ADC readings
    for _ in range(10):

        total += adc.read_u16()

        time.sleep_ms(2)

    raw_volume = total // 10

    # Convert 0-65535 -> 0-100
    volume = round(
        raw_volume / 65535 * 100
    )


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

        print(
            "[debug] Raw keypad key: "
            + pressed_key
        )

        # ----------------------------------------------------
        # KEY UP
        # ----------------------------------------------------

        if pressed_key == "1":

            active_state["key"] = increment_key(
                active_state["key"]
            )

            changed = True

            print(
                "state"
                + str(active_index + 1)
                + " key +: "
                + active_state["key"]
            )


        # ----------------------------------------------------
        # KEY DOWN
        # ----------------------------------------------------

        elif pressed_key == "4":

            active_state["key"] = decrement_key(
                active_state["key"]
            )

            changed = True

            print(
                "state"
                + str(active_index + 1)
                + " key -: "
                + active_state["key"]
            )


        # ----------------------------------------------------
        # OCTAVE UP
        # ----------------------------------------------------

        elif pressed_key == "2":

            active_state["octave"] = increment_octave(
                active_state["octave"]
            )

            changed = True

            print(
                "state"
                + str(active_index + 1)
                + " octave +: "
                + str(active_state["octave"])
            )


        # ----------------------------------------------------
        # OCTAVE DOWN
        # ----------------------------------------------------

        elif pressed_key == "5":

            active_state["octave"] = decrement_octave(
                active_state["octave"]
            )

            changed = True

            print(
                "state"
                + str(active_index + 1)
                + " octave -: "
                + str(active_state["octave"])
            )


        # ----------------------------------------------------
        # SAMPLE UP
        # ----------------------------------------------------

        elif pressed_key == "3":

            active_state["sample"] = increment_sample(
                active_state["sample"]
            )

            changed = True

            print(
                "state"
                + str(active_index + 1)
                + " sample +: "
                + active_state["sample"]
            )


        # ----------------------------------------------------
        # SAMPLE DOWN
        # ----------------------------------------------------

        elif pressed_key == "6":

            active_state["sample"] = decrement_sample(
                active_state["sample"]
            )

            changed = True

            print(
                "state"
                + str(active_index + 1)
                + " sample -: "
                + active_state["sample"]
            )


        # ----------------------------------------------------
        # SWITCH ACTIVE STATE
        # ----------------------------------------------------

        elif pressed_key == "*":

            active_index = (
                active_index + 1
            ) % len(states)

            changed = True

            active_state = states[active_index]

            # Reset previous button state so the OLED/state
            # immediately reflects the newly selected state.
            previous_keys = active_state["keys"][:]

            # Reset volume tracking so the active state's
            # stored volume isn't immediately overwritten.
            previous_volume = active_state["volume"]

            print(
                "Switched active state to state"
                + str(active_index + 1)
            )


        # ----------------------------------------------------
        # UNASSIGNED KEYS
        # ----------------------------------------------------

        else:

            print(
                "Key '"
                + pressed_key
                + "' has no assigned action."
            )


    # ========================================================
    # UPDATE OLED + JSON
    # ========================================================

    if changed:

        # Get active state again in case * changed it
        active_state = states[active_index]

        # Update OLED
        displayState(active_state)

        # Print JSON
        print(
            json.dumps(active_state)
        )

        print("")


    # ========================================================
    # LOOP DELAY
    # ========================================================



