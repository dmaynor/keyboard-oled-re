#!/usr/bin/env python3
"""
LCD Full Flow — implements the COMPLETE Web Hub image update sequence.

Previous scripts were missing critical steps that the Web Hub performs:
  1. Screen resource map registration (file 61)
  2. Screen modes layout update (in profile's layout file)
  3. Cookie update (profile commit signal)

This script:
  Phase 1: Probe — read profiles list (file 15), active profile, screen
           resource map (file 61), and screen modes layout
  Phase 2: Full image write — register in resource map, write config+image,
           update layout, update cookie, selectWidget → file 62
"""

import os
import sys
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

FILE_15 = 15      # Profiles list
FILE_61 = 61      # Screen resource map
FILE_62 = 62      # Active display
FILE_28007 = 28007  # Default screen resource

PROP_MODE = 3
MODE_SELF = 1
MODE_HOST = 2

WIDTH = 248
HEIGHT = 170


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


def read_file(fd, file_id, buf=0):
    """Read a file from the device. Returns bytes or None."""
    fid = struct.pack('<H', file_id)
    close_all(fd)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        print(f"  OPEN file {file_id} FAIL: {hx(resp)}")
        return None

    # Describe to get size
    resp = sr(fd, [BRAGI, CMD_DESCRIBE, buf])
    if not ok(resp):
        print(f"  DESCRIBE file {file_id} FAIL: {hx(resp)}")
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return None

    # Parse size - try offset 4 (bytes 4-7)
    size = struct.unpack_from('<I', resp, 4)[0]
    if size > 1000000:
        # Try offset 3
        size = struct.unpack_from('<I', resp, 3)[0]
    if size > 1000000 or size == 0:
        # Try offset 5
        size = struct.unpack_from('<I', resp, 5)[0]

    print(f"  File {file_id} size: {size} bytes")
    if size == 0 or size > 500000:
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        return bytes()

    # Read chunks
    data = bytearray()
    while len(data) < size:
        resp = sr(fd, [BRAGI, CMD_READ, buf])
        if resp is None:
            break
        chunk = resp[3:]  # Skip header bytes
        needed = size - len(data)
        data.extend(chunk[:needed])

    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    return bytes(data)


def write_file(fd, file_id, data, buf=0):
    """Write data to a file on the device."""
    fid = struct.pack('<H', file_id)
    resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
    if not ok(resp):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        sr(fd, [BRAGI, CMD_CREATE] + list(fid))
        resp = sr(fd, [BRAGI, CMD_OPEN, buf] + list(fid))
        if not ok(resp):
            print(f"  WRITE file {file_id}: OPEN FAIL after CREATE")
            return False
    total = len(data)
    chunk = min(total, PKT_SIZE - 7)
    pkt = [BRAGI, CMD_WRITE_BEGIN, buf] + list(struct.pack('<I', total)) + list(data[:chunk])
    if not ok(sr(fd, pkt)):
        sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
        print(f"  WRITE file {file_id}: WRITE_BEGIN FAIL")
        return False
    offset = chunk
    while offset < total:
        chunk = min(total - offset, PKT_SIZE - 3)
        pkt = [BRAGI, CMD_WRITE_CONT, buf] + list(data[offset:offset + chunk])
        if not ok(sr(fd, pkt)):
            sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
            print(f"  WRITE file {file_id}: WRITE_CONT FAIL at {offset}")
            return False
        offset += chunk
    sr(fd, [BRAGI, CMD_UNBIND, 1, buf], timeout=0.3)
    return True


def delete_file(fd, file_id):
    fid = struct.pack('<H', file_id)
    sr(fd, [BRAGI, CMD_DELETE] + list(fid))


def set_prop(fd, prop_id, value):
    pkt = [BRAGI, CMD_SET] + list(struct.pack('<H', prop_id)) + list(value)
    return ok(sr(fd, pkt))


def get_prop(fd, prop_id):
    pkt = [BRAGI, CMD_GET] + list(struct.pack('<H', prop_id))
    return sr(fd, pkt)


def take_photo(name):
    path = f"pics/10-full-flow/{name}.jpg"
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


# ============ FORMAT PARSERS ============

def parse_profiles_list(data):
    """Parse profiles list file (file 15).
    Format: [header(2), count(2 LE), fileIds(2 each)]
    """
    if data is None or len(data) < 4:
        return None
    header = data[0:2]
    count = struct.unpack_from('<H', data, 2)[0]
    file_ids = []
    for i in range(count):
        offset = 4 + i * 2
        if offset + 2 <= len(data):
            fid = struct.unpack_from('<H', data, offset)[0]
            file_ids.append(fid)
    return {'header': header, 'count': count, 'file_ids': file_ids}


def parse_profile(data):
    """Parse a profile file.
    Format:
      [0:2]   profileId
      [2:6]   cookie (4 bytes LE)
      [6:8]   assignmentsFileIndex
      [8:10]  propertiesFileIndex
      [10:12] archiveFileIndex
      [12:14] lightingsFileIndex
      [14:16] keyMappingsFileIndex
      [16:18] coolersSettingsFileIndex
      [18:20] dialModesSettingsIndex
      [20:22] gamepadSensitivityCurves
      [22:24] firmwareSpecificDataFile
      [24:26] primaryActuationDistanceFile
      [26:28] secondaryActuationDistanceFile
      [28:30] lightingChannelsStrobingSettings
      [30:32] nameLength (2 bytes LE)
      [32:32+nameLen+1] name (null terminated)
      Then after name:
        [+0:+2] primaryActuationResetDistances
        [+2:+4] secondaryActuationResetDistances
        [+4:+6] rapidTriggerSettings
        [+6:+8] screenModesLayoutFile  ← THIS IS WHAT WE NEED
    """
    if data is None or len(data) < 34:
        return None

    profile_id = struct.unpack_from('<H', data, 0)[0]
    cookie = struct.unpack_from('<I', data, 2)[0]
    assignments = struct.unpack_from('<H', data, 6)[0]
    properties = struct.unpack_from('<H', data, 8)[0]
    archive = struct.unpack_from('<H', data, 10)[0]
    lightings = struct.unpack_from('<H', data, 12)[0]
    key_mappings = struct.unpack_from('<H', data, 14)[0]
    coolers = struct.unpack_from('<H', data, 16)[0]
    dial_modes = struct.unpack_from('<H', data, 18)[0]
    gamepad = struct.unpack_from('<H', data, 20)[0]
    firmware_data = struct.unpack_from('<H', data, 22)[0]
    primary_actuation = struct.unpack_from('<H', data, 24)[0]
    secondary_actuation = struct.unpack_from('<H', data, 26)[0]
    lighting_strobing = struct.unpack_from('<H', data, 28)[0]
    name_length = struct.unpack_from('<H', data, 30)[0]

    name_start = 32
    name_end = name_start + name_length + 1  # +1 for null terminator
    name = data[name_start:name_start + name_length]
    try:
        name_str = name.decode('utf-8', errors='replace')
    except:
        name_str = repr(name)

    # After name: 4 more 2-byte fields
    after_name = name_end
    if after_name + 8 <= len(data):
        primary_reset = struct.unpack_from('<H', data, after_name)[0]
        secondary_reset = struct.unpack_from('<H', data, after_name + 2)[0]
        rapid_trigger = struct.unpack_from('<H', data, after_name + 4)[0]
        screen_modes_layout = struct.unpack_from('<H', data, after_name + 6)[0]
    else:
        primary_reset = secondary_reset = rapid_trigger = screen_modes_layout = 0

    return {
        'profile_id': profile_id,
        'cookie': cookie,
        'assignments': assignments,
        'properties': properties,
        'archive': archive,
        'lightings': lightings,
        'key_mappings': key_mappings,
        'coolers': coolers,
        'dial_modes': dial_modes,
        'gamepad': gamepad,
        'firmware_data': firmware_data,
        'primary_actuation': primary_actuation,
        'secondary_actuation': secondary_actuation,
        'lighting_strobing': lighting_strobing,
        'name_length': name_length,
        'name': name_str,
        'primary_reset': primary_reset,
        'secondary_reset': secondary_reset,
        'rapid_trigger': rapid_trigger,
        'screen_modes_layout': screen_modes_layout,
        'raw': data,
    }


def parse_screen_resource_map(data):
    """Parse screen resource map (file 61).
    Format: [header(2), count(2 LE), items(8 each)]
    Item: [resourceId(2 LE), resourceAddress(2 LE), hash(4)]
    """
    if data is None or len(data) < 4:
        return None
    header = data[0:2]
    count = struct.unpack_from('<H', data, 2)[0]
    items = []
    for i in range(count):
        offset = 4 + i * 8
        if offset + 8 <= len(data):
            res_id = struct.unpack_from('<H', data, offset)[0]
            res_addr = struct.unpack_from('<H', data, offset + 2)[0]
            hash_val = data[offset + 4:offset + 8]
            if res_id != 0:
                items.append({
                    'resource_id': res_id,
                    'resource_address': res_addr,
                    'hash': hash_val,
                })
    return {'header': header, 'count': count, 'items': items}


def serialize_screen_resource_map(srm):
    """Serialize screen resource map back to bytes."""
    count = len(srm['items'])
    data = bytearray(4 + count * 8)
    data[0:2] = srm['header']
    struct.pack_into('<H', data, 2, count)
    for i, item in enumerate(srm['items']):
        offset = 4 + i * 8
        struct.pack_into('<H', data, offset, item['resource_id'])
        struct.pack_into('<H', data, offset + 2, item['resource_address'])
        data[offset + 4:offset + 8] = item['hash']
    return bytes(data)


def parse_screen_modes_layout(data):
    """Parse screen modes layout file.
    Format: [header(2), rowCount(1), then for each row: count(1), fileIds(2 each)]
    """
    if data is None or len(data) < 3:
        return None
    header = data[0:2]
    row_count = data[2]
    rows = []
    offset = 3
    for _ in range(row_count):
        if offset >= len(data):
            break
        count = data[offset]
        offset += 1
        file_ids = []
        for _ in range(count):
            if offset + 2 <= len(data):
                fid = struct.unpack_from('<H', data, offset)[0]
                file_ids.append(fid)
                offset += 2
        rows.append({'count': count, 'file_ids': file_ids})
    return {'header': header, 'row_count': row_count, 'rows': rows}


def serialize_screen_modes_layout(layout):
    """Serialize screen modes layout back to bytes."""
    data = bytearray()
    data.extend(layout['header'])
    total_count = len(layout['rows'])
    data.append(total_count)
    for row in layout['rows']:
        count = len(row['file_ids'])
        data.append(count)
        for fid in row['file_ids']:
            data.extend(struct.pack('<H', fid))
    return bytes(data)


def update_cookie_in_profile(raw_profile_data, new_cookie):
    """Update the cookie field (bytes 2-5) in raw profile data."""
    data = bytearray(raw_profile_data)
    struct.pack_into('<I', data, 2, new_cookie)
    return bytes(data)


def create_corsair_bmp(r, g, b):
    """Create Corsair BMP format: [0x48,0x00] + BMP header + GRB pixels + timestamp."""
    row_size = (WIDTH * 3 + 3) & ~3
    pixel_data_size = row_size * HEIGHT
    bmp = bytearray()
    bmp += bytes([0x48, 0x00])  # Corsair prefix
    bmp += b'BM'
    bmp += struct.pack('<I', 54 + pixel_data_size)  # File size
    bmp += struct.pack('<HH', 0, 0)
    bmp += struct.pack('<I', 54)  # Pixel data offset (from BM, not from prefix)
    bmp += struct.pack('<I', 40)  # DIB header size
    bmp += struct.pack('<i', WIDTH)
    bmp += struct.pack('<i', HEIGHT)
    bmp += struct.pack('<HH', 1, 24)  # Planes, BPP
    bmp += struct.pack('<I', 0)  # Compression
    bmp += struct.pack('<I', pixel_data_size)
    bmp += struct.pack('<ii', 2835, 2835)  # DPI
    bmp += struct.pack('<II', 0, 0)  # Colors
    padding = row_size - WIDTH * 3
    # GRB order per Corsair convention
    row = bytes([g, r, b]) * WIDTH + bytes(padding)
    for _ in range(HEIGHT):
        bmp += row
    bmp += struct.pack('<I', int(time.time()) & 0xFFFFFFFF)  # Timestamp
    return bytes(bmp)


def main():
    hidraw = find_hidraw()
    if not hidraw:
        print("ERROR: Keyboard not found")
        return
    print(f"Device: {hidraw}")

    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)

    # Start session
    token = bytes(random.randint(0, 255) for _ in range(4))
    resp = sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])
    print(f"Session: {hx(resp)}")

    # ============ PHASE 1: PROBE ============
    print("\n" + "=" * 60)
    print("PHASE 1: PROBE — Reading device state")
    print("=" * 60)

    # Read profiles list (file 15)
    print("\n--- File 15 (Profiles List) ---")
    data_15 = read_file(fd, FILE_15)
    if data_15:
        print(f"  Raw: {hx(data_15)}")
        profiles_list = parse_profiles_list(data_15)
        if profiles_list:
            print(f"  Header: {hx(profiles_list['header'])}")
            print(f"  Count: {profiles_list['count']}")
            print(f"  Profile IDs: {profiles_list['file_ids']}")

    # Read screen resource map (file 61)
    print("\n--- File 61 (Screen Resource Map) ---")
    data_61 = read_file(fd, FILE_61)
    if data_61:
        print(f"  Raw ({len(data_61)} bytes): {hx(data_61)}")
        srm = parse_screen_resource_map(data_61)
        if srm:
            print(f"  Header: {hx(srm['header'])}")
            print(f"  Count: {srm['count']}")
            for item in srm['items']:
                print(f"    Resource {item['resource_id']} -> addr {item['resource_address']}, hash {hx(item['hash'])}")

    # Read file 62 (active display)
    print("\n--- File 62 (Active Display) ---")
    data_62 = read_file(fd, FILE_62)
    if data_62:
        print(f"  Raw ({len(data_62)} bytes): {hx(data_62, 64)}")

    # Read file 28007 (default screen resource)
    print("\n--- File 28007 (Default Screen Resource) ---")
    data_28007 = read_file(fd, FILE_28007)
    if data_28007:
        print(f"  Raw: {hx(data_28007)}")

    # Read profile files
    active_profile = None
    if profiles_list and profiles_list['file_ids']:
        for pid in profiles_list['file_ids']:
            print(f"\n--- Profile File {pid} ---")
            pdata = read_file(fd, pid)
            if pdata:
                print(f"  Raw ({len(pdata)} bytes): {hx(pdata, 48)}")
                profile = parse_profile(pdata)
                if profile:
                    print(f"  Profile ID: {profile['profile_id']}")
                    print(f"  Cookie: {profile['cookie']} (0x{profile['cookie']:08X})")
                    print(f"  Name: '{profile['name']}' (len={profile['name_length']})")
                    print(f"  Assignments: {profile['assignments']}")
                    print(f"  Properties: {profile['properties']}")
                    print(f"  Archive: {profile['archive']}")
                    print(f"  Lightings: {profile['lightings']}")
                    print(f"  Key Mappings: {profile['key_mappings']}")
                    print(f"  Coolers: {profile['coolers']}")
                    print(f"  Dial Modes: {profile['dial_modes']}")
                    print(f"  Gamepad: {profile['gamepad']}")
                    print(f"  Firmware Data: {profile['firmware_data']}")
                    print(f"  Primary Actuation: {profile['primary_actuation']}")
                    print(f"  Secondary Actuation: {profile['secondary_actuation']}")
                    print(f"  Lighting Strobing: {profile['lighting_strobing']}")
                    print(f"  Primary Reset: {profile['primary_reset']}")
                    print(f"  Secondary Reset: {profile['secondary_reset']}")
                    print(f"  Rapid Trigger: {profile['rapid_trigger']}")
                    print(f"  ** Screen Modes Layout File: {profile['screen_modes_layout']} **")
                    active_profile = profile

                    # Read the screen modes layout file
                    if profile['screen_modes_layout'] > 0:
                        print(f"\n--- Screen Modes Layout (file {profile['screen_modes_layout']}) ---")
                        layout_data = read_file(fd, profile['screen_modes_layout'])
                        if layout_data:
                            print(f"  Raw: {hx(layout_data)}")
                            layout = parse_screen_modes_layout(layout_data)
                            if layout:
                                print(f"  Header: {hx(layout['header'])}")
                                print(f"  Row count: {layout['row_count']}")
                                for i, row in enumerate(layout['rows']):
                                    print(f"  Row {i}: count={row['count']}, fileIds={row['file_ids']}")

    # ============ PHASE 2: FULL IMAGE WRITE ============
    print("\n" + "=" * 60)
    print("PHASE 2: FULL IMAGE WRITE")
    print("=" * 60)

    if not active_profile:
        print("ERROR: No active profile found. Cannot proceed.")
        os.close(fd)
        return

    # Choose file IDs in the 28000-32767 range
    IMAGE_FILE_ID = 28200   # u = image data
    CONFIG_FILE_ID = 28201  # d = config (points to image)

    print(f"\n  Image file ID: {IMAGE_FILE_ID}")
    print(f"  Config file ID: {CONFIG_FILE_ID}")

    # Step 1: Switch to HOST_CONTROLLED
    print("\n--- Step 1: Switch to HOST_CONTROLLED ---")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_HOST))
    os.close(fd)
    hidraw = reconnect()
    if not hidraw:
        print("  Lost device after mode switch!")
        return
    fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
    close_all(fd)
    token = bytes(random.randint(0, 255) for _ in range(4))
    sr(fd, [BRAGI, CMD_SESSION, 0x01] + list(token) + [0x00])
    print("  HOST_CONTROLLED mode active")
    take_photo("step1_host_mode")

    # Step 2: Update screen resource map (file 61) — add IMAGE_FILE_ID
    print("\n--- Step 2: Update screen resource map (file 61) ---")
    data_61 = read_file(fd, FILE_61)
    if data_61:
        srm = parse_screen_resource_map(data_61)
        if srm:
            # Remove any previous entries with our IDs
            srm['items'] = [i for i in srm['items']
                            if i['resource_id'] not in (IMAGE_FILE_ID, CONFIG_FILE_ID)]
            # Add our image file as a resource
            srm['items'].append({
                'resource_id': IMAGE_FILE_ID,
                'resource_address': IMAGE_FILE_ID,
                'hash': bytes([0, 0, 0, 0]),
            })
            srm['count'] = len(srm['items'])
            new_srm = serialize_screen_resource_map(srm)
            print(f"  New resource map ({len(new_srm)} bytes): {hx(new_srm)}")
            close_all(fd)
            result = write_file(fd, FILE_61, new_srm)
            print(f"  Write resource map: {'OK' if result else 'FAIL'}")
    else:
        print("  WARNING: Could not read file 61, creating new resource map")
        # Create a minimal resource map with header [0,0]
        srm = {
            'header': bytes([0, 0]),
            'count': 1,
            'items': [{
                'resource_id': IMAGE_FILE_ID,
                'resource_address': IMAGE_FILE_ID,
                'hash': bytes([0, 0, 0, 0]),
            }]
        }
        new_srm = serialize_screen_resource_map(srm)
        close_all(fd)
        write_file(fd, FILE_61, new_srm)

    # Step 3: Write config file (CONFIG_FILE_ID)
    #   Config: [56, 0, imageFileId_lo, imageFileId_hi] + 12 zeros = 16 bytes
    print("\n--- Step 3: Write config file ---")
    config = bytearray(16)
    config[0] = 56  # Static image type
    config[1] = 0
    struct.pack_into('<H', config, 2, IMAGE_FILE_ID)
    config = bytes(config)
    print(f"  Config ({len(config)} bytes): {hx(config)}")
    close_all(fd)
    delete_file(fd, CONFIG_FILE_ID)
    result = write_file(fd, CONFIG_FILE_ID, config)
    print(f"  Write config to file {CONFIG_FILE_ID}: {'OK' if result else 'FAIL'}")

    # Step 4: Update screen modes layout — add CONFIG_FILE_ID
    print("\n--- Step 4: Update screen modes layout ---")
    if active_profile['screen_modes_layout'] > 0:
        layout_file_id = active_profile['screen_modes_layout']
        layout_data = read_file(fd, layout_file_id)
        if layout_data:
            layout = parse_screen_modes_layout(layout_data)
            if layout and layout['rows']:
                # Remove any existing entries with our config ID
                layout['rows'][0]['file_ids'] = [
                    fid for fid in layout['rows'][0]['file_ids']
                    if fid != CONFIG_FILE_ID
                ]
                # Add our config file ID
                layout['rows'][0]['file_ids'].append(CONFIG_FILE_ID)
                layout['rows'][0]['count'] = len(layout['rows'][0]['file_ids'])
                new_layout = serialize_screen_modes_layout(layout)
                print(f"  New layout ({len(new_layout)} bytes): {hx(new_layout)}")
                close_all(fd)
                result = write_file(fd, layout_file_id, new_layout)
                print(f"  Write layout to file {layout_file_id}: {'OK' if result else 'FAIL'}")
            else:
                print("  WARNING: Layout has no rows, creating new layout")
                layout = {
                    'header': bytes([0, 0]),
                    'row_count': 1,
                    'rows': [{'count': 1, 'file_ids': [CONFIG_FILE_ID]}]
                }
                new_layout = serialize_screen_modes_layout(layout)
                close_all(fd)
                write_file(fd, layout_file_id, new_layout)
        else:
            print(f"  WARNING: Could not read layout file {layout_file_id}")
    else:
        print("  WARNING: No screen modes layout file in profile")

    # Step 5: Write image data (Corsair BMP, solid RED)
    print("\n--- Step 5: Write image data ---")
    image_data = create_corsair_bmp(255, 0, 0)  # Solid RED
    print(f"  Image size: {len(image_data)} bytes")
    print(f"  First 16 bytes: {hx(image_data, 16)}")
    close_all(fd)
    delete_file(fd, IMAGE_FILE_ID)
    result = write_file(fd, IMAGE_FILE_ID, image_data)
    print(f"  Write image to file {IMAGE_FILE_ID}: {'OK' if result else 'FAIL'}")

    # Step 6: selectWidget — write config to file 62
    print("\n--- Step 6: selectWidget — write config to file 62 ---")
    close_all(fd)
    result = write_file(fd, FILE_62, config)
    print(f"  Write config to file 62: {'OK' if result else 'FAIL'}")

    # Step 7: Update cookie in profile
    print("\n--- Step 7: Update cookie in profile ---")
    new_cookie = int(time.time()) & 0xFFFFFFFF
    profile_file_id = active_profile['profile_id']
    print(f"  Old cookie: {active_profile['cookie']} (0x{active_profile['cookie']:08X})")
    print(f"  New cookie: {new_cookie} (0x{new_cookie:08X})")

    # Re-read profile to get current state
    profile_data = read_file(fd, profile_file_id)
    if profile_data:
        updated_profile = update_cookie_in_profile(profile_data, new_cookie)
        close_all(fd)
        result = write_file(fd, profile_file_id, updated_profile)
        print(f"  Write updated profile: {'OK' if result else 'FAIL'}")
    else:
        print("  WARNING: Could not re-read profile")

    take_photo("step7_after_all_writes")

    # Step 8: Switch back to SELF_OPERATED
    print("\n--- Step 8: Switch to SELF_OPERATED ---")
    set_prop(fd, PROP_MODE, struct.pack('<I', MODE_SELF))
    os.close(fd)
    hidraw = reconnect()
    if hidraw:
        fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        take_photo("step8_self_operated")

        # Verify — read file 62 back
        print("\n--- Verification: Read file 62 ---")
        close_all(fd)
        verify_62 = read_file(fd, FILE_62)
        if verify_62:
            print(f"  File 62 content: {hx(verify_62, 32)}")

        # Verify — read resource map
        print("\n--- Verification: Read file 61 ---")
        verify_61 = read_file(fd, FILE_61)
        if verify_61:
            print(f"  Resource map: {hx(verify_61, 32)}")

        os.close(fd)

    print("\n" + "=" * 60)
    print("DONE — Check photos for results!")
    print("=" * 60)


if __name__ == "__main__":
    main()
