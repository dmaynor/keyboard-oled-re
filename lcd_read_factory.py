#!/usr/bin/env python3
"""
Read factory image file 28203 — this is what the default config (28007) points to.
If we can see the actual format, we'll know exactly what the firmware expects.
Also read a few nearby files for context.
"""

import os
import sys
import time
import struct
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
    """Read a file, returning (data, describe_response) for diagnostics."""
    fid = struct.pack('<H', file_id)
    close_all(fd)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        return None, resp

    # Describe
    desc = sr(fd, [BRAGI, CMD_DESCRIBE, buf])
    if not ok(desc):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return None, desc

    # Try parsing size from different offsets
    sizes = {}
    for off in [3, 4, 5]:
        if off + 4 <= len(desc):
            sizes[off] = struct.unpack_from('<I', desc, off)[0]

    print(f"    DESCRIBE raw: {hx(desc, 16)}")
    print(f"    Size candidates: offset3={sizes.get(3,'-')}, offset4={sizes.get(4,'-')}, offset5={sizes.get(5,'-')}")

    # Use offset 4 as primary, with sanity check
    size = sizes.get(4, 0)
    if size == 0 or size > max_size:
        size = sizes.get(5, 0)
    if size == 0 or size > max_size:
        size = sizes.get(3, 0)
    if size == 0 or size > max_size:
        print(f"    No valid size found, skipping read")
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return bytes(), desc

    print(f"    Reading {size} bytes...")

    data = bytearray()
    read_count = 0
    while len(data) < size:
        resp = sr(fd, [BRAGI, CMD_READ, buf])
        if resp is None:
            print(f"    READ returned None at offset {len(data)}")
            break
        read_count += 1
        chunk = resp[3:]
        needed = size - len(data)
        data.extend(chunk[:needed])
        if read_count % 50 == 0:
            print(f"    ... read {len(data)}/{size} bytes")

    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    print(f"    Read complete: {len(data)} bytes in {read_count} chunks")
    return bytes(data), desc


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

    # Key files to investigate
    files_to_read = [
        (28203, "Factory image (referenced by 28007 config)"),
        (28007, "Default screen config"),
        (62, "Active display"),
        (61, "Screen resource map"),
        (28001, "Properties file (from profile)"),
        (28005, "Possible factory image nearby"),
        (28006, "Screen modes layout"),
        (28000, "Profile file"),
    ]

    for file_id, desc in files_to_read:
        print(f"\n{'='*60}")
        print(f"FILE {file_id}: {desc}")
        print(f"{'='*60}")
        data, raw_desc = read_file_raw(fd, file_id)
        if data is None:
            print(f"  OPEN FAILED: {hx(raw_desc)}")
            continue
        if len(data) == 0:
            print(f"  Empty or invalid size")
            continue

        print(f"  Size: {len(data)} bytes")
        print(f"  First 128 bytes:")
        for i in range(0, min(128, len(data)), 16):
            hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
            print(f"    {i:04x}: {hex_part:<48s} {ascii_part}")

        if len(data) > 128:
            print(f"  Last 64 bytes:")
            start = max(128, len(data) - 64)
            for i in range(start, len(data), 16):
                hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
                ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
                print(f"    {i:04x}: {hex_part:<48s} {ascii_part}")

        # Analyze BMP-like content
        if len(data) >= 4:
            if data[0:2] == bytes([0x48, 0x00]) and data[2:4] == b'BM':
                print(f"\n  *** CORSAIR BMP DETECTED ***")
                if len(data) >= 56:
                    bmp_size = struct.unpack_from('<I', data, 4)[0]
                    pixel_offset = struct.unpack_from('<I', data, 12)[0]
                    width = struct.unpack_from('<i', data, 20)[0]
                    height = struct.unpack_from('<i', data, 24)[0]
                    bpp = struct.unpack_from('<H', data, 30)[0]
                    print(f"  BMP file size: {bmp_size}")
                    print(f"  Pixel offset: {pixel_offset}")
                    print(f"  Width: {width}, Height: {height}")
                    print(f"  BPP: {bpp}")
                    print(f"  Total file: {len(data)} bytes")
                    # Check timestamp at end
                    if len(data) >= bmp_size + 6:
                        ts = struct.unpack_from('<I', data, bmp_size + 2)[0]
                        print(f"  Timestamp: {ts} ({time.ctime(ts)})")
            elif data[0:2] == b'BM':
                print(f"\n  *** STANDARD BMP DETECTED ***")
            elif data[0] == 0x38:
                res_id = struct.unpack_from('<H', data, 2)[0]
                print(f"\n  *** SCREEN CONFIG: type=56, resourceId={res_id} ***")

        # Save large files for further analysis
        if len(data) > 100:
            save_path = f"factory_dumps/file_{file_id}.bin"
            os.makedirs("factory_dumps", exist_ok=True)
            with open(save_path, 'wb') as f:
                f.write(data)
            print(f"  Saved to {save_path}")

    # Also try reading the file that our BMP was written to (28200)
    print(f"\n{'='*60}")
    print(f"FILE 28200: Our image file (verification)")
    print(f"{'='*60}")
    data, _ = read_file_raw(fd, 28200)
    if data and len(data) > 0:
        print(f"  Size: {len(data)} bytes")
        print(f"  First 64 bytes: {hx(data, 64)}")
        if data[0:2] == bytes([0x48, 0x00]) and data[2:4] == b'BM':
            print(f"  *** CORSAIR BMP confirmed ***")

    os.close(fd)
    print("\nDone!")


if __name__ == "__main__":
    main()
