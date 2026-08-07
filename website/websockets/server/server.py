import asyncio
import websockets
import json
from datetime import datetime

# Store all messages
chat_logs = []
# Store all connected clients
connected_clients = set()


def print_chat_logs():
    print("\n" + "="*60)
    print("CHAT LOGS")
    print("="*60)
    if not chat_logs:
        print("No messages yet")
    else:
        for i, log in enumerate(chat_logs, 1):
            print(f"{i}. [{log['timestamp']}] {log['message']}")
    print("="*60 + "\n")


async def handle_client(websocket):
    print(f"Client connected from {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Store in chat logs
            chat_logs.append({
                "message": message,
                "timestamp": timestamp
            })
            print(f"Received: {message}")
            # Broadcast the message to all connected clients
            response = {
                "status": "received",
                "original_message": message,
                "timestamp": timestamp
            }
            # Send to all connected clients
            disconnected = set()
            for client in connected_clients:
                try:
                    await client.send(json.dumps(response))
                except websockets.exceptions.ConnectionClosed:
                    disconnected.add(client)
            # Remove disconnected clients
            for client in disconnected:
                connected_clients.discard(client)
    except websockets.exceptions.ConnectionClosed:
        print(f"Client {websocket.remote_address} disconnected")
    finally:
        connected_clients.discard(websocket)


async def main():
    async with websockets.serve(handle_client, "localhost", 8765):
        print("WebSocket server started on ws://localhost:8765")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted")
