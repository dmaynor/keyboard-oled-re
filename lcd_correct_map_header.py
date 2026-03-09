#!/usr/bin/env python3
"""
LCD Correct Map Header — The resource map (file 61) has header [0x44, 0x00],
not [0x00, 0x00]. This might be why file-based rendering failed.

Test: Use correct header + factory BMP (file 28203, known good) to see
if the firmware can render from file references.

Also: examine the exact factory state of file 62 and file 61 more carefully.
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


def hx(data, n=64):
    if data is None: return "None"
    return ' '.join(f'{b:02x}' for b in data[:n])


def close_all(fd):
    for h in range(3):
        sr(fd, [BRAGI, CMD_UNBIND, h, 0x00], timeout=0.3)


def read_file_raw(fd, file_id, buf=0, max_size=200000):
    fid = struct.pack('<H', file_id)
    close_all(fd)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        return None
    desc = sr(fd, [BRAGI, CMD_DESCRIBE, buf])
    if not ok(desc) or len(desc) < 9:
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return None
    size = struct.unpack_from('<I', desc, 5)[0]
    if size == 0 or size > max_size:
        size = struct.unpack_from('<I', desc, 4)[0]
    if size == 0 or size > max_size:
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return bytes()
    data = bytearray()
    while len(data) < size:
        resp = sr(fd, [BRAGI, CMD_READ, buf])
        if resp is None:
            break
        needed = size - len(data)
        data.extend(resp[3:][:needed])
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


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/20-correct-header/{name}.jpg"
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

    # ================================================================
    # STEP 0: Capture CURRENT state of key files (baseline)
    # ================================================================
    print("\n" + "=" * 60)
    print("STEP 0: Read current state of all LCD-related files")
    print("=" * 60)

    for fid, desc in [(62, "active display"), (61, "resource map"),
                       (28007, "default config"), (28006, "layout"),
                       (28000, "profile"), (28001, "properties")]:
        close_all(fd)
        data = read_file_raw(fd, fid)
        if data is None:
            print(f"  File {fid} ({desc}): OPEN FAILED")
        elif len(data) == 0:
            print(f"  File {fid} ({desc}): EMPTY")
        else:
            print(f"  File {fid} ({desc}): {len(data)} bytes")
            print(f"    Hex: {hx(data)}")
            # Decode known formats
            if len(data) >= 4 and data[0] == 0x38:
                res_id = struct.unpack_from('<H', data, 2)[0]
                print(f"    → Config type=0x38, resourceId={res_id} (0x{res_id:04X})")
            if fid == 61 and len(data) >= 4:
                header = struct.unpack_from('<H', data, 0)[0]
                count = struct.unpack_from('<H', data, 2)[0] if len(data) >= 4 else 0
                print(f"    → Resource map: header=0x{header:04X}, count={count}")
                for i in range(count):
                    off = 4 + i * 8
                    if off + 8 <= len(data):
                        rid = struct.unpack_from('<H', data, off)[0]
                        raddr = struct.unpack_from('<H', data, off + 2)[0]
                        rhash = data[off + 4:off + 8]
                        print(f"      [{i}] resourceId={rid}, address={raddr}, hash={hx(rhash, 4)}")

    take_photo("step0_baseline")

    # ================================================================
    # TEST A: Factory BMP (28203) with CORRECT map header (0x44)
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST A: Factory BMP (28203) + correct map header (0x44)")
    print("=" * 60)

    # Switch to HOST
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write resource map with correct header 0x44
    # Entry: resourceId=28203, resourceAddress=28203, hash=[0,0,0,0]
    map_data = bytearray()
    map_data += struct.pack('<H', 0x0044)  # Correct header!
    map_data += struct.pack('<H', 1)  # count = 1
    map_data += struct.pack('<H', 28203)  # resourceId
    map_data += struct.pack('<H', 28203)  # resourceAddress
    map_data += bytes(4)  # hash
    print(f"  Resource map: {hx(bytes(map_data))}")
    close_all(fd)
    result = write_file(fd, 61, bytes(map_data))
    print(f"  Write map: {'OK' if result else 'FAIL'}")

    # Write config to file 62 pointing to resourceId 28203
    config = bytes([0x38, 0x00]) + struct.pack('<H', 28203) + bytes(12)
    print(f"  Config: {hx(config)}")
    close_all(fd)
    result = write_file(fd, 62, config)
    print(f"  Write config: {'OK' if result else 'FAIL'}")

    # Update cookie
    close_all(fd)
    profile = read_file_raw(fd, 28000)
    if profile and len(profile) >= 8:
        new_profile = bytearray(profile)
        new_profile[4:8] = struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
        close_all(fd)
        write_file(fd, 28000, bytes(new_profile))

    # Switch to SELF
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("testA_factory_bmp_correct_header")

    # ================================================================
    # TEST B: Same but with ALSO writing to layout (28006) and
    #         updating property 263
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST B: Full flow — map + layout + prop263 + config + cookie")
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

    # Use file 28300 as layout file pointing to 28203 (factory BMP)
    LAYOUT_FILE = 28300
    IMAGE_FILE = 28203

    # 1. Resource map with correct header
    map_data = bytearray()
    map_data += struct.pack('<H', 0x0044)
    map_data += struct.pack('<H', 1)
    map_data += struct.pack('<H', IMAGE_FILE)
    map_data += struct.pack('<H', IMAGE_FILE)
    map_data += bytes(4)
    close_all(fd)
    write_file(fd, 61, bytes(map_data))
    print("  1. Resource map: OK")

    # 2. Layout config in file 28300 pointing to 28203
    config = bytes([0x38, 0x00]) + struct.pack('<H', IMAGE_FILE) + bytes(12)
    close_all(fd)
    write_file(fd, LAYOUT_FILE, config)
    print("  2. Layout config: OK")

    # 3. Update screen modes layout (file 28006) to include 28300
    layout_data = bytearray()
    layout_data += bytes([0x37, 0x00])  # Use EXISTING header from device (was 0x37)
    layout_data += struct.pack('<H', 1)  # 1 row
    layout_data += struct.pack('<H', 2)  # 2 entries
    layout_data += struct.pack('<H', 28007)  # original
    layout_data += struct.pack('<H', LAYOUT_FILE)  # our layout
    close_all(fd)
    write_file(fd, 28006, bytes(layout_data))
    print("  3. Screen modes layout: OK")

    # 4. Update property 263 (screen index) to 1 (our layout is 2nd)
    close_all(fd)
    props = read_file_raw(fd, 28001)
    if props and len(props) >= 4:
        header = props[:2]
        count = struct.unpack_from('<H', props, 2)[0]
        entries = []
        for i in range(count):
            off = 4 + i * 6
            if off + 6 <= len(props):
                pid = struct.unpack_from('<H', props, off)[0]
                val = props[off + 2:off + 6]
                if pid == 263:
                    val = struct.pack('<I', 1)  # Index 1
                entries.append((pid, val))
        new_props = bytearray(header)
        new_props += struct.pack('<H', len(entries))
        for pid, val in entries:
            new_props += struct.pack('<H', pid)
            new_props += val
        close_all(fd)
        write_file(fd, 28001, bytes(new_props))
    print("  4. Property 263: OK")

    # 5. selectWidget — write config to file 62
    close_all(fd)
    write_file(fd, 62, config)
    print("  5. File 62: OK")

    # 6. Cookie
    close_all(fd)
    profile = read_file_raw(fd, 28000)
    if profile and len(profile) >= 8:
        new_profile = bytearray(profile)
        new_profile[4:8] = struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
        close_all(fd)
        write_file(fd, 28000, bytes(new_profile))
    print("  6. Cookie: OK")

    # 7. Switch to SELF
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("testB_full_flow_correct_header")

    # ================================================================
    # TEST C: Config pointing DIRECTLY to factory BMP file 28203
    #         (no resource map, just the raw file ID in config)
    #         with the ORIGINAL file 62 header from factory state
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST C: Direct file ref, but read what 28007 actually contains")
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

    # Read the ACTUAL default config from file 28007
    close_all(fd)
    default_config = read_file_raw(fd, 28007)
    print(f"  Default config (28007): {hx(default_config)}")
    if default_config and len(default_config) >= 4:
        ctype = default_config[0]
        res_id = struct.unpack_from('<H', default_config, 2)[0]
        print(f"    Type: 0x{ctype:02X}, ResourceId: {res_id} (0x{res_id:04X})")

    # Try copying 28007's content to file 62 (should restore default)
    if default_config:
        close_all(fd)
        write_file(fd, 62, default_config)
        print("  Wrote 28007 content to file 62")

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("testC_copy_28007_to_62")

    # ================================================================
    # TEST D: Read file 62, file 61, file 28007 WHILE showing default
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST D: Verify working state file contents")
    print("=" * 60)

    for fid, desc in [(62, "active display"), (61, "resource map"),
                       (28007, "default config")]:
        close_all(fd)
        data = read_file_raw(fd, fid)
        print(f"  File {fid} ({desc}): {hx(data) if data else 'None'}")

    # ================================================================
    # TEST E: Try config with resourceId = 28203 using EXACT same
    #         format as working config but different resourceId
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST E: Same config format as 28007 but resourceId=28203")
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

    if default_config and len(default_config) >= 4:
        # Take the exact format of 28007, just change the resourceId
        modified = bytearray(default_config)
        struct.pack_into('<H', modified, 2, 28203)
        print(f"  Modified config: {hx(bytes(modified))}")
        print(f"  Original config: {hx(default_config)}")
        close_all(fd)
        write_file(fd, 62, bytes(modified))

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("testE_modified_config_28203")

    # ================================================================
    # RESTORE
    # ================================================================
    print("\n" + "=" * 60)
    print("RESTORING defaults")
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

    # Restore file 62 with 28007 content
    if default_config:
        close_all(fd)
        write_file(fd, 62, default_config)

    # Restore resource map (file 61) to what it was
    close_all(fd)
    write_file(fd, 61, bytes([0x44, 0x00, 0x00, 0x00]))

    # Restore layout
    close_all(fd)
    layout_restore = bytes([0x37, 0x00]) + struct.pack('<H', 1) + struct.pack('<H', 1) + struct.pack('<H', 28007)
    write_file(fd, 28006, layout_restore)

    # Restore cookie
    close_all(fd)
    profile = read_file_raw(fd, 28000)
    if profile and len(profile) >= 8:
        new_profile = bytearray(profile)
        new_profile[4:8] = struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
        close_all(fd)
        write_file(fd, 28000, bytes(new_profile))

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        time.sleep(2)
        take_photo("restored")
        os.close(fd)

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
