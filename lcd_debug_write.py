#!/usr/bin/env python3
"""
LCD Debug Write — try multiple approaches to find working LCD protocol.

Tests:
1. Direct write with 0x00 report ID prefix (original)
2. Direct write WITHOUT report ID prefix
3. Software mode + direct write
4. Bragi open LCD resource THEN direct write
5. Different opcode variants
"""

import os
import sys
import time
import struct
import io
import subprocess
import glob


MAX_PKT = 1024
HEADER_SIZE = 8
PAYLOAD_SIZE = MAX_PKT - HEADER_SIZE
BRAGI_MAGIC = 0x08
OUT_PKT_SIZE = 64


def find_hidraw():
    """Find the Bragi interface hidraw device (interface 2)."""
    for h in sorted(glob.glob("/dev/hidraw*")):
        name = h.split("/")[-1]
        try:
            uevent = open(f"/sys/class/hidraw/{name}/device/uevent").read()
            if "VANGUARD" in uevent and "input2" in uevent:
                return h
        except:
            pass
    return None


def send_bragi(fd, data, timeout=1.0):
    """Send a Bragi command and get response."""
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


def make_jpeg(width=320, height=170):
    """Create a JPEG test image."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([2, 2, width-3, height-3], outline=(0, 255, 0), width=2)
    draw.text((40, 30), "VIOLATOR", fill=(255, 0, 0))
    draw.text((50, 60), "ACTUAL", fill=(0, 255, 0))
    draw.text((20, 100), f"Test @ {time.strftime('%H:%M:%S')}", fill=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def make_lcd_packets(jpeg_data, opcode=0x02):
    """Split JPEG into LCD protocol packets."""
    packets = []
    offset = 0
    part_num = 0
    while offset < len(jpeg_data):
        remaining = len(jpeg_data) - offset
        chunk_size = min(PAYLOAD_SIZE, remaining)
        is_end = (offset + chunk_size >= len(jpeg_data))
        header = bytes([opcode, 0x05, 0x40, 0x01 if is_end else 0x00])
        header += struct.pack('<H', part_num)
        header += struct.pack('<H', chunk_size)
        data = jpeg_data[offset:offset + chunk_size]
        if len(data) < PAYLOAD_SIZE:
            data = data + bytes(PAYLOAD_SIZE - len(data))
        packets.append(header + data)
        offset += chunk_size
        part_num += 1
    return packets


def take_photo(name):
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")
    return path


def test_with_report_id(hidraw, packets, label):
    """Send packets WITH 0x00 report ID prefix."""
    print(f"\n--- Test: {label} (with 0x00 prefix, {len(packets)} pkts) ---")
    fd = os.open(hidraw, os.O_RDWR)
    for i, pkt in enumerate(packets):
        os.write(fd, bytes([0x00]) + pkt)
        time.sleep(0.001)
    os.close(fd)
    time.sleep(1)


def test_without_report_id(hidraw, packets, label):
    """Send packets WITHOUT report ID prefix."""
    print(f"\n--- Test: {label} (no prefix, {len(packets)} pkts) ---")
    fd = os.open(hidraw, os.O_RDWR)
    for i, pkt in enumerate(packets):
        try:
            os.write(fd, pkt)
            time.sleep(0.001)
        except OSError as e:
            print(f"  Write error on pkt {i}: {e}")
            break
    os.close(fd)
    time.sleep(1)


def test_with_bragi_init(hidraw, packets, jpeg_data):
    """Open LCD via Bragi first, then send LCD protocol packets."""
    print(f"\n--- Test: Bragi init + LCD protocol ---")
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)

    # Open LCD resource via Bragi
    send_bragi(fd, [BRAGI_MAGIC, 0x05, 0x01, 0x00], timeout=0.2)  # close stale
    time.sleep(0.1)
    resp = send_bragi(fd, [BRAGI_MAGIC, 0x0D, 0x01, 0x3F, 0x00, 0x00])
    print(f"  Bragi OPEN LCD: {'OK' if status_ok(resp) else 'FAIL'}")

    # Now send LCD protocol packets on same fd
    for i, pkt in enumerate(packets):
        os.write(fd, bytes([0x00]) + pkt)
        time.sleep(0.001)
    print(f"  Sent {len(packets)} LCD packets")

    time.sleep(1)

    # Try Bragi commit commands
    for cmd in [0x04, 0x07]:
        resp = send_bragi(fd, [BRAGI_MAGIC, cmd, 0x01, 0x00])
        print(f"  Bragi CMD 0x{cmd:02X}: {'OK' if status_ok(resp) else 'FAIL'}")

    # Close handle
    send_bragi(fd, [BRAGI_MAGIC, 0x05, 0x01, 0x00])
    os.close(fd)
    time.sleep(1)


def test_sw_mode_direct(hidraw, packets):
    """Switch to software mode, then send LCD packets."""
    print(f"\n--- Test: Software mode + LCD protocol ---")
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)

    # Get current mode
    resp = send_bragi(fd, [BRAGI_MAGIC, 0x02, 0x01, 0x00])
    mode = resp[3] if resp and len(resp) > 3 and resp[2] == 0x00 else None
    print(f"  Current mode: {mode}")

    if mode != 1:
        print("  Switching to software mode...")
        send_bragi(fd, [BRAGI_MAGIC, 0x01, 0x01, 0x00, 0x01])
        os.close(fd)
        time.sleep(3)

        # Wait for reconnect
        new_hidraw = None
        for _ in range(20):
            new_hidraw = find_hidraw()
            if new_hidraw:
                subprocess.run(["sudo", "chmod", "666", new_hidraw], capture_output=True)
                time.sleep(0.5)
                break
            time.sleep(0.5)

        if not new_hidraw:
            print("  ERROR: Device did not reconnect!")
            return None

        hidraw = new_hidraw
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        resp = send_bragi(fd, [BRAGI_MAGIC, 0x02, 0x01, 0x00])
        mode = resp[3] if resp and len(resp) > 3 and resp[2] == 0x00 else None
        print(f"  Mode after switch: {mode}")
    else:
        print("  Already in software mode")

    # Now send LCD packets
    print("  Sending LCD packets...")
    for i, pkt in enumerate(packets):
        os.write(fd, bytes([0x00]) + pkt)
        time.sleep(0.001)
    print(f"  Sent {len(packets)} packets")

    time.sleep(2)
    take_photo("sw_mode_lcd_direct")

    # Also try: open LCD resource then write
    print("  Trying with Bragi OPEN first...")
    send_bragi(fd, [BRAGI_MAGIC, 0x05, 0x01, 0x00], timeout=0.2)
    time.sleep(0.1)
    resp = send_bragi(fd, [BRAGI_MAGIC, 0x0D, 0x01, 0x3F, 0x00, 0x00])
    print(f"  Bragi OPEN: {'OK' if status_ok(resp) else 'FAIL'}")

    for i, pkt in enumerate(packets):
        os.write(fd, bytes([0x00]) + pkt)
        time.sleep(0.001)
    print(f"  Sent {len(packets)} packets after OPEN")

    time.sleep(2)
    take_photo("sw_mode_lcd_after_open")

    # Restore hardware mode
    print("  Restoring hardware mode...")
    send_bragi(fd, [BRAGI_MAGIC, 0x05, 0x01, 0x00])  # close handle
    send_bragi(fd, [BRAGI_MAGIC, 0x01, 0x01, 0x00, 0x04])  # mode 4
    os.close(fd)

    time.sleep(3)
    new_hidraw = None
    for _ in range(20):
        new_hidraw = find_hidraw()
        if new_hidraw:
            subprocess.run(["sudo", "chmod", "666", new_hidraw], capture_output=True)
            time.sleep(0.5)
            break
        time.sleep(0.5)

    return new_hidraw or hidraw


def test_opcodes(hidraw, jpeg_data):
    """Try different opcodes to see which ones the device accepts."""
    print(f"\n--- Test: Different opcodes ---")
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)

    for opcode in [0x01, 0x02, 0x03, 0x04, 0x05]:
        packets = make_lcd_packets(jpeg_data, opcode=opcode)
        print(f"  Opcode 0x{opcode:02X}: sending {len(packets)} packets...")

        for pkt in packets:
            os.write(fd, bytes([0x00]) + pkt)
            time.sleep(0.001)

        # Try to read any response
        time.sleep(0.1)
        try:
            resp = os.read(fd, 1024)
            print(f"    Response: {' '.join(f'{b:02x}' for b in resp[:16])}")
        except BlockingIOError:
            print(f"    No response")

    os.close(fd)
    time.sleep(1)


def main():
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")

    jpeg_data = make_jpeg()
    print(f"JPEG: {len(jpeg_data)} bytes")

    packets = make_lcd_packets(jpeg_data)
    print(f"Packets: {len(packets)} x {MAX_PKT} bytes")

    print("\n[1] Before photo...")
    take_photo("debug_before")

    # Test 1: Original approach (with 0x00 prefix)
    test_with_report_id(hidraw, packets, "0x02 opcode, 0x00 prefix")
    take_photo("debug_test1")

    # Test 2: Without report ID prefix
    test_without_report_id(hidraw, packets, "0x02 opcode, no prefix")
    take_photo("debug_test2")

    # Test 3: Try different opcodes
    test_opcodes(hidraw, jpeg_data)
    take_photo("debug_test3_opcodes")

    # Test 4: Bragi init then LCD protocol
    test_with_bragi_init(hidraw, packets, jpeg_data)
    take_photo("debug_test4_bragi_init")

    # Test 5: Software mode + LCD protocol (most promising)
    print("\n[5] Software mode test (this will cause USB reconnect)...")
    new_hidraw = test_sw_mode_direct(hidraw, packets)
    if new_hidraw:
        hidraw = new_hidraw

    print("\n[FINAL] Final photo...")
    take_photo("debug_final")
    print("\nDone!")


if __name__ == "__main__":
    main()
