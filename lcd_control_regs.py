#!/usr/bin/env python3
"""
LCD Control Registers — Read and analyze the small hardware resources.

Resources 0x0F (6B), 0x11 (66B), 0x22 (66B), 0x2E (66B) could be LCD
controller configuration registers. One might control the animation source
or allow disabling the animation loop.

Also: Try modifying resource 0x0F and 0x11 to see if display behavior changes.
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


def read_resource(fd, res_id, handle=1):
    """Read a hardware resource, return bytes."""
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, handle, res_id, 0x00, 0x00])
    if not ok(resp):
        return None
    desc = sr(fd, [BRAGI, CMD_DESCRIBE, handle])
    if not ok(desc) or len(desc) < 9:
        sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
        return None
    # Try different size offsets
    size5 = struct.unpack_from('<I', desc, 5)[0] if len(desc) >= 9 else 0
    size4 = struct.unpack_from('<I', desc, 4)[0] if len(desc) >= 8 else 0
    size = size5 if 0 < size5 < 100000 else size4
    if size == 0 or size > 100000:
        sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
        return bytes()
    data = bytearray()
    while len(data) < size:
        resp = sr(fd, [BRAGI, CMD_READ, handle])
        if resp is None:
            break
        needed = size - len(data)
        data.extend(resp[3:][:needed])
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    return bytes(data)


def write_resource(fd, res_id, data, handle=1):
    """Write to a hardware resource."""
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


def get_prop(fd, prop_id):
    pkt = [BRAGI, CMD_GET] + list(struct.pack('<H', prop_id))
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/17-control-regs/{name}.jpg"
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
    # PHASE 1: Read all small resources and dump their contents
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 1: Read all small hardware resources")
    print("=" * 60)

    resources = {}
    for res_id in [0x02, 0x0F, 0x11, 0x22, 0x2E, 0x3F]:
        print(f"\n  Resource 0x{res_id:02X}:")
        close_all(fd)
        data = read_resource(fd, res_id)
        if data is None:
            print(f"    OPEN failed")
            continue
        resources[res_id] = data
        print(f"    Size: {len(data)} bytes")
        if len(data) <= 140:
            # Full hex dump with ASCII
            for i in range(0, len(data), 16):
                hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
                ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
                print(f"    {i:04x}: {hex_part:<48s} {ascii_part}")
            # Also as uint16 LE pairs
            if len(data) >= 4:
                print(f"    As uint16 LE:")
                for i in range(0, min(len(data), 66), 2):
                    if i + 2 <= len(data):
                        val = struct.unpack_from('<H', data, i)[0]
                        print(f"      [{i:3d}]: 0x{val:04X} ({val:5d})", end="")
                        if (i // 2) % 4 == 3:
                            print()
                print()
        else:
            print(f"    First 32 bytes: {hx(data, 32)}")
            print(f"    (resource 0x3F = LCD framebuffer, skipping full dump)")

    # Save resource dumps
    os.makedirs("resource_dumps", exist_ok=True)
    for res_id, data in resources.items():
        path = f"resource_dumps/res_{res_id:02X}.bin"
        with open(path, 'wb') as f:
            f.write(data)
        print(f"  Saved {path}")

    # ================================================================
    # PHASE 2: Analyze resource structures
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 2: Analyze resource structures")
    print("=" * 60)

    for res_id in [0x0F, 0x11, 0x22, 0x2E]:
        data = resources.get(res_id)
        if not data:
            continue
        print(f"\n  Resource 0x{res_id:02X} ({len(data)} bytes):")

        # Check if it looks like a config structure
        if len(data) >= 2:
            print(f"    First uint16: 0x{struct.unpack_from('<H', data, 0)[0]:04X}")

        # Check for patterns
        unique_bytes = set(data)
        print(f"    Unique bytes: {len(unique_bytes)} ({', '.join(f'0x{b:02X}' for b in sorted(unique_bytes)[:20])})")

        # Check if values are in any meaningful range
        if len(data) == 66:
            # 66 bytes = 33 uint16 values, or header(2) + 32 uint16 values
            print(f"    Possible format: header(2) + 32 entries of uint16:")
            header = struct.unpack_from('<H', data, 0)[0]
            print(f"      Header: 0x{header:04X}")
            vals = []
            for i in range(2, min(66, len(data)), 2):
                val = struct.unpack_from('<H', data, i)[0]
                vals.append(val)
            print(f"      Values: {vals[:16]}")
            if len(vals) > 16:
                print(f"              {vals[16:]}")
        elif len(data) == 6:
            print(f"    6 bytes = possible 3 uint16 or 1.5 uint32:")
            for i in range(0, 6, 2):
                val = struct.unpack_from('<H', data, i)[0]
                print(f"      [{i}]: 0x{val:04X} ({val})")

    # ================================================================
    # PHASE 3: Try modifying resource 0x0F (smallest, 6 bytes)
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 3: Modify resource 0x0F (6 bytes) — test display effect")
    print("=" * 60)

    original_0f = resources.get(0x0F, bytes(6))
    print(f"  Original 0x0F: {hx(original_0f)}")

    take_photo("phase3_baseline")

    # Try writing all zeros
    print("\n  Writing all zeros to 0x0F...")
    close_all(fd)
    result = write_resource(fd, 0x0F, bytes(6))
    print(f"  Write: {'OK' if result else 'FAIL'}")
    time.sleep(1)
    take_photo("phase3_0f_zeros")

    # Read back
    close_all(fd)
    readback = read_resource(fd, 0x0F)
    print(f"  Readback: {hx(readback)}")

    # Restore
    close_all(fd)
    write_resource(fd, 0x0F, original_0f)

    # ================================================================
    # PHASE 4: Try modifying resource 0x11 (66 bytes)
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 4: Modify resource 0x11 (66 bytes)")
    print("=" * 60)

    original_11 = resources.get(0x11, bytes(66))
    print(f"  Original 0x11 first 32: {hx(original_11, 32)}")

    # Try writing all zeros
    print("\n  Writing all zeros to 0x11...")
    close_all(fd)
    result = write_resource(fd, 0x11, bytes(66))
    print(f"  Write: {'OK' if result else 'FAIL'}")
    time.sleep(1)
    take_photo("phase4_0x11_zeros")

    close_all(fd)
    readback = read_resource(fd, 0x11)
    print(f"  Readback: {hx(readback, 32)}")

    # Restore
    close_all(fd)
    write_resource(fd, 0x11, original_11)

    # ================================================================
    # PHASE 5: Deep property scan — find animation/LCD controls
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 5: Deep property scan (0-300)")
    print("=" * 60)

    responsive_props = []
    for pid in range(300):
        resp = get_prop(fd, pid)
        if ok(resp):
            raw = resp[3:11] if len(resp) >= 11 else resp[3:]
            val_le32 = struct.unpack_from('<I', raw, 0)[0] if len(raw) >= 4 else 0
            responsive_props.append((pid, val_le32, raw))

    print(f"  Found {len(responsive_props)} responsive properties:")
    for pid, val, raw in responsive_props:
        print(f"    Property {pid:3d} (0x{pid:02X}): val=0x{val:08X} ({val:10d})  raw={hx(bytes(raw), 8)}")

    # ================================================================
    # PHASE 6: Try SET on display-related properties
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 6: Try SET on display-related properties")
    print("=" * 60)

    # Try setting some potentially animation-related properties
    test_sets = [
        (230, 0, "screen present = 0 (disable?)"),
        (230, 2, "screen present = 2"),
        (234, 0, "property 234 = 0"),
        (234, 1, "property 234 = 1"),
        (260, 0, "property 260 = 0"),
        (260, 1, "property 260 = 1"),
        (261, 1, "property 261 = 1"),
        (265, 0, "active theme = 0"),
        (265, 1, "active theme = 1"),
    ]

    for pid, val, desc in test_sets:
        print(f"\n  SET property {pid} = {val} ({desc})")
        resp = set_prop(fd, pid, struct.pack('<I', val))
        status = resp[2] if resp and len(resp) > 2 else -1
        print(f"    Response: status={status} raw={hx(resp, 8)}")
        if status == 0:
            time.sleep(1)
            take_photo(f"phase6_prop{pid}_val{val}")
            # Read back
            rb = get_prop(fd, pid)
            if ok(rb):
                print(f"    Readback: {hx(rb, 8)}")

    # ================================================================
    # PHASE 7: Try opening 0x3F with different flags/params
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 7: Open resource 0x3F with different parameters")
    print("=" * 60)

    # Normal open: [BRAGI, CMD_OPEN, handle, res_id, 0x00, 0x00]
    # Try different 4th and 5th byte values (might be mode flags)
    for b4, b5 in [(0x00, 0x00), (0x01, 0x00), (0x00, 0x01), (0x01, 0x01),
                    (0x02, 0x00), (0x00, 0x02), (0xFF, 0x00), (0x00, 0xFF)]:
        sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)
        resp = sr(fd, [BRAGI, CMD_OPEN, 1, 0x3F, b4, b5])
        status = resp[2] if resp and len(resp) > 2 else -1
        if status == 0:
            desc = sr(fd, [BRAGI, CMD_DESCRIBE, 1])
            size = struct.unpack_from('<I', desc, 5)[0] if ok(desc) and len(desc) >= 9 else 0
            print(f"  OPEN(0x3F, {b4:02X}, {b5:02X}): OK, size={size}")
        else:
            print(f"  OPEN(0x3F, {b4:02X}, {b5:02X}): status={status}")
        sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)

    # ================================================================
    # PHASE 8: Try writing to 0x3F with 320×170 instead of 248×170
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 8: Write 320x170 RGB565 to 0x3F (full panel width)")
    print("=" * 60)

    # Resource 0x3F is 84,320 bytes = 248×170×2
    # But property 242 says width=320. What about 320×170×2 = 108,800?
    # Or maybe the firmware clips to resource size.
    # Try writing exactly 84,320 bytes of a visible pattern
    W248, H170 = 248, 170
    frame = bytearray()
    for y in range(H170):
        for x in range(W248):
            # Red and blue vertical stripes, 20px wide
            if (x // 20) % 2 == 0:
                pixel = 0xF800  # Red
            else:
                pixel = 0x001F  # Blue
            frame += struct.pack('<H', pixel)

    print(f"  Frame: {len(frame)} bytes ({W248}x{H170} RGB565)")
    close_all(fd)
    result = write_resource(fd, 0x3F, bytes(frame))
    print(f"  Write: {'OK' if result else 'FAIL'}")
    # Photo in SELF mode
    take_photo("phase8_stripes_248x170")

    # Also try 320×132 (320*132*2 = 84,480 — close to 84,320)
    # Actually 84320 / 2 / 320 = 131.75... not even
    # 84320 / 2 / 248 = 170.0 — confirms 248×170
    # 84320 / 2 / 170 = 248.0 — confirms

    # ================================================================
    # PHASE 9: Read 0x3F immediately after write to check persistence
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 9: Read 0x3F after write — check if animation overwrites")
    print("=" * 60)

    # Write a known pattern
    pattern = bytes([0xAA, 0x55]) * (248 * 170)  # alternating bytes
    close_all(fd)
    result = write_resource(fd, 0x3F, pattern)
    print(f"  Write pattern: {'OK' if result else 'FAIL'}")

    # Immediately read back first 32 bytes
    close_all(fd)
    sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, 1, 0x3F, 0x00, 0x00])
    if ok(resp):
        desc = sr(fd, [BRAGI, CMD_DESCRIBE, 1])
        resp = sr(fd, [BRAGI, CMD_READ, 1])
        if resp:
            first_bytes = resp[3:35]
            matches = sum(1 for i in range(0, min(32, len(first_bytes)), 2)
                         if first_bytes[i:i+2] == bytes([0xAA, 0x55]))
            print(f"  Readback first 32: {hx(bytes(first_bytes))}")
            print(f"  Pattern matches: {matches}/16 pairs")
            if matches == 0:
                print(f"  *** Animation has ALREADY overwritten our data! ***")
            elif matches == 16:
                print(f"  *** Our data persists! Animation not running? ***")
            else:
                print(f"  *** Partial overwrite — race condition ***")
    sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)

    # Wait and read again
    time.sleep(0.5)
    close_all(fd)
    sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, 1, 0x3F, 0x00, 0x00])
    if ok(resp):
        sr(fd, [BRAGI, CMD_DESCRIBE, 1])
        resp = sr(fd, [BRAGI, CMD_READ, 1])
        if resp:
            first_bytes = resp[3:35]
            matches = sum(1 for i in range(0, min(32, len(first_bytes)), 2)
                         if first_bytes[i:i+2] == bytes([0xAA, 0x55]))
            print(f"  After 0.5s: {hx(bytes(first_bytes))}")
            print(f"  Pattern matches: {matches}/16")
    sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)

    # ================================================================
    # PHASE 10: Try all SET properties near LCD range with value 0
    #           to try to disable animation
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 10: Brute-force SET to disable animation")
    print("=" * 60)

    # Save current property values first
    saved_props = {}
    for pid in range(220, 270):
        resp = get_prop(fd, pid)
        if ok(resp):
            saved_props[pid] = resp[3:7] if len(resp) >= 7 else resp[3:]

    print(f"  Saved {len(saved_props)} property values")

    # Try setting each to 0 and check if animation stops
    for pid in sorted(saved_props.keys()):
        resp = set_prop(fd, pid, struct.pack('<I', 0))
        if ok(resp):
            print(f"  SET property {pid} = 0: OK")

    # Now try writing to 0x3F and check if it persists
    time.sleep(1)
    pattern = bytes([0xAA, 0x55]) * (248 * 170)
    close_all(fd)
    result = write_resource(fd, 0x3F, pattern)
    print(f"  Write pattern after zeroing props: {'OK' if result else 'FAIL'}")

    close_all(fd)
    sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, 1, 0x3F, 0x00, 0x00])
    if ok(resp):
        sr(fd, [BRAGI, CMD_DESCRIBE, 1])
        resp = sr(fd, [BRAGI, CMD_READ, 1])
        if resp:
            first_bytes = resp[3:35]
            matches = sum(1 for i in range(0, min(32, len(first_bytes)), 2)
                         if first_bytes[i:i+2] == bytes([0xAA, 0x55]))
            print(f"  Readback: {hx(bytes(first_bytes))}")
            print(f"  Pattern matches: {matches}/16")
    sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)

    # Restore properties
    print("\n  Restoring properties...")
    for pid, val in saved_props.items():
        set_prop(fd, pid, val)

    take_photo("phase10_after_restore")

    print("\n=== DONE ===")
    os.close(fd)


if __name__ == "__main__":
    main()
