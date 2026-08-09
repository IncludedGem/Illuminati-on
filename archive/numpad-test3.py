import time
import json
from machine import Pin

# Define the 3x4 layout mapping array
KEY_MAP = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"]
]

# Set rows as Outputs on GP0, GP1, GP2, GP3 (default driven HIGH)
row_pins = [
    Pin(0, Pin.OUT, value=1),
    Pin(1, Pin.OUT, value=1),
    Pin(2, Pin.OUT, value=1),
    Pin(3, Pin.OUT, value=1)
]

# Set columns as Inputs on GP6, GP7, GP19 with internal pull-up resistors
col_pins = [
    Pin(6, Pin.IN, Pin.PULL_UP),
    Pin(7, Pin.IN, Pin.PULL_UP),
    Pin(19, Pin.IN, Pin.PULL_UP)
]

def scan_keypad():
    """Sequentially scans the grid to check for active key presses."""
    for row_idx, row_pin in enumerate(row_pins):
        # Drive the targeted row LOW to check connections
        row_pin.value(0)

        for col_idx, col_pin in enumerate(col_pins):
            # If column reads 0, a connection has been established to the ground state row
            if col_pin.value() == 0:
                # Key debounce trap: loop until the finger releases the button
                while col_pin.value() == 0:
                    time.sleep_ms(10)

                # Restore the targeted row back to HIGH before exiting
                row_pin.value(1)
                return KEY_MAP[row_idx][col_idx]

        # Restore row back to HIGH if no keys were pressed on this line
        row_pin.value(1)

    return None

# --- Chromatic scale helpers (half-step increments) ---
CHROMATIC_SCALE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

ENHARMONIC_MAP = {
    "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
    "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"
}

def normalize_key(key):
    """Convert flats/uncommon spellings to the sharps used in CHROMATIC_SCALE."""
    return ENHARMONIC_MAP.get(key, key)

def increment_key(current_key):
    """Return the key one half step up."""
    normalized = normalize_key(current_key)
    idx = CHROMATIC_SCALE.index(normalized)
    next_idx = (idx + 1) % len(CHROMATIC_SCALE)
    return CHROMATIC_SCALE[next_idx]

def decrement_key(current_key):
    """Return the key one half step down."""
    normalized = normalize_key(current_key)
    idx = CHROMATIC_SCALE.index(normalized)
    prev_idx = (idx - 1) % len(CHROMATIC_SCALE)
    return CHROMATIC_SCALE[prev_idx]

# --- Octave helpers ---
MIN_OCTAVE = 0
MAX_OCTAVE = 8

def increment_octave(current_octave):
    """Return the octave one step up, clamped to MAX_OCTAVE."""
    return min(current_octave + 1, MAX_OCTAVE)

def decrement_octave(current_octave):
    """Return the octave one step down, clamped to MIN_OCTAVE."""
    return max(current_octave - 1, MIN_OCTAVE)

# --- Sample (envelope) helpers ---
# (attack_ms, decay_ms, sustain_level, release_ms)
ENVELOPES = {
    "Sine": (30, 150, 0.75, 350),
    "Square": (20, 130, 0.65, 300),
    "Sawtooth": (30, 180, 0.70, 350),
    "Triangle": (30, 160, 0.75, 350),
    "Pulse": (20, 120, 0.65, 300),
    "Organ": (20, 50, 0.90, 250),
    "Bell": (10, 600, 0.15, 900),
    "Pluck": (10, 350, 0.08, 500),
    "Piano": (5, 500, 0.20, 350),
    "Guitar": (3, 400, 0.12, 500),
    "Bass": (5, 200, 0.85, 300),
    "Flute": (80, 100, 0.85, 200),
    "Clarinet": (60, 80, 0.85, 200),
    "Brass": (40, 120, 0.80, 250),
    "Strings": (150, 200, 0.85, 400),
}

SAMPLE_LIST = list(ENVELOPES.keys())

def increment_sample(current_sample):
    """Return the next sample in the ENVELOPES map, wrapping around."""
    idx = SAMPLE_LIST.index(current_sample)
    next_idx = (idx + 1) % len(SAMPLE_LIST)
    return SAMPLE_LIST[next_idx]

def decrement_sample(current_sample):
    """Return the previous sample in the ENVELOPES map, wrapping around."""
    idx = SAMPLE_LIST.index(current_sample)
    prev_idx = (idx - 1) % len(SAMPLE_LIST)
    return SAMPLE_LIST[prev_idx]

# --- Instrument states ---
state1 = {
    "octave": 4,
    "key": "C",
    "sample": "Sine",       # must be a key in ENVELOPES
    "volume": 75,
    "keys": [True, False, True, False, False, False, True, False]
}

state2 = {
    "octave": 5,
    "key": "F",
    "sample": "Sawtooth",   # must be a key in ENVELOPES
    "volume": 30,
    "keys": [False, True, False, True, True, False, False, True]
}

states = [state1, state2]
active_index = 0  # 0 = state1, 1 = state2

# Core runtime environment initialization
print("--- Raspberry Pi Pico 2 W Keypad Active ---")
print("Press any key on the matrix...")
print(f"Active state: state{active_index + 1}")
print(json.dumps(states[active_index]))
print("")

while True:
    pressed_key = scan_keypad()

    if pressed_key:
        # Debug line: always fires so you can confirm the physical key press
        # is actually being detected, even if it doesn't match a branch below.
        print(f"[debug] Raw key detected: {pressed_key}")

        if pressed_key == "1":
            active_state = states[active_index]
            active_state["key"] = increment_key(active_state["key"])
            print(f"state{active_index + 1} key incremented:")
            print(json.dumps(active_state))
            print("")

        elif pressed_key == "4":
            active_state = states[active_index]
            active_state["key"] = decrement_key(active_state["key"])
            print(f"state{active_index + 1} key decremented:")
            print(json.dumps(active_state))
            print("")

        elif pressed_key == "2":
            active_state = states[active_index]
            active_state["octave"] = increment_octave(active_state["octave"])
            print(f"state{active_index + 1} octave incremented:")
            print(json.dumps(active_state))
            print("")

        elif pressed_key == "5":
            active_state = states[active_index]
            active_state["octave"] = decrement_octave(active_state["octave"])
            print(f"state{active_index + 1} octave decremented:")
            print(json.dumps(active_state))
            print("")

        elif pressed_key == "3":
            active_state = states[active_index]
            active_state["sample"] = increment_sample(active_state["sample"])
            print(f"state{active_index + 1} sample incremented:")
            print(json.dumps(active_state))
            print("")

        elif pressed_key == "6":
            active_state = states[active_index]
            active_state["sample"] = decrement_sample(active_state["sample"])
            print(f"state{active_index + 1} sample decremented:")
            print(json.dumps(active_state))
            print("")

        elif pressed_key == "*":
            active_index = (active_index + 1) % len(states)
            print(f"Switched active state to: state{active_index + 1}")
            print(json.dumps(states[active_index]))
            print("")

        else:
            # Catches 7, 8, 9, 0, # -- keys with no mapped action yet
            print(f"Key '{pressed_key}' has no assigned action.")
            print("")

    time.sleep_ms(30)  # Polling cycle interval