#!/usr/bin/env python3
"""
LCD Two-File Write — correct Web Hub architecture.

The Web Hub uses TWO files for LCD images:
  1. Config file (16 bytes): [56, 0, imageFileId_lo, imageFileId_hi, 0*12]
  2. Image file: Full Corsair BMP data

Activation sequence (from Web Hub JS):
  1. Write BMP to image file
  2. Write 16-byte config to config file (pointing to image file)
  3. Copy config to file 62 (active display) via selectWidget
  4. Set property 263 (screen index) to layout index
  5. Update cookie in profile file
  6. Set SELF_OPERATED mode
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

FILE_62 = 62        # Active display
FILE_28007 = 28007   # Default screen resource (config)
IMAGE_FILE = 28100   # Our new image file

PROP_MODE = 3
PROP_SCREEN_IDX = 263
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
    """Send/receive with 1024-byte padding."""
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


def hx(data, n=20):
    if data is None: return "None"
    return ' '.join(f'{b:02x}' for b in data[:n])


def close_all(fd):
    """Close handles 0-2."""
    for h in range(3):
        sr(fd, [BRAGI, CMD_UNBIND, h, 0x00], timeout=0.3)


def open_file(fd, file_id, buf=0):
    fid = struct.pack('<H', file_id)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    return ok(resp), resp


def create_file(fd, file_id):
    fid = struct.pack('<H', file_id)
    resp = sr(fd, [BRAGI, CMD_CREATE] + list(fid))
    return ok(resp), resp


def delete_file(fd, file_id):
    fid = struct.pack('<H', file_id)
    resp = sr(fd, [BRAGI, CMD_DELETE] + list(fid))
    return ok(resp), resp


def unbind(fd, buf=0):
    resp = sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.5)
    return ok(resp)


def describe(fd, buf=0):
    resp = sr(fd, [BRAGI, CMD_DESCRIBE, buf])
    if ok(resp) and len(resp) > 7:
        return struct.unpack_from('<I', resp, 4)[0]  # size at offset 4
    return None


def read_file(fd, file_id, buf=0):
    """Read entire file contents."""
    success, _ = open_file(fd, file_id, buf)
    if not success:
        return None
    size = describe(fd, buf)
    if size is None or size > 200000:
        unbind(fd, buf)
        return None
    data = bytearray()
    while len(data) < size:
        resp = sr(fd, [BRAGI, CMD_READ, buf])
        if not ok(resp) or len(resp) <= 3:
            break
        chunk = resp[3:]
        remaining = size - len(data)
        data.extend(chunk[:remaining])
    unbind(fd, buf)
    return bytes(data)


def write_file(fd, file_id, data, buf=0):
    """Write data to file: open -> writeBegin -> writeCont -> unbind."""
    success, _ = open_file(fd, file_id, buf)
    if not success:
        # Try create then open
        unbind(fd, buf)
        create_file(fd, file_id)
        success, _ = open_file(fd, file_id, buf)
        if not success:
            return False

    # Write begin
    total = len(data)
    max_first = PKT_SIZE - 2 - 1 - 4  # 1017
    chunk = min(total, max_first)
    pkt = [BRAGI, CMD_WRITE_BEGIN, buf] + list(struct.pack('<I', total)) + list(data[:chunk])
    resp = sr(fd, pkt)
    if not ok(resp):
        unbind(fd, buf)
        return False

    offset = chunk
    while offset < total:
        max_cont = PKT_SIZE - 2 - 1  # 1021
        chunk = min(total - offset, max_cont)
        pkt = [BRAGI, CMD_WRITE_CONT, buf] + list(data[offset:offset + chunk])
        resp = sr(fd, pkt)
        if not ok(resp):
            unbind(fd, buf)
            return False
        offset += chunk

    unbind(fd, buf)
    return True


def get_prop(fd, prop_id):
    pkt = [BRAGI, CMD_GET] + list(struct.pack('<H', prop_id))
    resp = sr(fd, pkt)
    if ok(resp) and len(resp) > 3:
        return resp[3:]
    return None


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    resp = sr(fd, pkt)
    return ok(resp), resp


def take_photo(name):
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")


def create_corsair_bmp(width=248, height=170, color=(255, 0, 0)):
    """Create Corsair custom BMP: [0x48, 0x00] + BMP + GRB pixels + LE32 timestamp."""
    row_size = (width * 3 + 3) & ~3
    pixel_data_size = row_size * height
    file_size = 54 + pixel_data_size

    bmp = bytearray()
    # Corsair prefix
    bmp += bytes([0x48, 0x00])
    # BMP header
    bmp += b'BM'
    bmp += struct.pack('<I', file_size)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    # DIB header
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', width)
    bmp += struct.pack('<i', height)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pixel_data_size)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)
    # Pixel data: bottom-up, GRB order
    r, g, b = color
    padding = row_size - width * 3
    row = bytes([g, r, b]) * width + bytes(padding)
    for _ in range(height):
        bmp += row
    # Timestamp
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    return bytes(bmp)


def create_gradient_bmp(width=248, height=170):
    """Create a gradient BMP so we can clearly see if it displays."""
    row_size = (width * 3 + 3) & ~3
    pixel_data_size = row_size * height
    file_size = 54 + pixel_data_size

    bmp = bytearray()
    bmp += bytes([0x48, 0x00])
    bmp += b'BM'
    bmp += struct.pack('<I', file_size)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', width)
    bmp += struct.pack('<i', height)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pixel_data_size)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)

    padding = row_size - width * 3
    # Bottom-up rows, GRB order — red-green gradient
    for y in range(height - 1, -1, -1):
        for x in range(width):
            r = int(255 * x / width)
            g = int(255 * y / height)
            b = 128
            bmp += bytes([g, r, b])  # GRB
        bmp += bytes(padding)

    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    return bytes(bmp)


def main():
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")

    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)

    try:
        # Baseline
        mode = get_prop(fd, PROP_MODE)
        print(f"Mode: {mode[0] if mode else '?'}")

        print("\n=== PHASE 0: Clean slate ===")
        close_all(fd)
        take_photo("twofile_baseline")

        print("\n=== PHASE 1: Start session ===")
        token = bytes(random.randint(0, 255) for _ in range(4))
        resp = sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])
        print(f"  Session: {hx(resp)}")

        print("\n=== PHASE 2: Read current file 28007 ===")
        data_28007 = read_file(fd, FILE_28007)
        if data_28007:
            print(f"  File 28007 ({len(data_28007)} bytes): {hx(data_28007)}")
        else:
            print("  File 28007 not readable, will create")

        print("\n=== PHASE 3: Read current file 62 ===")
        data_62 = read_file(fd, FILE_62)
        if data_62:
            print(f"  File 62 ({len(data_62)} bytes): {hx(data_62)}")
        else:
            print("  File 62 not readable (expected on fresh boot?)")

        print("\n=== PHASE 4: Create image file ===")
        bmp = create_gradient_bmp()
        print(f"  BMP: {len(bmp)} bytes, header: {hx(bmp, 8)}")

        # Delete old image file if exists, create fresh
        delete_file(fd, IMAGE_FILE)
        result = write_file(fd, IMAGE_FILE, bmp)
        print(f"  Write image to file {IMAGE_FILE}: {'OK' if result else 'FAIL'}")

        print("\n=== PHASE 5: Create config pointing to image ===")
        img_id_bytes = struct.pack('<H', IMAGE_FILE)
        config = bytes([56, 0]) + img_id_bytes + bytes(12)  # 16 bytes
        print(f"  Config: {hx(config, 16)}")

        # Write config to file 28007
        delete_file(fd, FILE_28007)
        result = write_file(fd, FILE_28007, config)
        print(f"  Write config to file 28007: {'OK' if result else 'FAIL'}")

        print("\n=== PHASE 6: selectWidget — copy config to file 62 ===")
        # Read back config from 28007
        config_readback = read_file(fd, FILE_28007)
        if config_readback:
            print(f"  Config readback ({len(config_readback)} bytes): {hx(config_readback, 16)}")
            # Write to file 62
            result = write_file(fd, FILE_62, config_readback)
            print(f"  Write config to file 62: {'OK' if result else 'FAIL'}")
        else:
            print("  Config readback failed, writing directly")
            result = write_file(fd, FILE_62, config)
            print(f"  Direct write to file 62: {'OK' if result else 'FAIL'}")

        print("\n=== PHASE 7: Set screen index property ===")
        # Index 0 (first/only entry in layout list)
        success, resp = set_prop(fd, PROP_SCREEN_IDX, struct.pack('<I', 0))
        print(f"  SET prop 263 = 0: {'OK' if success else 'FAIL'} -> {hx(resp)}")

        time.sleep(2)
        take_photo("twofile_after_config")

        print("\n=== PHASE 8: Try SELF_OPERATED transition ===")
        success, _ = set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
        print(f"  SET mode SELF_OPERATED: {'OK' if success else 'FAIL'}")
        time.sleep(3)

        # Device may have reconnected
        os.close(fd)
        time.sleep(2)
        hidraw = find_hidraw()
        if hidraw:
            subprocess.run(["sudo", "chmod", "666", hidraw], capture_output=True)
            time.sleep(0.5)
        take_photo("twofile_after_self_mode")

        if not hidraw:
            print("  Device disconnected after mode change")
            return

        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)

        print("\n=== PHASE 9: Try HOST_CONTROLLED write then SELF_OPERATED ===")
        # Full Web Hub flow: HOST_CONTROLLED -> write -> SELF_OPERATED
        close_all(fd)

        print("  Setting HOST_CONTROLLED...")
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
        time.sleep(1)
        os.close(fd)
        time.sleep(3)

        hidraw = find_hidraw()
        if not hidraw:
            print("  Lost device after HOST mode")
            return
        subprocess.run(["sudo", "chmod", "666", hidraw], capture_output=True)
        time.sleep(0.5)
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        close_all(fd)

        mode = get_prop(fd, PROP_MODE)
        print(f"  Mode now: {mode[0] if mode else '?'}")
        take_photo("twofile_host_mode")

        # Re-do the full write sequence in HOST mode
        print("  Writing image in HOST_CONTROLLED mode...")
        delete_file(fd, IMAGE_FILE)
        write_file(fd, IMAGE_FILE, bmp)
        delete_file(fd, FILE_28007)
        write_file(fd, FILE_28007, config)
        config_rb = read_file(fd, FILE_28007)
        if config_rb:
            write_file(fd, FILE_62, config_rb)
        set_prop(fd, PROP_SCREEN_IDX, struct.pack('<I', 0))
        print("  All writes done in HOST mode")
        take_photo("twofile_host_written")

        # Now switch to SELF_OPERATED — this should be the refresh trigger
        print("  Switching to SELF_OPERATED (refresh trigger)...")
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
        os.close(fd)
        time.sleep(4)

        hidraw = find_hidraw()
        if hidraw:
            subprocess.run(["sudo", "chmod", "666", hidraw], capture_output=True)
            time.sleep(1)
        take_photo("twofile_self_refresh")

        if not hidraw:
            print("  Device gone after SELF mode switch")
            return

        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)

        print("\n=== PHASE 10: Alternative — write solid red BMP directly to file 62 ===")
        close_all(fd)
        red_bmp = create_corsair_bmp(248, 170, (255, 0, 0))
        print(f"  Red BMP: {len(red_bmp)} bytes")
        result = write_file(fd, FILE_62, red_bmp)
        print(f"  Write red BMP to file 62: {'OK' if result else 'FAIL'}")
        time.sleep(2)
        take_photo("twofile_red_direct")

        # Try mode toggle as refresh
        set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
        os.close(fd)
        time.sleep(3)
        hidraw = find_hidraw()
        if hidraw:
            subprocess.run(["sudo", "chmod", "666", hidraw], capture_output=True)
            time.sleep(0.5)
            fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
            set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
            os.close(fd)
            time.sleep(4)
            hidraw = find_hidraw()
            if hidraw:
                subprocess.run(["sudo", "chmod", "666", hidraw], capture_output=True)
            take_photo("twofile_red_after_toggle")

        print("\n=== DONE ===")
        take_photo("twofile_final")
        print("Complete!")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            os.close(fd)
        except:
            pass


if __name__ == "__main__":
    main()
