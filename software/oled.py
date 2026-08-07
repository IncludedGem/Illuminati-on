from machine import Pin, I2C
import ssd1306

# OLED setup
i2c = I2C(0, sda=Pin(4), scl=Pin(5))
display = ssd1306.SSD1306_I2C(128, 64, i2c)


def displayState(state):
    # Clear display
    display.fill(0)

    # Display state
    display.text("Octave: " + str(state["octave"]), 0, 0)
    display.text("Key: " + state["key"], 0, 8)
    display.text("Sample: " + state["sample"], 0, 16)
    display.text("Volume: " + str(state["volume"]), 0, 24)

    # Display pressed keys
    display.text("Keys:", 0, 32)

    key_string = "".join("1" if key else "0" for key in state["keys"])
    display.text(key_string, 40, 32)

    # Send to OLED
    display.show()


state1 = {
    "octave": 4,
    "key": "C#",
    "sample": "Piano",
    "volume": 75,
    "keys": [True, False, True, False, False, False, True, False]
}

# Display the state
displayState(state1)
