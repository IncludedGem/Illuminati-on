import asyncio
import json

import serial
import websockets

# Configuration
SERIAL_PORT = "COM8"  # Windows: COM3/COM7/... | macOS: /dev/tty.usbmodem* | Linux: /dev/ttyACM0
BAUD_RATE = 115200
WEBSOCKET_URL = "ws://192.168.50.16:8765"

RECONNECT_DELAY = 1.5


def open_serial():
    """Open the serial port, retrying until the Pico shows up."""
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            print(f"Connected to {SERIAL_PORT} at {BAUD_RATE} baud")
            return ser
        except serial.SerialException as e:
            print(f"Serial error ({e}); retrying in {RECONNECT_DELAY}s...")
            print(f"Check that the Pico is plugged in and that {SERIAL_PORT} is correct.")


def read_line(ser):
    """Blocking read of one line. Called via asyncio.to_thread.

    readline() blocks for up to `timeout` seconds. Calling it directly from
    the event loop stalls everything else -- including the websocket's ping
    handling -- whenever the Pico goes quiet or sends a partial line.
    """
    try:
        return ser.readline().decode("utf-8", errors="ignore").strip()
    except serial.SerialException:
        return None  # Port vanished (unplugged)


def is_state_line(line):
    """Cheap client-side filter so firmware debug prints don't hit the wire."""
    stripped = line.lstrip("#").strip()
    if not stripped.startswith("{"):
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True


async def pump(ser, websocket):
    """Forward serial state lines to the websocket until something breaks."""
    while True:
        line = await asyncio.to_thread(read_line, ser)

        if line is None:
            raise serial.SerialException("Serial port closed")
        if not line:
            continue  # readline() timed out; loop again

        if not is_state_line(line):
            print(f"Skipped: {line}")
            continue

        print(f"Serial: {line}")
        await websocket.send(line)


async def bridge():
    ser = open_serial()
    try:
        while True:
            try:
                async with websockets.connect(WEBSOCKET_URL) as websocket:
                    print(f"Connected to WebSocket server at {WEBSOCKET_URL}")
                    await pump(ser, websocket)

            except (OSError, websockets.exceptions.WebSocketException) as e:
                # Server not up yet, or it restarted mid-session.
                print(f"WebSocket error ({e}); retrying in {RECONNECT_DELAY}s...")
                await asyncio.sleep(RECONNECT_DELAY)

            except serial.SerialException as e:
                print(f"Serial error ({e}); reopening port...")
                try:
                    ser.close()
                except Exception:
                    pass
                await asyncio.sleep(RECONNECT_DELAY)
                ser = open_serial()
    finally:
        try:
            ser.close()
        except Exception:
            pass
        print("Serial connection closed")


if __name__ == "__main__":
    try:
        asyncio.run(bridge())
    except KeyboardInterrupt:
        print("\nStopping...")