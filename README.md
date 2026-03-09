# Keyboard OLED/LCD Reverse Engineering

Reverse engineering the display protocols for keyboard OLED/LCD screens to enable native Linux control without vendor software.

## Target Devices

### ASUS ROG Azoth X (OLED)
- **USB IDs**: Wired `0B05:1C24`, Bluetooth `0B05:1C27`, OMNI Receiver `0B05:1ACE`
- **Display**: 128x40 OLED
- **Status**: OLED mode switching working, display modes discovered via blind probing
- **Protocol**: HID vendor commands on interface 1 (wired) / interface 2 (receiver), 64-byte packets

### SteelSeries Apex Pro Gen 3 (OLED)
- **USB ID**: `1038:1622`
- **Display**: 128x40 OLED (SSD1309-like)
- **Status**: Full OLED write working - arbitrary text and images
- **Protocol**: HID feature reports, 1-bit monochrome framebuffer (640 bytes), 128x40 pixels

### Corsair Vanguard 96 (LCD)
- **USB ID**: `1B1C:2B0D`
- **Display**: 248x170 IPS LCD (color), located above numpad
- **MCU**: STM32U5A9 (Cortex-M33 @ 160MHz, 4MB flash, 2.5MB SRAM)
- **Status**: Bragi protocol extensively decoded. **TouchGFX L8 LZW9 image compression fully reverse-engineered** — all 60 animation frames decoded from firmware binary (Corsair Sail logo with animated rainbow ring). See [corsair-vanguard96-findings.md](corsair-vanguard96-findings.md) for full protocol details.
- **Protocol**: Corsair Bragi old protocol (2-byte header, 1024-byte HID reports on interface 2)
- **Firmware analysis**: Flash base 0x08020000, TouchGFX bitmap table at 0x06212C, LZW9 decompressor at 0x02763C
- **Next step**: Build LZW9 compressor to create custom animation frames for LCD upload

## Project Structure

```
keyboard-oled-re/
├── README.md                        # This file
├── findings.md                      # ROG Azoth X protocol findings (detailed)
├── corsair-vanguard96-findings.md   # Corsair Vanguard 96 LCD protocol findings (detailed)
├── session-log-2026-03-08.md        # Session log: Win11 VM setup for iCUE capture
├── azoth-x-capture-handoff.md       # Windows USB capture guide for Armoury Crate
├── azoth_oled.py                    # ROG Azoth X OLED control tool
├── bragi_probe.py                   # Corsair Bragi protocol explorer
├── lcd_write_test.py                # Early LCD write test via Bragi OPEN/WRITE
├── lcd_read_full.py                 # Read full LCD resource 0x3F contents
├── lcd_sw_mode_test.py              # Software mode switching test
├── lcd_jpeg_test.py                 # JPEG image write via Bragi file ops
├── lcd_direct_write.py              # corsair_lcd_tool protocol test (AIO cooler protocol)
├── lcd_debug_write.py               # Multi-approach debug test (5 methods)
├── lcd_v15_write.py                 # V1.5 protocol test (confirmed not supported)
├── lcd_bragi_file_write.py          # Bragi file-based write with Web Hub file IDs
├── lcd_session_write.py             # Session-based write with file 62 (latest)
├── qemu_type.sh                     # Helper: type text into QEMU VM via monitor sendkey
├── pics/                            # Photo evidence organized by RE phase
│   ├── 01-azoth-oled-probing/       # Initial HID probing & mode discovery
│   ├── 02-azoth-oled-modes/         # Mode demos & framing experiments
│   ├── 03-steelseries-oled/         # SteelSeries Apex Pro Gen 3 discovery
│   ├── 04-steelseries-writes/       # OLED writes, animations, call sign
│   ├── 05-corsair-lcd-probing/      # Corsair Vanguard 96 LCD probing
│   ├── 06-corsair-boot-sequence/    # Corsair keyboard boot animation capture (50 frames)
│   ├── 07-corsair-post-boot/        # Post-boot LCD state
│   ├── 08-win11-vm-setup/           # Windows 11 VM install & iCUE setup (VNC captures)
│   └── 09-lcd-probing/              # LCD protocol probing evidence (48 photos)
├── tools/                           # Analysis & decode tools
│   └── lzw9_decode.py               # TouchGFX L8 LZW9 decompressor
├── output/                          # Decoded firmware images
│   ├── animation.gif                # All 60 frames as animated GIF
│   ├── frame_000_1x.png             # Frame 0 at native 248x170
│   └── frame_*_3x.png              # Key frames at 3x upscale
├── ckb-next/                        # Corsair ckb-next source (gitignored)
├── vm/                              # Windows 11 VM (gitignored, see VM Setup)
└── venv/                            # Python virtualenv (gitignored)
```

## Key Findings

### ROG Azoth X OLED Modes (0x6x command family)
| Command | Mode | Notes |
|---------|------|-------|
| `0x61` | Animation | Default ROG globe animation |
| `0x63` | Dashboard | Date/time/stats (needs host data feed) |
| `0x64` | System Monitor | CPU/GPU/temp (needs host data feed) |
| `0x65` | Mail/Notifications | Shows envelope icon |
| `0x66` | CPU Usage | Shows "CPU Usage 0%" without host data |
| `0x68` | Unknown | Blank - possibly custom image slot |
| `0x69` | **OLED OFF** | **DANGEROUS** - requires MCU reset to recover |

### SteelSeries Apex Pro Gen 3 OLED Write Protocol
1. Open HID feature report interface
2. Send 640-byte framebuffer (128x40, 1-bit packed, MSB-first)
3. Prefix with report ID and command bytes
4. Display updates immediately - call sign "VIOLATOR ACTUAL" successfully rendered

### Corsair Vanguard 96 Bragi Protocol
- Old Bragi protocol (2-byte header): `[0x08, cmdId, ...payload]` padded to 1024 bytes
- 4 USB interfaces; Bragi on interface 2 (1024-byte HID reports)
- File-based display system discovered via Web Hub JS reverse engineering:
  - File **28007** = default screen resource (config or image data)
  - File **62** = active display (controls what LCD shows)
  - Resource **0x3F** = LCD framebuffer (84,320 bytes = 248x170x2 RGB565)
- Full protocol details: [corsair-vanguard96-findings.md](corsair-vanguard96-findings.md)

### Corsair Vanguard 96 Firmware Image Decompression (SOLVED)
- **TouchGFX L8 LZW9** — 9-bit LZW with per-block dictionaries
- Firmware binary: STM32U5A9, flash base **0x08020000** (128KB bootloader offset)
- 60 animation frames at 248x170, each with per-frame RGB565 palette
- **CLUT structure**: `[format, compression, size]` header + block offset table + palette
  - Block offset entries: `[max_literal(u8), offset_BE24(3B)]`
  - Palette: RGB565 little-endian, up to 256 entries
- **Block size**: `(1024 / width) * width` pixels (992 for 248px wide, NOT 1024)
- **9-bit code extraction**: byte-pair reads with cycling bit position (0-7)
- No clear code — dictionary fills to 512 entries and stops growing
- Decoder: [`tools/lzw9_decode.py`](tools/lzw9_decode.py) — zero errors on all 60 frames
- Output: Corsair Sail logo with animated rainbow ring → [`output/animation.gif`](output/animation.gif)

## VM Setup (for Corsair USB traffic capture)

The `vm/` directory is gitignored (contains an 80GB QCOW2 disk image and transient state). Here's how to recreate it from scratch.

### Prerequisites

```bash
sudo apt install qemu-system-x86 ovmf swtpm socat
pip install vncdotool   # or install in the project venv
```

You also need a Windows 11 ISO (e.g. `Win11_25H2_English_x64.iso`).

### Create the VM

```bash
mkdir -p vm/tpm

# Create 80GB disk image
qemu-img create -f qcow2 vm/win11.qcow2 80G

# Copy writable UEFI variable store (Secure Boot / MS keys variant)
cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd vm/OVMF_VARS.fd
```

### Install Windows 11

```bash
# Start TPM 2.0 emulator (required for Win11)
swtpm socket --tpmstate dir=vm/tpm --tpm2 \
  --ctrl type=unixio,path=vm/tpm/swtpm-sock &

# Launch VM with install ISO
qemu-system-x86_64 \
  -name "Win11-iCUE" -enable-kvm -machine q35,accel=kvm \
  -cpu host -smp 8 -m 8G \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.ms.fd \
  -drive if=pflash,format=raw,file=vm/OVMF_VARS.fd \
  -chardev socket,id=chrtpm,path=vm/tpm/swtpm-sock \
  -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
  -drive file=vm/win11.qcow2,format=qcow2,if=none,id=disk0 \
  -device ahci,id=ahci -device ide-hd,drive=disk0,bus=ahci.0 \
  -cdrom /path/to/Win11_25H2_English_x64.iso \
  -boot order=d,menu=on,splash-time=15000 \
  -device usb-ehci,id=usb -device usb-tablet,id=tablet0 \
  -device usb-host,vendorid=0x1b1c,productid=0x2b0d \
  -display gtk -vnc :0 \
  -device e1000,netdev=net0 \
  -netdev user,id=net0 \
  -global driver=cfi.pflash01,property=secure,value=on \
  -audio driver=none \
  -monitor unix:vm/qemu-monitor.sock,server,nowait
```

**Key notes:**
- Uses AHCI/SATA disk (not VirtIO) so Windows sees the drive without extra drivers
- Uses **e1000 NIC** (not virtio-net-pci) — Windows has built-in e1000 drivers, no extras needed
- `-vnc :0` enables VNC on port 5900 for programmatic control
- `-device usb-host,vendorid=0x1b1c,productid=0x2b0d` passes the Corsair keyboard through
- `-boot splash-time=15000` gives time to hit a key to boot from CD
- TPM 2.0 + Secure Boot (OVMF ms variant) satisfies Windows 11 requirements
- VM gets IP `10.0.2.15` via QEMU user-mode NAT (gateway `10.0.2.2`)

### Boot from CD (first install only)

The "Press any key to boot from CD" prompt times out fast. Spam keys via the monitor socket right after launch:

```bash
# In a separate terminal, immediately after starting QEMU:
for i in $(seq 1 30); do
  echo "sendkey ret" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
  sleep 0.3
done
```

### Windows 11 OOBE Notes

- **Offline account bypass**: Windows 11 OOBE requires internet sign-in by default. At the "Let's connect you to a network" screen, press `Shift+F10` to open a command prompt, type `oobe\bypassnro`, and the VM will reboot. After reboot, an "I don't have internet" option appears.
- **Local account**: Created as `ViolatorActual` with no password.
- The OOBE Next buttons at 1280x800 resolution are at approximately y=680.

### Interact via VNC

VNC bypasses QEMU's GTK mouse capture requirement, making it reliable for scripted control:

```bash
VNC="venv/bin/vncdo -s localhost:0"
$VNC move 500 400 click 1   # click at pixel coordinates
$VNC capture screenshot.png  # capture screen
$VNC key enter               # send keypress
```

**Known issue**: `vncdo type` sends `;` instead of `:` (colon). Use the QEMU monitor `sendkey` command for text containing colons (see `qemu_type.sh`).

**Screenshot helper** (auto-incrementing filenames):
```bash
V="venv/bin/vncdo -s localhost:0"
N(){ SNAP="vnc_$(printf '%03d' $(ls vnc_*.png 2>/dev/null | wc -l)).png"; $V capture "$SNAP"; echo "$SNAP"; }
```

### Interact via QEMU Monitor

```bash
# Send a command to the QEMU monitor
echo "sendkey ret" | socat - UNIX-CONNECT:vm/qemu-monitor.sock

# Take a screenshot (PPM format, full resolution)
echo "screendump vm/snap.ppm" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
python3 -c "from PIL import Image; Image.open('vm/snap.ppm').save('vm/snap.png')"

# Accept UAC prompts (VNC mouse clicks don't work on secure desktop)
echo "sendkey alt-y" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
```

### qemu_type.sh — Type Text via Monitor

`vncdo type` has a colon mapping bug (sends `;` instead of `:`). The `qemu_type.sh` helper types arbitrary text via QEMU monitor `sendkey` commands, correctly handling colons, backslashes, quotes, and all special characters:

```bash
# Type a URL into the VM
./qemu_type.sh 'https://example.com/path'
# Then press Enter
echo "sendkey ret" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
```

### Run the VM (after install)

Same as the install command but remove the `-cdrom` line and change boot to `-boot order=c`.

### Download iCUE in the VM

From a PowerShell prompt inside the VM (typed via `qemu_type.sh`):

```powershell
Invoke-WebRequest -Uri "https://www3.corsair.com/software/CUE_V5/public/modules/windows/installer/Install iCUE.exe" -OutFile "$env:USERPROFILE\Desktop\iCUE_setup.exe"
Start-Process "$env:USERPROFILE\Desktop\iCUE_setup.exe"
# UAC prompt: use Alt+Y via QEMU monitor (VNC can't click secure desktop)
```

## Tools & Dependencies

- **Python 3.12** + virtualenv (`hidapi`, `Pillow`)
- **QEMU/KVM 8.2.2** with OVMF (UEFI) and swtpm (TPM 2.0)
- **socat** for QEMU monitor socket communication
- **vncdotool** for VNC-based VM control (mouse clicks, screenshots)
- **qemu_type.sh** (custom) for typing text into VM via QEMU monitor sendkey — works around vncdo colon bug
- **ckb-next** source as Corsair Bragi protocol reference

## Lessons Learned

### VNC Automation Pitfalls
- `vncdo type` maps `:` to `;` — use QEMU monitor `sendkey shift-semicolon` instead (wrapped in `qemu_type.sh`)
- `vncdo type` also can't send `"` (double quotes) — use `sendkey shift-apostrophe`
- UAC secure desktop prompts don't respond to VNC mouse clicks — use `sendkey alt-y` via QEMU monitor
- VNC coordinate latency is ~250ms per `vncdo` invocation — no need for long sleeps between actions
- Screenshot early and often for evidence logging

### Windows 11 VM Tips
- Use **e1000** NIC, not virtio-net-pci (no built-in Windows driver for virtio)
- Use **AHCI/SATA** disk, not VirtIO (same reason)
- `oobe\bypassnro` via Shift+F10 cmd prompt skips mandatory Microsoft account sign-in
- Edge first-run sign-in modal blocks everything — use `Win+R` -> `cmd` to get a working shell
- PowerShell quoting in cmd.exe is fragile — launch `powershell` interactively instead of `powershell -command '...'`
- `curl.exe` in Windows cmd doesn't support single quotes — use double quotes or PowerShell `Invoke-WebRequest`

## Safety Notes

- **ROG Azoth X**: Avoid `0x69` (OLED shutdown) and `0xFC-0xFF` (USB controller crash). Recovery requires physical mode switch.
- **Corsair**: Bragi file writes are generally safe. **Avoid SET property 0x3E** (causes USB protocol error and device disconnect). Stale handles from crashed scripts can block subsequent file operations — always close/unbind handles first. File 28007 corruption is recoverable via DELETE + CREATE + WRITE.
- Always photograph/screenshot display state before and after probing for evidence.
