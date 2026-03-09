#!/usr/bin/env python3
"""
Corsair Vanguard 96 LCD — Software Mode Write Test

Switches to software mode (handles USB reset/reconnect),
writes framebuffer, tries commit, takes photos.
"""

import os
import sys
import time
import struct
import subprocess
import glob

BRAGI_MAGIC = 0x08
OUT_PKT_SIZE = 64
LCD_SIZE = 84320
RES_LCD = 0x3F
HANDLE = 0x01

CMD_SET = 0x01
CMD_GET = 0x02
CMD_CLOSE = 0x05
CMD_WRITE = 0x06
CMD_READ = 0x08
CMD_PROBE = 0x09
CMD_OPEN = 0x0D

VENDOR_ID = "1b1c"
PRODUCT_ID = "2b0d"


def find_hidraw():
    """Find the Bragi interface hidraw device (interface 2)."""
    for h in sorted(glob.glob("/dev/hidraw*")):
        name = h.split("/")[-1]
        try:
            uevent = open(f"/sys/class/hidraw/{name}/device/uevent").read()
            if "VANGUARD" in uevent and "input2" in uevent:
                return h
        except:
            pass
    return None


def wait_for_device(timeout=10):
    """Wait for the Corsair keyboard to appear on USB and return hidraw path."""
    print(f"  Waiting for device to reconnect (up to {timeout}s)...")
    end = time.time() + timeout
    while time.time() < end:
        h = find_hidraw()
        if h:
            # Fix permissions
            subprocess.run(["sudo", "chmod", "666", h], capture_output=True)
            time.sleep(0.5)  # Let it settle
            print(f"  Device found: {h}")
            return h
        time.sleep(0.5)
    return None


def send_recv(fd, data, timeout=1.0):
    pad = max(0, OUT_PKT_SIZE - len(data))
    pkt = bytes([0x00]) + bytes(data) + bytes(pad)
    os.write(fd, pkt)
    end = time.time() + timeout
    while time.time() < end:
        try:
            return os.read(fd, 1024)
        except BlockingIOError:
            time.sleep(0.01)
    return None


def status_ok(resp):
    return resp is not None and len(resp) > 2 and resp[2] == 0x00


def get_mode(fd):
    resp = send_recv(fd, [BRAGI_MAGIC, CMD_GET, 0x01, 0x00])
    if resp and len(resp) > 3 and resp[2] == 0x00:
        return resp[3]
    return None


def set_mode(fd, mode):
    resp = send_recv(fd, [BRAGI_MAGIC, CMD_SET, 0x01, 0x00, mode])
    return status_ok(resp)


def open_lcd(fd):
    send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00], timeout=0.2)
    time.sleep(0.1)
    resp = send_recv(fd, [BRAGI_MAGIC, CMD_OPEN, HANDLE, RES_LCD, 0x00, 0x00])
    return status_ok(resp)


def close_lcd(fd):
    send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00])


def write_lcd(fd, data):
    total = len(data)
    first_pkt = [BRAGI_MAGIC, CMD_WRITE, HANDLE]
    first_pkt += list(struct.pack('<I', total))
    chunk_size = min(total, OUT_PKT_SIZE - 7)
    first_pkt += list(data[:chunk_size])
    resp = send_recv(fd, first_pkt)
    if not status_ok(resp):
        print(f"  First write failed: {resp[2]:02x}" if resp else "  No response")
        return False

    offset = chunk_size
    pkt_count = 1
    while offset < total:
        chunk = min(total - offset, OUT_PKT_SIZE - 3)
        cont_pkt = [BRAGI_MAGIC, CMD_WRITE, HANDLE]
        cont_pkt += list(data[offset:offset + chunk])
        resp = send_recv(fd, cont_pkt)
        if not status_ok(resp):
            print(f"  Write at {offset} failed")
            return False
        offset += chunk
        pkt_count += 1
        if pkt_count % 300 == 0:
            print(f"    {offset}/{total} bytes ({100*offset//total}%)")

    print(f"  Write complete: {pkt_count} packets")
    return True


def take_photo(name):
    """Take a webcam photo."""
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")
    return path


def main():
    color = sys.argv[1] if len(sys.argv) > 1 else "red"

    # Generate framebuffer
    colors = {
        "red": 0xF800, "blue": 0x001F, "green": 0x07E0,
        "white": 0xFFFF, "black": 0x0000, "cyan": 0x07FF,
        "magenta": 0xF81F, "yellow": 0xFFE0,
    }
    if color in colors:
        pixel = struct.pack('<H', colors[color])
        fb = pixel * (LCD_SIZE // 2)
    elif color == "allff":
        fb = bytes([0xFF] * LCD_SIZE)
    elif color == "all00":
        fb = bytes([0x00] * LCD_SIZE)
    else:
        pixel = struct.pack('<H', 0xF800)
        fb = pixel * (LCD_SIZE // 2)

    print(f"=== LCD Software Mode Write Test ===")
    print(f"Color: {color}")
    print(f"Time: {time.strftime('%H:%M:%S')}")

    # Find device
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")

    # Take before photo
    print("\n[1] Before photo...")
    take_photo("sw_mode_before")

    # Open device and get mode
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    orig_mode = get_mode(fd)
    print(f"\n[2] Current mode: {orig_mode}")

    if orig_mode == 1:
        print("  Already in software mode!")
    else:
        # Switch to software mode
        print("\n[3] Switching to software mode (SET MODE=1)...")
        set_mode(fd, 0x01)
        os.close(fd)

        # Device will USB-reset. Wait for reconnect.
        time.sleep(2.0)
        hidraw = wait_for_device(timeout=15)
        if not hidraw:
            print("  ERROR: Device did not reconnect!")
            return

        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        new_mode = get_mode(fd)
        print(f"  Mode after reconnect: {new_mode}")

    # Take photo in SW mode (LCD might already change)
    print("\n[4] Photo after mode switch...")
    take_photo("sw_mode_active")

    # Open LCD resource
    print("\n[5] Opening LCD resource 0x3F...")
    if not open_lcd(fd):
        print("  OPEN FAILED")
        set_mode(fd, orig_mode or 4)
        os.close(fd)
        return
    print("  LCD opened")

    # Write framebuffer
    print(f"\n[6] Writing {color} framebuffer ({LCD_SIZE} bytes)...")
    t0 = time.time()
    if not write_lcd(fd, fb):
        print("  WRITE FAILED")
        close_lcd(fd)
        set_mode(fd, orig_mode or 4)
        os.close(fd)
        return
    elapsed = time.time() - t0
    print(f"  Write took {elapsed:.1f}s")

    # Photo after write
    print("\n[7] Photo after write (before commit)...")
    take_photo(f"sw_mode_write_{color}")

    # Try CMD 0x04 as commit
    print("\n[8] CMD 0x04 (potential commit)...")
    resp = send_recv(fd, [BRAGI_MAGIC, 0x04, HANDLE, 0x00])
    print(f"  Result: {'OK' if status_ok(resp) else 'FAIL'}")
    time.sleep(2.0)
    take_photo(f"sw_mode_commit04_{color}")

    # Try CMD 0x07 as commit
    print("\n[9] CMD 0x07 (potential commit)...")
    resp = send_recv(fd, [BRAGI_MAGIC, 0x07, HANDLE, 0x00])
    print(f"  Result: {'OK' if status_ok(resp) else 'FAIL'}")
    time.sleep(2.0)
    take_photo(f"sw_mode_commit07_{color}")

    # Close handle
    print("\n[10] Close LCD handle...")
    close_lcd(fd)
    time.sleep(2.0)
    take_photo(f"sw_mode_closed_{color}")

    # Restore hardware mode
    print(f"\n[11] Restoring hardware mode ({orig_mode or 4})...")
    set_mode(fd, orig_mode or 4)
    os.close(fd)

    # Wait for reconnect
    time.sleep(2.0)
    hidraw = wait_for_device(timeout=15)
    if hidraw:
        take_photo("sw_mode_restored")
    print("\nDone!")


if __name__ == "__main__":
    main()
