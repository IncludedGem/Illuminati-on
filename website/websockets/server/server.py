import asyncio
import json
import websockets

# Connected website clients
connected_clients = set()

# Current instrument state
instrument = {
    "octave": 4,
    "key": "C",
    "sample": "Grand Piano",
    "volume": 50,
    "keys": [False] * 8
}


async def broadcast():
    """Send the current instrument state to every connected client."""

    if not connected_clients:
        return

    message = json.dumps(instrument)

    disconnected = set()

    for client in connected_clients:
        try:
            await client.send(message)
        except websockets.exceptions.ConnectionClosed:
            disconnected.add(client)

    connected_clients.difference_update(disconnected)


async def handle_client(websocket):
    print(f"Client connected: {websocket.remote_address}")

    connected_clients.add(websocket)

    # Send the current state immediately when a client connects
    await websocket.send(json.dumps(instrument))

    try:
        async for message in websocket:
            print("Received:", message)

            try:
                # Parse JSON received from the serial client
                data = json.loads(message)

                # Update the current instrument state
                instrument.update(data)

                # Broadcast the new state to every connected client
                await broadcast()

            except json.JSONDecodeError:
                print("Invalid JSON received:")
                print(message)

    except websockets.exceptions.ConnectionClosed:
        print(f"Client disconnected: {websocket.remote_address}")

    finally:
        connected_clients.discard(websocket)


async def main():
    async with websockets.serve(handle_client, "localhost", 8765):
        print("WebSocket server running on ws://localhost:8765")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(main())