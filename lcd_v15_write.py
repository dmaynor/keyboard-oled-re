#!/usr/bin/env python3
"""
Corsair Vanguard 96 LCD — V1.5 Protocol Image Write

Based on reverse-engineering the Corsair Web Hub JavaScript.

Protocol V1.5 header (4 bytes):
  [subDevAddr, direction, sessionId, commandId]
  - subDevAddr: 0 for main device
  - direction: 1 = request, 0 = suppress answer
  - sessionId: assigned by session control
  - commandId: the command to execute

Commands:
  0x01 = SET property
  0x02 = GET property
  0x05 = UNBIND buffer (close)
  0x06 = WRITE buffer begin
  0x07 = WRITE buffer continue
  0x08 = READ buffer
  0x09 = DESCRIBE buffer (get size)
  0x0B = CREATE file
  0x0D = OPEN file
  0x1B = SESSION CONTROL

Properties:
  3 = Operating Mode (1=SELF_OPERATED, 2=HOST_CONTROLLED)
  230 (0xE6) = Screen Module Present
  240 (0xF0) = Startup Animation Active
  242 (0xF2) = Screen Width
  243 (0xF3) = Screen Height

Image format: Corsair custom BMP
  - 2-byte prefix: [0x48, 0x00]
  - Standard BMP header starting at offset 2
  - 24-bit GRB pixel order (not BGR)
  - Bottom-up rows, 4-byte aligned
  - 4-byte timestamp appended at end
"""

import os
import sys
import time
import struct
import subprocess
import glob
import io

# Device
VENDOR_ID = "1b1c"
PRODUCT_ID = "2b0d"

# Protocol
REPORT_SIZE = 1024
REPORT_ID = 0x00
HEADER_SIZE = 4

# Commands
CMD_SET = 0x01
CMD_GET = 0x02
CMD_UNBIND = 0x05
CMD_WRITE_BEGIN = 0x06
CMD_WRITE_CONTINUE = 0x07
CMD_READ = 0x08
CMD_DESCRIBE = 0x09
CMD_CREATE_FILE = 0x0B
CMD_OPEN_FILE = 0x0D
CMD_SESSION_CONTROL = 0x1B

# Properties
PROP_OPERATING_MODE = 3
PROP_SCREEN_MODULE_PRESENT = 230
PROP_SCREEN_WIDTH = 242
PROP_SCREEN_HEIGHT = 243

# Operating modes
MODE_SELF_OPERATED = 1
MODE_HOST_CONTROLLED = 2

# Buffer index
BUFFER_INDEX = 0  # Common_File_Index

# Screen resource for Vanguard 96
SCREEN_RESOURCE_ID = 28007  # from Web Hub config
ACTIVE_DISPLAY_FILE = 62    # pce = 62

# Screen dimensions
SCREEN_WIDTH = 248
SCREEN_HEIGHT = 170


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


class CorsairV15:
    """Corsair V1.5 protocol client."""

    def __init__(self, hidraw_path):
        self.fd = os.open(hidraw_path, os.O_RDWR | os.O_NONBLOCK)
        self.session_id = 0

    def close(self):
        os.close(self.fd)

    def _make_header(self, cmd_id, sub_addr=0, direction=1):
        """Create V1.5 4-byte header."""
        return bytes([sub_addr, direction, self.session_id, cmd_id])

    def _send_recv(self, data, timeout=2.0):
        """Send a command and wait for response."""
        # Pad to REPORT_SIZE
        if len(data) < REPORT_SIZE:
            data = data + bytes(REPORT_SIZE - len(data))

        # Prepend report ID
        pkt = bytes([REPORT_ID]) + data
        os.write(self.fd, pkt)

        end = time.time() + timeout
        while time.time() < end:
            try:
                resp = os.read(self.fd, REPORT_SIZE + 64)
                return resp
            except BlockingIOError:
                time.sleep(0.005)
        return None

    def _check_response(self, resp, cmd_id):
        """Check V1.5 response. Status is at header position."""
        if resp is None:
            return False, None
        # V1.5 response: [subAddr, status, sessionId?, cmdId, ...]
        # Based on checkInput: validates cmdId match
        # Status codes are in the data portion
        return True, resp

    def start_session(self):
        """Start a host software session (command 0x1B)."""
        random_bytes = os.urandom(4)
        header = bytes([0, 1, 0, CMD_SESSION_CONTROL])
        payload = bytes([1]) + random_bytes + bytes([0])
        data = header + payload

        resp = self._send_recv(data)
        if resp is None:
            print("  Session start: no response")
            return False

        print(f"  Session response: {' '.join(f'{b:02x}' for b in resp[:16])}")

        # Session ID is at byte 9 (from the JS code: P=9; sessionId = resp.slice(P, P+1))
        # But response format might differ — let's check various positions
        # In V1.5 response, header is 4 bytes, then command response data
        # For session control response: data starts at byte 4
        # The session ID could be at offset 4+5=9, or it could be at another position
        # Let's try to extract it
        if len(resp) > 9:
            self.session_id = resp[9]
            print(f"  Session ID (byte 9): {self.session_id}")
        if len(resp) > 5:
            print(f"  Alt session ID (byte 5): {resp[5]}")

        return True

    def get_property(self, prop_id):
        """GET a property value."""
        header = self._make_header(CMD_GET)
        payload = struct.pack('<H', prop_id)
        resp = self._send_recv(header + payload)
        if resp is None:
            return None
        # Response data starts after header
        # checkInput extracts: data = resp.slice(commandValue)
        # For V1.5 with 4-byte header, data starts at some offset
        return resp

    def set_property(self, prop_id, value):
        """SET a property value."""
        header = self._make_header(CMD_SET)
        payload = struct.pack('<H', prop_id) + struct.pack('<I', value)
        resp = self._send_recv(header + payload)
        return resp

    def open_file(self, file_id, buffer_index=BUFFER_INDEX):
        """Open a file for reading/writing."""
        header = self._make_header(CMD_OPEN_FILE)
        payload = bytes([buffer_index]) + struct.pack('<H', file_id)
        resp = self._send_recv(header + payload)
        if resp is None:
            print(f"  Open file {file_id}: no response")
            return False
        print(f"  Open file {file_id}: {' '.join(f'{b:02x}' for b in resp[:12])}")
        return True

    def create_file(self, file_id):
        """Create a new file."""
        header = self._make_header(CMD_CREATE_FILE)
        payload = struct.pack('<H', file_id)
        resp = self._send_recv(header + payload)
        if resp is None:
            print(f"  Create file {file_id}: no response")
            return False
        print(f"  Create file {file_id}: {' '.join(f'{b:02x}' for b in resp[:12])}")
        return True

    def unbind_buffers(self, buffer_indices):
        """Unbind/close buffer(s)."""
        header = self._make_header(CMD_UNBIND)
        payload = bytes([len(buffer_indices)] + buffer_indices)
        resp = self._send_recv(header + payload)
        return resp

    def describe_buffer(self, buffer_index=BUFFER_INDEX):
        """Get the size of data in a buffer."""
        header = self._make_header(CMD_DESCRIBE)
        payload = bytes([buffer_index])
        resp = self._send_recv(header + payload)
        if resp is None:
            return None
        # Size is at bytes 6-9 (2 bytes header offset + 4 bytes size)
        print(f"  Describe buffer: {' '.join(f'{b:02x}' for b in resp[:16])}")
        return resp

    def write_file(self, file_id, data, buffer_index=BUFFER_INDEX):
        """Write data to a file using the full write sequence."""
        # Try to open file, if fail then create and open
        if not self.open_file(file_id, buffer_index):
            print("  Open failed, trying create + open...")
            self.unbind_buffers([buffer_index])
            self.create_file(file_id)
            if not self.open_file(file_id, buffer_index):
                print("  CREATE + OPEN FAILED")
                return False

        # Write buffer begin
        max_first_chunk = REPORT_SIZE - HEADER_SIZE - 5  # 4 header + 1 bufIdx + 4 dataLen = 9
        first_chunk_size = min(len(data), max_first_chunk)

        header = self._make_header(CMD_WRITE_BEGIN)
        payload = bytes([buffer_index]) + struct.pack('<I', len(data)) + data[:first_chunk_size]
        resp = self._send_recv(header + payload)
        if resp is None:
            print("  Write begin: no response")
            self.unbind_buffers([buffer_index])
            return False
        print(f"  Write begin response: {' '.join(f'{b:02x}' for b in resp[:12])}")

        # Write buffer continue for remaining data
        offset = first_chunk_size
        max_cont_chunk = REPORT_SIZE - HEADER_SIZE - 1  # 4 header + 1 bufIdx = 5
        pkt_count = 1

        while offset < len(data):
            chunk_size = min(len(data) - offset, max_cont_chunk)
            header = self._make_header(CMD_WRITE_CONTINUE)
            payload = bytes([buffer_index]) + data[offset:offset + chunk_size]
            resp = self._send_recv(header + payload)
            if resp is None:
                print(f"  Write continue at {offset}: no response")
                self.unbind_buffers([buffer_index])
                return False
            offset += chunk_size
            pkt_count += 1
            if pkt_count % 50 == 0:
                print(f"    {offset}/{len(data)} bytes ({100*offset//len(data)}%)")

        print(f"  Write complete: {pkt_count} packets, {offset} bytes")

        # Unbind buffer
        self.unbind_buffers([buffer_index])
        return True

    def read_file(self, file_id, buffer_index=BUFFER_INDEX):
        """Read a file's contents."""
        if not self.open_file(file_id, buffer_index):
            return None

        # Describe to get size
        resp = self.describe_buffer(buffer_index)
        if resp is None:
            self.unbind_buffers([buffer_index])
            return None

        # Read buffer chunks
        data = bytearray()
        while True:
            header = self._make_header(CMD_READ)
            payload = bytes([buffer_index])
            resp = self._send_recv(header + payload)
            if resp is None:
                break
            # Extract data from response (after header)
            chunk = resp[HEADER_SIZE + 1:]  # Skip header + bufferIndex
            data.extend(chunk)
            if len(chunk) == 0:
                break

        self.unbind_buffers([buffer_index])
        return bytes(data)


def create_corsair_bmp(width, height, pixels_rgb=None):
    """Create a Corsair-format BMP image.

    Format:
    - 2 bytes: [0x48, 0x00] (Corsair prefix)
    - Standard BMP header starting at offset 2
    - 24-bit GRB pixel order
    - Bottom-up row order
    - 4-byte timestamp at end

    pixels_rgb: list of (R, G, B) tuples or None for test pattern
    """
    bpp = 24
    bytes_per_pixel = 3
    row_bytes = width * bytes_per_pixel
    padding = (4 - row_bytes % 4) % 4
    padded_row = row_bytes + padding
    pixel_data_size = padded_row * height
    header_size = 54  # 14 (file header) + 40 (DIB header)
    timestamp = struct.pack('<I', int(time.time()))
    total_size = header_size + pixel_data_size + len(timestamp) + 2  # +2 for prefix

    buf = bytearray(total_size)

    # Corsair prefix
    buf[0] = 0x48  # 72 decimal
    buf[1] = 0x00

    # BMP file header (at offset 2)
    struct.pack_into('<H', buf, 2, 0x4D42)  # 'BM'
    struct.pack_into('<I', buf, 4, total_size)
    struct.pack_into('<I', buf, 8, 0)  # Reserved
    struct.pack_into('<I', buf, 12, header_size)  # Pixel data offset

    # DIB header (BITMAPINFOHEADER at offset 16)
    struct.pack_into('<I', buf, 16, 40)  # DIB header size
    struct.pack_into('<i', buf, 20, width)
    struct.pack_into('<i', buf, 24, height)
    struct.pack_into('<H', buf, 28, 1)  # Planes
    struct.pack_into('<H', buf, 30, bpp)
    struct.pack_into('<I', buf, 32, 0)  # Compression (none)
    struct.pack_into('<I', buf, 36, pixel_data_size)
    struct.pack_into('<I', buf, 40, 2835)  # X pixels/meter
    struct.pack_into('<I', buf, 44, 2835)  # Y pixels/meter
    struct.pack_into('<I', buf, 48, 0)  # Colors used
    struct.pack_into('<I', buf, 52, 0)  # Important colors

    # Pixel data (bottom-up, GRB order)
    offset = header_size
    for y in range(height - 1, -1, -1):
        for x in range(width):
            if pixels_rgb:
                r, g, b = pixels_rgb[y * width + x]
            else:
                # Test pattern: gradient + border
                r, g, b = 0, 0, 0
                # Green border
                if x < 3 or x >= width - 3 or y < 3 or y >= height - 3:
                    r, g, b = 0, 255, 0
                # Red text area (crude)
                elif 30 < y < 60 and 40 < x < 200:
                    r, g, b = 255, 0, 0
                # Blue gradient
                else:
                    b = int(255 * x / width)
                    r = int(255 * y / height)

            # GRB order (Corsair custom)
            buf[offset] = g
            buf[offset + 1] = r
            buf[offset + 2] = b
            offset += 3

        # Row padding
        for _ in range(padding):
            buf[offset] = 0
            offset += 1

    # Append timestamp
    buf[total_size - 4:total_size] = timestamp

    return bytes(buf)


def create_test_bmp_with_text(width, height):
    """Create a BMP with text using PIL, then convert to Corsair format."""
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Green border
    draw.rectangle([2, 2, width-3, height-3], outline=(0, 255, 0), width=2)

    # Text
    draw.text((20, 20), "VIOLATOR", fill=(255, 0, 0))
    draw.text((20, 50), "ACTUAL", fill=(0, 255, 0))
    draw.text((20, 80), f"LCD Protocol RE", fill=(0, 128, 255))
    draw.text((20, 110), f"V1.5 @ {time.strftime('%H:%M:%S')}", fill=(255, 255, 0))
    draw.text((20, 140), f"{width}x{height} BMP", fill=(128, 128, 128))

    # Convert to pixel list
    pixels = list(img.getdata())

    return create_corsair_bmp(width, height, pixels)


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
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")

    # Ensure permissions
    subprocess.run(["sudo", "chmod", "666", hidraw], capture_output=True)

    client = CorsairV15(hidraw)

    try:
        # Step 0: Test basic V1.5 GET
        print("\n[0] Testing V1.5 protocol...")

        # Try GET property 3 (operating mode) with V1.5 header
        print("  GET operating mode (prop 3, V1.5 format):")
        resp = client.get_property(PROP_OPERATING_MODE)
        if resp:
            print(f"    Response: {' '.join(f'{b:02x}' for b in resp[:16])}")

        # Also try with old Bragi format for comparison
        print("  GET operating mode (old Bragi format):")
        old_resp = client._send_recv(bytes([0x08, CMD_GET, 0x01, 0x00]) + bytes(REPORT_SIZE - 4))
        if old_resp:
            print(f"    Response: {' '.join(f'{b:02x}' for b in old_resp[:16])}")

        # GET screen properties
        print("\n  GET screen properties:")
        for prop_id, name in [(PROP_SCREEN_WIDTH, "Width"), (PROP_SCREEN_HEIGHT, "Height"),
                              (PROP_SCREEN_MODULE_PRESENT, "Module Present")]:
            resp = client.get_property(prop_id)
            if resp:
                # Try to extract value from various positions
                val4 = struct.unpack_from('<I', resp, 4)[0] if len(resp) > 8 else 0
                val6 = struct.unpack_from('<I', resp, 6)[0] if len(resp) > 10 else 0
                print(f"    {name} ({prop_id}): raw={' '.join(f'{b:02x}' for b in resp[:12])}")
                print(f"      val@4={val4}, val@6={val6}")

        # Step 1: Start session
        print("\n[1] Starting V1.5 session...")
        if not client.start_session():
            print("  Session start failed, trying without session...")

        # Step 2: Take before photo
        print("\n[2] Before photo...")
        take_photo("v15_before")

        # Step 3: Set operating mode to HOST_CONTROLLED
        print("\n[3] Setting HOST_CONTROLLED mode (prop 3 = 2)...")
        resp = client.set_property(PROP_OPERATING_MODE, MODE_HOST_CONTROLLED)
        if resp:
            print(f"  Response: {' '.join(f'{b:02x}' for b in resp[:12])}")
        time.sleep(1)

        # Step 4: Create BMP image
        print(f"\n[4] Creating {SCREEN_WIDTH}x{SCREEN_HEIGHT} BMP image...")
        bmp_data = create_test_bmp_with_text(SCREEN_WIDTH, SCREEN_HEIGHT)
        print(f"  BMP size: {len(bmp_data)} bytes")
        print(f"  Header: {' '.join(f'{b:02x}' for b in bmp_data[:20])}")

        # Step 5: Write image to screen resource
        print(f"\n[5] Writing image to screen resource {SCREEN_RESOURCE_ID}...")
        t0 = time.time()
        if client.write_file(SCREEN_RESOURCE_ID, bmp_data):
            elapsed = time.time() - t0
            print(f"  Success! ({elapsed:.1f}s)")
        else:
            print("  WRITE FAILED")

        time.sleep(2)
        take_photo("v15_after_write")

        # Step 6: Try writing to active display file (62/0x3E)
        print(f"\n[6] Writing to active display file ({ACTIVE_DISPLAY_FILE})...")

        # Create a simple screen resource config: [nb=56, 0, resourceId_LE16]
        config = bytes([56, 0]) + struct.pack('<H', SCREEN_RESOURCE_ID)
        if client.write_file(ACTIVE_DISPLAY_FILE, config):
            print("  Config written!")
        else:
            print("  Config write failed, trying direct BMP write...")
            # Try writing the BMP directly to file 62
            client.write_file(ACTIVE_DISPLAY_FILE, bmp_data)

        time.sleep(2)
        take_photo("v15_after_activate")

        # Step 7: Restore SELF_OPERATED mode
        print(f"\n[7] Restoring SELF_OPERATED mode...")
        resp = client.set_property(PROP_OPERATING_MODE, MODE_SELF_OPERATED)
        if resp:
            print(f"  Response: {' '.join(f'{b:02x}' for b in resp[:12])}")

        time.sleep(2)
        take_photo("v15_final")

    finally:
        client.close()

    print("\nDone!")


if __name__ == "__main__":
    main()
