#!/usr/bin/env python3
"""Read the full LCD resource (0x3F) and dump to a file for analysis."""

import os
import sys
import time
import struct

HIDRAW = "/dev/hidraw3"
BRAGI_MAGIC = 0x08
OUT_PKT_SIZE = 64
LCD_SIZE = 84320
RES_LCD = 0x3F
HANDLE = 0x01

CMD_CLOSE = 0x05
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


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "lcd_dump.bin"

    fd = os.open(HIDRAW, os.O_RDWR | os.O_NONBLOCK)

    try:
        # Close stale handle
        send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00], timeout=0.2)
        time.sleep(0.1)

        # Open LCD resource
        resp = send_recv(fd, [BRAGI_MAGIC, CMD_OPEN, HANDLE, RES_LCD & 0xFF, (RES_LCD >> 8) & 0xFF, 0x00])
        if not status_ok(resp):
            print(f"OPEN failed: {resp[2]:02x}" if resp else "OPEN: no response")
            return
        print("LCD handle opened")

        # Probe size
        resp = send_recv(fd, [BRAGI_MAGIC, CMD_PROBE, HANDLE, 0x00])
        if status_ok(resp) and len(resp) >= 9:
            size = struct.unpack_from('<I', resp, 5)[0]
            print(f"Resource size: {size} bytes")
        else:
            size = LCD_SIZE
            print(f"Probe failed, using default size: {size}")

        # Read all data using READ command
        # Each READ returns data starting at byte[3] of the response
        # Response is up to 1024 bytes, so payload is up to 1021 bytes per read
        data = bytearray()
        read_count = 0
        payload_per_read = 1024 - 3  # 1021 bytes of payload per response

        print(f"Reading {size} bytes...")
        while len(data) < size:
            resp = send_recv(fd, [BRAGI_MAGIC, CMD_READ, HANDLE, 0x00])
            if resp is None:
                print(f"  READ failed at offset {len(data)} (no response)")
                break
            if not status_ok(resp):
                print(f"  READ failed at offset {len(data)}: status 0x{resp[2]:02x}")
                break

            # Data starts at byte 3
            chunk = resp[3:]
            remaining = size - len(data)
            chunk = chunk[:remaining]
            data.extend(chunk)
            read_count += 1

            if read_count % 20 == 0:
                print(f"  {len(data)}/{size} bytes ({100*len(data)//size}%)")

        print(f"Read complete: {len(data)} bytes in {read_count} reads")

        # Save to file
        with open(out_path, 'wb') as f:
            f.write(data)
        print(f"Saved to: {out_path}")

        # Print analysis
        print(f"\nFirst 64 bytes:")
        for i in range(0, min(64, len(data)), 16):
            hex_str = ' '.join(f'{b:02x}' for b in data[i:i+16])
            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
            print(f"  {i:04x}: {hex_str}  {ascii_str}")

        print(f"\nLast 64 bytes:")
        start = max(0, len(data) - 64)
        for i in range(start, len(data), 16):
            hex_str = ' '.join(f'{b:02x}' for b in data[i:i+16])
            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i+16])
            print(f"  {i:04x}: {hex_str}  {ascii_str}")

        # Check for known file signatures
        print(f"\nFile signature analysis:")
        if data[:2] == b'\xff\xd8':
            print("  JPEG detected (FF D8)")
        elif data[:4] == b'\x89PNG':
            print("  PNG detected")
        elif data[:2] == b'BM':
            print("  BMP detected")
        elif data[:4] == b'\x00\x00\x01\x00':
            print("  ICO detected")
        elif data[:3] == b'GIF':
            print("  GIF detected")
        else:
            print(f"  Unknown format: {' '.join(f'{b:02x}' for b in data[:8])}")

        # Statistics
        nonzero = sum(1 for b in data if b != 0)
        print(f"\n  Non-zero bytes: {nonzero}/{len(data)} ({100*nonzero//len(data)}%)")
        unique = len(set(data))
        print(f"  Unique byte values: {unique}/256")

        # Close
        send_recv(fd, [BRAGI_MAGIC, CMD_CLOSE, HANDLE, 0x00])
        print("\nHandle closed.")

    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
