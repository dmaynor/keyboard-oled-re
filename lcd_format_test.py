#!/usr/bin/env python3
"""
LCD Format Test — try every pixel format to find the right one.

We confirmed the two-file architecture works (config + image).
The LCD shows noise, meaning our pixel data IS being rendered
but in the wrong format. Try:
  1. Raw RGB565 (248x170x2 = 84,320 bytes)
  2. Raw BGR565
  3. Corsair BMP (with [0x48,0x00] prefix)
  4. Standard BMP (no prefix)
  5. Raw 24-bit RGB
  6. Raw 24-bit GRB
  7. Raw 24-bit BGR
"""

import os
import sys
import time
import struct
import subprocess
import glob
import random

BRAGI = 0x08
PKT_SIZE = 1024
CMD_SET = 0x01
CMD_GET = 0x02
CMD_UNBIND = 0x05
CMD_WRITE_BEGIN = 0x06
CMD_WRITE_CONT = 0x07
CMD_READ = 0x08
CMD_DESCRIBE = 0x09
CMD_CREATE = 0x0B
CMD_DELETE = 0x0C
CMD_OPEN = 0x0D
CMD_SESSION = 0x1B

FILE_62 = 62
FILE_28007 = 28007
PROP_MODE = 3
MODE_SELF = 1
MODE_HOST = 2

WIDTH = 248
HEIGHT = 170


def find_hidraw():
    for h in sorted(glob.glob("/dev/hidraw*")):
        name = h.split("/")[-1]
        try:
            uevent = open(f"/sys/class/hidraw/{name}/device/uevent").read()
            if "VANGUARD" in uevent and "input2" in uevent:
                return h
        except:
            pass
    return None


def sr(fd, data, timeout=2.0):
    padded = bytes(data) + bytes(PKT_SIZE - len(data))
    os.write(fd, bytes([0x00]) + padded)
    end = time.time() + timeout
    while time.time() < end:
        try:
            return os.read(fd, 2048)
        except BlockingIOError:
            time.sleep(0.005)
    return None


def ok(resp):
    return resp is not None and len(resp) > 2 and resp[2] == 0x00


def close_all(fd):
    for h in range(3):
        sr(fd, [BRAGI, CMD_UNBIND, h, 0x00], timeout=0.3)


def write_file(fd, file_id, data, buf=0):
    # Open (create if needed)
    fid = struct.pack('<H', file_id)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        sr(fd, [BRAGI, CMD_CREATE] + list(fid))
        resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
        if not ok(resp):
            return False
    # Write begin
    total = len(data)
    max_first = PKT_SIZE - 7  # 1017
    chunk = min(total, max_first)
    pkt = [BRAGI, CMD_WRITE_BEGIN, buf] + list(struct.pack('<I', total)) + list(data[:chunk])
    if not ok(sr(fd, pkt)):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return False
    offset = chunk
    while offset < total:
        chunk = min(total - offset, PKT_SIZE - 3)  # 1021
        pkt = [BRAGI, CMD_WRITE_CONT, buf] + list(data[offset:offset + chunk])
        if not ok(sr(fd, pkt)):
            sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
            return False
        offset += chunk
    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    return True


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return ok(sr(fd, pkt))


def take_photo(name):
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")


def activate_image(fd, image_file_id):
    """Write config pointing to image file, copy to file 62."""
    config = bytes([56, 0]) + struct.pack('<H', image_file_id) + bytes(12)
    # Write config to 28007
    sr(fd, [BRAGI, CMD_DELETE] + list(struct.pack('<H', FILE_28007)))
    write_file(fd, FILE_28007, config)
    # Copy to file 62
    write_file(fd, FILE_62, config)


def reconnect():
    """Wait for device reconnect after mode change."""
    time.sleep(3)
    for _ in range(20):
        h = find_hidraw()
        if h:
            subprocess.run(["sudo", "chmod", "666", h], capture_output=True)
            time.sleep(0.5)
            return h
        time.sleep(0.5)
    return None


# ============ IMAGE FORMAT GENERATORS ============

def make_rgb565(r, g, b):
    """Single RGB565 pixel: RRRRRGGGGGGBBBBB."""
    return struct.pack('<H', ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))


def fmt_rgb565_solid(r, g, b):
    """Raw RGB565, 248x170, solid color."""
    pixel = make_rgb565(r, g, b)
    return pixel * (WIDTH * HEIGHT)


def fmt_bgr565_solid(r, g, b):
    """Raw BGR565 (swap R and B), solid color."""
    pixel = struct.pack('<H', ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3))
    return pixel * (WIDTH * HEIGHT)


def fmt_corsair_bmp_solid(r, g, b):
    """Corsair BMP: [0x48,0x00] + BMP header + GRB pixels + timestamp."""
    row_size = (WIDTH * 3 + 3) & ~3
    pixel_data_size = row_size * HEIGHT
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])
    bmp += b'BM'
    bmp += struct.pack('<I', 54 + pixel_data_size)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', WIDTH)
    bmp += struct.pack('<i', HEIGHT)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pixel_data_size)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)
    padding = row_size - WIDTH * 3
    row = bytes([g, r, b]) * WIDTH + bytes(padding)
    for _ in range(HEIGHT):
        bmp += row
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    return bytes(bmp)


def fmt_std_bmp_solid(r, g, b):
    """Standard BMP (no Corsair prefix), BGR pixels."""
    row_size = (WIDTH * 3 + 3) & ~3
    pixel_data_size = row_size * HEIGHT
    bmp = bytearray()
    bmp += b'BM'
    bmp += struct.pack('<I', 54 + pixel_data_size)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', WIDTH)
    bmp += struct.pack('<i', HEIGHT)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pixel_data_size)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)
    padding = row_size - WIDTH * 3
    row = bytes([b, g, r]) * WIDTH + bytes(padding)  # Standard BGR
    for _ in range(HEIGHT):
        bmp += row
    return bytes(bmp)


def fmt_raw_rgb(r, g, b):
    """Raw 24-bit RGB, no header."""
    return bytes([r, g, b]) * (WIDTH * HEIGHT)


def fmt_raw_grb(r, g, b):
    """Raw 24-bit GRB, no header."""
    return bytes([g, r, b]) * (WIDTH * HEIGHT)


def fmt_raw_bgr(r, g, b):
    """Raw 24-bit BGR, no header."""
    return bytes([b, g, r]) * (WIDTH * HEIGHT)


def main():
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")

    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)

    # Start session
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Use RED (255,0,0) — easy to identify
    R, G, B = 255, 0, 0

    formats = [
        ("rgb565",      fmt_rgb565_solid,      "Raw RGB565 (84,320 bytes)"),
        ("bgr565",      fmt_bgr565_solid,      "Raw BGR565 (84,320 bytes)"),
        ("corsair_bmp", fmt_corsair_bmp_solid,  "Corsair BMP [0x48,0x00]+GRB"),
        ("std_bmp",     fmt_std_bmp_solid,      "Standard BMP (no prefix, BGR)"),
        ("raw_rgb",     fmt_raw_rgb,            "Raw 24-bit RGB (no header)"),
        ("raw_grb",     fmt_raw_grb,            "Raw 24-bit GRB (no header)"),
        ("raw_bgr",     fmt_raw_bgr,            "Raw 24-bit BGR (no header)"),
    ]

    image_base = 28100

    for i, (name, gen_func, desc) in enumerate(formats):
        file_id = image_base + i
        print(f"\n=== Format {i+1}/{len(formats)}: {desc} ===")

        data = gen_func(R, G, B)
        print(f"  Size: {len(data)} bytes, first 8: {' '.join(f'{x:02x}' for x in data[:8])}")

        # Write image
        close_all(fd)
        sr(fd, [BRAGI, CMD_DELETE] + list(struct.pack('<H', file_id)))
        result = write_file(fd, file_id, data)
        print(f"  Write to file {file_id}: {'OK' if result else 'FAIL'}")
        if not result:
            continue

        # Activate via config -> file 62
        close_all(fd)
        activate_image(fd, file_id)
        print(f"  Activated (config -> file 62)")

        time.sleep(2)
        take_photo(f"fmt_{name}")

    # Final: try mode toggle after last format
    print("\n=== Mode toggle test ===")
    print("  Switching to HOST_CONTROLLED then back to SELF_OPERATED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        take_photo("fmt_host_mode")
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
        os.close(fd)
        hidraw = reconnect()
        if hidraw:
            take_photo("fmt_final")

    print("\nDone! Check photos to find which format produced solid red.")


if __name__ == "__main__":
    main()
