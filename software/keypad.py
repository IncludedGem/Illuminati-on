"""3x4 matrix keypad scanning, debounced.

Self-contained: no dependency on audio, scale, or display state. Import
and call scan_keypad() once per main-loop pass.
"""

import time
from machine import Pin

KEY_MAP = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"]
]

# Rows start as INPUTS. An input pin is high-Z (electrically
# disconnected); we only drive a row for the few microseconds we are
# actually scanning it, so no two rows can ever fight each other.
row_pins = [Pin(n, Pin.IN) for n in (0, 1, 2, 3)]
col_pins = [Pin(n, Pin.IN, Pin.PULL_UP) for n in (6, 7, 19)]

DEBOUNCE_MS = 20          # ignore a key's changes within this of its last
key_down = bytearray(12)  # believed state, index = row * 3 + col
key_time = [0] * 12       # ms timestamp of each key's last accepted change


def scan_keypad():
    """Sample the keypad once, return the key that was JUST pressed, or
    None. Never blocks -- important, because this runs inside the audio
    loop's time budget.

    Note the sampling limit: this runs once per audio block, which is
    BUF_SAMPLES / SAMPLE_RATE in main.py -- 21.3 ms at 256 samples and
    12000 Hz. A tap shorter than that can fall entirely between two
    scans, so press keypad keys deliberately on stage. (This comment
    said 23 ms for a while after the rate moved to 16000, which put the
    real figure at 16 ms; it is derived, not fixed, so re-read it from
    those two constants rather than trusting the number here.)"""
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