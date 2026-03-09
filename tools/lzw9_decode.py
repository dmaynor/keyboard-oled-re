#!/usr/bin/env python3
"""TouchGFX L8 LZW9 Decompressor for Corsair Vanguard 96 firmware."""

import struct
import sys
from pathlib import Path
from collections import Counter

# LZW9 constants (from TouchGFX LCD16bpp.hpp)
MAX_DICT_SIZE = 512   # 2^9
BLOCK_SIZE = 1024     # pixels per block

FLASH_BASE = 0x08020000
BITMAP_TABLE = 0x06212C


def read_9bit(data, byte_pos, bit_pos):
    """Extract one 9-bit code from packed bitstream.

    Matches firmware at 0x027F4: two bytes read, shifted by bit_pos.
    bit_pos cycles 0-7, byte_pos advances by 1 per code (+1 extra every 8th).
    """
    if byte_pos + 1 >= len(data):
        return 0, byte_pos, bit_pos
    b0 = data[byte_pos]
    b1 = data[byte_pos + 1]
    code = (b0 >> bit_pos) | (((b1 << (7 - bit_pos)) & 0xFF) << 1)

    new_bit_pos = bit_pos + 1
    new_byte_pos = byte_pos + 1
    if new_bit_pos >= 8:
        new_bit_pos = 0
        new_byte_pos += 1
    return code, new_byte_pos, new_bit_pos


def decode_entry(dict_entries, code):
    """Follow prefix chain to reconstruct the full string for a code."""
    result = []
    current = code
    safety = 0
    while current is not None and current in dict_entries:
        char, _length, prefix = dict_entries[current]
        result.append(char)
        current = prefix
        safety += 1
        if safety > BLOCK_SIZE:
            break
    result.reverse()
    return result


def decode_block(data, max_literal, block_pixels=BLOCK_SIZE):
    """Decode one LZW9 block.

    Args:
        data: compressed bytes for this block
        max_literal: byte0 from BOT entry (highest literal code)
        block_pixels: pixels to decode (1024 or less for last block)

    Returns:
        list of palette indices (L8 values)
    """
    dict_entries = {}
    for i in range(max_literal + 1):
        dict_entries[i] = (i, 1, None)

    next_code = max_literal + 1
    output = []
    byte_pos = 0
    bit_pos = 0

    # First code (must be literal)
    code, byte_pos, bit_pos = read_9bit(data, byte_pos, bit_pos)
    prev_string = decode_entry(dict_entries, code)
    if not prev_string:
        prev_string = [code & 0xFF]
    output.extend(prev_string)
    prev_code = code

    while len(output) < block_pixels:
        code, byte_pos, bit_pos = read_9bit(data, byte_pos, bit_pos)

        if code <= max_literal:
            curr_string = [code]
        elif code < next_code:
            curr_string = decode_entry(dict_entries, code)
        elif code == next_code:
            # KwKwK case
            curr_string = prev_string + [prev_string[0]]
        else:
            # Beyond dictionary — treat as KwKwK
            curr_string = prev_string + [prev_string[0]]

        output.extend(curr_string)

        if next_code < MAX_DICT_SIZE:
            dict_entries[next_code] = (curr_string[0], len(prev_string) + 1, prev_code)
            next_code += 1

        prev_string = curr_string
        prev_code = code

    return output[:block_pixels]


def parse_clut(clut_data):
    """Parse CLUT structure: header(4) + BOT(44x4=176) + palette(256x2=512)."""
    fmt = clut_data[0]
    comp = clut_data[1]
    size = struct.unpack_from('<H', clut_data, 2)[0]

    bot_start = 4
    num_bot = 44
    pal_start = bot_start + num_bot * 4  # 180

    bot = []
    for i in range(num_bot):
        idx = bot_start + i * 4
        b0 = clut_data[idx]
        offset = (clut_data[idx+1] << 16) | (clut_data[idx+2] << 8) | clut_data[idx+3]
        bot.append((b0, offset))

    palette = []
    for i in range(256):
        if pal_start + i*2 + 1 < len(clut_data):
            val = struct.unpack_from('<H', clut_data, pal_start + i*2)[0]
        else:
            val = 0
        palette.append(val)

    return fmt, comp, size, bot, palette


def decode_frame(firmware_path, frame_idx=0):
    """Decode a complete animation frame from the firmware."""
    with open(firmware_path, 'rb') as f:
        # Read bitmap entry
        f.seek(BITMAP_TABLE + frame_idx * 20)
        entry = f.read(20)
        pixel_ptr = struct.unpack_from('<I', entry, 0)[0]
        clut_ptr = struct.unpack_from('<I', entry, 4)[0]
        width, height = struct.unpack_from('<HH', entry, 8)

        pixel_foff = pixel_ptr - FLASH_BASE
        clut_foff = clut_ptr - FLASH_BASE

        # Get CLUT size from next frame
        f.seek(BITMAP_TABLE + (frame_idx + 1) * 20)
        next_entry = f.read(20)
        next_pixel_ptr = struct.unpack_from('<I', next_entry, 0)[0]
        clut_size = (next_pixel_ptr - FLASH_BASE) - clut_foff

        pixel_size = clut_foff - pixel_foff

        print(f"Frame {frame_idx}: {width}x{height}, pixel={pixel_size}B, clut={clut_size}B")

        # Read data
        f.seek(clut_foff)
        clut_data = f.read(clut_size)
        f.seek(pixel_foff)
        pixel_data = f.read(pixel_size)

    fmt, comp, size, bot, palette = parse_clut(clut_data)
    print(f"  CLUT: format={fmt}, comp={comp}, size={size}")

    total_pixels = width * height

    # Block size = rows_per_block * width
    # Firmware computes: rows_per_block = 1024 / width (integer division)
    # For 248px width: 1024/248 = 4 rows, so block = 4*248 = 992 pixels
    if width <= BLOCK_SIZE:
        rows_per_block = BLOCK_SIZE // width
    else:
        rows_per_block = 1
    actual_block_pixels = rows_per_block * width
    num_blocks = (total_pixels + actual_block_pixels - 1) // actual_block_pixels

    all_indices = []
    errors = 0

    for block_idx in range(num_blocks):
        if block_idx >= len(bot):
            break

        max_literal, block_offset = bot[block_idx]

        # Block data extends to next block's offset
        if block_idx + 1 < len(bot):
            next_offset = bot[block_idx + 1][1]
        else:
            next_offset = pixel_size

        block_data = pixel_data[block_offset:next_offset]
        remaining = total_pixels - len(all_indices)
        block_pixels = min(actual_block_pixels, remaining)

        try:
            indices = decode_block(block_data, max_literal, block_pixels)
            all_indices.extend(indices)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Block {block_idx} error (literal={max_literal}, offset={block_offset}, size={len(block_data)}): {e}")
            all_indices.extend([0] * block_pixels)

    print(f"  Decoded {len(all_indices)}/{total_pixels} pixels ({errors} errors)")

    # Convert L8 indices to RGB via palette
    rgb_data = bytearray(width * height * 3)
    for i in range(min(len(all_indices), width * height)):
        idx = all_indices[i]
        rgb565 = palette[idx] if idx < len(palette) else 0
        r = ((rgb565 >> 11) & 0x1F) * 255 // 31
        g = ((rgb565 >> 5) & 0x3F) * 255 // 63
        b = (rgb565 & 0x1F) * 255 // 31
        rgb_data[i*3] = r
        rgb_data[i*3 + 1] = g
        rgb_data[i*3 + 2] = b

    return width, height, rgb_data, all_indices


def main():
    fw_path = '/home/dmaynor/code/keyboard-oled-re/firmware/VANGUARD96_App_v2.8.59.bin'
    frame_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    width, height, rgb_data, indices = decode_frame(fw_path, frame_idx)

    output_dir = Path('/home/dmaynor/code/keyboard-oled-re/output')
    output_dir.mkdir(exist_ok=True)

    # Save as PPM
    ppm_path = output_dir / f'frame_{frame_idx:03d}.ppm'
    with open(ppm_path, 'wb') as f:
        f.write(f'P6\n{width} {height}\n255\n'.encode())
        f.write(rgb_data)
    print(f"Saved: {ppm_path}")

    # Stats
    hist = Counter(indices)
    print(f"Index usage: {len(hist)} unique values")
    print(f"Most common: {hist.most_common(5)}")


if __name__ == '__main__':
    main()
