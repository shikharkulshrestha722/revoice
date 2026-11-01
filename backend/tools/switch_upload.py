"""Compiles and uploads a ReVoice switch sketch with arduino-cli.

    cd backend
    py -3.10 tools/switch_upload.py --board uno --port COM3        # hardware/switch_ir (wired IR)
    py -3.10 tools/switch_upload.py --board esp32 --port COM7      # hardware/switch_esp32_ble (Bluetooth)
    py -3.10 tools/switch_upload.py --board esp32 --compile-only   # just check that it builds

Change the settings at the top of the sketch first (sensor type, TWO_SWITCH, MIN_HOLD_MS ...).
The ESP32 sketch needs core esp32:esp32 (3.x) and the libraries "ESP32 BLE Keyboard" (T-vK) and
"NimBLE-Arduino" 1.4.3; it is built with -DUSE_NIMBLE (see hardware/HARDWARE.md).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import serial_tools                           # noqa: E402

BOARDS = {
    "uno": {"folder": serial_tools.IR_SKETCH_DIR, "fqbn": serial_tools.FQBN, "props": ()},
    "esp32": {"folder": serial_tools.ESP32_SKETCH_DIR, "fqbn": serial_tools.ESP32_FQBN,
              "props": serial_tools.ESP32_BUILD_PROPERTIES},
}

ESP32_LIBRARIES = {"ESP32 BLE Keyboard": "arduino-cli lib install --git-url https://github.com/T-vK/ESP32-BLE-Keyboard.git"
                                         "   (first: arduino-cli config set library.enable_unsafe_install true)",
                   "NimBLE-Arduino": 'arduino-cli lib install "NimBLE-Arduino@1.4.3"'}
ESP32_CORE_HELP = ("arduino-cli config add board_manager.additional_urls "
                   "https://espressif.github.io/arduino-esp32/package_esp32_index.json\n"
                   "arduino-cli core update-index\narduino-cli core install esp32:esp32")


def missing_esp32_parts(lib_list_text, core_list_text):
    """What still has to be installed for the ESP32 sketch, as install commands."""
    missing = [cmd for name, cmd in ESP32_LIBRARIES.items() if name not in lib_list_text]
    if "esp32:esp32" not in core_list_text:
        missing.insert(0, ESP32_CORE_HELP)
    return missing


def main(argv=None, run=None):
    parser = argparse.ArgumentParser(description="Compile and upload a ReVoice switch sketch")
    parser.add_argument("--board", choices=sorted(BOARDS), required=True)
    parser.add_argument("--port", help="e.g. COM3 (default: find the Arduino / CH340 port; ESP32: give it)")
    parser.add_argument("--compile-only", action="store_true")
    args = parser.parse_args(argv)
    board = BOARDS[args.board]
    kwargs = {"run": run} if run else {}
    try:
        if args.board == "esp32":
            missing = missing_esp32_parts(serial_tools.run_cli(["lib", "list"], **kwargs),
                                          serial_tools.run_cli(["core", "list"], **kwargs))
            if missing:
                print("The ESP32 sketch needs these first:\n\n" + "\n\n".join(missing))
                return 1
        if args.compile_only:
            print(serial_tools.compile_sketch(board["folder"], fqbn=board["fqbn"],
                                              build_properties=board["props"], **kwargs))
            return 0
        port = args.port or serial_tools.choose_port()
        print(f"Compiling and uploading {os.path.basename(board['folder'])} to {port}...")
        print(serial_tools.upload_sketch(board["folder"], port, fqbn=board["fqbn"],
                                         build_properties=board["props"], **kwargs))
        print("Done.")
        return 0
    except serial_tools.PortError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
