import asyncio
import serial
import websockets
import json
from datetime import datetime

# Configuration
SERIAL_PORT = '/dev/cu.usbmodem1201'  # Change to 'COM3' on Windows, '/dev/ttyUSB0' for other boards
BAUD_RATE = 115200
WEBSOCKET_URL = 'ws://localhost:8765'


async def read_serial_and_send():
    """Read from serial port and send to WebSocket server"""
    try:
        # Open serial connection
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"Connected to {SERIAL_PORT} at {BAUD_RATE} baud")
        # Connect to WebSocket server
        async with websockets.connect(WEBSOCKET_URL) as websocket:
            print(f"Connected to WebSocket server at {WEBSOCKET_URL}")
            try:
                while True:
                    # Read line from serial
                    if ser.in_waiting:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            print(f"Serial: {line}")
                            # Send to WebSocket server
                            await websocket.send(line)
                    # Small delay to prevent CPU spinning
                    await asyncio.sleep(0.01)
            except KeyboardInterrupt:
                print("\nStopping...")
            finally:
                ser.close()
                print("Serial connection closed")
    except FileNotFoundError:
        print(f"Error: Could not find serial port {SERIAL_PORT}")
        print("Make sure your RPi Pico is connected and the port is correct.")
    except serial.SerialException as e:
        print(f"Serial Error: {e}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(read_serial_and_send())
    except KeyboardInterrupt:
        print("Interrupted")
