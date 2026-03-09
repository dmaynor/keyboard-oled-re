#!/usr/bin/env python3
"""
Corsair Vanguard 96 LCD — Direct Image Write

Based on corsair_lcd_tool protocol (UDPSendToFailed/corsair_lcd_tool):
  - Uses opcode 0x02, NOT Bragi protocol (0x08)
  - Sends JPEG-encoded images in chunked 1024-byte packets
  - 8-byte header per packet:
    [opcode=0x02, 0x05, 0x40, is_end, part_num_LE16, datalen_LE16]
  - No commit/flush needed — display updates after last packet

LCD dimensions: 320x170 (from properties 0xF2, 0xF3)
"""

import os
import sys
import time
import struct
import subprocess
import io

HIDRAW = "/dev/hidraw3"
MAX_PKT = 1024
HEADER_SIZE = 8
PAYLOAD_SIZE = MAX_PKT - HEADER_SIZE  # 1016 bytes per chunk


def make_packets(jpeg_data, opcode=0x02):
    """Split JPEG data into LCD protocol packets."""
    packets = []
    offset = 0
    part_num = 0

    while offset < len(jpeg_data):
        remaining = len(jpeg_data) - offset
        chunk_size = min(PAYLOAD_SIZE, remaining)
        is_end = (offset + chunk_size >= len(jpeg_data))

        # 8-byte header
        header = bytes([
            opcode,         # 0x02
            0x05,           # unknown1
            0x40,           # unknown2
            0x01 if is_end else 0x00,
        ])
        header += struct.pack('<H', part_num)
        header += struct.pack('<H', chunk_size)

        # Data + padding
        data = jpeg_data[offset:offset + chunk_size]
        if len(data) < PAYLOAD_SIZE:
            data = data + bytes(PAYLOAD_SIZE - len(data))

        packets.append(header + data)
        offset += chunk_size
        part_num += 1

    return packets


def send_packets(fd, packets):
    """Send all packets via hidraw."""
    for i, pkt in enumerate(packets):
        # Prepend HID report ID 0x00
        os.write(fd, bytes([0x00]) + pkt)
        # Small delay between packets
        time.sleep(0.001)
    return len(packets)


def take_photo(name):
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")
    return path


def create_test_image(width, height, color_name):
    """Create a JPEG test image."""
    from PIL import Image, ImageDraw
    if color_name == "red":
        img = Image.new('RGB', (width, height), (255, 0, 0))
    elif color_name == "blue":
        img = Image.new('RGB', (width, height), (0, 0, 255))
    elif color_name == "green":
        img = Image.new('RGB', (width, height), (0, 255, 0))
    elif color_name == "white":
        img = Image.new('RGB', (width, height), (255, 255, 255))
    elif color_name == "black":
        img = Image.new('RGB', (width, height), (0, 0, 0))
    elif color_name == "callsign":
        img = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Draw border
        draw.rectangle([2, 2, width-3, height-3], outline=(0, 255, 0), width=2)
        # Draw text
        draw.text((40, 30), "VIOLATOR", fill=(255, 0, 0))
        draw.text((50, 60), "ACTUAL", fill=(0, 255, 0))
        draw.text((20, 100), "Corsair Vanguard 96", fill=(0, 128, 255))
        draw.text((20, 130), "LCD Protocol RE", fill=(128, 128, 128))
    else:
        img = Image.new('RGB', (width, height), (255, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def main():
    color = sys.argv[1] if len(sys.argv) > 1 else "callsign"
    width = 320
    height = 170

    # Accept a file path or a color name
    if os.path.isfile(color):
        with open(color, 'rb') as f:
            jpeg_data = f.read()
        print(f"Using image file: {color} ({len(jpeg_data)} bytes)")
    else:
        jpeg_data = create_test_image(width, height, color)
        print(f"Generated {color} image: {width}x{height} ({len(jpeg_data)} bytes)")

    packets = make_packets(jpeg_data)
    print(f"Split into {len(packets)} packets of {MAX_PKT} bytes")
    print(f"Header of first packet: {' '.join(f'{b:02x}' for b in packets[0][:16])}")

    print("\n[1] Before photo...")
    take_photo("direct_before")

    print("\n[2] Sending to LCD...")
    fd = os.open(HIDRAW, os.O_RDWR)
    t0 = time.time()
    count = send_packets(fd, packets)
    elapsed = time.time() - t0
    os.close(fd)
    print(f"  Sent {count} packets in {elapsed:.2f}s")

    print("\n[3] Waiting 2s...")
    time.sleep(2)

    print("\n[4] After photo...")
    take_photo("direct_after")

    # Also try with non-blocking read to drain any responses
    print("\n[5] Trying with response drain...")
    fd = os.open(HIDRAW, os.O_RDWR | os.O_NONBLOCK)

    # Send packets and drain responses
    for pkt in packets:
        os.write(fd, bytes([0x00]) + pkt)
        # Try to read response
        try:
            resp = os.read(fd, 1024)
            if resp[0:2] != b'\x00\x00':
                print(f"  Response: {' '.join(f'{b:02x}' for b in resp[:16])}")
        except BlockingIOError:
            pass
        time.sleep(0.001)

    os.close(fd)
    time.sleep(2)
    take_photo("direct_after_v2")

    print("\nDone!")


if __name__ == "__main__":
    main()
