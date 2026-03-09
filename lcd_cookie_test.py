#!/usr/bin/env python3
"""
LCD Cookie Test — Fix the cookie update and try multiple approaches.

Findings from lcd_full_flow.py probe:
  - Profiles list (file 15): 1 profile, stored at file 28000
  - Profile 28000: cookie=0, screenModesLayoutFile=28006
  - Layout (file 28006): 1 row, fileIds=[28007]
  - Screen resource map (file 61): EMPTY (count=0)
  - File 62: currently [38 00 3f 00] = points to resource 0x3F (logo)
  - File 28007: [38 00 2b 6e] = points to file 28203

This script tries three approaches:
  A) Full flow with CORRECT cookie update (using file 28000, not internal profile ID)
  B) Write BMP to existing file 28007 (already in layout) and point config to it
  C) Write BMP directly to file 62 (bypass the config indirection entirely)
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

PROP_MODE = 3
MODE_SELF = 1
MODE_HOST = 2

WIDTH = 248
HEIGHT = 170

PROFILE_FILE = 28000    # Where profile data is stored
LAYOUT_FILE = 28006     # Screen modes layout
RESOURCE_MAP = 61       # Screen resource map
ACTIVE_DISPLAY = 62     # File 62 = active display


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


def read_file(fd, file_id, buf=0):
    fid = struct.pack('<H', file_id)
    close_all(fd)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        return None
    resp = sr(fd, [BRAGI, CMD_DESCRIBE, buf])
    if not ok(resp):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return None
    size = struct.unpack_from('<I', resp, 4)[0]
    if size > 500000 or size == 0:
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return bytes()
    data = bytearray()
    while len(data) < size:
        resp = sr(fd, [BRAGI, CMD_READ, buf])
        if resp is None:
            break
        chunk = resp[3:]
        needed = size - len(data)
        data.extend(chunk[:needed])
    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    return bytes(data)


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


def delete_file(fd, file_id):
    fid = struct.pack('<H', file_id)
    sr(fd, [BRAGI, CMD_DELETE] + list(fid))


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return ok(sr(fd, pkt))


def take_photo(name):
    path = f"pics/11-cookie-test/{name}.jpg"
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


def create_corsair_bmp(r, g, b):
    """Corsair BMP: [0x48,0x00] + BMP header + GRB pixels + timestamp."""
    row_size = (WIDTH * 3 + 3) & ~3
    pds = row_size * HEIGHT
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])
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
    row = bytes([g, r, b]) * WIDTH + bytes(padding)
    for _ in range(HEIGHT):
        bmp += row
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    return bytes(bmp)


def create_corsair_bmp_320(r, g, b):
    """Corsair BMP at 320x170 (reported LCD width)."""
    w, h = 320, 170
    row_size = (w * 3 + 3) & ~3
    pds = row_size * h
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])
    bmp += b'BM'
    bmp += struct.pack('<I', 54 + pds)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', w)
    bmp += struct.pack('<i', h)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pds)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)
    padding = row_size - w * 3
    row = bytes([g, r, b]) * w + bytes(padding)
    for _ in range(h):
        bmp += row
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    return bytes(bmp)


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

    IMAGE_FILE = 28200
    CONFIG_FILE = 28201

    # ============ APPROACH A: Full flow with cookie fix ============
    print("\n" + "=" * 60)
    print("APPROACH A: Full flow + cookie update via file 28000")
    print("=" * 60)

    # Switch to HOST mode
    print("\n  Switching to HOST_CONTROLLED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write image
    print("  Writing RED Corsair BMP to file 28200...")
    bmp_red = create_corsair_bmp(255, 0, 0)
    close_all(fd)
    delete_file(fd, IMAGE_FILE)
    write_file(fd, IMAGE_FILE, bmp_red)

    # Write config pointing to image
    config = bytearray(16)
    config[0] = 56; config[1] = 0
    struct.pack_into('<H', config, 2, IMAGE_FILE)
    config = bytes(config)
    close_all(fd)
    delete_file(fd, CONFIG_FILE)
    write_file(fd, CONFIG_FILE, config)

    # Update resource map (file 61)
    print("  Updating resource map (file 61)...")
    close_all(fd)
    data_61 = read_file(fd, 61)
    if data_61 and len(data_61) >= 2:
        header = data_61[0:2]
    else:
        header = bytes([0x44, 0x00])
    # Build new map with our image
    new_map = bytearray(12)
    new_map[0:2] = header
    struct.pack_into('<H', new_map, 2, 1)  # count=1
    struct.pack_into('<H', new_map, 4, IMAGE_FILE)
    struct.pack_into('<H', new_map, 6, IMAGE_FILE)
    new_map[8:12] = bytes(4)
    close_all(fd)
    write_file(fd, 61, bytes(new_map))

    # Update layout (file 28006) - add config
    print("  Updating layout (file 28006)...")
    close_all(fd)
    layout_data = read_file(fd, LAYOUT_FILE)
    if layout_data and len(layout_data) >= 3:
        layout_header = layout_data[0:2]
    else:
        layout_header = bytes([0x37, 0x00])
    # Build layout: header + 1 row with [28007, CONFIG_FILE]
    new_layout = bytearray()
    new_layout.extend(layout_header)
    new_layout.append(1)  # 1 row
    new_layout.append(2)  # 2 items in row
    new_layout.extend(struct.pack('<H', 28007))
    new_layout.extend(struct.pack('<H', CONFIG_FILE))
    close_all(fd)
    write_file(fd, LAYOUT_FILE, bytes(new_layout))

    # selectWidget: write config to file 62
    print("  selectWidget: writing config to file 62...")
    close_all(fd)
    write_file(fd, ACTIVE_DISPLAY, config)

    # Cookie update: read profile from file 28000, update cookie, write back
    print("  Updating cookie in profile (file 28000)...")
    close_all(fd)
    profile_data = read_file(fd, PROFILE_FILE)
    if profile_data and len(profile_data) > 6:
        new_cookie = int(time.time()) & 0xFFFFFFFF
        updated = bytearray(profile_data)
        struct.pack_into('<I', updated, 2, new_cookie)
        close_all(fd)
        result = write_file(fd, PROFILE_FILE, bytes(updated))
        print(f"  Cookie update: {'OK' if result else 'FAIL'} (new cookie: 0x{new_cookie:08X})")
    else:
        print(f"  Cookie update: SKIP (profile read returned {len(profile_data) if profile_data else 0} bytes)")

    # Switch to SELF
    print("  Switching to SELF_OPERATED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(2)
    take_photo("a_full_flow_cookie")

    # ============ APPROACH B: Write BMP to default file 28007 ============
    print("\n" + "=" * 60)
    print("APPROACH B: Write BMP to existing default file 28007")
    print("=" * 60)

    # The layout already has 28007 in row 0
    # File 28007 normally has a 4-byte config, but what if we write a full BMP there?
    # Then we need file 62 to point to 28007

    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    print("  Switching to HOST_CONTROLLED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write GREEN BMP to file 28007
    print("  Writing GREEN Corsair BMP to file 28007...")
    bmp_green = create_corsair_bmp(0, 255, 0)
    close_all(fd)
    delete_file(fd, 28007)
    result = write_file(fd, 28007, bmp_green)
    print(f"  Write BMP to 28007: {'OK' if result else 'FAIL'} ({len(bmp_green)} bytes)")

    # Config pointing to 28007
    config_28007 = bytearray(16)
    config_28007[0] = 56; config_28007[1] = 0
    struct.pack_into('<H', config_28007, 2, 28007)
    config_28007 = bytes(config_28007)

    # Write to file 62
    close_all(fd)
    write_file(fd, ACTIVE_DISPLAY, config_28007)
    print(f"  Config to file 62: [38 00 67 6d] -> 28007")

    # Cookie update
    close_all(fd)
    profile_data = read_file(fd, PROFILE_FILE)
    if profile_data and len(profile_data) > 6:
        new_cookie = int(time.time()) & 0xFFFFFFFF
        updated = bytearray(profile_data)
        struct.pack_into('<I', updated, 2, new_cookie)
        close_all(fd)
        write_file(fd, PROFILE_FILE, bytes(updated))
        print(f"  Cookie updated: 0x{new_cookie:08X}")

    # Switch to SELF
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(2)
    take_photo("b_bmp_in_28007")

    # ============ APPROACH C: BMP directly in file 62 ============
    print("\n" + "=" * 60)
    print("APPROACH C: Write BMP directly to file 62 (no config layer)")
    print("=" * 60)

    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    print("  Switching to HOST_CONTROLLED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write BLUE BMP directly to file 62
    print("  Writing BLUE Corsair BMP directly to file 62...")
    bmp_blue = create_corsair_bmp(0, 0, 255)
    close_all(fd)
    result = write_file(fd, ACTIVE_DISPLAY, bmp_blue)
    print(f"  Write BMP to file 62: {'OK' if result else 'FAIL'} ({len(bmp_blue)} bytes)")

    # Cookie
    close_all(fd)
    profile_data = read_file(fd, PROFILE_FILE)
    if profile_data and len(profile_data) > 6:
        new_cookie = int(time.time()) & 0xFFFFFFFF
        updated = bytearray(profile_data)
        struct.pack_into('<I', updated, 2, new_cookie)
        close_all(fd)
        write_file(fd, PROFILE_FILE, bytes(updated))
        print(f"  Cookie updated: 0x{new_cookie:08X}")

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(2)
    take_photo("c_bmp_in_f62")

    # ============ APPROACH D: Restore default + try 320x170 ============
    print("\n" + "=" * 60)
    print("APPROACH D: 320x170 BMP (reported width) to new file + full flow")
    print("=" * 60)

    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    print("  Switching to HOST_CONTROLLED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Restore 28007 to a 4-byte config first
    close_all(fd)
    delete_file(fd, 28007)
    restore_config = bytes([0x38, 0x00, 0x00, 0x00])  # Default: type=56, resourceId=0
    write_file(fd, 28007, restore_config)

    # Write 320x170 BMP
    print("  Writing YELLOW 320x170 BMP to file 28200...")
    bmp_320 = create_corsair_bmp_320(255, 255, 0)
    print(f"  BMP size: {len(bmp_320)} bytes")
    close_all(fd)
    delete_file(fd, IMAGE_FILE)
    write_file(fd, IMAGE_FILE, bmp_320)

    # Config pointing to 28200
    config = bytearray(16)
    config[0] = 56; config[1] = 0
    struct.pack_into('<H', config, 2, IMAGE_FILE)
    close_all(fd)
    write_file(fd, ACTIVE_DISPLAY, bytes(config))

    # Cookie
    close_all(fd)
    profile_data = read_file(fd, PROFILE_FILE)
    if profile_data and len(profile_data) > 6:
        new_cookie = int(time.time()) & 0xFFFFFFFF
        updated = bytearray(profile_data)
        struct.pack_into('<I', updated, 2, new_cookie)
        close_all(fd)
        write_file(fd, PROFILE_FILE, bytes(updated))

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(2)
    take_photo("d_320x170_bmp")

    # ============ RESTORE DEFAULT ============
    print("\n" + "=" * 60)
    print("RESTORE: Writing default config to file 62")
    print("=" * 60)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)

    # Restore file 62 to point to resource 0x3F (Corsair logo)
    default_config = bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12)
    close_all(fd)
    write_file(fd, ACTIVE_DISPLAY, default_config)
    print("  Restored file 62 -> resource 0x3F")

    # Restore layout
    close_all(fd)
    restore_layout = bytes([0x37, 0x00, 0x01, 0x01, 0x67, 0x6D])
    write_file(fd, LAYOUT_FILE, restore_layout)

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        time.sleep(2)
        take_photo("e_restored")
        os.close(fd)

    print("\n=== DONE ===")
    print("Check photos: a=full_flow, b=bmp_in_28007, c=bmp_in_f62, d=320x170, e=restored")


if __name__ == "__main__":
    main()
