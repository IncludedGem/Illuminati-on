from machine import Pin, I2C, ADC
import ssd1306
import time
import json


# =========================
# BUTTON SETUP
# =========================

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

previous_keys = [False] * len(buttons)


# =========================
# VOLUME SETUP
# =========================

adc = ADC(26)

previous_volume = -1


# =========================
# OLED SETUP
# =========================

i2c = I2C(0, sda=Pin(4), scl=Pin(5))

display = ssd1306.SSD1306_I2C(
    128,
    64,
    i2c
)


# =========================
# INSTRUMENT STATE
# =========================

state = {
    "octave": 5,
    "key": "F",
    "sample": "Violin",
    "volume": 30,
    "keys": [False] * 8
}


# =========================
# OLED DISPLAY FUNCTION
# =========================

def displayState(state):

    # Clear display
    display.fill(0)

    # Instrument information
    display.text("Octave: " + str(state["octave"]), 0, 0)
    display.text("Key: " + state["key"], 0, 8)
    display.text("Sample: " + state["sample"], 0, 16)
    display.text("Volume: " + str(state["volume"]), 0, 24)

    # Pressed keys
    display.text("Keys:", 0, 32)

    key_string = "".join(
        "1" if key else "0"
        for key in state["keys"]
    )

    display.text(key_string, 40, 32)

    # Update OLED
    display.show()


# =========================
# INITIAL DISPLAY
# =========================

displayState(state)


# =========================
# MAIN LOOP
# =========================

while True:

    changed = False


    # -------------------------
    # Read buttons
    # -------------------------

    keys = [
        button.value() == 0
        for button in buttons
    ]

    if keys != previous_keys:

        state["keys"] = keys[:]

        previous_keys = keys[:]

        changed = True


    # -------------------------
    # Read volume
    # -------------------------

    total = 0

    # Take 10 readings and average them
    for _ in range(10):
        total += adc.read_u16()
        time.sleep_ms(2)

    raw_volume = total // 10

    # Convert 0-65535 to 0-100
    volume = round(raw_volume / 65535 * 100)


    # -------------------------
    # Volume deadband
    # -------------------------

    # Only update if volume changes
    # by at least 2%
    if previous_volume == -1:
        previous_volume = volume
        state["volume"] = volume
        changed = True

    elif abs(volume - previous_volume) >= 2:

        previous_volume = volume
        state["volume"] = volume

        changed = True


    # -------------------------
    # Update OLED + JSON
    # -------------------------

    if changed:

        # Update OLED
        displayState(state)

        # Send state as JSON
        print(json.dumps(state))


    # Small delay
    time.sleep_ms(10)

