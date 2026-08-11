import asyncio
import json

import websockets

# Connected clients. This set holds BOTH the serial bridge (client.py) and
# any browsers, since they all speak the same ws:// endpoint. The bridge is
# the only one that ever sends; the browsers only listen.
connected_clients = set()

# Current instrument state. Mirrors the Pico's serial protocol (see main.py)
# so a browser that connects before the Pico has sent anything still gets a
# fully-shaped object instead of a partial one.
instrument = {
    "preset": 1,
    "octave": 4,
    "key": "C",
    "sample": "Sine",
    "mode": "Major",
    "volume": 50,
    "cutoff": 100,
    "keys": [False] * 8,
    "loop": "empty",
    "loop_pos": 0,
}


def parse_state_line(raw):
    """Return a state dict from a Pico line, or None if it isn't one.

    main.py prefixes state lines with '#'. Any other print() left in the
    firmware for debugging arrives on the same wire, so anything that
    doesn't look like a JSON object is dropped rather than logged as an
    error -- otherwise one stray print floods the console.
    """
    line = raw.strip()
    if line.startswith("#"):
        line = line[1:].strip()
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def broadcast(exclude=None):
    """Send the current instrument state to every connected client.

    `exclude` skips the sender -- no reason to echo state back to the
    serial bridge, which never reads its socket.
    """
    targets = [c for c in connected_clients if c is not exclude]
    if not targets:
        return

    message = json.dumps(instrument)

    # Snapshot + gather: sending inside a `for` over the live set can mutate
    # it mid-iteration when a client drops during an await.
    results = await asyncio.gather(
        *(client.send(message) for client in targets),
        return_exceptions=True,
    )

    for client, result in zip(targets, results):
        if isinstance(result, Exception):
            connected_clients.discard(client)


async def handle_client(websocket):
    print(f"Client connected: {websocket.remote_address}")
    connected_clients.add(websocket)

    try:
        # Send current state immediately so a late-joining browser isn't
        # blank until the next key press.
        await websocket.send(json.dumps(instrument))

        async for message in websocket:
            data = parse_state_line(message)
            if data is None:
                continue

            instrument.update(data)
            await broadcast(exclude=websocket)

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_clients.discard(websocket)
        print(f"Client disconnected: {websocket.remote_address}")


async def main():
    async with websockets.serve(handle_client, "0.0.0.0", 8765):
        print("WebSocket server running on ws://localhost:8765")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")