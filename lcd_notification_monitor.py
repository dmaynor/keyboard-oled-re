#!/usr/bin/env python3
"""
LCD Notification Monitor — Listen on the second HID endpoint (Usage 2)
while performing display operations on the primary endpoint (Usage 1).

DISCOVERY: /dev/hidraw3 = Usage 0xFF42:02 = 64-byte notification endpoint.
The Web Hub JS uses this endpoint for device-to-host notifications:
  - PropertyValueChange (1)
  - KeyPressStateChange (2)
  - CalibrationProgress (3)
  - DeviceLifetime (4)
  etc.

The firmware may send display update notifications that we need to handle
or acknowledge for the LCD to actually update.
"""

import os
import time
import struct
import subprocess
import glob
import random
import threading
import select

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


def find_hidraw():
    """Find Corsair Vanguard command (input2) and notification (input3) endpoints."""
    cmd_dev = None
    notify_dev = None
    for h in sorted(glob.glob("/dev/hidraw*")):
        name = h.split("/")[-1]
        try:
            uevent = open(f"/sys/class/hidraw/{name}/device/uevent").read()
            if "VANGUARD" in uevent:
                if "input2" in uevent:
                    cmd_dev = h
                elif "input3" in uevent:
                    notify_dev = h
        except:
            pass
    return cmd_dev, notify_dev


# Notification collector (runs in background thread)
notifications = []
notify_running = True


def notification_listener(notify_path):
    """Background thread that reads all notifications from the Usage 2 endpoint."""
    global notify_running
    try:
        fd = os.open(notify_path, os.O_RDONLY | os.O_NONBLOCK)
        print(f"  Notification listener started on {notify_path}")
        while notify_running:
            try:
                data = os.read(fd, 256)
                ts = time.time()
                notifications.append((ts, data))
                hex_str = ' '.join(f'{b:02x}' for b in data[:32])
                print(f"  [NOTIFY {ts:.3f}] ({len(data)}B): {hex_str}")
            except BlockingIOError:
                time.sleep(0.01)
            except Exception as e:
                print(f"  [NOTIFY ERROR] {e}")
                time.sleep(0.1)
        os.close(fd)
    except Exception as e:
        print(f"  Failed to open notification endpoint: {e}")


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


def read_file_raw(fd, file_id, buf=0, max_size=200000):
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
        needed = size - len(data)
        data.extend(resp[3:][:needed])
    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    return bytes(data)


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/19-notifications/{name}.jpg"
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
        cmd_dev, _ = find_hidraw()
        if cmd_dev:
            subprocess.run(["sudo", "chmod", "666", cmd_dev], capture_output=True)
            time.sleep(0.5)
            return cmd_dev
        time.sleep(0.5)
    return None


def main():
    global notify_running

    cmd_dev, notify_dev = find_hidraw()
    if not cmd_dev:
        print("ERROR: Keyboard command endpoint not found")
        return
    print(f"Command endpoint: {cmd_dev}")
    print(f"Notification endpoint: {notify_dev}")

    if not notify_dev:
        print("WARNING: Notification endpoint not found!")
        return

    # Set permissions
    subprocess.run(["sudo", "chmod", "666", cmd_dev], capture_output=True)
    subprocess.run(["sudo", "chmod", "666", notify_dev], capture_output=True)

    # Start notification listener in background
    notify_thread = threading.Thread(target=notification_listener, args=(notify_dev,), daemon=True)
    notify_thread.start()
    time.sleep(0.5)

    fd = os.open(cmd_dev, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    # ================================================================
    # TEST 1: Monitor notifications during idle (baseline)
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST 1: Baseline — monitor notifications for 3 seconds")
    print("=" * 60)

    n_before = len(notifications)
    time.sleep(3)
    n_after = len(notifications)
    print(f"  Received {n_after - n_before} notifications during idle")

    # ================================================================
    # TEST 2: Monitor during mode switch HOST → SELF
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST 2: Notifications during HOST → SELF mode switch")
    print("=" * 60)

    n_before = len(notifications)
    print("  Setting HOST mode...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)

    # Reconnect with notification monitoring
    time.sleep(3)
    for _ in range(20):
        cmd_dev, notify_dev = find_hidraw()
        if cmd_dev:
            subprocess.run(["sudo", "chmod", "666", cmd_dev], capture_output=True)
            if notify_dev:
                subprocess.run(["sudo", "chmod", "666", notify_dev], capture_output=True)
            time.sleep(0.5)
            break
        time.sleep(0.5)

    if not cmd_dev:
        print("  Lost device!"); return

    fd = os.open(cmd_dev, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    time.sleep(1)
    n_host = len(notifications)
    print(f"  Notifications during HOST switch: {n_host - n_before}")

    print("  Setting SELF mode...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)

    time.sleep(3)
    for _ in range(20):
        cmd_dev, notify_dev = find_hidraw()
        if cmd_dev:
            subprocess.run(["sudo", "chmod", "666", cmd_dev], capture_output=True)
            if notify_dev:
                subprocess.run(["sudo", "chmod", "666", notify_dev], capture_output=True)
            time.sleep(0.5)
            break
        time.sleep(0.5)

    if not cmd_dev:
        print("  Lost device!"); return

    fd = os.open(cmd_dev, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    n_self = len(notifications)
    print(f"  Notifications during SELF switch: {n_self - n_host}")

    # ================================================================
    # TEST 3: Monitor during file 62 write + cookie update
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST 3: Notifications during display update sequence")
    print("=" * 60)

    n_before = len(notifications)

    # Switch to HOST
    print("  Switching to HOST...")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    time.sleep(3)
    for _ in range(20):
        cmd_dev, _ = find_hidraw()
        if cmd_dev:
            subprocess.run(["sudo", "chmod", "666", cmd_dev], capture_output=True)
            time.sleep(0.5)
            break
        time.sleep(0.5)
    if not cmd_dev:
        print("  Lost device!"); return
    fd = os.open(cmd_dev, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    n_after_host = len(notifications)
    print(f"  Notifications after HOST: {n_after_host - n_before}")

    # Write BMP to file 28200
    W, H = 248, 170
    row_size = (W * 3 + 3) & ~3
    pds = row_size * H
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])
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
            bmp += bytes([0x00, 0x00, 0xFF])  # GRB: blue
        bmp += bytes(row_size - W * 3)
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)

    print(f"  Writing BMP ({len(bmp)} bytes) to file 28200...")
    n_before_write = len(notifications)
    close_all(fd)
    write_file(fd, 28200, bytes(bmp))
    time.sleep(0.5)
    n_after_write = len(notifications)
    print(f"  Notifications during BMP write: {n_after_write - n_before_write}")

    # Update resource map (file 61)
    print("  Updating resource map (file 61)...")
    map_data = bytearray([0x00, 0x00])
    map_data += struct.pack('<H', 1)
    map_data += struct.pack('<H', 28200)
    map_data += struct.pack('<H', 28200)
    map_data += bytes(4)
    close_all(fd)
    write_file(fd, 61, bytes(map_data))

    # Write config to layout file
    print("  Writing layout config to file 28210...")
    config = bytes([0x38, 0x00]) + struct.pack('<H', 28200) + bytes(12)
    close_all(fd)
    write_file(fd, 28210, config)

    # Write config to file 62 (selectWidget)
    print("  Writing config to file 62 (selectWidget)...")
    n_before_62 = len(notifications)
    close_all(fd)
    write_file(fd, 62, config)
    time.sleep(0.5)
    n_after_62 = len(notifications)
    print(f"  Notifications after file 62 write: {n_after_62 - n_before_62}")

    # Update cookie
    print("  Updating cookie (profile 28000)...")
    close_all(fd)
    profile = read_file_raw(fd, 28000)
    if profile and len(profile) >= 20:
        new_profile = bytearray(profile)
        new_profile[4:8] = struct.pack('<I', int(time.time()) & 0xFFFFFFFF)
        close_all(fd)
        n_before_cookie = len(notifications)
        write_file(fd, 28000, bytes(new_profile))
        time.sleep(0.5)
        n_after_cookie = len(notifications)
        print(f"  Notifications after cookie: {n_after_cookie - n_before_cookie}")

    # Switch to SELF
    print("  Switching to SELF...")
    n_before_self = len(notifications)
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)

    time.sleep(5)  # Wait longer for notifications
    n_after_self = len(notifications)
    print(f"  Notifications after SELF switch: {n_after_self - n_before_self}")

    # Reconnect
    for _ in range(20):
        cmd_dev, _ = find_hidraw()
        if cmd_dev:
            subprocess.run(["sudo", "chmod", "666", cmd_dev], capture_output=True)
            time.sleep(0.5)
            break
        time.sleep(0.5)
    if not cmd_dev:
        print("  Lost device!"); return
    fd = os.open(cmd_dev, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)

    take_photo("test3_display_update")

    # ================================================================
    # TEST 4: Try sending data ON the notification endpoint
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST 4: Try writing TO notification endpoint")
    print("=" * 60)

    try:
        nfd = os.open(notify_dev, os.O_RDWR | os.O_NONBLOCK)
        # Try sending a command on the notification endpoint
        test_pkt = bytes([0x00, BRAGI, CMD_GET]) + struct.pack('<H', 3) + bytes(61)  # 64 bytes + report ID
        try:
            os.write(nfd, test_pkt)
            print(f"  Write to notification endpoint: OK ({len(test_pkt)} bytes)")
            time.sleep(0.5)
            try:
                resp = os.read(nfd, 256)
                print(f"  Response: {hx(resp, 32)}")
            except BlockingIOError:
                print(f"  No response on notification endpoint")
        except OSError as e:
            print(f"  Write failed: {e}")
        os.close(nfd)
    except Exception as e:
        print(f"  Failed to open for write: {e}")

    # ================================================================
    # TEST 5: Monitor notifications during property SET
    # ================================================================
    print("\n" + "=" * 60)
    print("TEST 5: Notifications during property SET operations")
    print("=" * 60)

    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    for pid, val, desc in [(234, 0, "prop234=0"), (234, 1000, "prop234=1000"),
                            (260, 0, "prop260=0"), (260, 7, "prop260=7"),
                            (65, 0, "prop65=0"), (65, 1, "prop65=1")]:
        n_before = len(notifications)
        resp = set_prop(fd, pid, struct.pack('<I', val))
        status = resp[2] if resp and len(resp) > 2 else -1
        time.sleep(0.3)
        n_after = len(notifications)
        print(f"  SET {desc}: status={status}, notifications: {n_after - n_before}")

    # ================================================================
    # RESTORE
    # ================================================================
    print("\n" + "=" * 60)
    print("RESTORING defaults")
    print("=" * 60)

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    time.sleep(3)
    for _ in range(20):
        cmd_dev, _ = find_hidraw()
        if cmd_dev:
            subprocess.run(["sudo", "chmod", "666", cmd_dev], capture_output=True)
            time.sleep(0.5)
            break
        time.sleep(0.5)
    if not cmd_dev:
        print("  Lost device!"); return
    fd = os.open(cmd_dev, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])

    close_all(fd)
    write_file(fd, 62, bytes([0x38, 0x00, 0x3F, 0x00]) + bytes(12))
    write_file(fd, 61, bytes([0x00, 0x00, 0x00, 0x00]))  # restore empty map

    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)

    time.sleep(3)
    notify_running = False
    time.sleep(0.5)

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 60)
    print(f"NOTIFICATION SUMMARY: {len(notifications)} total notifications received")
    print("=" * 60)
    for ts, data in notifications:
        hex_str = ' '.join(f'{b:02x}' for b in data[:32])
        print(f"  [{ts:.3f}] ({len(data)}B): {hex_str}")

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
