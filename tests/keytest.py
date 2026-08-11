"""Raw keypad diagnostic -- HAcK 2026, Team 13.

Standalone. No audio, no OLED, no debounce, no edge detection. Prints
which keys are held down RIGHT NOW, so you can tell a wiring fault from a
scanning fault.

Same pins as keypad.py:
    rows GP0, GP1, GP2, GP3   (high-Z except while being scanned)
    cols GP6, GP7, GP19       (internal pull-ups, so pressed reads 0)

What to look for:
  - Nothing ever fires            -> col pin, ground, or a dead pull-up
  - One whole ROW never fires     -> that row's GPIO or its trace
  - One whole COLUMN never fires  -> that col's GPIO or its pull-up
  - Pressing one key lights two   -> rows shorted, or a missing diode on a
                                     matrix that needs them
  - Keys fire with nothing held   -> a row stuck driven low, usually a pin
                                     left as OUT by other code
"""

import time
from machine import Pin

ROW_PINS = (0, 1, 2, 3)
COL_PINS = (6, 7, 19)

KEY_MAP = (
    ("1", "2", "3"),
    ("4", "5", "6"),
    ("7", "8", "9"),
    ("*", "0", "#"),
)

rows = [Pin(n, Pin.IN) for n in ROW_PINS]
cols = [Pin(n, Pin.IN, Pin.PULL_UP) for n in COL_PINS]


def scan_raw():
    """Return the set of keys currently held. No debounce on purpose --
    this is meant to show you the contact bounce, not hide it."""
    down = []
    for r in range(4):
        row = rows[r]
        # Drive this row low only for the microseconds it is being read,
        # so no two rows can ever fight each other.
        row.init(Pin.OUT, value=0)
        for c in range(3):
            if cols[c].value() == 0:
                down.append(KEY_MAP[r][c])
        row.init(Pin.IN)
    return down


print("Keypad raw monitor. Ctrl-C to stop.")
print("rows", ROW_PINS, " cols", COL_PINS)
print("Press keys. A line prints on every change.\n")

# Idle check: with nothing pressed, every column should read 1 while no
# row is driven. If any reads 0 here, a row is stuck low and every scan
# will report phantom presses.
stuck = [COL_PINS[c] for c in range(3) if cols[c].value() == 0]
if stuck:
    print("!! col", stuck, "reads LOW with no row driven --")
    print("!! a row pin is stuck as an output, or that column is"
          " shorted to ground.\n")

last = None
presses = 0

while True:
    down = scan_raw()

    if down != last:
        if down:
            presses += 1
            print("DOWN:", " ".join(down), "   (event", presses, ")")
        else:
            print("  ---- all released ----")
        last = down

    # Fast enough to catch a deliberate tap, slow enough that bounce shows
    # up as a burst of lines rather than a solid wall of them.
    time.sleep_ms(5)
