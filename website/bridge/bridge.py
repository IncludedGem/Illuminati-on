import asyncio
import serial
import websockets

# --- CONFIGURATION ---
# Change this to match your Pico's serial port!
# Windows example: 'COM3'
# Mac/Linux example: '/dev/ttyACM0' or '/dev/cu.usbmodem14101'
SERIAL_PORT = 'COM3' 
BAUD_RATE = 115200

# Keep track of connected React clients
connected_clients = set()

async def websocket_handler(websocket):
    """Handles new WebSocket connections from the React app."""
    connected_clients.add(websocket)
    try:
        # Keep the connection open until the client disconnects
        await websocket.wait_closed()
    finally:
        connected_clients.remove(websocket)

async def serial_reader():
    """Reads data from the Pico and broadcasts it to React."""
    try:
        # Open the serial port with a short timeout to prevent blocking
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        print(f"Successfully connected to Pico on {SERIAL_PORT}")
    except Exception as e:
        print(f"Failed to connect to serial port. Is the Pico plugged in? Error: {e}")
        return

    while True:
        # Check if there is data waiting from the Pico
        if ser.in_waiting:
            try:
                # Read the line, decode it, and strip any extra whitespace
                line = ser.readline().decode('utf-8').strip()
                
                # Look for our special '#' prefix
                if line.startswith('#'):
                    # Strip the '#' so it's perfectly valid JSON
                    json_data = line[1:] 
                    
                    # If React is connected, broadcast the JSON!
                    if connected_clients:
                        websockets.broadcast(connected_clients, json_data)
                        
            except Exception as e:
                print(f"Error reading from serial: {e}")
        
        # Yield control back to the async event loop briefly
        await asyncio.sleep(0.01) 

async def main():
    # Start the WebSocket server on port 8765
    async with websockets.serve(websocket_handler, "localhost", 8765):
        print("WebSocket server running at ws://localhost:8765")
        
        # Start reading the serial port
        await serial_reader()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBridge stopped by user.")