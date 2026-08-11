"""3x4 matrix keypad scanning, debounced.

Self-contained. Call scan_keypad() once per main-loop pass.
"""

import time
from machine import Pin

KEY_MAP = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"]
]

# Rows start as inputs (high-Z) and are only driven for the microseconds
# they are being scanned, so no two rows can fight each other.
row_pins = [Pin(n, Pin.IN) for n in (0, 1, 2, 3)]
col_pins = [Pin(n, Pin.IN, Pin.PULL_UP) for n in (6, 7, 19)]

DEBOUNCE_MS = 20
key_down = bytearray(12)    # believed state, index = row * 3 + col
key_time = [0] * 12         # ms timestamp of each key's last accepted change


def scan_keypad():
    """Sample once, return the key just pressed or None. Never blocks.

    Runs once per audio block (BUF_SAMPLES / SAMPLE_RATE, 21.3 ms at the
    current settings), so a tap shorter than that can fall between scans.
    """
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