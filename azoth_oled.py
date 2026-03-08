#!/usr/bin/env python3
"""ROG Azoth X OLED Control - Reverse Engineering Tool

Known display modes and commands discovered via blind probing.
Run with sudo for HID access.
"""

import hid
import time
import sys

VENDOR_ID = 0x0B05
PRODUCT_ID_WIRED = 0x1C24
PRODUCT_ID_RECEIVER = 0x1ACE
PRODUCT_ID_BT = 0x1C27

VENDOR_INTERFACE_WIRED = 1
VENDOR_INTERFACE_RECEIVER = 2

# Discovered OLED display modes
OLED_MODES = {
    "animation": 0x61,   # Default ROG globe animation
    "dashboard": 0x63,   # Date/time/stats (needs host data)
    "sysmon":    0x64,   # System monitor CPU/GPU/temp
    "mail":      0x65,   # Notifications / mail
    "cpu":       0x66,   # CPU usage display
    "unknown":   0x68,   # Blank/custom image slot?
    # "off":     0x69,   # OLED SHUTDOWN - requires MCU reset to recover!
}

# Other known commands
CMD_DEVICE_INFO = 0x12
CMD_PROFILE = 0x51

# DANGEROUS - do not use
DANGEROUS_CMDS = [0x69, 0xFC, 0xFD, 0xFE, 0xFF]


def find_device():
    """Find and open the Azoth X vendor HID interface."""
    # Try wired first
    for d in hid.enumerate(VENDOR_ID, PRODUCT_ID_WIRED):
        if d['interface_number'] == VENDOR_INTERFACE_WIRED:
            dev = hid.device()
            dev.open_path(d['path'])
            print(f"Connected: {dev.get_product_string()} (wired)")
            return dev, "wired"

    # Try receiver
    for d in hid.enumerate(VENDOR_ID, PRODUCT_ID_RECEIVER):
        if d['interface_number'] == VENDOR_INTERFACE_RECEIVER:
            dev = hid.device()
            dev.open_path(d['path'])
            print(f"Connected: {dev.get_product_string()} (receiver)")
            return dev, "receiver"

    print("ERROR: Azoth X not found. Is it plugged in?")
    sys.exit(1)


def send_cmd(dev, cmd_bytes, connection="wired"):
    """Send a command and return the response."""
    if connection == "wired":
        # Wired: no report ID, prepend 0x00 for HID write
        packet = [0x00] + cmd_bytes + [0x00] * (63 - len(cmd_bytes))
    else:
        # Receiver: uses report ID 0x02 for vendor commands
        packet = [0x02] + cmd_bytes + [0x00] * (62 - len(cmd_bytes))

    dev.set_nonblocking(1)
    # Drain pending reads
    while dev.read(65):
        pass

    dev.write(packet)
    time.sleep(0.2)
    data = dev.read(65)

    if data and data[0] == 0xFF and data[1] == 0xAA:
        # Heartbeat, try one more read
        data = dev.read(65)

    return data


def get_device_info(dev, connection="wired"):
    """Query device info."""
    data = send_cmd(dev, [CMD_DEVICE_INFO], connection)
    if data:
        print(f"Device info: {' '.join(f'{b:02x}' for b in data[:16])}")
        # Firmware version appears to be at bytes 8-10
        if connection == "wired":
            fw = f"{data[4]}.{data[5]}.{data[6]}"
        else:
            fw = f"{data[5]}.{data[6]}.{data[7]}"
        print(f"Firmware: {fw}")
    return data


def set_oled_mode(dev, mode_name, connection="wired"):
    """Switch OLED display mode."""
    if mode_name not in OLED_MODES:
        print(f"Unknown mode: {mode_name}")
        print(f"Available: {', '.join(OLED_MODES.keys())}")
        return None

    cmd = OLED_MODES[mode_name]
    if cmd in DANGEROUS_CMDS:
        print(f"REFUSING to send dangerous command 0x{cmd:02X}")
        return None

    data = send_cmd(dev, [cmd], connection)
    print(f"OLED mode -> {mode_name} (0x{cmd:02X})")
    return data


def main():
    if len(sys.argv) < 2:
        print("Usage: sudo python3 azoth_oled.py <command>")
        print()
        print("Commands:")
        print("  info              - Query device info")
        print("  mode <name>       - Switch OLED mode")
        print(f"  modes             - List available modes")
        print("  probe <hex>       - Send raw command byte")
        print("  cycle             - Cycle through all safe modes")
        return

    dev, connection = find_device()
    cmd = sys.argv[1]

    if cmd == "info":
        get_device_info(dev, connection)

    elif cmd == "modes":
        for name, byte in OLED_MODES.items():
            print(f"  {name:12s} = 0x{byte:02X}")

    elif cmd == "mode" and len(sys.argv) > 2:
        set_oled_mode(dev, sys.argv[2], connection)

    elif cmd == "probe" and len(sys.argv) > 2:
        byte = int(sys.argv[2], 16)
        if byte in DANGEROUS_CMDS:
            print(f"REFUSING dangerous command 0x{byte:02X}")
        else:
            data = send_cmd(dev, [byte], connection)
            if data:
                print(f"0x{byte:02X} -> {' '.join(f'{b:02x}' for b in data[:24])}")
            else:
                print(f"0x{byte:02X} -> no response")

    elif cmd == "cycle":
        safe_modes = [m for m in OLED_MODES if OLED_MODES[m] not in DANGEROUS_CMDS]
        for mode_name in safe_modes:
            set_oled_mode(dev, mode_name, connection)
            time.sleep(3)
        # Return to animation
        set_oled_mode(dev, "animation", connection)

    else:
        print(f"Unknown command: {cmd}")

    dev.close()


if __name__ == "__main__":
    main()
