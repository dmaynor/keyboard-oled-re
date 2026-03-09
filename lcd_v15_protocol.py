#!/usr/bin/env python3
"""
LCD V1.5 Protocol Test — Try using 4-byte Bragi header format.

The Web Hub JS supports two header formats:
  V1.0 (2-byte): [deviceIndex(0x08), commandId]
  V1.5 (4-byte): [subDeviceAddr(0), direction(1=req), sessionId, commandId]

Our device responds to V1.0. But maybe V1.5 unlocks display functionality.
Also: scan for firmware version properties and try diagnostic commands.
"""

import os
import time
import struct
import subprocess
import glob
import random

BRAGI = 0x08
PKT_SIZE = 1024

# V1.0 commands
CMD_SET = 0x01
CMD_GET = 0x02
CMD_UNBIND = 0x05
CMD_WRITE_BEGIN = 0x06
CMD_WRITE_CONT = 0x07
CMD_READ = 0x08
CMD_DESCRIBE = 0x09
CMD_CALIBRATION = 0x0A
CMD_CREATE = 0x0B
CMD_DELETE = 0x0C
CMD_OPEN = 0x0D
CMD_RESET_FACTORY = 0x0F
CMD_PING = 0x12
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
    """Send/receive with V1.0 (2-byte) header."""
    padded = bytes(data) + bytes(PKT_SIZE - len(data))
    os.write(fd, bytes([0x00]) + padded)
    end = time.time() + timeout
    while time.time() < end:
        try:
            return os.read(fd, 2048)
        except BlockingIOError:
            time.sleep(0.005)
    return None


def sr15(fd, cmd_id, payload=b'', session_id=0, sub_device=0, timeout=2.0):
    """Send/receive with V1.5 (4-byte) header."""
    header = bytes([sub_device, 0x01, session_id, cmd_id])  # direction=1 for request
    data = header + bytes(payload)
    padded = data + bytes(PKT_SIZE - len(data))
    os.write(fd, bytes([0x00]) + padded)
    end = time.time() + timeout
    while time.time() < end:
        try:
            resp = os.read(fd, 2048)
            return resp
        except BlockingIOError:
            time.sleep(0.005)
    return None


def ok(resp):
    return resp is not None and len(resp) > 2 and resp[2] == 0x00


def ok15(resp):
    """Check V1.5 response (status at offset 4)."""
    return resp is not None and len(resp) > 4 and resp[4] == 0x00


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


def get_prop(fd, prop_id):
    pkt = [BRAGI, CMD_GET] + list(struct.pack('<H', prop_id))
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/18-v15-protocol/{name}.jpg"
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

    # ================================================================
    # PHASE 1: Get firmware version and device info
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 1: Firmware version and device info")
    print("=" * 60)

    # Firmware version properties typically in 17-20 range
    fw_props = {
        1: "Device status",
        2: "Polling rate ms",
        3: "Operating mode",
        4: "Connection type",
        9: "Battery charging",
        10: "Battery level",
        17: "FW version 1",
        18: "FW version 2",
        19: "FW build date",
        20: "FW build time",
        56: "Hardware layout",
        57: "Connection count",
        96: "Bragi version",
        150: "Device type/features",
    }

    for pid, desc in sorted(fw_props.items()):
        resp = get_prop(fd, pid)
        if ok(resp):
            raw = resp[3:11] if len(resp) >= 11 else resp[3:]
            val = struct.unpack_from('<I', raw, 0)[0] if len(raw) >= 4 else 0
            # Also try decoding as version bytes
            b0, b1, b2, b3 = raw[0], raw[1], raw[2], raw[3] if len(raw) >= 4 else (0, 0, 0, 0)
            print(f"  Property {pid:3d}: {desc}")
            print(f"    Raw: {hx(bytes(raw), 8)}")
            print(f"    LE32: {val} (0x{val:08X})")
            print(f"    Bytes: {b0}.{b1}.{b2}.{b3}")

    # ================================================================
    # PHASE 2: Try V1.5 protocol header
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 2: Test V1.5 protocol (4-byte header)")
    print("=" * 60)

    # V1.5 GET property (mode, property 3)
    print("\n  V1.5 GET property 3 (operating mode):")
    payload = struct.pack('<H', 3)
    resp = sr15(fd, CMD_GET, payload, session_id=0, sub_device=0)
    print(f"    Response: {hx(resp, 16)}")

    # V1.5 session start
    print("\n  V1.5 SESSION start:")
    token15 = bytes(random.randint(0, 255) for _ in range(4))
    resp = sr15(fd, CMD_SESSION, bytes([0x01]) + token15 + bytes([0x00]))
    print(f"    Response: {hx(resp, 16)}")

    # V1.5 PING
    print("\n  V1.5 PING:")
    resp = sr15(fd, CMD_PING, bytes([0x01, 0x00]))
    print(f"    Response: {hx(resp, 16)}")

    # V1.5 GET multiple properties
    print("\n  V1.5 GET property 242 (width):")
    payload = struct.pack('<H', 242)
    resp = sr15(fd, CMD_GET, payload)
    print(f"    Response: {hx(resp, 16)}")

    # Check if V1.5 responses use different status offset
    print("\n  Analyzing V1.5 response format:")
    for cmd_name, cmd_id, payload in [
        ("GET mode", CMD_GET, struct.pack('<H', 3)),
        ("GET width", CMD_GET, struct.pack('<H', 242)),
        ("DESCRIBE buf0", CMD_DESCRIBE, bytes([0])),
    ]:
        resp = sr15(fd, cmd_id, payload)
        if resp:
            print(f"    {cmd_name}: {hx(resp, 16)}")
            # Check various status positions
            for pos in [2, 3, 4]:
                if pos < len(resp):
                    print(f"      offset {pos}: 0x{resp[pos]:02X} ({'OK' if resp[pos]==0 else 'ERR'})")

    # ================================================================
    # PHASE 3: Try V1.5 file operations
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 3: V1.5 file operations")
    print("=" * 60)

    # V1.5 OPEN file 62
    print("\n  V1.5 OPEN file 62:")
    fid = struct.pack('<H', 62)
    resp = sr15(fd, CMD_OPEN, bytes([0]) + fid)
    print(f"    Response: {hx(resp, 16)}")

    if resp and len(resp) > 4 and resp[4] == 0x00:
        print("    *** V1.5 OPEN succeeded! ***")
        # V1.5 DESCRIBE
        resp = sr15(fd, CMD_DESCRIBE, bytes([0]))
        print(f"    DESCRIBE: {hx(resp, 16)}")
        # V1.5 READ
        resp = sr15(fd, CMD_READ, bytes([0]))
        print(f"    READ: {hx(resp, 16)}")
        # V1.5 UNBIND
        sr15(fd, CMD_UNBIND, bytes([0]))

    # V1.5 OPEN file 28007
    print("\n  V1.5 OPEN file 28007:")
    fid = struct.pack('<H', 28007)
    resp = sr15(fd, CMD_OPEN, bytes([0]) + fid)
    print(f"    Response: {hx(resp, 16)}")

    # ================================================================
    # PHASE 4: Try V1.5 write to file 62 with image config
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 4: V1.5 write config to file 62")
    print("=" * 60)

    # First set to HOST mode (V1.0 — we know this works)
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # Write BMP to file 28200 (V1.0 — we know this works)
    W, H = 248, 170
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
    for y in range(H):
        for x in range(W):
            bmp += bytes([0x00, 0xFF, 0x00])  # Red in GRB
        bmp += bytes(row_size - W * 3)
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)

    print("  Writing BMP to file 28200 (V1.0)...")
    close_all(fd)
    result = write_file(fd, 28200, bytes(bmp))
    print(f"  Write: {'OK' if result else 'FAIL'}")

    # Now try V1.5 write to file 62
    print("\n  V1.5 OPEN file 62 for write:")
    close_all(fd)
    fid62 = struct.pack('<H', 62)
    resp = sr15(fd, CMD_OPEN, bytes([0]) + fid62)
    print(f"    OPEN: {hx(resp, 16)}")

    config = bytes([0x38, 0x00]) + struct.pack('<H', 28200) + bytes(12)

    if resp and len(resp) > 4 and resp[4] == 0x00:
        print("  V1.5 OPEN OK — writing config...")
        total = len(config)
        wb_payload = bytes([0]) + struct.pack('<I', total) + config
        resp = sr15(fd, CMD_WRITE_BEGIN, wb_payload)
        print(f"    WRITE_BEGIN: {hx(resp, 16)}")

        resp = sr15(fd, CMD_UNBIND, bytes([0]))
        print(f"    UNBIND: {hx(resp, 16)}")
    else:
        print("  V1.5 OPEN failed — trying V1.0 for file 62")
        close_all(fd)
        write_file(fd, 62, config)

    # Switch to SELF
    print("\n  Switching to SELF...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device!"); return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    time.sleep(2)
    take_photo("phase4_v15_file62")

    # ================================================================
    # PHASE 5: Try CALIBRATION command (0x0A) — only unused cmd
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 5: Try command 0x0A (calibration/unknown)")
    print("=" * 60)

    # The Web Hub shows: calibrationControlCommand(session, mode, type)
    # mode values: gB.Start, gB.Stop
    # type values: pB.MagneticSwitch
    # But maybe other mode/type combos do something with the display?
    for mode in range(4):
        for ctype in range(4):
            resp = sr(fd, [BRAGI, CMD_CALIBRATION, mode, ctype])
            status = resp[2] if resp and len(resp) > 2 else -1
            if status != 5 and status != 4:  # Not "property not supported" or "command not supported"
                print(f"  CMD 0x0A mode={mode} type={ctype}: status={status} raw={hx(resp, 10)}")

    # ================================================================
    # PHASE 6: Try RESET_FACTORY command (0x0F) — might reload configs
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 6: Explore RESET_FACTORY command (0x0F)")
    print("=" * 60)
    print("  NOTE: Not sending actual reset — just probing response format")

    # The Web Hub shows: resetToFactorySettingCommand(session, mode)
    # mode might control what gets reset
    # Let's just check which modes respond without actually resetting
    for mode in range(10):
        resp = sr(fd, [BRAGI, CMD_RESET_FACTORY, mode], timeout=0.5)
        if resp:
            status = resp[2] if len(resp) > 2 else -1
            print(f"  RESET mode={mode}: status={status} raw={hx(resp, 8)}")

    # ================================================================
    # PHASE 7: Check all HID interfaces — maybe there's a direct FB endpoint
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 7: Enumerate ALL HID interfaces")
    print("=" * 60)

    for h in sorted(glob.glob("/dev/hidraw*")):
        name = h.split("/")[-1]
        try:
            uevent = open(f"/sys/class/hidraw/{name}/device/uevent").read()
            if "VANGUARD" in uevent:
                # Get more info
                info = {}
                for line in uevent.split('\n'):
                    if '=' in line:
                        k, v = line.split('=', 1)
                        info[k] = v
                print(f"  {h}: {info.get('HID_NAME', '?')}")
                print(f"    PHYS: {info.get('HID_PHYS', '?')}")
                print(f"    ID: {info.get('HID_ID', '?')}")
                # Check report descriptor size
                try:
                    desc_path = f"/sys/class/hidraw/{name}/device/report_descriptor"
                    desc_size = os.path.getsize(desc_path)
                    with open(desc_path, 'rb') as f:
                        desc = f.read(32)
                    print(f"    Report descriptor: {desc_size} bytes, first 32: {hx(desc)}")
                except:
                    pass
        except:
            pass

    # ================================================================
    # PHASE 8: Try writing raw pixel data directly without OPEN
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 8: Try raw WRITE_BEGIN without OPEN (direct framebuffer?)")
    print("=" * 60)

    # What if we can WRITE_BEGIN to a buffer index that maps to the display
    # without OPEN? Some devices support direct buffer writes.
    for buf_idx in range(8):
        close_all(fd)
        pixel_data = bytes([0xFF, 0x00]) * 100  # 200 bytes of red pixels
        total = len(pixel_data)
        pkt = [BRAGI, CMD_WRITE_BEGIN, buf_idx] + list(struct.pack('<I', total)) + list(pixel_data)
        resp = sr(fd, pkt, timeout=0.5)
        status = resp[2] if resp and len(resp) > 2 else -1
        if status == 0:
            print(f"  Buffer {buf_idx}: WRITE_BEGIN OK (no prior OPEN!)")
        else:
            print(f"  Buffer {buf_idx}: status={status}")
        close_all(fd)

    # ================================================================
    # PHASE 9: Try SET on property 65 (0x41) — previously saw it writable
    # ================================================================
    print("\n" + "=" * 60)
    print("PHASE 9: Try SET on property 65 with various values")
    print("=" * 60)

    # Property 65 had value 1. What if it controls animation on/off?
    original_65 = get_prop(fd, 65)
    print(f"  Original: {hx(original_65, 8)}")

    for val in [0, 1, 2, 3, 4, 255]:
        resp = set_prop(fd, 65, struct.pack('<I', val))
        status = resp[2] if resp and len(resp) > 2 else -1
        if status == 0:
            print(f"  SET 65 = {val}: OK")
            time.sleep(0.5)
            take_photo(f"phase9_prop65_val{val}")
        else:
            print(f"  SET 65 = {val}: status={status}")

    # Restore
    if ok(original_65):
        set_prop(fd, 65, original_65[3:7] if len(original_65) >= 7 else bytes([1, 0, 0, 0]))

    # ================================================================
    # RESTORE: Set config back to resource 0x3F
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

    close_all(fd)
    write_file(fd, 62, bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12))

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
