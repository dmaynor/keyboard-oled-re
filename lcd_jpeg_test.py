#!/usr/bin/env python3
"""
Corsair Vanguard 96 LCD — JPEG Image Write Test

The LCD is 320x170 pixels. Resource 0x3F (84,320 bytes) likely stores
JPEG-compressed image data.

This script writes a JPEG image to the LCD resource and tries various
commit approaches.
"""

import os
import sys
import time
import struct
import subprocess

HIDRAW = "/dev/hidraw3"
BRAGI_MAGIC = 0x08
OUT_PKT_SIZE = 64
RES_LCD = 0x3F
HANDLE = 0x01

CMD_SET = 0x01
CMD_GET = 0x02
CMD_CLOSE = 0x05
CMD_WRITE = 0x06
CMD_READ = 0x08
CMD_PROBE = 0x09
CMD_OPEN = 0x0D


def send_recv(fd, data, timeout=1.0):
    pad = max(0, OUT_PKT_SIZE - len(data))
    pkt = bytes([0x00]) + bytes(data) + bytes(pad)
    os.write(fd, pkt)
    end = time.time() + timeout
    while time.time() < end:
        try:
            return os.read(fd, 1024)
        except BlockingIOError:
            time.sleep(0.01)
    return None


def status_ok(resp):
    return resp is not None and len(resp) > 2 and resp[2] == 0x00


def write_resource(fd, data):
    """Write data to HANDLE using chunked Bragi transfers."""
    total = len(data)
    first_pkt = [BRAGI_MAGIC, CMD_WRITE, HANDLE]
    first_pkt += list(struct.pack('<I', total))
    chunk_size = min(total, OUT_PKT_SIZE - 7)
    first_pkt += list(data[:chunk_size])
    resp = send_recv(fd, first_pkt)
    if not status_ok(resp):
        print(f"  First write failed")
        return False

    offset = chunk_size
    while offset < total:
        chunk = min(total - offset, OUT_PKT_SIZE - 3)
        cont_pkt = [BRAGI_MAGIC, CMD_WRITE, HANDLE]
        cont_pkt += list(data[offset:offset + chunk])
        resp = send_recv(fd, cont_pkt)
        if not status_ok(resp):
            print(f"  Write at {offset} failed")
            return False
        offset += chunk
    return True


def take_photo(name):
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")
    return path


def main():
    jpeg_path = sys.argv[1] if len(sys.argv) > 1 else "test_red.jpg"

    with open(jpeg_path, 'rb') as f:
        jpeg_data = f.read()

    print(f"=== LCD JPEG Write Test ===")
    print(f"Image: {jpeg_path} ({len(jpeg_data)} bytes)")
    print(f"JPEG header: {' '.join(f'{b:02x}' for b in jpeg_data[:8])}")

    fd = os.open(HIDRAW, os.O_RDWR | os.O_NONBLOCK)

    try:
        # Check mode
        resp = send_recv(fd, [BRAGI_MAGIC, CMD_GET, 0x01, 0x00])
        mode = resp[3] if resp and len(resp) > 3 and resp[2] == 0x00 else None
        print(f"Mode: {mode}")

        # Ensure software mode
        if mode != 1:
            print("Switching to software mode...")
            send_recv(fd, [BRAGI_MAGIC, CMD_SET, 0x01, 0x00, 0x01])
            os.close(fd)
            time.sleep(3)
            subprocess.run(["sudo", "chmod", "666", HIDRAW], capture_output=True)
            fd = os.open(HIDRAW, os.O_RDWR | os.O_NONBLOCK)
            time.sleep(1)

        print("\n[1] Before photo...")
        take_photo("jpeg_before")

        # Close stale handle, open LCD
        send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00], timeout=0.2)
        time.sleep(0.1)
        resp = send_recv(fd, [BRAGI_MAGIC, CMD_OPEN, HANDLE, RES_LCD, 0x00, 0x00])
        if not status_ok(resp):
            print("OPEN FAILED")
            return
        print("\n[2] LCD handle opened")

        # Write JPEG data
        print(f"\n[3] Writing JPEG ({len(jpeg_data)} bytes)...")
        t0 = time.time()
        if not write_resource(fd, jpeg_data):
            print("  WRITE FAILED")
            return
        print(f"  Write done in {time.time()-t0:.1f}s")

        # Try commit
        print("\n[4] Trying commits...")
        for cmd_id, name in [(0x04, "CMD_04"), (0x07, "CMD_07")]:
            resp = send_recv(fd, [BRAGI_MAGIC, cmd_id, HANDLE, 0x00])
            ok = status_ok(resp)
            print(f"  {name}: {'OK' if ok else 'FAIL'}")

        time.sleep(1)
        take_photo("jpeg_after_commit")

        # Try close-reopen cycle
        print("\n[5] Close handle...")
        send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00])
        time.sleep(2)
        take_photo("jpeg_after_close")

        # Try setting LCD-related properties
        print("\n[6] Trying LCD properties...")

        # Property 0x40 = 62 (0x3E). Try setting it to 0x3F (LCD resource ID)?
        resp = send_recv(fd, [BRAGI_MAGIC, CMD_SET, 0x40, 0x00, 0x3F])
        print(f"  SET 0x40=0x3F: {'OK' if status_ok(resp) else 'FAIL'}")
        time.sleep(1)
        take_photo("jpeg_set40")

        # Property 0x41 = 1. Try toggling it.
        resp = send_recv(fd, [BRAGI_MAGIC, CMD_SET, 0x41, 0x00, 0x00])
        print(f"  SET 0x41=0: {'OK' if status_ok(resp) else 'FAIL'}")
        time.sleep(1)
        resp = send_recv(fd, [BRAGI_MAGIC, CMD_SET, 0x41, 0x00, 0x01])
        print(f"  SET 0x41=1: {'OK' if status_ok(resp) else 'FAIL'}")
        time.sleep(1)
        take_photo("jpeg_set41")

        # Try properties near 0xF0 (where LCD dimensions are)
        # 0xF0, 0xFA, 0xFB might be LCD control
        for prop, val in [(0xFA, 0x00), (0xFB, 0x01), (0xF0, 0x00)]:
            resp = send_recv(fd, [BRAGI_MAGIC, CMD_SET, prop, 0x00, val])
            print(f"  SET 0x{prop:02X}={val}: {'OK' if status_ok(resp) else 'FAIL'}")

        time.sleep(2)
        take_photo("jpeg_after_props")

        print("\n[7] Done")

    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
