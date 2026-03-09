#!/usr/bin/env python3
"""
LCD Profile Path — bypass file 62, use the firmware's profile path.

Theory: The firmware loads images via:
  1. Profile → screenModesLayoutFile (28006)
  2. Layout → fileIds[screen_index]
  3. Properties file (28001) → property 263 = screen_index
  4. Config file → [56, 0, imageFileId_lo, imageFileId_hi]
  5. Image file → Corsair BMP data

File 62 may just be a Web Hub shortcut that the firmware ignores.

Plan: Update ALL profile-path files correctly, DON'T touch file 62.
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

PROFILE_FILE = 28000
PROPERTIES_FILE = 28001
LAYOUT_FILE = 28006
RESOURCE_MAP_FILE = 61
ACTIVE_DISPLAY_FILE = 62

IMAGE_FILE = 28300
CONFIG_FILE = 28301


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
    # Try multiple offsets for size
    size5 = struct.unpack_from('<I', resp, 5)[0] if len(resp) >= 9 else 0
    size4 = struct.unpack_from('<I', resp, 4)[0] if len(resp) >= 8 else 0
    size = size5 if 0 < size5 < 500000 else size4
    if size == 0 or size > 500000:
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
    sr(fd, [BRAGI, CMD_DELETE] + list(struct.pack('<H', file_id)))


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/13-profile-path/{name}.jpg"
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


def parse_property_map(data):
    """Parse property map file.
    Format: [header(2), count(2 LE), entries(6 each: propId(2 LE) + value(4))]
    """
    if not data or len(data) < 4:
        return None
    header = data[0:2]
    count = struct.unpack_from('<H', data, 2)[0]
    props = {}
    for i in range(count):
        offset = 4 + i * 6
        if offset + 6 <= len(data):
            prop_id = struct.unpack_from('<H', data, offset)[0]
            value = data[offset + 2:offset + 6]
            props[prop_id] = value
    return {'header': header, 'count': count, 'props': props}


def serialize_property_map(pmap):
    """Serialize property map back to bytes."""
    count = len(pmap['props'])
    data = bytearray(4 + count * 6)
    data[0:2] = pmap['header']
    struct.pack_into('<H', data, 2, count)
    offset = 4
    for prop_id in sorted(pmap['props'].keys()):
        struct.pack_into('<H', data, offset, prop_id)
        data[offset + 2:offset + 6] = pmap['props'][prop_id]
        offset += 6
    return bytes(data)


def create_320x170_bmp(r, g, b):
    """Create 320x170 Corsair BMP (matching factory format)."""
    W, H = 320, 170
    row_size = (W * 3 + 3) & ~3  # 960
    pds = row_size * H  # 163200
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
    row = bytes([g, r, b]) * W  # GRB order
    for _ in range(H):
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

    # Read current properties file
    print("\n--- Reading current properties file (28001) ---")
    prop_data = read_file(fd, PROPERTIES_FILE)
    if prop_data:
        pmap = parse_property_map(prop_data)
        if pmap:
            print(f"  Header: {hx(pmap['header'])}")
            print(f"  Property count: {pmap['count']}")
            for pid, val in sorted(pmap['props'].items()):
                val_num = struct.unpack_from('<I', val, 0)[0]
                print(f"    Prop {pid} (0x{pid:04X}): {hx(val)} = {val_num}")

    # ============ APPROACH 1: Profile path only ============
    print("\n" + "=" * 60)
    print("APPROACH 1: Profile path (no file 62 write)")
    print("=" * 60)

    # Switch to HOST
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

    # Step 1: Write 320x170 solid RED BMP
    print("  Step 1: Writing RED 320x170 BMP to file 28300...")
    bmp = create_320x170_bmp(255, 0, 0)
    close_all(fd)
    delete_file(fd, IMAGE_FILE)
    result = write_file(fd, IMAGE_FILE, bmp)
    print(f"    Write: {'OK' if result else 'FAIL'} ({len(bmp)} bytes)")

    # Step 2: Write config file pointing to image
    print("  Step 2: Writing config to file 28301...")
    config = bytearray(16)
    config[0] = 56; config[1] = 0
    struct.pack_into('<H', config, 2, IMAGE_FILE)
    close_all(fd)
    delete_file(fd, CONFIG_FILE)
    result = write_file(fd, CONFIG_FILE, bytes(config))
    print(f"    Config: {hx(config)}")
    print(f"    Write: {'OK' if result else 'FAIL'}")

    # Step 3: Update resource map (file 61)
    print("  Step 3: Updating resource map (file 61)...")
    new_map = bytearray(12)
    new_map[0:2] = bytes([0x44, 0x00])
    struct.pack_into('<H', new_map, 2, 1)
    struct.pack_into('<H', new_map, 4, IMAGE_FILE)
    struct.pack_into('<H', new_map, 6, IMAGE_FILE)
    close_all(fd)
    result = write_file(fd, RESOURCE_MAP_FILE, bytes(new_map))
    print(f"    Write: {'OK' if result else 'FAIL'}")

    # Step 4: Update layout (file 28006) — put OUR config at index 0
    print("  Step 4: Updating layout (file 28006)...")
    new_layout = bytes([0x37, 0x00, 0x01, 0x01]) + struct.pack('<H', CONFIG_FILE)
    close_all(fd)
    result = write_file(fd, LAYOUT_FILE, new_layout)
    print(f"    Layout: {hx(new_layout)}")
    print(f"    Write: {'OK' if result else 'FAIL'}")

    # Step 5: Update properties file — screen index = 0
    print("  Step 5: Updating properties file (28001)...")
    close_all(fd)
    prop_data = read_file(fd, PROPERTIES_FILE)
    if prop_data:
        pmap = parse_property_map(prop_data)
        if pmap:
            # Set property 263 (0x107) = 0 (first index)
            pmap['props'][263] = struct.pack('<I', 0)
            new_props = serialize_property_map(pmap)
            close_all(fd)
            result = write_file(fd, PROPERTIES_FILE, new_props)
            print(f"    Prop 263 = 0, Write: {'OK' if result else 'FAIL'}")

    # Step 6: Update cookie in profile
    print("  Step 6: Updating cookie in profile (28000)...")
    close_all(fd)
    profile_data = read_file(fd, PROFILE_FILE)
    if profile_data and len(profile_data) > 6:
        new_cookie = int(time.time()) & 0xFFFFFFFF
        updated = bytearray(profile_data)
        struct.pack_into('<I', updated, 2, new_cookie)
        close_all(fd)
        result = write_file(fd, PROFILE_FILE, bytes(updated))
        print(f"    Cookie: 0x{new_cookie:08X}, Write: {'OK' if result else 'FAIL'}")

    take_photo("approach1_host_after_writes")

    # Step 7: Switch to SELF (triggers firmware reload)
    print("  Step 7: Switching to SELF_OPERATED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("approach1_self_operated")

    # ============ APPROACH 2: Profile path + file 62 + selectWidget ============
    print("\n" + "=" * 60)
    print("APPROACH 2: Profile path + file 62 (belt and suspenders)")
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
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write GREEN BMP
    bmp_green = create_320x170_bmp(0, 255, 0)
    close_all(fd)
    delete_file(fd, IMAGE_FILE)
    write_file(fd, IMAGE_FILE, bmp_green)
    print(f"  GREEN BMP written to {IMAGE_FILE}")

    # Config
    config = bytearray(16)
    config[0] = 56; config[1] = 0
    struct.pack_into('<H', config, 2, IMAGE_FILE)
    close_all(fd)
    delete_file(fd, CONFIG_FILE)
    write_file(fd, CONFIG_FILE, bytes(config))

    # Resource map
    new_map = bytearray(12)
    new_map[0:2] = bytes([0x44, 0x00])
    struct.pack_into('<H', new_map, 2, 1)
    struct.pack_into('<H', new_map, 4, IMAGE_FILE)
    struct.pack_into('<H', new_map, 6, IMAGE_FILE)
    close_all(fd)
    write_file(fd, RESOURCE_MAP_FILE, bytes(new_map))

    # Layout with just our config
    new_layout = bytes([0x37, 0x00, 0x01, 0x01]) + struct.pack('<H', CONFIG_FILE)
    close_all(fd)
    write_file(fd, LAYOUT_FILE, new_layout)

    # Properties: screen index = 0
    close_all(fd)
    prop_data = read_file(fd, PROPERTIES_FILE)
    if prop_data:
        pmap = parse_property_map(prop_data)
        if pmap:
            pmap['props'][263] = struct.pack('<I', 0)
            close_all(fd)
            write_file(fd, PROPERTIES_FILE, serialize_property_map(pmap))

    # ALSO write to file 62 (selectWidget style)
    close_all(fd)
    write_file(fd, ACTIVE_DISPLAY_FILE, bytes(config))
    print(f"  Config also written to file 62")

    # Cookie
    close_all(fd)
    profile_data = read_file(fd, PROFILE_FILE)
    if profile_data and len(profile_data) > 6:
        new_cookie = int(time.time()) & 0xFFFFFFFF
        updated = bytearray(profile_data)
        struct.pack_into('<I', updated, 2, new_cookie)
        close_all(fd)
        write_file(fd, PROFILE_FILE, bytes(updated))
        print(f"  Cookie: 0x{new_cookie:08X}")

    # SELF mode
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("approach2_belt_and_suspenders")

    # ============ APPROACH 3: Write BMP to EXISTING file 28007 ============
    print("\n" + "=" * 60)
    print("APPROACH 3: Overwrite 28007 with BMP + update config to point to self")
    print("=" * 60)

    # Idea: 28007 is already in the layout. What if we put the BMP IN 28007
    # and make the config in file 62 point to 28007?
    # But 28007 is a CONFIG file... the firmware expects it to be [56,0,id_lo,id_hi]
    # What if we make 28007 an IMAGE file instead?

    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write BLUE BMP to file 28300 (image)
    bmp_blue = create_320x170_bmp(0, 0, 255)
    close_all(fd)
    delete_file(fd, IMAGE_FILE)
    write_file(fd, IMAGE_FILE, bmp_blue)

    # Write config [56, 0, 28300_lo, 28300_hi] to file 28007
    config = bytearray(16)
    config[0] = 56; config[1] = 0
    struct.pack_into('<H', config, 2, IMAGE_FILE)
    close_all(fd)
    delete_file(fd, 28007)
    write_file(fd, 28007, bytes(config))
    print(f"  28007 now config: {hx(config)}")

    # Layout already has [28007], set screen index = 0
    layout = bytes([0x37, 0x00, 0x01, 0x01, 0x67, 0x6D])  # [28007]
    close_all(fd)
    write_file(fd, LAYOUT_FILE, layout)

    # Properties
    close_all(fd)
    prop_data = read_file(fd, PROPERTIES_FILE)
    if prop_data:
        pmap = parse_property_map(prop_data)
        if pmap:
            pmap['props'][263] = struct.pack('<I', 0)
            close_all(fd)
            write_file(fd, PROPERTIES_FILE, serialize_property_map(pmap))

    # Also selectWidget to file 62
    close_all(fd)
    write_file(fd, ACTIVE_DISPLAY_FILE, bytes(config))

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
    time.sleep(3)
    take_photo("approach3_config_in_28007")

    # ============ RESTORE ============
    print("\n  Restoring defaults...")
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)

    # Restore file 62 → resource 0x3F
    close_all(fd)
    write_file(fd, ACTIVE_DISPLAY_FILE, bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12))
    # Restore 28007 → resource 0
    close_all(fd)
    delete_file(fd, 28007)
    write_file(fd, 28007, bytes([0x38, 0x00, 0x00, 0x00]))
    # Restore layout
    close_all(fd)
    write_file(fd, LAYOUT_FILE, bytes([0x37, 0x00, 0x01, 0x01, 0x67, 0x6D]))

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        time.sleep(2)
        take_photo("restored")

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
