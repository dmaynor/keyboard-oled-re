#!/usr/bin/env python3
"""
LCD Verify + Property Probe — Two goals:
  1. Verify file writes round-trip correctly (write→read→compare)
  2. Probe LCD-related properties to find animation control
  3. Try disabling animation + writing framebuffer
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
    # Use offset 5 for actual data size (offset 4 is sector/allocated size)
    size = struct.unpack_from('<I', resp, 5)[0]
    if size == 0 or size > 500000:
        # Fallback to offset 4
        size = struct.unpack_from('<I', resp, 4)[0]
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
    resp = sr(fd, pkt)
    return resp


def get_prop(fd, prop_id):
    pkt = [BRAGI, CMD_GET] + list(struct.pack('<H', prop_id))
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/12-verify/{name}.jpg"
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

    # ============ TEST 1: Write round-trip verification ============
    print("\n" + "=" * 60)
    print("TEST 1: Write/Read round-trip verification")
    print("=" * 60)

    test_file = 28300
    # Write a known pattern
    pattern = bytes(range(256)) * 4  # 1024 bytes: 00 01 02 ... FF 00 01 ...
    close_all(fd)
    delete_file(fd, test_file)
    result = write_file(fd, test_file, pattern)
    print(f"  Write {len(pattern)} bytes to file {test_file}: {'OK' if result else 'FAIL'}")

    # Read it back
    readback = read_file(fd, test_file)
    if readback is not None:
        print(f"  Read back {len(readback)} bytes")
        if readback == pattern:
            print(f"  *** ROUND-TRIP: MATCH *** (data integrity OK)")
        else:
            mismatches = sum(1 for a, b in zip(pattern, readback) if a != b)
            print(f"  *** ROUND-TRIP: MISMATCH *** ({mismatches} bytes differ)")
            print(f"  Expected first 32: {hx(pattern)}")
            print(f"  Got first 32:      {hx(readback)}")
    else:
        print(f"  Read FAILED")

    # Also test with a larger file (BMP-sized)
    print(f"\n  Large file round-trip test (163260 bytes)...")
    large_pattern = bytes([(i * 7 + 3) & 0xFF for i in range(163260)])
    close_all(fd)
    delete_file(fd, test_file)
    result = write_file(fd, test_file, large_pattern)
    print(f"  Write: {'OK' if result else 'FAIL'}")

    readback = read_file(fd, test_file)
    if readback is not None:
        print(f"  Read back {len(readback)} bytes")
        if len(readback) == len(large_pattern) and readback == large_pattern:
            print(f"  *** LARGE ROUND-TRIP: MATCH ***")
        else:
            if len(readback) != len(large_pattern):
                print(f"  Size mismatch: wrote {len(large_pattern)}, read {len(readback)}")
            else:
                mismatches = sum(1 for a, b in zip(large_pattern, readback) if a != b)
                print(f"  *** LARGE ROUND-TRIP: {mismatches} mismatches ***")
                # Find first mismatch
                for i in range(len(readback)):
                    if readback[i] != large_pattern[i]:
                        print(f"  First mismatch at byte {i}: expected 0x{large_pattern[i]:02X}, got 0x{readback[i]:02X}")
                        break
    else:
        print(f"  Read FAILED")

    # Cleanup
    delete_file(fd, test_file)

    # ============ TEST 2: LCD Property Probe ============
    print("\n" + "=" * 60)
    print("TEST 2: LCD-related property probe")
    print("=" * 60)

    # Known LCD properties
    lcd_props = [
        (0x3E, "Property 62 (active file?)"),
        (0x3F, "Property 63 (LCD resource?)"),
        (0x40, "Property 64 (unknown LCD)"),
        (0x41, "Property 65 (LCD toggle)"),
        (0xE6, "Property 230 (screen present)"),
        (0xF0, "Property 240 (startup animation)"),
        (0xF1, "Property 241 (unknown)"),
        (0xF2, "Property 242 (width)"),
        (0xF3, "Property 243 (height)"),
        (0xF4, "Property 244 (unknown)"),
        (0xF5, "Property 245 (unknown)"),
        (0x107, "Property 263 (screen index)"),
        (0x108, "Property 264 (unknown)"),
        (0x109, "Property 265 (unknown)"),
    ]

    for prop_id, desc in lcd_props:
        resp = get_prop(fd, prop_id)
        if resp and len(resp) > 3:
            status = resp[2]
            data = resp[4:8]
            val = struct.unpack_from('<I', resp, 4)[0] if len(resp) >= 8 else 0
            print(f"  {desc}: status=0x{status:02X}, raw={hx(resp, 12)}, value={val}")
        else:
            print(f"  {desc}: no response")

    # ============ TEST 3: Try setting animation property ============
    print("\n" + "=" * 60)
    print("TEST 3: Disable animation + framebuffer write")
    print("=" * 60)

    # Try setting startup animation to 0 (disable)
    print("  Trying to set property 0xF0 (startup animation) = 0...")
    resp = set_prop(fd, 0xF0, struct.pack('<I', 0))
    print(f"    Response: {hx(resp)}")

    # Try setting property 0x41 (LCD toggle) to different values
    for val in [0, 2, 3]:
        print(f"  Trying property 0x41 = {val}...")
        resp = set_prop(fd, 0x41, struct.pack('<I', val))
        print(f"    Response: {hx(resp)}")
        time.sleep(1)
        take_photo(f"prop41_val{val}")
        # Restore
        set_prop(fd, 0x41, struct.pack('<I', 1))

    # ============ TEST 4: HOST mode + write file 62 + read-verify ============
    print("\n" + "=" * 60)
    print("TEST 4: Write 320x170 Corsair BMP in HOST mode, verify, SELF mode")
    print("=" * 60)

    # Create 320x170 Corsair BMP (solid magenta to match factory pixel pattern)
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
    # Solid green in GRB: G=0xFF, R=0x00, B=0x00
    row = bytes([0xFF, 0x00, 0x00]) * W
    for _ in range(H):
        bmp += row
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    bmp = bytes(bmp)
    print(f"  BMP: 320x170, {len(bmp)} bytes, solid green")

    IMAGE_FILE = 28300
    CONFIG_FILE = 28301

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

    # Write image
    close_all(fd)
    delete_file(fd, IMAGE_FILE)
    result = write_file(fd, IMAGE_FILE, bmp)
    print(f"  Write BMP to file {IMAGE_FILE}: {'OK' if result else 'FAIL'}")

    # Verify round-trip
    readback = read_file(fd, IMAGE_FILE)
    if readback:
        if readback == bmp:
            print(f"  *** ROUND-TRIP VERIFIED: {len(readback)} bytes match ***")
        else:
            print(f"  MISMATCH! Wrote {len(bmp)}, read {len(readback)}")
            if len(readback) == len(bmp):
                mismatches = sum(1 for a, b in zip(bmp, readback) if a != b)
                print(f"    {mismatches} byte mismatches")

    # Write config: [56, 0, IMAGE_FILE_lo, IMAGE_FILE_hi, 0*12]
    config = bytearray(16)
    config[0] = 56; config[1] = 0
    struct.pack_into('<H', config, 2, IMAGE_FILE)
    config = bytes(config)

    # Write config to file 62
    close_all(fd)
    write_file(fd, 62, config)
    print(f"  Config written to file 62: {hx(config)}")

    # Update resource map (file 61)
    new_map = bytearray(12)
    new_map[0:2] = bytes([0x44, 0x00])
    struct.pack_into('<H', new_map, 2, 1)
    struct.pack_into('<H', new_map, 4, IMAGE_FILE)
    struct.pack_into('<H', new_map, 6, IMAGE_FILE)
    close_all(fd)
    write_file(fd, 61, bytes(new_map))
    print(f"  Resource map updated")

    # Update layout (file 28006)
    new_layout = bytes([0x37, 0x00, 0x01, 0x01]) + struct.pack('<H', CONFIG_FILE)
    close_all(fd)
    write_file(fd, 28006, new_layout)

    # Write config file too
    close_all(fd)
    delete_file(fd, CONFIG_FILE)
    write_file(fd, CONFIG_FILE, config)
    print(f"  Config file {CONFIG_FILE} written")

    # Cookie update
    close_all(fd)
    profile_data = read_file(fd, 28000)
    if profile_data and len(profile_data) > 6:
        new_cookie = int(time.time()) & 0xFFFFFFFF
        updated = bytearray(profile_data)
        struct.pack_into('<I', updated, 2, new_cookie)
        close_all(fd)
        write_file(fd, 28000, bytes(updated))
        print(f"  Cookie updated: 0x{new_cookie:08X}")

    take_photo("test4_host_after_writes")

    # Switch to SELF
    print("  Switching to SELF_OPERATED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("test4_self_operated")

    # Read file 62 to verify it persisted
    data_62 = read_file(fd, 62)
    if data_62:
        print(f"  File 62 after mode switch: {hx(data_62, 16)}")

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

    default_config = bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12)
    close_all(fd)
    write_file(fd, 62, default_config)
    default_layout = bytes([0x37, 0x00, 0x01, 0x01, 0x67, 0x6D])
    close_all(fd)
    write_file(fd, 28006, default_layout)

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
