from machine import Pin
import time
import json

button = Pin(10, Pin.IN, Pin.PULL_UP)

state1 = {
    "octave": 4,
    "key": "C",
    "sample": "Piano",
    "volume": 75,
    "keys": [True, False, True, False, False, False, True, False]
}

state2 = {
    "octave": 5,
    "key": "G",
    "sample": "Violin",
    "volume": 30,
    "keys": [False, True, False, True, True, False, False, True]
}

toggle = False

while True:
    # Wait for button press (active low)
    if button.value() == 0:

        if toggle:
            print(json.dumps(state1))
        else:
            print(json.dumps(state2))

        toggle = not toggle

        # Wait until button is released
        while button.value() == 0:
            time.sleep(0.01)

        # Debounce
        time.sleep(0.05)