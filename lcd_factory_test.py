#!/usr/bin/env python3
"""
LCD Factory Test — Point file 62 config to the EXISTING factory image (28203).

If this works: our BMP format is wrong, but the file-loading mechanism works.
If this fails: the file-loading mechanism itself is broken for all files.

Also test: config pointing to nonexistent file vs existing file.
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


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/14-factory-test/{name}.jpg"
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


def do_test(fd, name, config_bytes):
    """Write config to file 62, switch HOST→SELF, take photo."""
    print(f"\n--- {name} ---")
    print(f"  Config: {hx(config_bytes)}")

    # Switch to HOST
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!")
        return None
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write config to file 62
    close_all(fd)
    result = write_file(fd, 62, config_bytes)
    print(f"  Write to file 62: {'OK' if result else 'FAIL'}")

    # Switch to SELF
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!")
        return None
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    safe_name = name.replace(" ", "_").replace("→", "to").replace("(", "").replace(")", "")
    take_photo(safe_name)
    return fd


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

    configs = [
        # (name, config_bytes)
        ("Test A: Config → file 28203 (existing BMP on device)",
         bytes([0x38, 0x00]) + struct.pack('<H', 28203) + bytes(12)),

        ("Test B: Config → file 28300 (our test BMP)",
         bytes([0x38, 0x00]) + struct.pack('<H', 28300) + bytes(12)),

        ("Test C: Config → file 99999 (nonexistent file)",
         bytes([0x38, 0x00]) + struct.pack('<H', 60000) + bytes(12)),

        ("Test D: Config → resource 0 (null resource)",
         bytes([0x38, 0x00, 0x00, 0x00]) + bytes(12)),

        ("Test E: Config → resource 1 (LED resource)",
         bytes([0x38, 0x00, 0x01, 0x00]) + bytes(12)),

        ("Test F: Config type 102 (GIF type) → resource 0x3F",
         bytes([0x66, 0x00, 0x3F, 0x00]) + bytes(12)),

        ("Test G: Just 4 bytes config (no padding)",
         bytes([0x38, 0x00, 0x3F, 0x00])),

        ("Test H: Config → resource 0x3F (known working, control test)",
         bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12)),
    ]

    for name, config in configs:
        fd = do_test(fd, name, config)
        if fd is None:
            hidraw = find_hidraw()
            if not hidraw:
                print("FATAL: Can't reconnect")
                return
            fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
            close_all(fd)
            token = bytes(random.randint(0, 255) for _ in range(4))
            sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Restore
    print("\n--- Restoring default (resource 0x3F) ---")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        close_all(fd)
        write_file(fd, 62, bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12))
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
        os.close(fd)

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
