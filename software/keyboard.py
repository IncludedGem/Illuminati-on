from machine import Pin
import time
import json

buttons = [
    Pin(15, Pin.IN, Pin.PULL_UP),
    Pin(14, Pin.IN, Pin.PULL_UP),
    Pin(13, Pin.IN, Pin.PULL_UP),
    Pin(12, Pin.IN, Pin.PULL_UP),
    Pin(11, Pin.IN, Pin.PULL_UP),
    Pin(10, Pin.IN, Pin.PULL_UP),
    Pin(9, Pin.IN, Pin.PULL_UP),
    Pin(8, Pin.IN, Pin.PULL_UP),
]

previous = [False] * len(buttons)

while True:
    # True = pressed, False = released
    keys = [button.value() == 0 for button in buttons]

    # Only print if something changed
    if keys != previous:
        state = {
            "octave": 5,
            "key": "F",
            "sample": "Violin",
            "volume": 30,
            "keys": keys
        }

        print(json.dumps(state))
        previous = keys[:]

    time.sleep_ms(10)
