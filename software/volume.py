from machine import ADC
import time

adc = ADC(26)

while True:
    total = 0

    for _ in range(10):
        total += adc.read_u16()
        time.sleep_ms(2)

    rawVol = total // 10
    volume = round(rawVol / 65535 * 100)

    print("ADC:", rawVol, "Volume:", volume)

    time.sleep(0.1)
