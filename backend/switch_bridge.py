"""Single-switch bridge: reads "DOWN"/"UP" lines from the Arduino (EMG sensor) over USB serial.

Only ONE program can open a COM port at a time, so the whole server shares one reader thread
(started by app.py when SWITCH_SERIAL_PORT is set). Every event is passed to the listeners;
app.py's SwitchHub forwards it to all open /ws/switch connections.
"""

import asyncio
import threading
import time

try:
    import serial                      # pyserial
except ImportError:                    # the server still runs (spacebar only) without pyserial
    serial = None

VALID_STATES = ("DOWN", "UP")
DEBOUNCE_S = 0.3        # a new DOWN within 300 ms of the last one is a bounce, not a press
RETRY_S = 3.0           # wait between attempts to open a missing or busy port
READ_TIMEOUT_S = 0.5    # readline gives up after this, so stop() is noticed quickly


def parse_line(raw) -> str | None:
    """b"DOWN\\r\\n" -> "DOWN". Anything else (noise, start-up text) -> None."""
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    text = (raw or "").strip().upper()
    return text if text in VALID_STATES else None


def open_serial_port(port: str, baud: int):
    """Opens the real port (tests pass their own fake instead)."""
    if serial is None:
        raise RuntimeError("pyserial is not installed: py -3.10 -m pip install pyserial")
    return serial.Serial(port, baud, timeout=READ_TIMEOUT_S)


class SwitchBridge:
    def __init__(self, port: str = "", baud: int = 9600, open_port=open_serial_port,
                 clock=time.monotonic, retry_s: float = RETRY_S, debounce_s: float = DEBOUNCE_S):
        self.port = port
        self.baud = baud
        self.open_port = open_port
        self.clock = clock                  # for the debounce (never jumps like the wall clock)
        self.retry_s = retry_s
        self.debounce_s = debounce_s
        self.listeners = []                 # functions called with each accepted event
        self.connected = False
        self.error = ""
        self.last_event = None
        self.last_event_time = None
        self._last_down = None              # clock() of the last accepted DOWN
        self._skip_next_up = False          # the UP that belongs to an ignored DOWN
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._serial = None

    @property
    def enabled(self) -> bool:
        return bool(self.port)

    def status(self) -> dict:
        return {"enabled": self.enabled, "connected": self.connected, "port": self.port,
                "baud": self.baud, "error": self.error, "last_event": self.last_event,
                "last_event_time": self.last_event_time}

    def handle_state(self, state: str) -> dict | None:
        """Debounces one DOWN/UP and tells the listeners. Returns the event, or None if ignored."""
        with self._lock:                    # the reader thread and /switch/simulate can both call this
            now = self.clock()
            if state == "DOWN":
                if self._last_down is not None and now - self._last_down < self.debounce_s:
                    self._skip_next_up = True
                    return None
                self._last_down = now
            elif state == "UP" and self._skip_next_up:
                self._skip_next_up = False
                return None
            event = {"state": state, "t": time.time()}
            self.last_event, self.last_event_time = state, event["t"]
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception as e:          # one broken listener must not stop the switch
                print(f"[switch] listener failed: {e}")
        return event

    def handle_line(self, raw) -> dict | None:
        state = parse_line(raw)
        return self.handle_state(state) if state else None

    def start(self):
        """Starts the reader thread (does nothing if no port is set)."""
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="switch-reader", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._close()

    def _close(self):
        port, self._serial = self._serial, None
        self.connected = False
        if port is not None:
            try:
                port.close()
            except Exception:
                pass

    def _run(self):
        """Open the port, read lines; on any problem close it, warn once and retry every few seconds."""
        last_warning = ""
        while not self._stop.is_set():
            try:
                self._serial = self.open_port(self.port, self.baud)
                self.connected, self.error = True, ""
                last_warning = ""
                print(f"[switch] connected on {self.port} at {self.baud} baud")
                while not self._stop.is_set():
                    line = self._serial.readline()      # b"" after READ_TIMEOUT_S with no data
                    if line:
                        self.handle_line(line)
            except Exception as e:          # missing, busy (Serial Monitor open) or unplugged
                self.error = describe_error(self.port, e, was_connected=self.connected)
                if self.error != last_warning:          # don't repeat the same warning every 3 s
                    print(f"[switch] WARNING: {self.error} Retrying every {self.retry_s:g} s.")
                    last_warning = self.error
            finally:
                self._close()
            self._stop.wait(self.retry_s)


def describe_error(port: str, error: Exception, was_connected: bool = False) -> str:
    """A message a caregiver can act on. (pyserial on Windows says e.g.
    "could not open port 'COM3': PermissionError(13, 'Access is denied.')" when it is busy.)"""
    text = str(error)
    if was_connected:
        return f"Lost the switch on {port} (unplugged?). ({text})"
    if "PermissionError" in text or "Access is denied" in text:
        return (f"{port} is busy. Close the Arduino Serial Monitor (or any other program "
                f"using it). ({text})")
    if "FileNotFoundError" in text or "No such file" in text:
        return f"{port} not found. Is the Arduino plugged in, and is the port name right? ({text})"
    return f"Could not use {port}. ({text})"


class SwitchHub:
    """Hands events from the reader thread to every connected websocket.
    Each client has its own asyncio queue; call_soon_threadsafe puts the event in from any thread."""

    def __init__(self):
        self._clients = {}                  # queue -> the event loop that reads it
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue()
        with self._lock:
            self._clients[queue] = asyncio.get_running_loop()
        return queue

    def unsubscribe(self, queue):
        with self._lock:
            self._clients.pop(queue, None)

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, event: dict):
        with self._lock:
            clients = list(self._clients.items())
        for queue, loop in clients:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:            # that client's loop has closed
                self.unsubscribe(queue)
