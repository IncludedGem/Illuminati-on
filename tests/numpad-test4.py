import time
from machine import Pin

# ============================================================
# 3x4 KEYPAD SETUP
# ============================================================

KEY_MAP = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"]
]

# Rows: GP0, GP1, GP2, GP3 -- start as high-Z inputs
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

DEBOUNCE_MS = 20

key_down = bytearray(12)
key_time = [0] * 12


def scan_keypad():
    """Sample the keypad once and return the key that was JUST
    pressed, or None. Never waits for anything."""

    now = time.ticks_ms()
    just_pressed = None

    for row_idx in range(4):
        row = row_pins[row_idx]
        row.init(Pin.OUT, value=0)

        for col_idx in range(3):
            i = row_idx * 3 + col_idx

            is_down = 1 if col_pins[col_idx].value() == 0 else 0

            if is_down != key_down[i]:
                if time.ticks_diff(now, key_time[i]) > DEBOUNCE_MS:
                    key_down[i] = is_down
                    key_time[i] = now

                    if is_down and just_pressed is None:
                        just_pressed = KEY_MAP[row_idx][col_idx]

        row.init(Pin.IN)

    return just_pressed


# ============================================================
# TEST LOOP
# ============================================================

print("Keypad test. Press keys...")

while True:
    k = scan_keypad()
    if k:
        print("pressed:", k)