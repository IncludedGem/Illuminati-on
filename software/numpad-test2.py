import time
from machine import Pin

# Define the 3x4 layout mapping array
KEY_MAP = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"]
]

# Set rows as Outputs on GP2, GP3, GP4, GP5 (default driven HIGH)
row_pins = [
    Pin(2, Pin.OUT, value=1),
    Pin(3, Pin.OUT, value=1),
    Pin(4, Pin.OUT, value=1),
    Pin(5, Pin.OUT, value=1)
]

# Set columns as Inputs on GP6, GP7, GP8 with internal pull-up resistors
col_pins = [
    Pin(6, Pin.IN, Pin.PULL_UP),
    Pin(7, Pin.IN, Pin.PULL_UP),
    Pin(8, Pin.IN, Pin.PULL_UP)
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

# Core runtime environment initialization
print("--- Raspberry Pi Pico 2 W Keypad Active ---")
print("Press any key on the matrix...")

while True:
    pressed_key = scan_keypad()
    if pressed_key:
        print(f"Detected Key: {pressed_key}")
    time.sleep_ms(30)  # Polling cycle interval


