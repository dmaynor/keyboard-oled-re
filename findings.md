# ROG Azoth X Protocol Findings

## Device IDs
- Wired USB: `0B05:1C24` (4 interfaces, vendor on IF1)
- Bluetooth: `0B05:1C27`
- OMNI Receiver: `0B05:1ACE` (4 interfaces, vendor on IF2)

## Wired Vendor Interface (IF1, hidraw11)
- Usage Page: 0xFF00
- Single collection, NO report ID
- 64-byte packets (0x40) in/out
- Path: `1-1:1.1`

## OMNI Receiver Vendor Interface (IF2, hidraw9)
- Usage Pages: 0xFF02, 0xFF00, 0xFF01
- 3 collections with Report IDs 0x01, 0x02, 0x03
- 63-byte packets (0x3F) per report ID

## Heartbeat
- Device sends `ff aa 00 00 ...` periodically (wired)
- Via receiver: Report ID 0x02, `02 ff aa 00 00 ...`

## Known Commands (wired, prepend 0x00 for HID write)

### 0x12 — Device Info (SAFE)
```
Send: 12 00 00 ...
Recv: 12 00 00 00 11 00 01 00 06 04 06 00 01 00 00 ...
```
- `06 04 06` = likely firmware version 6.4.6
- Works on all 3 report IDs via receiver too

### 0x6x — OLED Display Mode Commands (CONFIRMED)
- 0x61 = Animation mode (default ROG globe) — CONFIRMED via camera
- 0x63 = Dashboard mode (date/time/stats, shows 00/00/00 without host data) — CONFIRMED
- 0x64 = System monitor mode (CPU/GPU/temp, shows 0% without host data) — CONFIRMED
- 0x65 = Notifications/Mail mode (shows "Mail" with envelope icon) — CONFIRMED
- 0x66 = CPU Usage mode (shows "CPU Usage 0%") — CONFIRMED
- 0x68 = Unknown (mostly blank, possibly empty custom image slot) — needs investigation
- 0x69 = **OLED OFF / SHUTDOWN — DANGEROUS** — kills display, requires full MCU reset

### Other ACK Commands (untested for OLED effect)
- 0x21, 0x27
- 0x41, 0x43
- 0x51 (returned `51 00 00 00 06` — has data, profile/mode related?)
- 0x71, 0x72, 0x74
- 0xFA

### DANGEROUS Commands
- 0x69 = OLED shutdown (requires mode switch to recover)
- 0xFC, 0xFD, 0xFE, 0xFF — returned `fc 44 fc`, caused USB controller crash
- Brute forcing all 256 commands rapidly destabilized the device

## Error Response
- `fc 44 fc` = NACK / error / device in fault state

## Recovery
- USB software reset (USBDEVFS_RESET) does NOT recover OLED
- Must switch physical mode (receiver -> wired) to reinit MCU
- OLED off state from 0x69 is volatile (comes back after MCU reset)

## Notes
- Wired path is simpler (no report ID framing)
- Receiver path has 3 report IDs with different usage pages
- Display modes accept payloads — likely for feeding data (time, CPU%, etc.)
- Need Armoury Crate captures to see actual data payloads for each mode
- **AVOID: 0x69, 0xFC-0xFF**
