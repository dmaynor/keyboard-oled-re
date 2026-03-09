#!/usr/bin/env python3
"""
Corsair Vanguard 96 Bragi Protocol Probe with usbmon capture.

Probes the LCD resource trying various init/commit sequences
to discover what's needed to update the display.

Protocol (from ckb-next bragi_common.c):
  BRAGI_MAGIC = 0x08
  Commands: GET=0x02, SET=0x01, OPEN_HANDLE=0x0D, CLOSE_HANDLE=0x05,
            WRITE_DATA=0x06, READ_DATA=0x08, PROBE_HANDLE=0x09

  OPEN_HANDLE: [08 0d handle resource_lo resource_hi 00]
  WRITE_DATA:  [08 06 handle len[0:3] 00 data...]  (first pkt: 7-byte hdr)
  READ_DATA:   [08 08 handle 00]
  PROBE_HANDLE:[08 09 handle 00]  -> size at response[5:9]

Response format (this device):
  [00 cmd_echo status data...]
  Note: byte[0] is 0x00, not 0x08 (magic). This differs from ckb-next's
  expectation. The status byte at [2] uses 0x00=OK, 0x05=unsupported,
  0x06=resource not found.

Endpoints: Interface 2, hidraw3
  OUT = 0x03, IN = 0x83 (1024 byte interrupt transfers)
"""

import os
import sys
import time
import struct

HIDRAW = "/dev/hidraw3"
BRAGI_MAGIC = 0x08
OUT_PKT_SIZE = 64   # bytes we send per USB interrupt transfer

# Bragi commands (from ckb-next bragi_common.c)
BRAGI_SET = 0x01
BRAGI_GET = 0x02
BRAGI_CLOSE_HANDLE = 0x05
BRAGI_WRITE_DATA = 0x06
BRAGI_READ_DATA = 0x08
BRAGI_PROBE_HANDLE = 0x09
BRAGI_OPEN_HANDLE = 0x0D

# Handle IDs (host-chosen, ckb-next convention)
HANDLE_LIGHTING = 0x00
HANDLE_GENERIC = 0x01
HANDLE_2ND = 0x02

# Known properties
BRAGI_MODE = 0x01

# Resources
RES_LIGHTING = 0x01
RES_LIGHTING_MONO = 0x10
RES_ALT_LIGHTING = 0x22
RES_LIGHTING_EXTRA = 0x2E
RES_PAIRINGID = 0x05
RES_ENCRYPTIONKEY = 0x06
RES_LCD = 0x3F  # Discovered in earlier probing


def open_device():
    fd = os.open(HIDRAW, os.O_RDWR | os.O_NONBLOCK)
    return fd


def send_raw(fd, data):
    """Send a raw packet (padded to OUT_PKT_SIZE with HID report ID prefix)."""
    pad_len = max(0, OUT_PKT_SIZE - len(data))
    pkt = bytes([0x00]) + bytes(data) + bytes(pad_len)
    os.write(fd, pkt)


def recv(fd, timeout=0.5):
    """Read a response."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            return os.read(fd, 1024)
        except BlockingIOError:
            time.sleep(0.01)
    return None


def send_recv(fd, data, timeout=0.5):
    """Send a packet and read the response."""
    send_raw(fd, data)
    return recv(fd, timeout)


def hex_dump(data, prefix="", max_bytes=64):
    if data is None:
        print(f"{prefix}(no response)")
        return
    n = min(len(data), max_bytes)
    hex_str = " ".join(f"{b:02x}" for b in data[:n])
    if len(data) > n:
        hex_str += f" ... ({len(data)} total)"
    print(f"{prefix}[{len(data)}] {hex_str}")


def resp_status(resp):
    """Extract status byte from response. Returns (status, ok)."""
    if resp is None or len(resp) < 3:
        return (0xFF, False)
    return (resp[2], resp[2] == 0x00)


def bragi_get(fd, prop):
    """GET a property value. Returns (value, resp) or (None, resp)."""
    pkt = [BRAGI_MAGIC, BRAGI_GET, prop, 0x00]
    resp = send_recv(fd, pkt)
    status, ok = resp_status(resp)
    if ok and resp and len(resp) > 5:
        # Value is 24-bit LE at bytes 3-5 (per ckb-next)
        val = resp[3] | (resp[4] << 8) | (resp[5] << 16)
        return (val, resp)
    return (None, resp)


def bragi_set(fd, prop, *values):
    """SET a property. Values are individual bytes."""
    pkt = [BRAGI_MAGIC, BRAGI_SET, prop, 0x00] + list(values)
    resp = send_recv(fd, pkt)
    return resp


def bragi_open_handle(fd, handle_id, resource):
    """Open a handle for a resource.

    handle_id: host-chosen handle (0x00=lighting, 0x01=generic, 0x02=secondary)
    resource: resource ID to open
    Returns: (status, resp)
    """
    # Format: 08 0d handle resource_lo resource_hi 00
    res_lo = resource & 0xFF
    res_hi = (resource >> 8) & 0xFF
    pkt = [BRAGI_MAGIC, BRAGI_OPEN_HANDLE, handle_id, res_lo, res_hi, 0x00]
    resp = send_recv(fd, pkt)
    status, ok = resp_status(resp)
    return (status, resp)


def bragi_close_handle(fd, handle_id):
    """Close a handle."""
    pkt = [BRAGI_MAGIC, BRAGI_CLOSE_HANDLE, handle_id, 0x00]
    resp = send_recv(fd, pkt)
    return resp


def bragi_probe_handle(fd, handle_id):
    """Probe a handle to get resource size. Returns size or 0 on error."""
    pkt = [BRAGI_MAGIC, BRAGI_PROBE_HANDLE, handle_id, 0x00]
    resp = send_recv(fd, pkt)
    status, ok = resp_status(resp)
    if ok and resp and len(resp) >= 9:
        size = struct.unpack_from('<I', resp, 5)[0]
        return size
    elif resp and len(resp) >= 9:
        # Try reading size anyway even if status non-zero
        size = struct.unpack_from('<I', resp, 5)[0]
        print(f"  PROBE status=0x{status:02X}, size={size}")
    return 0


def bragi_write_data(fd, handle_id, data_bytes):
    """Write data to an open handle using chunked transfers.

    First packet: [08 06 handle len[0:3] 00 data...]  (7-byte header)
    Continue packets: [08 06 handle data...]  (3-byte header)
    """
    data_len = len(data_bytes)

    # First packet: 7-byte header
    first_pkt = [BRAGI_MAGIC, BRAGI_WRITE_DATA, handle_id]
    first_pkt += list(struct.pack('<I', data_len))
    first_data_size = min(data_len, OUT_PKT_SIZE - 7)
    first_pkt += list(data_bytes[:first_data_size])

    resp = send_recv(fd, first_pkt)
    status, ok = resp_status(resp)
    if not ok:
        return (status, resp)

    # Continue packets if needed
    offset = first_data_size
    while offset < data_len:
        chunk_size = min(data_len - offset, OUT_PKT_SIZE - 3)
        cont_pkt = [BRAGI_MAGIC, BRAGI_WRITE_DATA, handle_id]
        cont_pkt += list(data_bytes[offset:offset + chunk_size])
        resp = send_recv(fd, cont_pkt)
        status, ok = resp_status(resp)
        if not ok:
            return (status, resp)
        offset += chunk_size

    return (0x00, resp)


def bragi_read_data(fd, handle_id, length):
    """Read data from an open handle. Returns bytes or None."""
    pkt = [BRAGI_MAGIC, BRAGI_READ_DATA, handle_id, 0x00]
    resp = send_recv(fd, pkt)
    status, ok = resp_status(resp)
    if ok and resp and len(resp) > 3:
        # Data starts at byte 3
        return resp[3:3+length]
    return None


# ============================================================
# Probe routines
# ============================================================

def probe_properties(fd):
    """Read known and unknown properties."""
    print("\n=== Probing Properties ===")
    props = {
        0x01: "MODE",
        0x02: "PROP_02",
        0x03: "PROP_03",
        0x04: "PROP_04",
        0x05: "PROP_05",
        0x06: "POLLRATE",
        0x07: "PROP_07",
        0x08: "PROP_08",
        0x09: "PROP_09",
        0x0A: "PROP_0A",
        0x0F: "HWLAYOUT",
        0x15: "FWVERSION",
        0x16: "BRIGHTNESS",
        0x24: "PAIRING_ID",
    }
    for prop_id in sorted(props.keys()):
        name = props[prop_id]
        val, resp = bragi_get(fd, prop_id)
        status, ok = resp_status(resp)
        if ok:
            print(f"  GET 0x{prop_id:02X} ({name:12s}): value={val} (0x{val:06X})")
        else:
            print(f"  GET 0x{prop_id:02X} ({name:12s}): error 0x{status:02X}")


def scan_all_properties(fd):
    """Scan property IDs 0x00-0x60."""
    print("\n=== Scanning All Properties 0x00-0x60 ===")
    found = 0
    for prop_id in range(0x61):
        val, resp = bragi_get(fd, prop_id)
        status, ok = resp_status(resp)
        if ok:
            print(f"  0x{prop_id:02X}: value={val} (0x{val:06X})")
            found += 1
    print(f"  Found {found} valid properties")


def scan_resources(fd):
    """Try opening various resources to see what exists."""
    print("\n=== Scanning Resources 0x00-0x60 ===")
    found = []
    for res in range(0x61):
        # Use HANDLE_GENERIC (0x01) for probing
        status, resp = bragi_open_handle(fd, HANDLE_GENERIC, res)
        if status == 0x00:
            # Successfully opened! Probe size.
            size = bragi_probe_handle(fd, HANDLE_GENERIC)
            print(f"  Resource 0x{res:02X}: OPEN OK, size={size} bytes")
            found.append((res, size))
            bragi_close_handle(fd, HANDLE_GENERIC)
        elif status == 0x03:
            # Handle already open, close and retry
            bragi_close_handle(fd, HANDLE_GENERIC)
            status, resp = bragi_open_handle(fd, HANDLE_GENERIC, res)
            if status == 0x00:
                size = bragi_probe_handle(fd, HANDLE_GENERIC)
                print(f"  Resource 0x{res:02X}: OPEN OK (retry), size={size} bytes")
                found.append((res, size))
                bragi_close_handle(fd, HANDLE_GENERIC)
        time.sleep(0.02)

    print(f"\n  Found {len(found)} resources:")
    for res, size in found:
        print(f"    0x{res:02X}: {size} bytes")
    return found


def probe_lcd(fd):
    """Full LCD resource probing with correct packet format."""
    print("\n=== LCD Resource Probe ===")

    # Step 1: Open handle for LCD resource
    print("\n--- Step 1: Open LCD handle ---")
    # Close first in case handle is stale
    bragi_close_handle(fd, HANDLE_GENERIC)
    time.sleep(0.1)

    status, resp = bragi_open_handle(fd, HANDLE_GENERIC, RES_LCD)
    hex_dump(resp, "  OPEN response: ")
    if status != 0x00:
        print(f"  OPEN failed with status 0x{status:02X}")
        # Try other resource IDs near 0x3F
        for alt_res in [0x3E, 0x40, 0x41, 0x30, 0x20]:
            status, resp = bragi_open_handle(fd, HANDLE_GENERIC, alt_res)
            if status == 0x00:
                print(f"  Alternative resource 0x{alt_res:02X} opened!")
                bragi_close_handle(fd, HANDLE_GENERIC)
        return

    # Step 2: Probe size
    print("\n--- Step 2: Probe handle size ---")
    size = bragi_probe_handle(fd, HANDLE_GENERIC)
    print(f"  LCD resource size: {size} bytes")
    if size > 0:
        # Analyze possible framebuffer dimensions
        print(f"  Possible dimensions:")
        for bpp_name, bpp in [("1bpp", 1), ("8bpp", 8), ("RGB565", 16), ("RGB888", 24)]:
            pixels = (size * 8) // bpp
            # Try common aspect ratios
            for w in [480, 320, 240, 128, 160, 256, 400]:
                h = pixels // w
                if w * h * bpp == size * 8:
                    print(f"    {bpp_name}: {w}x{h}")

    # Step 3: Read current LCD data
    print("\n--- Step 3: Read current LCD data ---")
    data = bragi_read_data(fd, HANDLE_GENERIC, min(size, 64) if size > 0 else 64)
    if data:
        hex_dump(data, "  Current data: ", 64)

    # Step 4: Write test pattern
    print("\n--- Step 4: Write test pattern (16 bytes) ---")
    test = bytes([0xFF] * 16)  # All white
    write_status, resp = bragi_write_data(fd, HANDLE_GENERIC, test)
    hex_dump(resp, "  WRITE response: ")
    print(f"  Write status: 0x{write_status:02X} ({'OK' if write_status == 0 else 'FAIL'})")

    # Step 5: Try PROBE_HANDLE as potential commit
    print("\n--- Step 5: Probe after write (commit?) ---")
    resp = send_recv(fd, [BRAGI_MAGIC, BRAGI_PROBE_HANDLE, HANDLE_GENERIC, 0x00])
    hex_dump(resp, "  PROBE response: ")

    # Step 6: Close and reopen (commit-on-close?)
    print("\n--- Step 6: Close handle (commit-on-close?) ---")
    resp = bragi_close_handle(fd, HANDLE_GENERIC)
    hex_dump(resp, "  CLOSE response: ")
    time.sleep(0.5)

    # Step 7: Try unknown commands that might be "commit/flush"
    print("\n--- Step 7: Scan for commit/flush commands ---")
    # Reopen first
    status, resp = bragi_open_handle(fd, HANDLE_GENERIC, RES_LCD)
    if status != 0x00:
        print(f"  Reopen failed: 0x{status:02X}")
        return

    # Write small pattern
    bragi_write_data(fd, HANDLE_GENERIC, bytes([0xAA] * 16))

    # Try various command IDs that might trigger display update
    for cmd in range(0x03, 0x20):
        if cmd in [BRAGI_CLOSE_HANDLE, BRAGI_WRITE_DATA, BRAGI_READ_DATA,
                   BRAGI_PROBE_HANDLE, BRAGI_OPEN_HANDLE, BRAGI_GET, BRAGI_SET]:
            continue  # Skip known commands
        pkt = [BRAGI_MAGIC, cmd, HANDLE_GENERIC, 0x00]
        resp = send_recv(fd, pkt, timeout=0.3)
        status, ok = resp_status(resp)
        status_str = "OK" if ok else f"0x{status:02X}"
        if resp:
            hex_dump(resp, f"  CMD 0x{cmd:02X} ({status_str}): ")

    # Step 8: Try SET on properties that might control LCD
    print("\n--- Step 8: Try SET on properties ---")
    # Get current mode
    val, _ = bragi_get(fd, BRAGI_MODE)
    print(f"  Current MODE: {val}")

    # Try setting mode to various values
    for mode_val in [0x00, 0x01, 0x02, 0x03, 0x04, 0x05]:
        resp = bragi_set(fd, BRAGI_MODE, mode_val)
        s, ok = resp_status(resp)
        print(f"  SET MODE={mode_val}: {'OK' if ok else f'err 0x{s:02X}'}")
        if ok:
            time.sleep(0.3)

    # Restore original mode
    if val is not None:
        bragi_set(fd, BRAGI_MODE, val & 0xFF)

    # Cleanup
    print("\n--- Cleanup ---")
    bragi_close_handle(fd, HANDLE_GENERIC)
    print("  Handle closed.")


def main():
    print("Corsair Vanguard 96 Bragi Protocol Probe v2")
    print(f"Device: {HIDRAW}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    fd = open_device()

    try:
        cmd = sys.argv[1] if len(sys.argv) > 1 else "default"

        if cmd == "props":
            probe_properties(fd)
        elif cmd == "allprops":
            scan_all_properties(fd)
        elif cmd == "resources":
            scan_resources(fd)
        elif cmd == "lcd":
            probe_lcd(fd)
        elif cmd == "all":
            probe_properties(fd)
            scan_all_properties(fd)
            scan_resources(fd)
            probe_lcd(fd)
        elif cmd == "default":
            probe_properties(fd)
            probe_lcd(fd)
        else:
            print(f"Unknown: {cmd}")
            print("Usage: bragi_probe.py [props|allprops|resources|lcd|all]")
    finally:
        os.close(fd)
        print("\nDone.")


if __name__ == "__main__":
    main()
