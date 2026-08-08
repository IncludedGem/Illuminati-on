import time
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

# Set columns as Inputs on GP6, GP7, GP16 with internal pull-up resistors
col_pins = [
    Pin(6, Pin.IN, Pin.PULL_UP),
    Pin(7, Pin.IN, Pin.PULL_UP),
    Pin(16, Pin.IN, Pin.PULL_UP)
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

# --- Circle of fifths helpers ---
CIRCLE_OF_FIFTHS = ["C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]

ENHARMONIC_MAP = {
    "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
    "Cb": "B", "Fb": "E", "E#": "F", "B#": "C"
}

def normalize_key(key):
    """Convert flats/uncommon spellings to the sharps used in CIRCLE_OF_FIFTHS."""
    return ENHARMONIC_MAP.get(key, key)

def increment_key(current_key):
    """Return the key one step clockwise around the circle of fifths."""
    normalized = normalize_key(current_key)
    idx = CIRCLE_OF_FIFTHS.index(normalized)
    next_idx = (idx + 1) % len(CIRCLE_OF_FIFTHS)
    return CIRCLE_OF_FIFTHS[next_idx]

def decrement_key(current_key):
    """Return the key one step counter-clockwise around the circle of fifths."""
    normalized = normalize_key(current_key)
    idx = CIRCLE_OF_FIFTHS.index(normalized)
    prev_idx = (idx - 1) % len(CIRCLE_OF_FIFTHS)
    return CIRCLE_OF_FIFTHS[prev_idx]

# --- Instrument states ---
state1 = {
    "octave": 4,
    "key": "C#",
    "sample": "Piano",
    "volume": 75,
    "keys": [True, False, True, False, False, False, True, False]
}

state2 = {
    "octave": 5,
    "key": "F",
    "sample": "Violin",
    "volume": 30,
    "keys": [False, True, False, True, True, False, False, True]
}

states = [state1, state2]
active_index = 0  # 0 = state1, 1 = state2

# Core runtime environment initialization
print("--- Raspberry Pi Pico 2 W Keypad Active ---")
print("Press any key on the matrix...")
print(f"Active state: state{active_index + 1} -> {states[active_index]}")

while True:
    pressed_key = scan_keypad()
    if pressed_key:
        print(f"Detected Key: {pressed_key}")

        if pressed_key == "1":
            active_state = states[active_index]
            active_state["key"] = increment_key(active_state["key"])
            print(f"state{active_index + 1} key incremented to: {active_state['key']}")

        elif pressed_key == "4":
            active_state = states[active_index]
            active_state["key"] = decrement_key(active_state["key"])
            print(f"state{active_index + 1} key decremented to: {active_state['key']}")

        elif pressed_key == "*":
            active_index = (active_index + 1) % len(states)
            print(f"Switched active state to: state{active_index + 1}")

    time.sleep_ms(30)  # Polling cycle interval