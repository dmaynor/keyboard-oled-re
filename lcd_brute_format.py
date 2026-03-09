#!/usr/bin/env python3
"""
LCD Brute Format — try everything to get a clean image on screen.

All 7 pixel formats showed identical noise, meaning the firmware isn't
reading our image files. Try:
  1. All-black / all-white fills (any format mismatch still shows solid)
  2. Direct write to resource 0x3F (hardware framebuffer)
  3. Write image data directly IN file 62 (not via config pointer)
  4. Different config format bytes
  5. Write to resource 0x3F AND set config
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
RES_LCD = 0x3F  # Resource 63 = LCD framebuffer
PROP_MODE = 3
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


def hx(data, n=16):
    if data is None: return "None"
    return ' '.join(f'{b:02x}' for b in data[:n])


def close_all(fd):
    for h in range(3):
        sr(fd, [BRAGI, CMD_UNBIND, h, 0x00], timeout=0.3)


def open_resource(fd, res_id, handle=1):
    """Open a resource by ID (old Bragi style)."""
    resp = sr(fd, [BRAGI, CMD_OPEN, handle, res_id, 0x00, 0x00])
    return ok(resp), resp


def write_file(fd, file_id, data, buf=0):
    fid = struct.pack('<H', file_id)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        sr(fd, [BRAGI, CMD_CREATE] + list(fid))
        resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
        if not ok(resp):
            return False
    total = len(data)
    chunk = min(total, PKT_SIZE - 7)
    pkt = [BRAGI, CMD_WRITE_BEGIN, buf] + list(struct.pack('<I', total)) + list(data[:chunk])
    if not ok(sr(fd, pkt)):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return False
    offset = chunk
    while offset < total:
        chunk = min(total - offset, PKT_SIZE - 3)
        pkt = [BRAGI, CMD_WRITE_CONT, buf] + list(data[offset:offset + chunk])
        if not ok(sr(fd, pkt)):
            sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
            return False
        offset += chunk
    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    return True


def write_resource(fd, res_id, data, handle=1):
    """Write to a resource (old Bragi handle-based style)."""
    # Close stale handle
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    # Open resource
    resp = sr(fd, [BRAGI, CMD_OPEN, handle, res_id, 0x00, 0x00])
    if not ok(resp):
        print(f"    OPEN resource 0x{res_id:02X} FAIL: {hx(resp)}")
        return False
    # Write begin
    total = len(data)
    chunk = min(total, PKT_SIZE - 7)
    pkt = [BRAGI, CMD_WRITE_BEGIN, handle] + list(struct.pack('<I', total)) + list(data[:chunk])
    if not ok(sr(fd, pkt)):
        print(f"    WRITE_BEGIN FAIL")
        sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
        return False
    offset = chunk
    while offset < total:
        chunk = min(total - offset, PKT_SIZE - 3)
        pkt = [BRAGI, CMD_WRITE_CONT, handle] + list(data[offset:offset + chunk])
        if not ok(sr(fd, pkt)):
            print(f"    WRITE_CONT at {offset} FAIL")
            sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
            return False
        offset += chunk
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
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


def reconnect():
    time.sleep(3)
    for _ in range(20):
        h = find_hidraw()
        if h:
            subprocess.run(["sudo", "chmod", "666", h], capture_output=True)
            time.sleep(0.5)
            return h
        time.sleep(0.5)
    return None


def main():
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")

    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # ============ TEST 1: All black to file 62 directly ============
    print("\n=== TEST 1: All 0x00 (black) written to file 62 ===")
    black = bytes(84320)  # 248*170*2 = 84320 zeros
    close_all(fd)
    result = write_file(fd, FILE_62, black)
    print(f"  Write 84,320 zeros to file 62: {'OK' if result else 'FAIL'}")
    time.sleep(2)
    take_photo("brute_black_f62")

    # ============ TEST 2: All white to file 62 directly ============
    print("\n=== TEST 2: All 0xFF (white) written to file 62 ===")
    white = bytes([0xFF] * 84320)
    close_all(fd)
    result = write_file(fd, FILE_62, white)
    print(f"  Write 84,320 0xFF to file 62: {'OK' if result else 'FAIL'}")
    time.sleep(2)
    take_photo("brute_white_f62")

    # ============ TEST 3: RGB565 red directly to resource 0x3F ============
    print("\n=== TEST 3: RGB565 red to resource 0x3F (framebuffer) ===")
    red565 = bytes([0x00, 0xF8] * (WIDTH * HEIGHT))  # RGB565 LE red
    close_all(fd)
    result = write_resource(fd, RES_LCD, red565)
    print(f"  Write RGB565 red to resource 0x3F: {'OK' if result else 'FAIL'}")
    time.sleep(2)
    take_photo("brute_red_res3f")

    # ============ TEST 4: RGB565 red BE to resource 0x3F ============
    print("\n=== TEST 4: RGB565 red (big-endian) to resource 0x3F ===")
    red565be = bytes([0xF8, 0x00] * (WIDTH * HEIGHT))
    close_all(fd)
    result = write_resource(fd, RES_LCD, red565be)
    print(f"  Write RGB565 red BE to resource 0x3F: {'OK' if result else 'FAIL'}")
    time.sleep(2)
    take_photo("brute_red_be_res3f")

    # ============ TEST 5: All white to resource 0x3F ============
    print("\n=== TEST 5: All 0xFF to resource 0x3F ===")
    close_all(fd)
    result = write_resource(fd, RES_LCD, white)
    print(f"  Write 0xFF to resource 0x3F: {'OK' if result else 'FAIL'}")
    time.sleep(2)
    take_photo("brute_white_res3f")

    # ============ TEST 6: Config + resource combo ============
    print("\n=== TEST 6: Config file 62 + RGB565 green to resource 0x3F ===")
    close_all(fd)
    # Config pointing to resource 0x3F (63)
    config_3f = bytes([56, 0, 0x3F, 0x00]) + bytes(12)
    write_file(fd, FILE_62, config_3f)
    # Write green to framebuffer
    green565 = bytes([0xE0, 0x07] * (WIDTH * HEIGHT))  # RGB565 LE green
    close_all(fd)
    write_resource(fd, RES_LCD, green565)
    print(f"  Config -> 0x3F + green framebuffer")
    time.sleep(2)
    take_photo("brute_config_plus_green")

    # ============ TEST 7: Write Corsair BMP directly to file 62 ============
    print("\n=== TEST 7: Corsair BMP (solid blue) directly to file 62 ===")
    # Create minimal Corsair BMP
    row_size = (WIDTH * 3 + 3) & ~3
    pds = row_size * HEIGHT
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])  # Corsair prefix
    bmp += b'BM'
    bmp += struct.pack('<I', 54 + pds)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', WIDTH)
    bmp += struct.pack('<i', HEIGHT)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pds)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)
    padding = row_size - WIDTH * 3
    row = bytes([255, 0, 0]) * WIDTH + bytes(padding)  # GRB: G=255 → green channel
    for _ in range(HEIGHT):
        bmp += row
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    bmp = bytes(bmp)

    close_all(fd)
    result = write_file(fd, FILE_62, bmp)
    print(f"  Write {len(bmp)}-byte Corsair BMP to file 62: {'OK' if result else 'FAIL'}")
    time.sleep(2)
    take_photo("brute_bmp_in_f62")

    # ============ TEST 8: Mode toggle after all writes ============
    print("\n=== TEST 8: HOST -> writes -> SELF transition ===")
    set_prop(fd, PROP_MODE, struct.pack('<I', 2))  # HOST_CONTROLLED
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device")
        return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)

    # Write green to resource 0x3F in HOST mode
    green565 = bytes([0xE0, 0x07] * (WIDTH * HEIGHT))
    write_resource(fd, RES_LCD, green565)
    take_photo("brute_host_green_res3f")

    # Write config pointing to 0x3F
    config_3f = bytes([56, 0, 0x3F, 0x00]) + bytes(12)
    close_all(fd)
    write_file(fd, FILE_62, config_3f)

    # Switch back to SELF
    set_prop(fd, PROP_MODE, struct.pack('<I', 1))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        take_photo("brute_self_after_all")

    print("\n=== DONE ===")
    print("Check photos for any solid colors!")


if __name__ == "__main__":
    main()
