from machine import Pin, I2S
import time

audio = I2S(
    0,
    sck=Pin(16),
    ws=Pin(17),
    sd=Pin(18),
    mode=I2S.TX,
    bits=16,
    format=I2S.MONO,
    rate=44100,
    ibuf=8192
)

with open("song.wav", "rb") as f:
    # Skip the 44-byte WAV header
    f.read(44)

    while True:
        data = f.read(4096)
        if not data:
            break
        audio.write(data)

audio.deinit()
