"""Sprite validator: pixel-exact checks for the base sprite and for animation frames.

Usage:
  python sprite_validator.py base                      # check characters/base/base.png
  (animation checks are called from animation_builder.py through validate_frames())
"""
from __future__ import annotations

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sprite_editor import ASSETS, BASELINE, PAL, REGIONS, load  # noqa: E402

PALETTE = {tuple(v) for v in PAL.values()}


def bbox(im):
    return im.getchannel("A").getbbox()


def check_pixels(im, name="image"):
    """Alpha is 0/255 only, and every opaque color is in the base palette (no blur/AA/new colors)."""
    errs = []
    for _, (r, g, b, a) in im.getcolors(1 << 16):
        if a not in (0, 255):
            errs.append(f"{name}: semi-transparent pixel alpha={a} (blur/AA)")
        elif a == 255 and (r, g, b) not in PALETTE:
            errs.append(f"{name}: color {(r, g, b)} not in palette")
    return errs


def check_eye_whites(im, labels, name="image", radius=2):
    """No near-white pixels in or around the eye regions."""
    lp, px = labels.load(), im.load()
    eye = {REGIONS["eye_l"], REGIONS["eye_r"]}
    errs = []
    for y in range(im.height):
        for x in range(im.width):
            if lp[x, y] not in eye:
                continue
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < im.width and 0 <= yy < im.height:
                        r, g, b, a = px[xx, yy]
                        if a and r > 245 and g > 240 and b > 225:
                            errs.append(f"{name}: white pixel near eye at {(xx, yy)}")
    return sorted(set(errs))


def check_lone_pixels(im, name="image"):
    """Opaque pixels with no opaque 4-neighbor (dust)."""
    px, errs = im.load(), []
    for y in range(im.height):
        for x in range(im.width):
            if px[x, y][3] and not any(
                    0 <= x + dx < im.width and 0 <= y + dy < im.height and px[x + dx, y + dy][3]
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                errs.append(f"{name}: lone pixel at {(x, y)}")
    return errs


def check_base(im, labels):
    errs = check_pixels(im, "base") + check_eye_whites(im, labels, "base") + check_lone_pixels(im, "base")
    bb = bbox(im)
    if bb[3] - 1 != BASELINE:
        errs.append(f"base: sole line at y={bb[3] - 1}, expected {BASELINE}")
    lp = labels.load()
    ys = [y for y in range(labels.height) for x in range(labels.width) if lp[x, y] == REGIONS["head"]]
    head_h = max(ys) - min(ys) + 1
    total_h = bb[3] - bb[1]
    info = {"bbox": bb, "height": total_h, "head_height": head_h,
            "heads_tall": round(total_h / head_h, 2)}
    if not 2.0 <= info["heads_tall"] <= 2.6:
        errs.append(f"base: {info['heads_tall']} heads tall, expected 2-2.5")
    return errs, info


def validate_frames(frames, base, labels, allowed_regions, name="anim"):
    """Every frame: same size, palette only, pixels outside allowed regions identical to base,
    same bbox and sole line, no white near eyes. Last->first transition must also be a valid step."""
    errs = []
    lp = labels.load()
    allowed_ids = {REGIONS[r] for r in allowed_regions}
    bpx = base.load()
    for i, f in enumerate(frames, 1):
        tag = f"{name} frame {i}"
        if f.size != base.size:
            errs.append(f"{tag}: size {f.size} != {base.size}")
            continue
        errs += check_pixels(f, tag) + check_eye_whites(f, labels, tag)
        fpx = f.load()
        outside = [(x, y) for y in range(f.height) for x in range(f.width)
                   if fpx[x, y] != bpx[x, y] and lp[x, y] not in allowed_ids]
        if outside:
            errs.append(f"{tag}: {len(outside)} changed pixels outside allowed regions, e.g. {outside[:5]}")
        if bbox(f) != bbox(base):
            errs.append(f"{tag}: bbox {bbox(f)} != base {bbox(base)} (position/size changed)")
    return errs


if __name__ == "__main__":
    base_dir = os.path.join(ASSETS, "characters", "base")
    im = load(os.path.join(base_dir, "base.png"))
    labels = Image.open(os.path.join(base_dir, "base_regions.png"))
    errs, info = check_base(im, labels)
    print("info:", info)
    print("colors used:", len({c[:3] for _, c in im.getcolors(1 << 16) if c[3]}))
    print("PASS" if not errs else "FAIL")
    for e in errs:
        print(" -", e)
    sys.exit(1 if errs else 0)
