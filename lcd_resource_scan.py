#!/usr/bin/env python3
"""
LCD Resource Scan — Enumerate all hardware resources that produce display output.

We discovered the firmware can only render from HARDWARE RESOURCES (0x3F, 0x01, etc.),
not from user files. Let's scan all possible resource IDs (0-255) to find:
  - Which resource IDs produce display output
  - Which produce different images
  - Whether there's a "writable" resource we can use

Also try: write image data to resource 0x3F in HOST mode (stops animation).
"""

import os
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

PROP_MODE = 3
MODE_SELF = 1
MODE_HOST = 2


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


def hx(data, n=32):
    if data is None: return "None"
    return ' '.join(f'{b:02x}' for b in data[:n])


def close_all(fd):
    for h in range(3):
        sr(fd, [BRAGI, CMD_UNBIND, h, 0x00], timeout=0.3)


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


def open_resource(fd, res_id, handle=1):
    """Open a resource using old Bragi handle-based OPEN."""
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, handle, res_id, 0x00, 0x00])
    return ok(resp), resp


def describe_resource(fd, handle=1):
    resp = sr(fd, [BRAGI, CMD_DESCRIBE, handle])
    if ok(resp) and len(resp) >= 9:
        size5 = struct.unpack_from('<I', resp, 5)[0]
        size4 = struct.unpack_from('<I', resp, 4)[0]
        return size5 if 0 < size5 < 10000000 else size4
    return 0


def write_resource(fd, res_id, data, handle=1):
    """Write to a resource (old Bragi style)."""
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, handle, res_id, 0x00, 0x00])
    if not ok(resp):
        return False
    total = len(data)
    chunk = min(total, PKT_SIZE - 7)
    pkt = [BRAGI, CMD_WRITE_BEGIN, handle] + list(struct.pack('<I', total)) + list(data[:chunk])
    if not ok(sr(fd, pkt)):
        sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
        return False
    offset = chunk
    while offset < total:
        chunk = min(total - offset, PKT_SIZE - 3)
        pkt = [BRAGI, CMD_WRITE_CONT, handle] + list(data[offset:offset + chunk])
        if not ok(sr(fd, pkt)):
            sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
            return False
        offset += chunk
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    return True


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/15-resource-scan/{name}.jpg"
    os.makedirs(os.path.dirname(path), exist_ok=True)
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

    # ============ SCAN 1: Enumerate resources that can be opened ============
    print("\n" + "=" * 60)
    print("SCAN 1: Enumerate openable resources (0-127)")
    print("=" * 60)

    openable = []
    for res_id in range(128):
        success, resp = open_resource(fd, res_id)
        if success:
            size = describe_resource(fd)
            print(f"  Resource 0x{res_id:02X} ({res_id}): OPEN OK, size={size}")
            openable.append((res_id, size))
            sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)

    print(f"\n  Found {len(openable)} openable resources")

    # ============ SCAN 2: Try each resource in config ============
    print("\n" + "=" * 60)
    print("SCAN 2: Display test — key resources")
    print("=" * 60)

    # Only test resources that opened successfully + a few interesting ones
    test_resources = [r for r, s in openable if r <= 0x40]
    # Add some specific ones
    for r in [0, 1, 2, 0x3E, 0x3F, 0x40]:
        if r not in test_resources:
            test_resources.append(r)
    test_resources.sort()

    for res_id in test_resources:
        print(f"\n  Testing resource 0x{res_id:02X} ({res_id})...")
        # Switch to HOST
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
        os.close(fd)
        hidraw = reconnect()
        if not hidraw:
            print("    Lost device!"); return
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        close_all(fd)
        token = bytes(random.randint(0, 255) for _ in range(4))
        sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

        # Write config pointing to this resource
        config = bytes([0x38, 0x00]) + struct.pack('<H', res_id) + bytes(12)
        close_all(fd)
        write_file(fd, 62, config)

        # Switch to SELF
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
        os.close(fd)
        hidraw = reconnect()
        if not hidraw:
            print("    Lost device!"); return
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        close_all(fd)
        time.sleep(2)
        take_photo(f"res_{res_id:02X}")

    # ============ TEST 3: Write to resource 0x3F in HOST mode ============
    print("\n" + "=" * 60)
    print("TEST 3: Write to resource 0x3F in HOST mode (animation stopped)")
    print("=" * 60)

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Set config to point to 0x3F
    config = bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12)
    close_all(fd)
    write_file(fd, 62, config)

    # Write solid RED to resource 0x3F (RGB565 format, 320x170)
    # RGB565 LE red: 0x00F8 → bytes [0xF8, 0x00] (wait, LE means low byte first)
    # Actually for RGB565: R=31<<11|G=0<<5|B=0 = 0xF800
    # In LE: [0x00, 0xF8]
    red_pixel = bytes([0x00, 0xF8])
    red_frame = red_pixel * (320 * 170)
    print(f"  Writing {len(red_frame)} bytes of RGB565 red to resource 0x3F...")
    close_all(fd)
    result = write_resource(fd, 0x3F, red_frame)
    print(f"  Write: {'OK' if result else 'FAIL'}")
    time.sleep(1)
    take_photo("res3f_red_host_mode")

    # Don't switch to SELF yet — take photo in HOST mode
    # Now try Corsair BMP to resource 0x3F
    print("  Writing 320x170 Corsair BMP to resource 0x3F...")
    W, H = 320, 170
    row_size = (W * 3 + 3) & ~3
    pds = row_size * H
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])
    bmp += b'BM'
    bmp += struct.pack('<I', 54 + pds)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', W)
    bmp += struct.pack('<i', H)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pds)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)
    row = bytes([0x00, 0x00, 0xFF]) * W  # GRB: G=0, R=0, B=255 → blue
    for _ in range(H):
        bmp += row
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    close_all(fd)
    result = write_resource(fd, 0x3F, bytes(bmp))
    print(f"  Write BMP: {'OK' if result else 'FAIL'}")
    time.sleep(1)
    take_photo("res3f_bmp_host_mode")

    # Switch to SELF and see if it persists
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        time.sleep(2)
        take_photo("res3f_after_self")

        # Restore
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
        os.close(fd)
        hidraw = reconnect()
        if hidraw:
            fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
            close_all(fd)
            write_file(fd, 62, bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12))
            set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
            os.close(fd)
            reconnect()

    print("\n=== DONE ===")
    print(f"Openable resources: {[(f'0x{r:02X}', s) for r, s in openable]}")


if __name__ == "__main__":
    main()
