#!/usr/bin/env python3
"""
Corsair Vanguard 96 LCD Write Test

Writes a full framebuffer to LCD resource 0x3F and tries various
commit commands to trigger a display update.

Usage: python3 lcd_write_test.py [color]
  color: "red", "blue", "white", "black", "gradient" (default: red)
"""

import os
import sys
import time
import struct

HIDRAW = "/dev/hidraw3"
BRAGI_MAGIC = 0x08
OUT_PKT_SIZE = 64
LCD_SIZE = 84320
RES_LCD = 0x3F
HANDLE = 0x01  # GENERIC handle

# Commands
CMD_SET = 0x01
CMD_GET = 0x02
CMD_CLOSE = 0x05
CMD_WRITE = 0x06
CMD_READ = 0x08
CMD_PROBE = 0x09
CMD_OPEN = 0x0D


def send_recv(fd, data, timeout=0.5):
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


def open_lcd(fd):
    """Open LCD resource on HANDLE."""
    # Close first in case stale
    send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00], timeout=0.2)
    time.sleep(0.1)

    resp = send_recv(fd, [BRAGI_MAGIC, CMD_OPEN, HANDLE, RES_LCD & 0xFF, (RES_LCD >> 8) & 0xFF, 0x00])
    if not status_ok(resp):
        print(f"  OPEN failed: {resp[2]:02x}" if resp else "  OPEN: no response")
        return False
    print("  LCD handle opened")
    return True


def close_lcd(fd):
    resp = send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00])
    print(f"  LCD handle closed (status: {resp[2]:02x})" if resp else "  CLOSE: no response")


def write_lcd(fd, data):
    """Write full framebuffer data using chunked transfers."""
    total = len(data)
    print(f"  Writing {total} bytes...")

    # First packet: 7-byte header + data
    first_pkt = [BRAGI_MAGIC, CMD_WRITE, HANDLE]
    first_pkt += list(struct.pack('<I', total))
    first_chunk = min(total, OUT_PKT_SIZE - 7)
    first_pkt += list(data[:first_chunk])
    resp = send_recv(fd, first_pkt)
    if not status_ok(resp):
        print(f"  First WRITE failed: {resp[2]:02x}" if resp else "  WRITE: no response")
        return False

    # Continue packets: 3-byte header + data
    offset = first_chunk
    pkt_count = 1
    while offset < total:
        chunk = min(total - offset, OUT_PKT_SIZE - 3)
        cont_pkt = [BRAGI_MAGIC, CMD_WRITE, HANDLE]
        cont_pkt += list(data[offset:offset + chunk])
        resp = send_recv(fd, cont_pkt)
        if not status_ok(resp):
            print(f"  WRITE chunk at {offset} failed: {resp[2]:02x}" if resp else f"  WRITE chunk at {offset}: no response")
            return False
        offset += chunk
        pkt_count += 1
        if pkt_count % 200 == 0:
            print(f"    {offset}/{total} bytes ({100*offset//total}%)")

    print(f"  Write complete: {pkt_count} packets, {offset} bytes")
    return True


def make_framebuffer(color_name):
    """Create framebuffer data. Assumes RGB565 format (2 bytes/pixel).

    84,320 bytes / 2 = 42,160 pixels.
    """
    if color_name == "red":
        # RGB565: R=31, G=0, B=0 = 0xF800
        pixel = struct.pack('<H', 0xF800)
    elif color_name == "blue":
        # RGB565: R=0, G=0, B=31 = 0x001F
        pixel = struct.pack('<H', 0x001F)
    elif color_name == "green":
        # RGB565: R=0, G=63, B=0 = 0x07E0
        pixel = struct.pack('<H', 0x07E0)
    elif color_name == "white":
        # RGB565: R=31, G=63, B=31 = 0xFFFF
        pixel = struct.pack('<H', 0xFFFF)
    elif color_name == "black":
        pixel = struct.pack('<H', 0x0000)
    elif color_name == "gradient":
        # Horizontal gradient, repeating every 256 pixels
        fb = bytearray(LCD_SIZE)
        for i in range(0, LCD_SIZE, 2):
            x = (i // 2) % 256
            r = x >> 3  # 5 bits
            g = x >> 2  # 6 bits
            b = x >> 3  # 5 bits
            val = (r << 11) | (g << 5) | b
            struct.pack_into('<H', fb, i, val)
        return bytes(fb)
    elif color_name == "bars":
        # Color bars: red, green, blue, white, repeating
        fb = bytearray(LCD_SIZE)
        colors = [0xF800, 0x07E0, 0x001F, 0xFFFF]
        for i in range(0, LCD_SIZE, 2):
            pixel_idx = i // 2
            bar = (pixel_idx // 100) % len(colors)
            struct.pack_into('<H', fb, i, colors[bar])
        return bytes(fb)
    elif color_name == "allff":
        return bytes([0xFF] * LCD_SIZE)
    elif color_name == "all00":
        return bytes([0x00] * LCD_SIZE)
    else:
        print(f"Unknown color: {color_name}, using red")
        pixel = struct.pack('<H', 0xF800)

    return pixel * (LCD_SIZE // len(pixel))


def try_commit(fd, cmd_id, name):
    """Try a command as a potential commit/flush."""
    resp = send_recv(fd, [BRAGI_MAGIC, cmd_id, HANDLE, 0x00], timeout=0.5)
    ok = status_ok(resp)
    status = resp[2] if resp and len(resp) > 2 else 0xFF
    print(f"  {name} (0x{cmd_id:02X}): {'OK' if ok else f'err 0x{status:02X}'}")
    return ok


def main():
    color = sys.argv[1] if len(sys.argv) > 1 else "red"
    commit_cmd = sys.argv[2] if len(sys.argv) > 2 else "all"

    print(f"LCD Write Test — color={color}, commit={commit_cmd}")
    print(f"Time: {time.strftime('%H:%M:%S')}")

    fd = os.open(HIDRAW, os.O_RDWR | os.O_NONBLOCK)

    try:
        # Open LCD
        print("\n[1] Opening LCD resource 0x3F...")
        if not open_lcd(fd):
            return

        # Generate framebuffer
        print(f"\n[2] Generating {color} framebuffer ({LCD_SIZE} bytes)...")
        fb = make_framebuffer(color)
        print(f"  First 16 bytes: {' '.join(f'{b:02x}' for b in fb[:16])}")

        # Write
        print(f"\n[3] Writing framebuffer...")
        t0 = time.time()
        if not write_lcd(fd, fb):
            print("  WRITE FAILED")
            close_lcd(fd)
            return
        elapsed = time.time() - t0
        print(f"  Write took {elapsed:.2f}s")

        # Try commit commands
        print(f"\n[4] Trying commit commands...")
        if commit_cmd == "all" or commit_cmd == "0x04":
            try_commit(fd, 0x04, "CMD_0x04")
            time.sleep(0.5)

        if commit_cmd == "all" or commit_cmd == "0x07":
            try_commit(fd, 0x07, "CMD_0x07")
            time.sleep(0.5)

        if commit_cmd == "all" or commit_cmd == "close":
            print("  Closing handle (commit-on-close?)...")
            close_lcd(fd)
            time.sleep(1.0)
            # Reopen for further experiments
            open_lcd(fd)

        if commit_cmd == "all" or commit_cmd == "0x0f":
            try_commit(fd, 0x0F, "CMD_0x0F")
            time.sleep(0.5)

        if commit_cmd == "all" or commit_cmd == "0x1b":
            try_commit(fd, 0x1B, "CMD_0x1B")
            time.sleep(0.5)

        # Mode switch experiment
        if commit_cmd == "all" or commit_cmd == "mode":
            print("\n[5] Mode switch experiment...")
            # Get current mode
            resp = send_recv(fd, [BRAGI_MAGIC, CMD_GET, 0x01, 0x00])
            if resp and len(resp) > 3:
                cur_mode = resp[3]
                print(f"  Current mode: {cur_mode}")
                # Try switching to mode 1 (software control?)
                if cur_mode != 0x01:
                    resp = send_recv(fd, [BRAGI_MAGIC, CMD_SET, 0x01, 0x00, 0x01])
                    print(f"  SET mode=1: {'OK' if status_ok(resp) else 'FAIL'}")
                    time.sleep(1.0)
                    # Write again in new mode
                    write_lcd(fd, fb)
                    try_commit(fd, 0x04, "CMD_0x04 (mode 1)")
                    try_commit(fd, 0x07, "CMD_0x07 (mode 1)")
                    time.sleep(1.0)
                    # Restore mode
                    send_recv(fd, [BRAGI_MAGIC, CMD_SET, 0x01, 0x00, cur_mode])
                    print(f"  Restored mode={cur_mode}")

        print("\n[6] Cleanup...")
        close_lcd(fd)
        print("Done.")

    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
