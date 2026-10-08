"""Shared helpers for the hardware tools: find the Arduino's COM port, open it with a clear
error, and compile/upload sketches with arduino-cli.

`serial` is looked up when a function runs (not copied at import), so tests can replace it.
"""

import os
import shutil
import subprocess
import tempfile

import serial
import serial.tools.list_ports

FQBN = "arduino:avr:uno"
HARDWARE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "hardware")
SWITCH_SKETCH = os.path.abspath(os.path.join(HARDWARE_DIR, "switch_emg.ino"))

# USB ids of genuine Arduinos and the CH340 chip used by most Uno clones
ARDUINO_VIDS = {0x2341, 0x2A03, 0x1A86}
ARDUINO_WORDS = ("arduino", "ch340", "usb-serial", "usb serial")


class PortError(Exception):
    """A port problem, with a message that says what to do."""


def arduino_ports(ports=None):
    """COM ports that look like an Arduino (or CH340 clone)."""
    ports = serial.tools.list_ports.comports() if ports is None else ports
    found = []
    for p in ports:
        text = f"{p.description or ''} {p.manufacturer or ''}".lower()
        if p.vid in ARDUINO_VIDS or any(word in text for word in ARDUINO_WORDS):
            found.append(p.device)
    return found


def choose_port(ask=input, ports=None):
    """The one Arduino port, or asks which one (or for a name, if none was found)."""
    found = arduino_ports(ports)
    if len(found) == 1:
        print(f"Found Arduino on {found[0]}")
        return found[0]
    if found:
        answer = ask(f"Several Arduino-like ports: {', '.join(found)}. Which one? ").strip()
        return answer or found[0]
    answer = ask("No Arduino found. Is it plugged in? Type its port (e.g. COM3), or Enter to quit: ").strip()
    if not answer:
        raise PortError("No port given.")
    return answer


def port_error_message(port, error):
    """Busy / missing / other, in words a caregiver can act on."""
    text = str(error)
    if "PermissionError" in text or "Access is denied" in text:
        return (f"{port} is busy: another program has it open. Close the Arduino Serial Monitor, "
                f"stop the ReVoice backend if SWITCH_SERIAL_PORT={port} is set in backend/.env, "
                f"and stop any other twitch_to_space.py / emg_calibrate.py. ({text})")
    if "FileNotFoundError" in text or "No such file" in text:
        return f"{port} not found. Check the USB cable and the port name (Device Manager -> Ports). ({text})"
    return f"Could not open {port}. ({text})"


def open_port(port, baud, timeout=0.1):
    try:
        return serial.Serial(port, baud, timeout=timeout)
    except (serial.SerialException, OSError) as e:
        raise PortError(port_error_message(port, e)) from e


def find_arduino_cli():
    """arduino-cli on PATH, or where the winget installer puts it (PATH updates need a new terminal)."""
    for candidate in (shutil.which("arduino-cli"),
                      r"C:\Program Files\Arduino CLI\arduino-cli.exe",
                      os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\arduino-cli.exe")):
        if candidate and os.path.isfile(candidate):
            return candidate
    raise PortError("arduino-cli not found. Install it: winget install ArduinoSA.CLI, "
                    "then: arduino-cli core install arduino:avr")


def stage_sketch(name, source_text):
    """arduino-cli needs <name>/<name>.ino, so the sketch is written into a temporary folder."""
    folder = os.path.join(tempfile.mkdtemp(prefix="revoice_sketch_"), name)
    os.makedirs(folder)
    with open(os.path.join(folder, name + ".ino"), "w", encoding="utf-8") as f:
        f.write(source_text)
    return folder


def run_cli(args, run=subprocess.run):
    cli = find_arduino_cli()
    result = run([cli, *args], capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PortError(f"arduino-cli {args[0]} failed:\n{detail}")
    return result.stdout


def compile_sketch(folder, run=subprocess.run):
    return run_cli(["compile", "--fqbn", FQBN, folder], run)


def upload_sketch(folder, port, run=subprocess.run):
    """Compiles then uploads. Upload fails if anything else has the port open."""
    compile_sketch(folder, run)
    try:
        return run_cli(["upload", "-p", port, "--fqbn", FQBN, folder], run)
    except PortError as e:
        raise PortError(f"{e}\n\nIf it says the port is busy or access is denied: "
                        + port_error_message(port, "PermissionError").split(" (")[0]) from e
