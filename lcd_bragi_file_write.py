#!/usr/bin/env python3
"""
Corsair Vanguard 96 LCD — Bragi file-based write

Uses the old Bragi 2-byte header format (0x08 + cmd) but with
file IDs and properties discovered from the Corsair Web Hub JS.

Key Web Hub discoveries:
  - Property 3 = Operating Mode (1=SELF_OPERATED, 2=HOST_CONTROLLED)
  - File 28007 (0x6D67) = default screen resource
  - File 62 (0x3E) = active display file
  - Screen dimensions: 248x170
  - Image format: Custom BMP (24-bit GRB, with [0x48, 0x00] prefix)
"""

import os
import sys
import time
import struct
import subprocess
import glob
import io

PKT_SIZE = 1024  # Full 1024-byte packets for this endpoint
BRAGI = 0x08

CMD_SET = 0x01
CMD_GET = 0x02
CMD_UNBIND = 0x05
CMD_WRITE_BEGIN = 0x06
CMD_WRITE_CONTINUE = 0x07
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


def send_recv(fd, data, timeout=2.0, pad_size=None):
    """Send data with 0x00 report ID prefix and read response."""
    if pad_size is None:
        pad_size = PKT_SIZE
    if len(data) < pad_size:
        data = data + bytes(pad_size - len(data))
    pkt = bytes([0x00]) + data
    os.write(fd, pkt)
    end = time.time() + timeout
    while time.time() < end:
        try:
            return os.read(fd, 2048)
        except BlockingIOError:
            time.sleep(0.005)
    return None


def status_ok(resp):
    """Check Bragi response status (byte 2 = 0x00 means success)."""
    return resp is not None and len(resp) > 2 and resp[2] == 0x00


def hexdump(data, n=16):
    return ' '.join(f'{b:02x}' for b in data[:n])


def take_photo(name):
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")
    return path


def create_corsair_bmp(width, height, pixels_rgb):
    """Create Corsair-format BMP (24-bit GRB, custom header)."""
    bpp = 24
    row_bytes = width * 3
    padding = (4 - row_bytes % 4) % 4
    padded_row = row_bytes + padding
    pixel_data_size = padded_row * height
    header_offset = 54  # Standard BMP info header
    timestamp = struct.pack('<I', int(time.time()))
    total_size = header_offset + pixel_data_size + len(timestamp) + 2

    buf = bytearray(total_size)

    # Corsair prefix
    buf[0] = 0x48
    buf[1] = 0x00

    # BMP header at offset 2
    struct.pack_into('<H', buf, 2, 0x4D42)  # 'BM'
    struct.pack_into('<I', buf, 4, total_size)
    struct.pack_into('<I', buf, 8, 0)
    struct.pack_into('<I', buf, 12, header_offset)

    # BITMAPINFOHEADER at offset 16
    struct.pack_into('<I', buf, 16, 40)
    struct.pack_into('<i', buf, 20, width)
    struct.pack_into('<i', buf, 24, height)
    struct.pack_into('<H', buf, 28, 1)
    struct.pack_into('<H', buf, 30, bpp)
    struct.pack_into('<I', buf, 32, 0)
    struct.pack_into('<I', buf, 36, pixel_data_size)
    struct.pack_into('<I', buf, 40, 2835)
    struct.pack_into('<I', buf, 44, 2835)
    struct.pack_into('<I', buf, 48, 0)
    struct.pack_into('<I', buf, 52, 0)

    # Pixel data (bottom-up, GRB)
    off = header_offset
    for y in range(height - 1, -1, -1):
        for x in range(width):
            r, g, b = pixels_rgb[y * width + x]
            buf[off] = g
            buf[off + 1] = r
            buf[off + 2] = b
            off += 3
        for _ in range(padding):
            buf[off] = 0
            off += 1

    buf[total_size - 4:total_size] = timestamp
    return bytes(buf)


def make_test_image(width, height):
    """Create test image with PIL."""
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([2, 2, width-3, height-3], outline=(0, 255, 0), width=2)
    draw.text((20, 20), "VIOLATOR", fill=(255, 0, 0))
    draw.text((20, 50), "ACTUAL", fill=(0, 255, 0))
    draw.text((20, 80), f"Vanguard 96 LCD", fill=(0, 128, 255))
    draw.text((20, 110), f"V1.5 @ {time.strftime('%H:%M:%S')}", fill=(255, 255, 0))
    draw.text((20, 140), f"{width}x{height} BMP", fill=(128, 128, 128))

    pixels = list(img.getdata())
    return create_corsair_bmp(width, height, pixels)


def main():
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")
    subprocess.run(["sudo", "chmod", "666", hidraw], capture_output=True)

    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)

    try:
        # ===== PHASE 1: Probe properties =====
        print("\n=== PHASE 1: Property probe ===")

        # GET property 1 (old mode property, works)
        resp = send_recv(fd, bytes([BRAGI, CMD_GET, 0x01, 0x00]))
        mode1 = resp[3] if resp and len(resp) > 3 and resp[2] == 0x00 else None
        print(f"  Property 1 (old mode): {mode1}")

        # GET property 3 (operating mode from Web Hub)
        resp = send_recv(fd, bytes([BRAGI, CMD_GET, 0x03, 0x00]))
        if status_ok(resp):
            mode3 = resp[3] if len(resp) > 3 else None
            print(f"  Property 3 (operating mode): {mode3}")
            print(f"    Raw: {hexdump(resp)}")
        else:
            print(f"  Property 3: FAIL (resp={hexdump(resp) if resp else 'None'})")

        # Property 230 (0xE6) - screen module present
        resp = send_recv(fd, bytes([BRAGI, CMD_GET, 0xE6, 0x00]))
        if status_ok(resp):
            print(f"  Property 0xE6 (screen present): {hexdump(resp)}")
        else:
            print(f"  Property 0xE6: FAIL")

        # Property 62 (0x3E) - available flash / active display
        resp = send_recv(fd, bytes([BRAGI, CMD_GET, 0x3E, 0x00]))
        if status_ok(resp):
            print(f"  Property 0x3E (flash/display): {hexdump(resp)}")
        else:
            print(f"  Property 0x3E: FAIL")

        # ===== PHASE 2: Try 2-byte property IDs =====
        print("\n=== PHASE 2: 2-byte property GET (V1.5 style) ===")

        # The old Bragi GET might only support 1-byte property IDs
        # V1.5 SET format: [0x08, CMD_SET, prop_lo, prop_hi, val_lo, val_hi, val2, val3]
        # Let's try getting property 3 with explicit 2-byte format
        for prop_id in [3, 62, 230, 240, 242, 243]:
            prop_bytes = struct.pack('<H', prop_id)
            resp = send_recv(fd, bytes([BRAGI, CMD_GET]) + prop_bytes)
            if status_ok(resp):
                # Extract 4-byte value after status
                val_bytes = resp[3:7] if len(resp) > 6 else resp[3:]
                val = struct.unpack_from('<I', resp, 3)[0] if len(resp) > 6 else resp[3]
                print(f"  Property {prop_id}: val={val} raw={hexdump(resp)}")
            else:
                print(f"  Property {prop_id}: FAIL ({hexdump(resp) if resp else 'None'})")

        # ===== PHASE 3: Try opening file 28007 with old Bragi =====
        print("\n=== PHASE 3: File operations (old Bragi header) ===")

        # First close any stale handle
        send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)
        time.sleep(0.1)

        # Try OPEN with buffer_index=0, file_id=28007 (0x6D67)
        file_id = 28007
        file_lo = file_id & 0xFF  # 0x67
        file_hi = (file_id >> 8) & 0xFF  # 0x6D
        print(f"  Opening file {file_id} (0x{file_id:04X})...")
        resp = send_recv(fd, bytes([BRAGI, CMD_OPEN, 0x00, file_lo, file_hi, 0x00]))
        if status_ok(resp):
            print(f"    OPEN OK: {hexdump(resp)}")
        else:
            print(f"    OPEN FAIL: {hexdump(resp) if resp else 'None'}")

        # Also try with handle=1 (as we used before)
        send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x01, 0x00]), timeout=0.3)
        time.sleep(0.1)
        resp = send_recv(fd, bytes([BRAGI, CMD_OPEN, 0x01, file_lo, file_hi, 0x00]))
        if status_ok(resp):
            print(f"    OPEN (handle=1) OK: {hexdump(resp)}")
        else:
            print(f"    OPEN (handle=1) FAIL: {hexdump(resp) if resp else 'None'}")

        # Try resource 62 (0x3E) - active display
        send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)
        time.sleep(0.1)
        resp = send_recv(fd, bytes([BRAGI, CMD_OPEN, 0x00, 0x3E, 0x00, 0x00]))
        if status_ok(resp):
            print(f"    OPEN file 62 OK: {hexdump(resp)}")
            # Describe it
            resp = send_recv(fd, bytes([BRAGI, CMD_DESCRIBE, 0x00, 0x00]))
            if status_ok(resp):
                size = struct.unpack_from('<I', resp, 5)[0] if len(resp) > 8 else 0
                print(f"    DESCRIBE: size={size} raw={hexdump(resp)}")

            # Try reading it
            resp = send_recv(fd, bytes([BRAGI, CMD_READ, 0x00, 0x00]))
            if status_ok(resp):
                print(f"    READ: {hexdump(resp, 32)}")
        else:
            print(f"    OPEN file 62 FAIL: {hexdump(resp) if resp else 'None'}")

        send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)

        # ===== PHASE 4: Set HOST_CONTROLLED and retry =====
        print("\n=== PHASE 4: Set HOST_CONTROLLED mode ===")

        # Set property 3 to 2 (HOST_CONTROLLED)
        resp = send_recv(fd, bytes([BRAGI, CMD_SET, 0x03, 0x00, 0x02, 0x00, 0x00, 0x00]))
        if status_ok(resp):
            print(f"  SET prop 3=2: OK ({hexdump(resp)})")
        else:
            print(f"  SET prop 3=2: FAIL ({hexdump(resp) if resp else 'None'})")

        # Verify
        resp = send_recv(fd, bytes([BRAGI, CMD_GET, 0x03, 0x00]))
        print(f"  GET prop 3: {hexdump(resp) if resp else 'None'}")

        time.sleep(1)

        # Now try opening file again
        print("\n  Retry file operations in HOST_CONTROLLED mode:")
        for fid, name in [(28007, "screen_resource"), (62, "active_display"), (63, "old_lcd_res")]:
            flo = fid & 0xFF
            fhi = (fid >> 8) & 0xFF
            send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)
            time.sleep(0.1)
            resp = send_recv(fd, bytes([BRAGI, CMD_OPEN, 0x00, flo, fhi, 0x00]))
            ok = status_ok(resp)
            print(f"    OPEN {name} ({fid}/0x{fid:04X}): {'OK' if ok else 'FAIL'} {hexdump(resp) if resp else ''}")
            if ok:
                resp = send_recv(fd, bytes([BRAGI, CMD_DESCRIBE, 0x00, 0x00]))
                if status_ok(resp) and len(resp) > 8:
                    size = struct.unpack_from('<I', resp, 5)[0]
                    print(f"      Size: {size} bytes")
                # Read first chunk
                resp = send_recv(fd, bytes([BRAGI, CMD_READ, 0x00, 0x00]))
                if status_ok(resp):
                    print(f"      Data: {hexdump(resp, 24)}")
            send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)

        # ===== PHASE 5: Try writing BMP to file 28007 =====
        print("\n=== PHASE 5: Write BMP image ===")

        take_photo("bragi_file_before")

        bmp_data = make_test_image(248, 170)
        print(f"  BMP: {len(bmp_data)} bytes, header: {hexdump(bmp_data[:16])}")

        # Open file 28007
        send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)
        time.sleep(0.1)

        # Try CREATE first
        resp = send_recv(fd, bytes([BRAGI, CMD_CREATE, file_lo, file_hi]))
        print(f"  CREATE {file_id}: {'OK' if status_ok(resp) else 'FAIL'} {hexdump(resp) if resp else ''}")

        resp = send_recv(fd, bytes([BRAGI, CMD_OPEN, 0x00, file_lo, file_hi, 0x00]))
        if not status_ok(resp):
            print(f"  OPEN failed, trying with handle=1...")
            resp = send_recv(fd, bytes([BRAGI, CMD_OPEN, 0x01, file_lo, file_hi, 0x00]))

        if status_ok(resp):
            handle = 0x00  # or 0x01 depending on which worked
            print(f"  File opened, writing {len(bmp_data)} bytes...")

            # WRITE_BEGIN: [BRAGI, CMD_WRITE_BEGIN, handle, total_size_LE32, data...]
            first_chunk_max = PKT_SIZE - 7  # 2 header + 1 handle + 4 size = 7
            first_chunk = min(len(bmp_data), first_chunk_max)

            write_pkt = bytes([BRAGI, CMD_WRITE_BEGIN, handle])
            write_pkt += struct.pack('<I', len(bmp_data))
            write_pkt += bmp_data[:first_chunk]

            resp = send_recv(fd, write_pkt)
            if status_ok(resp):
                print(f"  Write begin OK, sent {first_chunk} bytes")
            else:
                print(f"  Write begin FAIL: {hexdump(resp) if resp else 'None'}")

            # Continue writing
            offset = first_chunk
            cont_max = PKT_SIZE - 3  # 2 header + 1 handle = 3
            pkt_count = 1
            while offset < len(bmp_data):
                chunk_size = min(len(bmp_data) - offset, cont_max)
                cont_pkt = bytes([BRAGI, CMD_WRITE_CONTINUE, handle])
                cont_pkt += bmp_data[offset:offset + chunk_size]
                resp = send_recv(fd, cont_pkt)
                if not status_ok(resp):
                    print(f"  Write continue FAIL at {offset}: {hexdump(resp) if resp else 'None'}")
                    break
                offset += chunk_size
                pkt_count += 1
                if pkt_count % 30 == 0:
                    print(f"    {offset}/{len(bmp_data)} ({100*offset//len(bmp_data)}%)")

            print(f"  Write done: {pkt_count} packets, {offset} bytes")

            # Unbind
            send_recv(fd, bytes([BRAGI, CMD_UNBIND, handle, 0x00]), timeout=0.3)

            time.sleep(2)
            take_photo("bragi_file_after_write")

            # Now try to activate: write config to file 62
            print("\n  Writing config to active display (file 62)...")
            config = bytes([56, 0]) + struct.pack('<H', 28007)
            send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)
            time.sleep(0.1)
            resp = send_recv(fd, bytes([BRAGI, CMD_OPEN, 0x00, 0x3E, 0x00, 0x00]))
            if status_ok(resp):
                write_pkt = bytes([BRAGI, CMD_WRITE_BEGIN, 0x00])
                write_pkt += struct.pack('<I', len(config))
                write_pkt += config
                resp = send_recv(fd, write_pkt)
                print(f"  Config write: {'OK' if status_ok(resp) else 'FAIL'} {hexdump(resp) if resp else ''}")
                send_recv(fd, bytes([BRAGI, CMD_UNBIND, 0x00, 0x00]), timeout=0.3)
            else:
                print(f"  OPEN file 62 FAIL: {hexdump(resp) if resp else 'None'}")

            time.sleep(2)
            take_photo("bragi_file_after_activate")

        else:
            print(f"  Cannot open file {file_id}")

        # ===== PHASE 6: Restore =====
        print("\n=== PHASE 6: Restore ===")
        # Set back to SELF_OPERATED
        resp = send_recv(fd, bytes([BRAGI, CMD_SET, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00]))
        print(f"  SET prop 3=1: {'OK' if status_ok(resp) else 'FAIL'}")

        time.sleep(2)
        take_photo("bragi_file_final")

    finally:
        os.close(fd)

    print("\nDone!")


if __name__ == "__main__":
    main()
