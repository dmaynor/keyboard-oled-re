#!/usr/bin/env python3
"""
LCD Framebuffer Race — Write directly to resource 0x3F (the LCD framebuffer).

Key insight: This firmware (BragiVersion=0) only renders from hardware resources,
NOT from file IDs. Resource 0x3F = 84,320 bytes = 248×170×2 (RGB565).

Approaches to try:
  1. Write RGB565 to 0x3F in HOST mode, then IMMEDIATELY switch to SELF
  2. Write RGB565 to 0x3F in SELF mode (never leave it)
  3. Rapid-fire writes to 0x3F in SELF mode to overpower the animation loop
  4. Write to 0x3F in HOST mode and STAY in HOST mode (no mode switch)
  5. Try writing to resource 0x02 (138 bytes) - maybe a control register?
  6. Try config pointing to a resource we wrote (0x3F) with cookie update
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

# Display: 248×170 visible, RGB565, 2 bytes/pixel
W, H = 248, 170
FRAME_SIZE = W * H * 2  # 84,320 bytes


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


def write_resource(fd, res_id, data, handle=1):
    """Write to a hardware resource (old Bragi style with single-byte resource ID)."""
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, handle, res_id, 0x00, 0x00])
    if not ok(resp):
        print(f"    OPEN resource 0x{res_id:02X} failed: {hx(resp)}")
        return False
    total = len(data)
    chunk = min(total, PKT_SIZE - 7)
    pkt = [BRAGI, CMD_WRITE_BEGIN, handle] + list(struct.pack('<I', total)) + list(data[:chunk])
    if not ok(sr(fd, pkt)):
        print(f"    WRITE_BEGIN failed")
        sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
        return False
    offset = chunk
    while offset < total:
        chunk = min(total - offset, PKT_SIZE - 3)
        pkt = [BRAGI, CMD_WRITE_CONT, handle] + list(data[offset:offset + chunk])
        if not ok(sr(fd, pkt)):
            print(f"    WRITE_CONT failed at offset {offset}")
            sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
            return False
        offset += chunk
    sr(fd, [BRAGI, CMD_UNBIND, handle, 0x00], timeout=0.3)
    return True


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


def get_prop(fd, prop_id):
    pkt = [BRAGI, CMD_GET] + list(struct.pack('<H', prop_id))
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/16-framebuffer-race/{name}.jpg"
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


def make_solid_rgb565(r, g, b):
    """Create solid color frame in RGB565 LE format."""
    # RGB565: RRRRR GGGGGG BBBBB
    r5 = (r >> 3) & 0x1F
    g6 = (g >> 2) & 0x3F
    b5 = (b >> 3) & 0x1F
    pixel = (r5 << 11) | (g6 << 5) | b5
    pixel_bytes = struct.pack('<H', pixel)
    return pixel_bytes * (W * H)


def make_checkerboard_rgb565():
    """Create a visible checkerboard pattern."""
    frame = bytearray()
    white = struct.pack('<H', 0xFFFF)
    black = struct.pack('<H', 0x0000)
    for y in range(H):
        for x in range(W):
            if ((x // 20) + (y // 20)) % 2 == 0:
                frame += white
            else:
                frame += black
    return bytes(frame)


def make_gradient_rgb565():
    """Create a visible gradient pattern — red to blue horizontal."""
    frame = bytearray()
    for y in range(H):
        for x in range(W):
            r5 = int(31 * (1 - x / W))
            b5 = int(31 * (x / W))
            g6 = int(63 * (y / H))
            pixel = (r5 << 11) | (g6 << 5) | b5
            frame += struct.pack('<H', pixel)
    return bytes(frame)


def read_file_raw(fd, file_id, buf=0, max_size=200000):
    """Read a file, return bytes."""
    fid = struct.pack('<H', file_id)
    close_all(fd)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        return None
    desc = sr(fd, [BRAGI, CMD_DESCRIBE, buf])
    if not ok(desc) or len(desc) < 9:
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return None
    size = struct.unpack_from('<I', desc, 5)[0]
    if size == 0 or size > max_size:
        size = struct.unpack_from('<I', desc, 4)[0]
    if size == 0 or size > max_size:
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return bytes()
    data = bytearray()
    while len(data) < size:
        resp = sr(fd, [BRAGI, CMD_READ, buf])
        if resp is None:
            break
        chunk = resp[3:]
        needed = size - len(data)
        data.extend(chunk[:needed])
    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    return bytes(data)


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

    # Pre-generate test frames
    red_frame = make_solid_rgb565(255, 0, 0)
    green_frame = make_solid_rgb565(0, 255, 0)
    blue_frame = make_solid_rgb565(0, 0, 255)
    white_frame = make_solid_rgb565(255, 255, 255)
    checker_frame = make_checkerboard_rgb565()
    gradient_frame = make_gradient_rgb565()

    print(f"Frame size: {len(red_frame)} bytes (expected {FRAME_SIZE})")

    # ================================================================
    # APPROACH 1: Write to 0x3F in SELF mode (no mode switch)
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 1: Write RGB565 to resource 0x3F in SELF mode")
    print("=" * 60)
    print("  (Firmware is running animation — this is a race)")

    close_all(fd)
    print("  Writing solid RED...")
    t0 = time.time()
    result = write_resource(fd, 0x3F, red_frame)
    t1 = time.time()
    print(f"  Write: {'OK' if result else 'FAIL'} in {t1-t0:.2f}s")
    # Take photo IMMEDIATELY after write completes
    take_photo("approach1_red_self_mode")

    # ================================================================
    # APPROACH 2: Write to 0x3F in HOST mode, stay in HOST
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 2: Write to 0x3F in HOST mode, STAY in HOST")
    print("=" * 60)

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    print("  Writing solid GREEN to 0x3F in HOST mode...")
    close_all(fd)
    result = write_resource(fd, 0x3F, green_frame)
    print(f"  Write: {'OK' if result else 'FAIL'}")
    time.sleep(1)
    take_photo("approach2_green_host_stay")

    print("  Writing checkerboard to 0x3F in HOST mode...")
    close_all(fd)
    result = write_resource(fd, 0x3F, checker_frame)
    print(f"  Write: {'OK' if result else 'FAIL'}")
    time.sleep(1)
    take_photo("approach2_checker_host_stay")

    # ================================================================
    # APPROACH 3: Write to 0x3F + IMMEDIATELY switch to SELF (minimal delay)
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 3: Write to 0x3F in HOST, INSTANT switch to SELF")
    print("=" * 60)

    # Already in HOST mode
    print("  Writing gradient to 0x3F...")
    close_all(fd)
    result = write_resource(fd, 0x3F, gradient_frame)
    print(f"  Write: {'OK' if result else 'FAIL'}")

    # IMMEDIATELY switch to SELF — no sleep
    print("  Switching to SELF immediately...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    # Photo immediately
    take_photo("approach3_gradient_instant_self")
    # And after a short delay
    time.sleep(2)
    take_photo("approach3_gradient_after_2s")

    # ================================================================
    # APPROACH 4: Full WebHub flow with resource map + cookie
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 4: Full WebHub flow — exact sequence")
    print("  resource map + config + cookie + mode switch")
    print("=" * 60)

    # Switch to HOST
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Use file ID 28200 for our image resource, 28210 for layout
    IMAGE_FILE = 28200
    LAYOUT_FILE = 28210
    PROFILE_FILE = 28000

    # Step 1: Create and write Corsair BMP to image file
    print("  Step 1: Writing Corsair BMP to image file...")
    row_size = (W * 3 + 3) & ~3
    pds = row_size * H
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])  # Corsair prefix
    bmp += b'BM'
    bmp += struct.pack('<I', 54 + pds)
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)
    bmp += struct.pack('<I', 40)
    bmp += struct.pack('<i', W)
    bmp += struct.pack('<i', H)
    bmp += struct.pack('<HH', 1, 24)
    bmp += struct.pack('<I', 0)
    bmp += struct.pack('<I', pds)
    bmp += struct.pack('<ii', 2835, 2835)
    bmp += struct.pack('<II', 0, 0)
    # Solid red in GRB order (Corsair uses GRB)
    for y in range(H):
        for x in range(W):
            bmp += bytes([0x00, 0xFF, 0x00])  # G=0, R=255, B=0 → red
        bmp += bytes(row_size - W * 3)  # row padding
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)  # timestamp
    close_all(fd)
    result = write_file(fd, IMAGE_FILE, bytes(bmp))
    print(f"    Write image ({len(bmp)} bytes): {'OK' if result else 'FAIL'}")

    # Step 2: Update screen resource map (file 61)
    print("  Step 2: Updating screen resource map (file 61)...")
    # Read current resource map
    close_all(fd)
    current_map = read_file_raw(fd, 61)
    print(f"    Current map: {hx(current_map, 32) if current_map else 'None'}")

    # Build new resource map with our entry
    # Format: [header(2), count(2 LE), items of (resourceId(2 LE) + resourceAddress(2 LE) + hash(4))]
    map_data = bytearray()
    map_data += bytes([0x00, 0x00])  # header
    map_data += struct.pack('<H', 1)  # count = 1
    map_data += struct.pack('<H', IMAGE_FILE)  # resourceId = our image file ID
    map_data += struct.pack('<H', IMAGE_FILE)  # resourceAddress = same as resourceId
    map_data += bytes(4)  # hash = [0,0,0,0]
    close_all(fd)
    result = write_file(fd, 61, bytes(map_data))
    print(f"    Write resource map: {'OK' if result else 'FAIL'}")

    # Step 3: Create layout config and write to layout file
    print("  Step 3: Writing layout config to layout file...")
    config = bytes([0x38, 0x00]) + struct.pack('<H', IMAGE_FILE) + bytes(12)  # 16 bytes
    close_all(fd)
    result = write_file(fd, LAYOUT_FILE, config)
    print(f"    Write layout config: {'OK' if result else 'FAIL'}")

    # Step 4: Update screen modes layout (file 28006)
    print("  Step 4: Updating screen modes layout...")
    close_all(fd)
    current_layout = read_file_raw(fd, 28006)
    print(f"    Current layout: {hx(current_layout, 32) if current_layout else 'None'}")

    # Build new layout with our layout file added
    # Format: [header(2), rowCount(2 LE), row: count(2 LE) + fileIds(2 LE each)]
    layout_data = bytearray()
    layout_data += bytes([0x00, 0x00])  # header
    layout_data += struct.pack('<H', 1)  # rowCount = 1
    layout_data += struct.pack('<H', 2)  # row0 count = 2 entries
    layout_data += struct.pack('<H', 28007)  # original layout file
    layout_data += struct.pack('<H', LAYOUT_FILE)  # our new layout file
    close_all(fd)
    result = write_file(fd, 28006, bytes(layout_data))
    print(f"    Write layout: {'OK' if result else 'FAIL'}")

    # Step 5: Write config to file 62 (active display) — selectWidget equivalent
    print("  Step 5: Writing config to file 62 (selectWidget)...")
    close_all(fd)
    result = write_file(fd, 62, config)
    print(f"    Write file 62: {'OK' if result else 'FAIL'}")

    # Step 6: Update property 263 (screen index) in properties file (28001)
    print("  Step 6: Updating property 263 (screen index)...")
    close_all(fd)
    props = read_file_raw(fd, 28001)
    if props and len(props) >= 4:
        print(f"    Current props: {hx(props, 32)}")
        # Parse and update property 263
        header = props[:2]
        count = struct.unpack_from('<H', props, 2)[0]
        entries = []
        found_263 = False
        for i in range(count):
            off = 4 + i * 6
            if off + 6 <= len(props):
                pid = struct.unpack_from('<H', props, off)[0]
                val = props[off + 2:off + 6]
                if pid == 263:
                    # Set to index 1 (our image is 2nd in the layout)
                    val = struct.pack('<I', 1)
                    found_263 = True
                entries.append((pid, val))
        if not found_263:
            entries.append((263, struct.pack('<I', 1)))
        # Rebuild
        new_props = bytearray(header)
        new_props += struct.pack('<H', len(entries))
        for pid, val in entries:
            new_props += struct.pack('<H', pid)
            new_props += val
        close_all(fd)
        result = write_file(fd, 28001, bytes(new_props))
        print(f"    Write props: {'OK' if result else 'FAIL'}")

    # Step 7: Update cookie in profile file (28000)
    print("  Step 7: Updating cookie in profile file...")
    close_all(fd)
    profile = read_file_raw(fd, 28000)
    if profile and len(profile) >= 20:
        print(f"    Current profile: {hx(profile, 32)}")
        # Cookie is at offset 4-7 (after 2-byte header + 2-byte profileId)
        new_cookie = struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
        new_profile = bytearray(profile)
        new_profile[4:8] = new_cookie
        close_all(fd)
        result = write_file(fd, 28000, bytes(new_profile))
        print(f"    Write cookie: {'OK' if result else 'FAIL'}")

    # Step 8: Switch to SELF
    print("  Step 8: Switching to SELF_OPERATED...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(3)
    take_photo("approach4_full_webhub_flow")

    # ================================================================
    # APPROACH 5: Write to resource 0x02 (138 bytes) — mystery resource
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 5: Investigate resource 0x02 (138 bytes)")
    print("=" * 60)

    # Read current contents of resource 0x02
    sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)
    resp = sr(fd, [BRAGI, CMD_OPEN, 1, 0x02, 0x00, 0x00])
    if ok(resp):
        desc = sr(fd, [BRAGI, CMD_DESCRIBE, 1])
        print(f"  DESCRIBE: {hx(desc)}")
        if ok(desc):
            size = struct.unpack_from('<I', desc, 5)[0]
            if size == 0 or size > 10000:
                size = struct.unpack_from('<I', desc, 4)[0]
            print(f"  Size: {size}")
            data = bytearray()
            while len(data) < size:
                resp = sr(fd, [BRAGI, CMD_READ, 1])
                if resp is None: break
                needed = size - len(data)
                data.extend(resp[3:][:needed])
            print(f"  Contents ({len(data)} bytes): {hx(bytes(data), 64)}")
            # Analyze — could be a control structure
            if len(data) >= 4:
                print(f"  As uint16 pairs:")
                for i in range(0, min(32, len(data)), 4):
                    a = struct.unpack_from('<H', data, i)[0]
                    b = struct.unpack_from('<H', data, i + 2)[0] if i + 4 <= len(data) else 0
                    print(f"    [{i:3d}]: 0x{a:04X} ({a:5d})  0x{b:04X} ({b:5d})")
        sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)

    # ================================================================
    # APPROACH 6: Rapid-fire writes to 0x3F in SELF mode
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 6: Rapid-fire writes to 0x3F in SELF mode (10x)")
    print("=" * 60)
    print("  Attempting to overpower animation with repeated writes...")

    for i in range(10):
        close_all(fd)
        t0 = time.time()
        result = write_resource(fd, 0x3F, white_frame)
        t1 = time.time()
        if i == 0:
            print(f"  Write #{i}: {'OK' if result else 'FAIL'} in {t1-t0:.2f}s")
    # Photo right after the burst
    take_photo("approach6_rapid_fire_white")

    # ================================================================
    # APPROACH 7: Try different resource IDs for display
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 7: Try writing to unused resource IDs (0x40-0x50)")
    print("=" * 60)

    # Try writing our frame data to resource IDs that aren't currently used
    for res_id in [0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x50, 0x60, 0x7F]:
        close_all(fd)
        sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)
        resp = sr(fd, [BRAGI, CMD_OPEN, 1, res_id, 0x00, 0x00])
        if ok(resp):
            print(f"  Resource 0x{res_id:02X}: OPEN OK — writing frame...")
            result = write_resource(fd, res_id, red_frame)
            print(f"    Write: {'OK' if result else 'FAIL'}")
            if result:
                # Point config to this resource
                config = bytes([0x38, 0x00, res_id, 0x00]) + bytes(12)
                close_all(fd)
                write_file(fd, 62, config)
                time.sleep(1)
                take_photo(f"approach7_res_{res_id:02X}")
        else:
            status = resp[2] if resp and len(resp) > 2 else -1
            print(f"  Resource 0x{res_id:02X}: OPEN failed (status {status})")
        sr(fd, [BRAGI, CMD_UNBIND, 1, 0x00], timeout=0.3)

    # ================================================================
    # APPROACH 8: Probe screen-related properties we haven't tried
    # ================================================================
    print("\n" + "=" * 60)
    print("APPROACH 8: Probe display properties")
    print("=" * 60)

    # Property scan around display-related ranges
    interesting_props = [
        60, 61, 62, 63, 64, 65,  # Near file 62
        100, 101, 102, 103,  # Unknown range
        230, 231, 232, 233, 234, 235,  # Near screen present
        240, 241, 242, 243, 244, 245,  # Near dimensions
        260, 261, 262, 263, 264, 265,  # Near screen index
        300, 301, 302,
    ]
    for pid in interesting_props:
        resp = get_prop(fd, pid)
        if ok(resp):
            val_bytes = resp[4:8] if len(resp) >= 8 else resp[3:]
            val = struct.unpack_from('<I', val_bytes, 0)[0] if len(val_bytes) >= 4 else val_bytes
            print(f"  Property {pid}: value={val} (raw: {hx(resp, 12)})")

    # ================================================================
    # RESTORE
    # ================================================================
    print("\n" + "=" * 60)
    print("RESTORING defaults")
    print("=" * 60)

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Restore file 62 to default (resource 0x3F)
    close_all(fd)
    write_file(fd, 62, bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12))

    # Restore file 61 (resource map) — try to restore original
    # Read what was there originally (likely empty or minimal)
    close_all(fd)
    write_file(fd, 61, bytes([0x00, 0x00, 0x00, 0x00]))  # empty map

    # Restore file 28006 (layout)
    close_all(fd)
    layout_restore = bytearray()
    layout_restore += bytes([0x00, 0x00])
    layout_restore += struct.pack('<H', 1)  # 1 row
    layout_restore += struct.pack('<H', 1)  # 1 entry
    layout_restore += struct.pack('<H', 28007)  # original
    write_file(fd, 28006, bytes(layout_restore))

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        time.sleep(2)
        take_photo("restored")
        os.close(fd)

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
