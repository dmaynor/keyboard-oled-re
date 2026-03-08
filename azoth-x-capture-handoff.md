# ROG Azoth X OLED Protocol Reverse Engineering — Windows Capture Handoff

## PROJECT GOAL

We are reverse engineering the USB/HID protocol that ASUS Armoury Crate uses to control the OLED display on an ASUS ROG Azoth X keyboard. The end goal is to build a Linux tool that can send custom images and control the OLED screen without Armoury Crate.

Analysis will happen on a separate Linux machine (System76 Thelio). Your job on this Windows Razer Blade is to:
1. Install capture tools
2. Install Armoury Crate (if not already installed)
3. Capture USB/HID traffic between Armoury Crate and the keyboard using a Python HID logger (primary) and Wireshark (supplementary)
4. Export the logs and captures for transfer to the Linux machine

---

## DEVICE DETAILS

- **Keyboard:** ASUS ROG Azoth X
- **Connection:** Via ROG OMNI RECEIVER (USB 2.4GHz dongle)
- **USB Dongle VID:PID:** `0B05:1ACE`
- **Dongle Name:** "ASUSTeK ROG OMNI RECEIVER"
- **Dongle Serial:** `T7MPKRD002BM`
- **Bluetooth VID:PID:** `0B05:1C27` (when connected via BT instead)

### HID Interfaces (observed on Linux)

The ROG OMNI RECEIVER exposes **4 HID interfaces**:

| Interface | Linux hidraw | Usage Page        | Likely Purpose                  |
|-----------|-------------|-------------------|---------------------------------|
| input0    | hidraw2     | 0x0501 (Generic Desktop, Keyboard) | Standard keyboard HID reports |
| input1    | hidraw3     | 0x0501 (Generic Desktop, Mouse)    | Integrated mouse/pointer      |
| input2    | hidraw9     | 0xFF02, 0xFF00, 0xFF01 (Vendor)    | **VENDOR COMMAND INTERFACE — THIS IS THE TARGET** |
| input3    | hidraw10    | 0x050C (Consumer)                  | Media keys / consumer controls |

**The vendor interface (input2) is the one Armoury Crate uses to send OLED data and configuration commands.** It has three vendor-defined HID collections with report IDs 0x01, 0x02, and 0x03, each with 63-byte (0x3F) input and output reports.

---

## STEP 1: INSTALL TOOLS

Run these in an elevated PowerShell:

```powershell
# Python HID library (PRIMARY capture tool)
pip install pywinusb

# Wireshark + USBPcap (SUPPLEMENTARY — for low-level USB details if needed)
# MAKE SURE TO CHECK THE USBPCAP BOX during Wireshark install
winget install WiresharkFoundation.Wireshark
winget install Desowin.USBPcap
```

**IMPORTANT:** Reboot after installing USBPcap. The USB capture driver requires a restart to load.

Also install Armoury Crate from https://rog.asus.com/armoury-crate/ if not already present. Make sure the Azoth X is detected in Armoury Crate before starting captures.

---

## STEP 2: SET UP PYTHON HID LOGGER (PRIMARY CAPTURE METHOD)

The Python HID logger captures clean, targeted data directly from the vendor HID interface — no filtering needed, no USB noise. It logs both directions (host→device and device→host) with timestamps.

### 2a. Enumerate HID Interfaces

First, find all the HID interfaces the keyboard exposes:

```python
# enumerate_hid.py
import pywinusb.hid as hid

devices = hid.HidDeviceFilter(vendor_id=0x0B05, product_id=0x1ACE).get_devices()
for i, d in enumerate(devices):
    print(f"[{i}] {d.product_name}")
    print(f"    Path: {d.device_path}")
    print(f"    Usage Page: 0x{d.hid_caps.usage_page:04X}")
    print(f"    Usage:      0x{d.hid_caps.usage:04X}")
    print()
```

Run this and identify the **vendor interface** — it will have Usage Page `0xFF02`, `0xFF00`, or `0xFF01`. There may be multiple vendor collections; note all of them.

### 2b. Capture Script

Save this as `capture_hid.py`. It intercepts HID reports on the vendor interface(s) and logs everything to a timestamped file:

```python
# capture_hid.py
import pywinusb.hid as hid
import time
import sys
import os
from datetime import datetime

VENDOR_ID = 0x0B05
PRODUCT_ID = 0x1ACE
# Vendor usage pages — the interfaces we care about
VENDOR_USAGE_PAGES = [0xFF00, 0xFF01, 0xFF02]

def format_bytes(data):
    return ' '.join(f'{b:02x}' for b in data)

class HIDLogger:
    def __init__(self, label, log_file):
        self.label = label
        self.log_file = log_file
        self.start_time = time.time()

    def handler(self, data):
        elapsed = time.time() - self.start_time
        line = f"[{elapsed:10.4f}] IN  {self.label}: {format_bytes(data)}"
        print(line)
        self.log_file.write(line + "\n")
        self.log_file.flush()

def main():
    capture_name = sys.argv[1] if len(sys.argv) > 1 else "capture"
    os.makedirs("azoth-x-captures", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"azoth-x-captures/{capture_name}_{timestamp}.log"

    print(f"Logging to: {log_path}")
    print(f"Looking for ASUS devices (VID={VENDOR_ID:04X}, PID={PRODUCT_ID:04X})...")

    devices = hid.HidDeviceFilter(
        vendor_id=VENDOR_ID, product_id=PRODUCT_ID
    ).get_devices()

    if not devices:
        print("ERROR: No devices found! Is the ROG OMNI RECEIVER plugged in?")
        return

    vendor_devices = []
    for d in devices:
        up = d.hid_caps.usage_page
        if up in VENDOR_USAGE_PAGES:
            vendor_devices.append(d)
            print(f"  Found vendor interface: Usage Page 0x{up:04X}, Usage 0x{d.hid_caps.usage:04X}")

    if not vendor_devices:
        print("WARNING: No vendor interfaces found. Listing all interfaces:")
        for d in devices:
            print(f"  Usage Page: 0x{d.hid_caps.usage_page:04X}, Usage: 0x{d.hid_caps.usage:04X}")
        print("Opening ALL interfaces instead...")
        vendor_devices = devices

    log_file = open(log_path, "w")
    log_file.write(f"# ROG Azoth X HID Capture: {capture_name}\n")
    log_file.write(f"# Started: {datetime.now().isoformat()}\n")
    log_file.write(f"# Interfaces: {len(vendor_devices)}\n\n")

    loggers = []
    for d in vendor_devices:
        label = f"UP=0x{d.hid_caps.usage_page:04X}"
        d.open()
        logger = HIDLogger(label, log_file)
        d.set_raw_data_handler(logger.handler)
        loggers.append(logger)
        print(f"  Listening on {label}")

    print(f"\n--- CAPTURE RUNNING: {capture_name} ---")
    print("Perform actions in Armoury Crate now.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping capture...")

    for d in vendor_devices:
        d.close()
    log_file.close()
    print(f"Saved: {log_path}")

if __name__ == "__main__":
    main()
```

### 2c. Capture Sequence

Run the script once per action. Start the script, perform the action in Armoury Crate, wait a few seconds, then Ctrl+C.

```powershell
# Terminal 1: Run captures one at a time
python capture_hid.py 01-baseline-idle          # Idle for ~10s, Ctrl+C
python capture_hid.py 02-oled-solid-white        # Set OLED to solid white preset
python capture_hid.py 03-oled-solid-black        # Set OLED to solid black preset
python capture_hid.py 04-oled-custom-image       # Upload a simple custom image (checkerboard/text)
python capture_hid.py 05-oled-second-image       # Upload a DIFFERENT custom image
python capture_hid.py 06-oled-animation          # Upload a GIF/animation
python capture_hid.py 07-oled-brightness         # Change brightness (try multiple levels)
python capture_hid.py 08-profile-switch          # Switch keyboard profiles
python capture_hid.py 09-oled-off-on             # Toggle OLED off then on
python capture_hid.py 10-full-session            # Do ALL of the above in one long capture
```

**IMPORTANT NOTES:**
- The logger only captures **incoming** reports (device→host). To also capture **outgoing** (host→device) commands that Armoury Crate sends, we need to hook at a lower level. If the incoming data alone isn't enough, fall back to Wireshark for the outgoing direction.
- If `pywinusb` can't open the device because Armoury Crate has exclusive access, try starting the logger FIRST, then opening Armoury Crate.
- If exclusive access is still a problem, use `hidapi` instead: `pip install hidapi`

### 2d. Alternative: Bidirectional Capture with hidapi

If you need to see what Armoury Crate **sends** (not just receives), this alternative uses Windows API hooking. However, the simpler approach is to use Wireshark alongside the Python logger — Python gives clean incoming data, Wireshark gives the full bidirectional picture.

---

## STEP 3: SUPPLEMENTARY — WIRESHARK CAPTURE

Use Wireshark alongside the Python logger to capture the **outgoing** (host→device) commands that Armoury Crate sends. The Python logger above only sees incoming responses.

### 3a. Identify the USB Bus

```powershell
# Find which USB bus the ROG OMNI RECEIVER is on
Get-PnpDevice -Class USB | Where-Object { $_.InstanceId -match "VID_0B05" } | Format-List
```

Or in Wireshark: go to **Capture > Options**, look for USBPcap interfaces.

### 3b. Wireshark Filters

After starting a capture on the correct USBPcap interface:

```
usb.idVendor == 0x0b05 && usb.idProduct == 0x1ace
```

Or to see only HID data:
```
usbhid.data
```

### 3c. Capture Alongside Python Logger

Run Wireshark capture at the same time as the Python logger. Save each Wireshark capture as a matching `.pcapng`:

```
azoth-x-captures/04-oled-custom-image_*.log      ← Python (clean, incoming)
azoth-x-captures/04-oled-custom-image.pcapng      ← Wireshark (bidirectional, noisier)
```

### Tips for Clean Captures
- Close other USB-heavy applications to reduce noise
- Don't touch the keyboard during captures (except when testing key-related features)
- If Armoury Crate has an "apply" button, start capture BEFORE clicking apply

---

## STEP 5: TRANSFER FILES

Once all captures are done, transfer the `.pcapng` files to the Linux machine. Options:
- USB drive
- `scp` / SFTP
- Network share
- Cloud storage

Put all files in a folder called `azoth-x-captures/`.

---

## WHAT WE'RE LOOKING FOR

When the Linux side analyzes these captures, we'll be searching for:

1. **OLED image transfer protocol** — How images are encoded, chunked, and sent
2. **Image format** — Raw bitmap? RGB565? Compressed? What resolution?
3. **Command structure** — Report ID, command byte, subcommand, payload format
4. **Framing** — Start/end markers for multi-packet transfers
5. **Handshake** — Does the keyboard ACK each packet? Is there flow control?
6. **Brightness control** — Likely a short single-packet command
7. **OLED on/off** — Another short command
8. **Profile system** — How profiles map to OLED content

---

## KNOWN TECHNICAL DETAILS FROM LINUX ANALYSIS

The vendor HID interface uses **three report collections**:

- **Report ID 0x01** (Usage Page 0xFF02): 63 bytes in, 63 bytes out
- **Report ID 0x02** (Usage Page 0xFF00): 63 bytes in, 63 bytes out
- **Report ID 0x03** (Usage Page 0xFF01): 63 bytes in, 63 bytes out

Armoury Crate likely sends commands on one report ID and receives responses on another. The 63-byte payload size means image data will be chunked into ~63-byte segments.

For a 128x40 OLED at 1-bit depth: ~640 bytes (≈11 packets)
For a 128x40 OLED at 16-bit RGB565: ~10,240 bytes (≈163 packets)

The actual OLED resolution is not confirmed yet — captures will reveal this.

---

## SUMMARY CHECKLIST

- [ ] Install `pywinusb` (pip install pywinusb)
- [ ] Install Wireshark + USBPcap (supplementary)
- [ ] Reboot
- [ ] Install/update Armoury Crate
- [ ] Verify keyboard detected in Armoury Crate
- [ ] Run `enumerate_hid.py` to identify vendor interfaces
- [ ] Run Python HID logger captures 01 through 10
- [ ] Run Wireshark alongside Python logger for bidirectional captures
- [ ] Transfer `azoth-x-captures/` folder to Linux machine
