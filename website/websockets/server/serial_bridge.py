"""Serial-to-WebSocket bridge -- HAcK 2026, Team 13.

Reads the Pico's USB serial output and forwards its state updates to
server.py, which then broadcasts them to every connected browser tab.

This is a SEPARATE process from server.py on purpose: a USB replug or a
Pico reboot mid-performance should only interrupt this bridge's own
reconnect loop, never take down the WebSocket hub or drop browser
clients that are already connected.

PROTOCOL (see main.py's module docstring, "SERIAL PROTOCOL"):
Every state change is one line: '#' + JSON. A looping performance also
gets a heartbeat line every ~100 ms so the website can animate loop
position. Anything NOT starting with '#' is human debug output (the
startup banner, "[debug] ..." keypress chatter when DEBUG=True in
main.py) and must be dropped here, not forwarded -- forwarding it would
hand server.py a line that fails json.loads() and gets logged as
"Invalid JSON received," or worse, silently confuses a downstream
consumer that isn't expecting stray text.

This script is a client of server.py's WebSocket, exactly like a
browser tab -- it does NOT run its own server. Run server.py first,
then this.

Requires: pip install pyserial websockets
"""

import asyncio
import json
import sys

import serial
import serial.tools.list_ports
import websockets

# --- Config -----------------------------------------------------------

# None = auto-detect (see find_pico_port()). Set explicitly if
# auto-detect ever guesses wrong on your machine, e.g.:
#   SERIAL_PORT = "/dev/ttyACM0"   # Linux
#   SERIAL_PORT = "COM5"           # Windows
SERIAL_PORT = None

BAUD_RATE = 115200          # MicroPython's USB-CDC serial ignores the
                             # actual baud rate, but pyserial requires
                             # a value be passed
SERVER_URL = "ws://localhost:8765"

# How long to wait before retrying after the Pico disconnects (unplugged,
# reset, firmware reload) or the WebSocket server isn't up yet.
RECONNECT_DELAY_S = 2.0


def find_pico_port():
    """Best-effort auto-detect: look for a USB serial device whose
    description mentions the Pico's known identifiers. Falls back to
    None (caller must set SERIAL_PORT manually) if nothing matches --
    deliberately does NOT guess the first port on the list, since
    guessing wrong means silently reading from the wrong device."""
    for port in serial.tools.list_ports.comports():
        text = f"{port.description} {port.manufacturer or ''} {port.hwid}".lower()
        if "pico" in text or "2e8a" in text.lower():   # 2e8a = Raspberry Pi Ltd VID
            return port.device
    return None


def is_state_line(line):
    """True if `line` is a state update per the documented protocol.
    Everything else (startup banner, [debug] chatter, blank lines) is
    human-readable output and must NOT be forwarded."""
    return line.startswith("#")


async def read_serial_lines(ser, queue):
    """Blocking pyserial reads happen in a thread via to_thread, so they
    never stall the asyncio event loop that also owns the WebSocket
    connection. Pushes each decoded line onto `queue` for the sender
    coroutine to pick up."""
    loop = asyncio.get_running_loop()
    while True:
        raw = await loop.run_in_executor(None, ser.readline)
        if not raw:
            continue   # read timeout with nothing new; keep waiting
        try:
            line = raw.decode("utf-8", errors="replace").strip()
        except Exception as e:
            print(f"[bridge] decode error, skipping line: {e}")
            continue
        if line:
            await queue.put(line)


async def forward_to_server(queue):
    """Pull lines off the queue, keep only #-prefixed state lines,
    parse them, and send the parsed JSON to server.py. Reconnects to
    the WebSocket server on its own schedule, independent of the
    serial read loop above.

    If the connection drops between queue.get() and ws.send()
    succeeding, that one line is lost rather than requeued -- acceptable
    here since every line is a full state snapshot (or a heartbeat that
    repeats ~every 100ms while looping), so the next one supersedes it.
    Worth revisiting if a future field is ever an incremental delta
    instead of a full snapshot."""
    last_error_logged = 0.0

    while True:
        try:
            async with websockets.connect(SERVER_URL) as ws:
                print(f"[bridge] connected to {SERVER_URL}")
                while True:
                    line = await queue.get()

                    if not is_state_line(line):
                        # Human debug output -- print locally, don't forward.
                        print(f"[pico] {line}")
                        continue

                    payload = line[1:]   # drop the leading '#'
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        # A state line that doesn't parse means either a
                        # torn read (partial write straddling two
                        # readline() calls) or a firmware bug -- either
                        # way, forwarding broken JSON just pushes the
                        # failure downstream to server.py's own
                        # JSONDecodeError handler. Drop it here instead,
                        # where the raw line is still visible for
                        # debugging.
                        print(f"[bridge] malformed state line, dropped: {payload!r}")
                        continue

                    await ws.send(json.dumps(data))

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            now = asyncio.get_event_loop().time()
            if now - last_error_logged > 10:
                print(f"[bridge] server connection lost ({e}); "
                      f"retrying every {RECONNECT_DELAY_S}s until it's back")
                last_error_logged = now
            await asyncio.sleep(RECONNECT_DELAY_S)


async def main():
    port = SERIAL_PORT or find_pico_port()
    if port is None:
        print("[bridge] could not auto-detect the Pico's serial port.")
        print("[bridge] set SERIAL_PORT at the top of this file, e.g.:")
        print('           SERIAL_PORT = "/dev/ttyACM0"   # Linux/Mac')
        print('           SERIAL_PORT = "COM5"            # Windows')
        sys.exit(1)

async def read_serial_forever(port, queue):
    """Owns the serial connection's own retry loop, independent of the
    WebSocket side. A USB unplug only restarts THIS loop -- it never
    tears down or rebuilds the WebSocket connection in
    forward_to_server, which keeps running (and keeps its own backoff)
    the whole time."""
    last_error_logged = 0.0

    while True:
        try:
            # timeout=1 keeps readline() from blocking forever, so a
            # USB unplug surfaces as an OSError promptly instead of
            # hanging the read thread indefinitely.
            with serial.Serial(port, BAUD_RATE, timeout=1) as ser:
                print(f"[bridge] reading from {port}")
                await read_serial_lines(ser, queue)
        except (serial.SerialException, OSError) as e:
            now = asyncio.get_event_loop().time()
            # Rate-limit the log: a port that's genuinely gone fails
            # instantly on every retry, and logging every single 2s
            # attempt floods the terminal for as long as it's unplugged.
            if now - last_error_logged > 10:
                print(f"[bridge] serial connection lost ({e}); "
                      f"retrying every {RECONNECT_DELAY_S}s until it's back")
                last_error_logged = now
            await asyncio.sleep(RECONNECT_DELAY_S)


async def main():
    port = SERIAL_PORT or find_pico_port()
    if port is None:
        print("[bridge] could not auto-detect the Pico's serial port.")
        print("[bridge] set SERIAL_PORT at the top of this file, e.g.:")
        print('           SERIAL_PORT = "/dev/ttyACM0"   # Linux/Mac')
        print('           SERIAL_PORT = "COM5"            # Windows')
        sys.exit(1)

    queue = asyncio.Queue()

    # Two independent long-running loops, each with its own retry
    # policy: one for the Pico (serial), one for server.py (WebSocket).
    # Neither restarts the other -- a serial hiccup doesn't reset an
    # otherwise-healthy WebSocket connection, and vice versa.
    await asyncio.gather(
        read_serial_forever(port, queue),
        forward_to_server(queue),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[bridge] stopped")
