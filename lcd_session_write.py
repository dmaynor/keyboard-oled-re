#!/usr/bin/env python3
"""
LCD Session Write — establish Bragi session before file 62 operations.

The Web Hub establishes a session (cmd 0x1B) before doing file operations.
We've been skipping this step, which may be why file 62 returns error 0x06.

Protocol flow (from Web Hub JS):
1. Start session (cmd 0x1B) → get assigned sessionId
2. Read file 28007 (widget layout) via: open → describe → read → unbind
3. Write to file 62 (active display) via: open → writeBegin → [writeCont] → unbind
4. Update screen index property
5. Set operating mode to SELF_OPERATED (1)

Old Bragi protocol (2-byte header):
  [0x08 + subDevAddr, cmdId, ...payload]
"""

import os
import sys
import time
import struct
import io
import subprocess
import glob
import random

BRAGI = 0x08  # Device_Itself
OUT_PKT_SIZE = 1024  # Interface 2 uses 1024-byte HID reports

# Command IDs (from Web Hub JS)
CMD_SET = 0x01       # $ce = 1
CMD_GET = 0x02       # FA = 2
CMD_UNBIND = 0x05    # qce = 5
CMD_WRITE_BEGIN = 0x06  # Yce = 6
CMD_WRITE_CONT = 0x07  # Zce = 7
CMD_READ = 0x08      # Xce = 8
CMD_DESCRIBE = 0x09  # Jce = 9
CMD_CREATE = 0x0B    # eue = 11
CMD_DELETE = 0x0C    # tue = 12
CMD_OPEN = 0x0D      # nue = 13
CMD_SESSION = 0x1B   # ub = 27

# File IDs
FILE_ACTIVE_DISPLAY = 62   # pce = 62
FILE_WIDGET_28007 = 28007  # defaultScreenResources[0]

# Properties
PROP_MODE = 0x03       # operating mode (Wle = 3)
PROP_SCREEN_IDX = 263  # J8 = 263 (screen index)

# Operating modes
MODE_SELF_OPERATED = 1
MODE_HOST_CONTROLLED = 2


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


def send_recv(fd, data, timeout=1.0):
    """Send command and receive response."""
    pad = max(0, OUT_PKT_SIZE - len(data))
    pkt = bytes([0x00]) + bytes(data) + bytes(pad)
    os.write(fd, pkt)
    end = time.time() + timeout
    while time.time() < end:
        try:
            resp = os.read(fd, 1024)
            return resp
        except BlockingIOError:
            time.sleep(0.01)
    return None


def status_ok(resp):
    return resp is not None and len(resp) > 2 and resp[2] == 0x00


def hex_dump(data, n=32):
    if data is None:
        return "None"
    return ' '.join(f'{b:02x}' for b in data[:n])


def start_session(fd):
    """Start a host software session (cmd 0x1B)."""
    # Generate random 4-byte session token
    token = bytes([random.randint(0, 255) for _ in range(4)])
    print(f"  Session token: {hex_dump(token)}")

    # Packet: [BRAGI, CMD_SESSION, 0x01, token[0..3], 0x00]
    pkt = [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00]
    resp = send_recv(fd, pkt)
    print(f"  Session response: {hex_dump(resp)}")

    if resp and len(resp) > 9 and resp[2] == 0x00:
        session_id = resp[9]
        print(f"  Assigned sessionId: {session_id}")
        return session_id
    elif resp and len(resp) > 2:
        print(f"  Session status: 0x{resp[2]:02x}")
        # Try extracting sessionId from different offsets
        for off in [3, 4, 5, 6, 7, 8, 9]:
            if off < len(resp):
                print(f"    resp[{off}] = 0x{resp[off]:02x} ({resp[off]})")
        return None
    else:
        print("  No session response!")
        return None


def stop_session(fd):
    """Stop the host software session (cmd 0x1B with stop flag)."""
    pkt = [BRAGI, CMD_SESSION, 0x00]
    resp = send_recv(fd, pkt, timeout=0.5)
    print(f"  Stop session: {hex_dump(resp)}")


def open_file(fd, file_id, buffer_index=0):
    """Open a file with buffer index (cmd 0x0D)."""
    file_bytes = struct.pack('<H', file_id)
    pkt = [BRAGI, CMD_OPEN, buffer_index] + list(file_bytes)
    resp = send_recv(fd, pkt)
    ok = status_ok(resp)
    print(f"  OPEN file {file_id} (buf={buffer_index}): {'OK' if ok else 'FAIL'} -> {hex_dump(resp)}")
    return ok


def unbind_buffers(fd, buffer_indices):
    """Unbind buffers (cmd 0x05)."""
    pkt = [BRAGI, CMD_UNBIND, len(buffer_indices)] + list(buffer_indices)
    resp = send_recv(fd, pkt, timeout=0.5)
    ok = status_ok(resp)
    print(f"  UNBIND buffers {buffer_indices}: {'OK' if ok else 'FAIL'} -> {hex_dump(resp)}")
    return ok


def create_file(fd, file_id):
    """Create a file (cmd 0x0B)."""
    file_bytes = struct.pack('<H', file_id)
    pkt = [BRAGI, CMD_CREATE] + list(file_bytes)
    resp = send_recv(fd, pkt)
    ok = status_ok(resp)
    print(f"  CREATE file {file_id}: {'OK' if ok else 'FAIL'} -> {hex_dump(resp)}")
    return ok


def describe_buffer(fd, buffer_index=0):
    """Describe buffer to get size (cmd 0x09)."""
    pkt = [BRAGI, CMD_DESCRIBE, buffer_index]
    resp = send_recv(fd, pkt)
    if status_ok(resp) and len(resp) > 6:
        size = struct.unpack_from('<I', resp, 3)[0]
        print(f"  DESCRIBE buffer {buffer_index}: size={size} -> {hex_dump(resp)}")
        return size
    else:
        print(f"  DESCRIBE buffer {buffer_index}: FAIL -> {hex_dump(resp)}")
        return None


def read_buffer(fd, buffer_index=0):
    """Read from buffer (cmd 0x08)."""
    pkt = [BRAGI, CMD_READ, buffer_index]
    resp = send_recv(fd, pkt)
    if status_ok(resp) and len(resp) > 3:
        data = resp[3:]
        return data
    return None


def read_file(fd, file_id, buffer_index=0):
    """Read a file: open → describe → read loop → unbind."""
    print(f"\n  --- Reading file {file_id} ---")
    if not open_file(fd, file_id, buffer_index):
        return None

    size = describe_buffer(fd, buffer_index)
    if size is None:
        unbind_buffers(fd, [buffer_index])
        return None

    data = bytearray()
    while len(data) < size:
        chunk = read_buffer(fd, buffer_index)
        if chunk is None:
            break
        remaining = size - len(data)
        data.extend(chunk[:remaining])

    unbind_buffers(fd, [buffer_index])
    print(f"  Read {len(data)} bytes from file {file_id}")
    if len(data) > 0:
        print(f"  First bytes: {hex_dump(bytes(data), 32)}")
    return bytes(data)


def write_buffer_begin(fd, data, buffer_index=0):
    """Write first chunk to buffer (cmd 0x06)."""
    total = len(data)
    max_first = OUT_PKT_SIZE - 2 - 1 - 4  # header(2) + bufIdx(1) + length(4) = 1017
    chunk_size = min(total, max_first)

    pkt = [BRAGI, CMD_WRITE_BEGIN, buffer_index]
    pkt += list(struct.pack('<I', total))
    pkt += list(data[:chunk_size])

    resp = send_recv(fd, pkt)
    ok = status_ok(resp)
    print(f"  WRITE_BEGIN buf={buffer_index} total={total} first_chunk={chunk_size}: {'OK' if ok else 'FAIL'} -> {hex_dump(resp)}")
    if not ok:
        return 0
    return chunk_size


def write_buffer_cont(fd, data, buffer_index=0):
    """Write continuation chunk (cmd 0x07)."""
    max_cont = OUT_PKT_SIZE - 2 - 1  # header(2) + bufIdx(1) = 1021
    chunk_size = min(len(data), max_cont)

    pkt = [BRAGI, CMD_WRITE_CONT, buffer_index]
    pkt += list(data[:chunk_size])

    resp = send_recv(fd, pkt)
    ok = status_ok(resp)
    if not ok:
        print(f"  WRITE_CONT FAIL -> {hex_dump(resp)}")
        return 0
    return chunk_size


def write_file(fd, file_id, data, buffer_index=0):
    """Write a file: open → writeBegin → [writeCont] → unbind."""
    print(f"\n  --- Writing file {file_id} ({len(data)} bytes) ---")

    # Try open
    if not open_file(fd, file_id, buffer_index):
        # Fallback: unbind → create → open
        print("  Open failed, trying unbind + create + open...")
        unbind_buffers(fd, [buffer_index])
        create_file(fd, file_id)
        if not open_file(fd, file_id, buffer_index):
            print("  WRITE ABORTED: Cannot open file")
            return False

    # Write data
    offset = write_buffer_begin(fd, data, buffer_index)
    if offset == 0:
        unbind_buffers(fd, [buffer_index])
        return False

    while offset < len(data):
        written = write_buffer_cont(fd, data[offset:], buffer_index)
        if written == 0:
            unbind_buffers(fd, [buffer_index])
            return False
        offset += written
        if offset % 10000 < 100:
            print(f"    ... {offset}/{len(data)} bytes")

    unbind_buffers(fd, [buffer_index])
    print(f"  Write complete: {offset} bytes to file {file_id}")
    return True


def get_property(fd, prop_id):
    """Get a property value."""
    prop_bytes = struct.pack('<H', prop_id)
    pkt = [BRAGI, CMD_GET] + list(prop_bytes)
    resp = send_recv(fd, pkt)
    if status_ok(resp) and len(resp) > 3:
        return resp[3:]
    return None


def set_property(fd, prop_id, value_bytes):
    """Set a property value."""
    prop_bytes = struct.pack('<H', prop_id)
    pkt = [BRAGI, CMD_SET] + list(prop_bytes) + list(value_bytes)
    resp = send_recv(fd, pkt)
    ok = status_ok(resp)
    return ok


def take_photo(name):
    path = f"pics/09-lcd-probing/{name}.jpg"
    subprocess.run([
        "ffmpeg", "-f", "v4l2", "-video_size", "1920x1080",
        "-i", "/dev/video0", "-frames:v", "1", "-update", "1",
        "-y", path
    ], capture_output=True)
    print(f"  Photo: {path}")
    return path


def make_test_image(width=248, height=170):
    """Create a test image in Corsair custom BMP format."""
    try:
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([2, 2, width-3, height-3], outline=(0, 255, 0), width=2)
        draw.text((30, 30), "VIOLATOR", fill=(255, 0, 0))
        draw.text((40, 60), "ACTUAL", fill=(0, 255, 0))
        draw.text((20, 100), f"Session Test", fill=(0, 128, 255))
        draw.text((20, 130), f"{time.strftime('%H:%M:%S')}", fill=(128, 128, 128))
        return create_corsair_bmp(img)
    except ImportError:
        print("  PIL not available, creating simple solid-color BMP")
        return create_solid_bmp(width, height, (255, 0, 0))


def create_solid_bmp(width=248, height=170, color=(255, 0, 0)):
    """Create Corsair BMP without PIL — solid color fill."""
    r, g, b = color
    row_size = (width * 3 + 3) & ~3
    pixel_data_size = row_size * height
    bmp_header_size = 54
    file_size = bmp_header_size + pixel_data_size

    bmp = bytearray()
    bmp += bytes([0x48, 0x00])  # Corsair prefix
    bmp += b'BM'
    bmp += struct.pack('<I', file_size)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', bmp_header_size)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', width)
    bmp += struct.pack('<i', height)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pixel_data_size)
    bmp += struct.pack('<i', 2835)
    bmp += struct.pack('<i', 2835)
    bmp += struct.pack('<II', 0, 0)

    padding = row_size - width * 3
    row = bytes([g, r, b]) * width + bytes(padding)  # GRB order
    for _ in range(height):
        bmp += row

    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
    return bytes(bmp)


def create_corsair_bmp(img):
    """Convert PIL Image to Corsair custom BMP format.
    Format: [0x48, 0x00] + BMP header at offset 2 + GRB pixel data (bottom-up) + LE32 timestamp
    """
    width, height = img.size
    pixels = list(img.getdata())

    # Row size with 4-byte alignment (24-bit = 3 bytes per pixel)
    row_size = (width * 3 + 3) & ~3
    pixel_data_size = row_size * height
    bmp_header_size = 54  # 14 (file header) + 40 (DIB header)
    file_size = bmp_header_size + pixel_data_size

    bmp = bytearray()

    # Corsair prefix
    bmp += bytes([0x48, 0x00])

    # BMP file header (14 bytes) at offset 2
    bmp += b'BM'
    bmp += struct.pack('<I', file_size)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', bmp_header_size)

    # DIB header (40 bytes)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', width)
    bmp += struct.pack('<i', height)  # positive = bottom-up
    bmp += struct.pack('<HH', 1, 24)  # 1 plane, 24 bits
    bmp += struct.pack('<I', 0)       # no compression
    bmp += struct.pack('<I', pixel_data_size)
    bmp += struct.pack('<i', 2835)    # 72 DPI
    bmp += struct.pack('<i', 2835)
    bmp += struct.pack('<II', 0, 0)

    # Pixel data: bottom-up rows, GRB order (not BGR!)
    for y in range(height - 1, -1, -1):
        for x in range(width):
            r, g, b = pixels[y * width + x]
            bmp += bytes([g, r, b])  # GRB order
        # Pad row to 4-byte boundary
        padding = row_size - width * 3
        bmp += bytes(padding)

    # Timestamp (4 bytes LE)
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
        # Check current mode
        mode_data = get_property(fd, PROP_MODE)
        mode = mode_data[0] if mode_data else None
        print(f"Current mode: {mode}")

        # Get screen index
        screen_data = get_property(fd, PROP_SCREEN_IDX)
        print(f"Screen index property (263): {hex_dump(screen_data) if screen_data else 'None'}")

        print("\n=== PHASE 0: Close any stale handles ===")
        for handle in [0, 1, 2]:
            # Old Bragi close format: [0x08, 0x05, handle, 0x00]
            resp = send_recv(fd, bytes([BRAGI, CMD_UNBIND, handle, 0x00]))
            ok = status_ok(resp)
            print(f"  Close handle {handle}: {'OK' if ok else 'FAIL'} -> {hex_dump(resp)}")

        print("\n=== PHASE 1: Try file 62 WITHOUT session ===")
        print("(This should fail with error 0x06 as before)")
        open_file(fd, FILE_ACTIVE_DISPLAY, 0)

        print("\n=== PHASE 2: Start session ===")
        session_id = start_session(fd)

        if session_id is not None:
            print(f"\nSession established! ID = {session_id}")
        else:
            print("\nSession start returned non-standard response, continuing anyway...")

        print("\n=== PHASE 3: Try file 62 WITH session active ===")
        ok = open_file(fd, FILE_ACTIVE_DISPLAY, 0)
        if ok:
            print("*** FILE 62 OPENED! Session was the key! ***")
            unbind_buffers(fd, [0])
        else:
            print("File 62 still fails. Trying unbind + create + open...")
            unbind_buffers(fd, [0])
            create_file(fd, FILE_ACTIVE_DISPLAY)
            ok = open_file(fd, FILE_ACTIVE_DISPLAY, 0)
            if ok:
                print("*** FILE 62 OPENED after create! ***")
                unbind_buffers(fd, [0])

        print("\n=== PHASE 4: Read file 28007 (widget layout) ===")
        widget_data = read_file(fd, FILE_WIDGET_28007, 0)

        print("\n=== PHASE 5: Try selectWidget sequence ===")
        if widget_data and len(widget_data) > 0:
            print(f"Widget data ({len(widget_data)} bytes): {hex_dump(widget_data)}")

            # Now write widget data to file 62
            print("\nWriting widget data to file 62...")
            take_photo("session_before")

            success = write_file(fd, FILE_ACTIVE_DISPLAY, widget_data, 0)
            if success:
                print("*** WRITE TO FILE 62 SUCCEEDED! ***")

                # Update screen index property
                idx_bytes = struct.pack('<I', FILE_WIDGET_28007)
                set_ok = set_property(fd, PROP_SCREEN_IDX, idx_bytes)
                print(f"Set screen index to 28007: {'OK' if set_ok else 'FAIL'}")

                time.sleep(2)
                take_photo("session_after_write62")
            else:
                print("Write to file 62 failed")
        else:
            print("Could not read widget data from file 28007")
            print("Trying to write test image directly...")

        print("\n=== PHASE 6: Try writing custom image to resource 0x3F ===")
        print("Creating test image...")
        bmp_data = make_test_image()
        print(f"BMP data: {len(bmp_data)} bytes, header: {hex_dump(bmp_data, 16)}")

        # Write BMP to file 28007 (the screen resource)
        take_photo("session_before_custom")
        success = write_file(fd, FILE_WIDGET_28007, bmp_data, 0)
        if success:
            print("Custom image written to file 28007!")

            # Try to activate it via file 62
            # Read back what we wrote
            widget_data = read_file(fd, FILE_WIDGET_28007, 0)
            if widget_data:
                write_file(fd, FILE_ACTIVE_DISPLAY, widget_data, 0)

            time.sleep(2)
            take_photo("session_after_custom")

        print("\n=== PHASE 7: Try operating mode changes ===")
        # Set HOST_CONTROLLED, write, then set SELF_OPERATED
        print("Setting HOST_CONTROLLED mode...")
        set_property(fd, PROP_MODE, struct.pack('<I', MODE_HOST_CONTROLLED))
        time.sleep(1)

        mode_data = get_property(fd, PROP_MODE)
        print(f"Mode now: {mode_data[0] if mode_data else 'unknown'}")

        # Try file 62 in HOST_CONTROLLED mode
        print("Trying file 62 in HOST_CONTROLLED mode...")
        ok = open_file(fd, FILE_ACTIVE_DISPLAY, 0)
        if ok:
            print("*** FILE 62 OPENS IN HOST_CONTROLLED MODE! ***")
            unbind_buffers(fd, [0])

        # Restore SELF_OPERATED
        print("Restoring SELF_OPERATED mode...")
        set_property(fd, PROP_MODE, struct.pack('<I', MODE_SELF_OPERATED))
        time.sleep(1)

        print("\n=== PHASE 8: Stop session ===")
        stop_session(fd)

        take_photo("session_final")
        print("\nDone!")

    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
