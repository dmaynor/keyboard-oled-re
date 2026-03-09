#!/usr/bin/env python3
"""Decode all animation frames from both firmware versions and generate comparison GIFs."""

import struct
import sys
from pathlib import Path
from collections import Counter

MAX_DICT_SIZE = 512
BLOCK_SIZE = 1024


def read_9bit(data, byte_pos, bit_pos):
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
    dict_entries = {}
    for i in range(max_literal + 1):
        dict_entries[i] = (i, 1, None)
    next_code = max_literal + 1
    output = []
    byte_pos = 0
    bit_pos = 0
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
            curr_string = prev_string + [prev_string[0]]
        else:
            curr_string = prev_string + [prev_string[0]]
        output.extend(curr_string)
        if next_code < MAX_DICT_SIZE:
            dict_entries[next_code] = (curr_string[0], len(prev_string) + 1, prev_code)
            next_code += 1
        prev_string = curr_string
        prev_code = code
    return output[:block_pixels]


def decode_frame(data, bitmap_table_offset, frame_idx, flash_base=0x08020000):
    off = bitmap_table_offset + frame_idx * 20
    pixel_ptr = struct.unpack_from('<I', data, off)[0]
    clut_ptr = struct.unpack_from('<I', data, off + 4)[0]
    width, height = struct.unpack_from('<HH', data, off + 8)

    pixel_foff = pixel_ptr - flash_base
    clut_foff = clut_ptr - flash_base

    # Get next entry for size calculation
    next_off = bitmap_table_offset + (frame_idx + 1) * 20
    next_pixel_ptr = struct.unpack_from('<I', data, next_off)[0]
    clut_size = (next_pixel_ptr - flash_base) - clut_foff
    pixel_size = clut_foff - pixel_foff

    clut_data = data[clut_foff:clut_foff + clut_size]
    pixel_data = data[pixel_foff:pixel_foff + pixel_size]

    # Parse CLUT
    fmt = clut_data[0]
    comp = clut_data[1]

    total_pixels = width * height
    if width <= BLOCK_SIZE:
        rows_per_block = BLOCK_SIZE // width
    else:
        rows_per_block = 1
    actual_block_pixels = rows_per_block * width
    num_blocks = (total_pixels + actual_block_pixels - 1) // actual_block_pixels

    bot_start = 4
    pal_start = bot_start + num_blocks * 4

    bot = []
    for i in range(num_blocks):
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

    all_indices = []
    errors = 0
    for block_idx in range(num_blocks):
        if block_idx >= len(bot):
            break
        max_literal, block_offset = bot[block_idx]
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
            all_indices.extend([0] * block_pixels)

    # Convert to RGB
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

    return width, height, rgb_data, errors


def main():
    from PIL import Image

    firmwares = [
        ('firmware/Nord_App_v1.18.42.bin', 0x05ED28, 59, 'v1.18.42'),
        ('firmware/VANGUARD96_App_v2.8.59.bin', 0x06212C, 60, 'v2.8.59'),
    ]

    output_dir = Path('output')
    output_dir.mkdir(exist_ok=True)

    for fw_path, bt_offset, num_frames, version in firmwares:
        print(f"\n{'='*60}")
        print(f"Decoding {version} — {num_frames} animation frames")
        print(f"{'='*60}")

        with open(fw_path, 'rb') as f:
            data = f.read()

        frames = []
        total_errors = 0

        for i in range(num_frames):
            w, h, rgb, errs = decode_frame(data, bt_offset, i)
            total_errors += errs
            img = Image.frombytes('RGB', (w, h), bytes(rgb))
            frames.append(img)
            if i % 10 == 0:
                print(f"  Frame {i}/{num_frames} decoded ({errs} errors)")

        print(f"  All {num_frames} frames decoded, {total_errors} total errors")

        # Save animated GIF
        gif_path = output_dir / f'animation_{version.replace(".", "_")}.gif'
        frames[0].save(
            gif_path,
            save_all=True,
            append_images=frames[1:],
            duration=33,  # ~30fps
            loop=0
        )
        print(f"  Saved: {gif_path}")

        # Save key frames as PNG
        key_indices = [0, num_frames//4, num_frames//2, 3*num_frames//4]
        for idx in key_indices:
            png_path = output_dir / f'frame_{version.replace(".", "_")}_{idx:03d}.png'
            # 3x upscale
            big = frames[idx].resize((frames[idx].width * 3, frames[idx].height * 3), Image.NEAREST)
            big.save(png_path)

        # Save frame 0 at native size
        native_path = output_dir / f'frame_{version.replace(".", "_")}_000_1x.png'
        frames[0].save(native_path)
        print(f"  Saved key frames")

    # === Now decode ALL non-animation bitmaps (icons) ===
    print(f"\n{'='*60}")
    print(f"Decoding icons from both firmware versions")
    print(f"{'='*60}")

    for fw_path, bt_offset, num_anim, version in firmwares:
        with open(fw_path, 'rb') as f:
            data = f.read()

        # Count total entries
        total_entries = 0
        for i in range(200):
            off = bt_offset + i * 20
            if off + 20 > len(data):
                break
            ptr = struct.unpack_from('<I', data, off)[0]
            if ptr < 0x08020000 or ptr > 0x08020000 + len(data):
                break
            total_entries += 1

        print(f"\n  {version}: {total_entries} total bitmaps, {num_anim} animation frames")

        icon_dir = output_dir / f'icons_{version.replace(".", "_")}'
        icon_dir.mkdir(exist_ok=True)

        for i in range(num_anim, total_entries - 1):  # -1 because we need next entry for size
            off = bt_offset + i * 20
            w = struct.unpack_from('<H', data, off + 8)[0]
            h = struct.unpack_from('<H', data, off + 10)[0]

            try:
                iw, ih, rgb, errs = decode_frame(data, bt_offset, i)
                img = Image.frombytes('RGB', (iw, ih), bytes(rgb))

                # Save at 3x for visibility
                big = img.resize((iw * 3, ih * 3), Image.NEAREST)
                icon_path = icon_dir / f'icon_{i:03d}_{iw}x{ih}.png'
                big.save(icon_path)
                print(f"    Icon {i}: {iw}x{ih} ({errs} errors) -> {icon_path.name}")
            except Exception as e:
                print(f"    Icon {i}: {w}x{h} FAILED: {e}")

    print(f"\nDone!")


if __name__ == '__main__':
    main()
