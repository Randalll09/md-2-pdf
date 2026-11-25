"""Sprite editor: builds the base chibi sprite and provides pixel-exact editing helpers.

Rules kept by every function here:
- Only NEAREST scaling (and only for previews; sprite files are never resized).
- No filters, no blur, no anti-aliasing: every pixel is either fully opaque palette color or fully clear.
- Every pixel of the base belongs to a named region (see REGIONS) so later animation steps can
  edit one region through a mask and prove nothing else changed.
"""
from __future__ import annotations

import json
import math
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.dirname(HERE)

W, H = 48, 52          # canvas size of every frame
AXIS = 23.5            # vertical symmetry axis (between columns 23 and 24)
BASELINE = 49          # y of the lowest foot row (sole line)

# ---------------------------------------------------------------- palette
# Hue-shifted ramps: dark steps lean cool/rose, light steps lean warm/yellow.
PAL = {
    "skin_hi":   (255, 246, 228),
    "skin":      (252, 231, 205),
    "skin_sh":   (240, 203, 176),
    "skin_dk":   (214, 162, 146),
    "skin_ln":   (176, 121, 120),   # darkest skin edge (no black outline)
    "brow":      (190, 136, 126),
    "mouth":     (214, 128, 122),
    "eye_ln":    (74, 30, 44),      # upper lash line
    "eye_dk":    (122, 26, 40),
    "eye_md":    (184, 40, 52),
    "eye_lt":    (226, 88, 78),
    "top_hi":    (122, 136, 168),
    "top":       (90, 102, 136),
    "top_sh":    (66, 74, 108),
    "top_ln":    (46, 50, 82),
    "pant_hi":   (78, 80, 104),
    "pant":      (58, 59, 82),
    "pant_sh":   (42, 42, 64),
    "pant_ln":   (30, 29, 48),
}

# Region ids for the label map (0 = empty).
REGIONS = {
    "head": 1, "ear": 2, "brow": 3, "eye_l": 4, "eye_r": 5, "mouth": 6,
    "neck": 7, "shoulder": 8, "chest": 9, "waist": 10, "arm_l": 11, "arm_r": 12,
    "hand_l": 13, "hand_r": 14, "hips": 15, "leg_l": 16, "leg_r": 17, "foot_l": 18, "foot_r": 19,
}
REGION_NAMES = {v: k for k, v in REGIONS.items()}


# ---------------------------------------------------------------- helpers
def sym_row(y, hw):
    """Columns of a row centered on AXIS with half-width hw (whole pixels per side)."""
    return [(x, y) for x in range(int(AXIS + 0.5 - hw), int(AXIS + 0.5 + hw))]


def mirror(pts):
    return [(int(2 * AXIS - x), y) for x, y in pts]


class Canvas:
    def __init__(self):
        self.lab = [[0] * W for _ in range(H)]
        self.col = [[None] * W for _ in range(H)]

    def put(self, pts, region, color=None):
        rid = REGIONS[region]
        for x, y in pts:
            if 0 <= x < W and 0 <= y < H:
                self.lab[y][x] = rid
                self.col[y][x] = color

    def mask(self, *regions):
        ids = {REGIONS[r] for r in regions}
        return {(x, y) for y in range(H) for x in range(W) if self.lab[y][x] in ids}


def shade_region(cv, pts, ramp, light=(-0.55, -0.65, 0.52), cx=None, cy=None, rx=None, ry=None,
                 cyl=False, th=(0.80, 0.50, 0.22)):
    """Color a region with flat clusters from a simple normal. ramp = (hi, base, sh, dk)."""
    pts = list(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = (min(xs) + max(xs)) / 2 if cx is None else cx
    cy = (min(ys) + max(ys)) / 2 if cy is None else cy
    rx = (max(xs) - min(xs) + 1) / 2 if rx is None else rx
    ry = (max(ys) - min(ys) + 1) / 2 if ry is None else ry
    lx, ly, lz = light
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln
    out = {}
    for x, y in pts:
        nx = (x - cx) / rx
        ny = 0.0 if cyl else (y - cy) / ry
        nz = math.sqrt(max(0.0, 1 - nx * nx - ny * ny))
        d = nx * lx + ny * ly + nz * lz
        if d > th[0]:
            c = ramp[0]
        elif d > th[1]:
            c = ramp[1]
        elif d > th[2]:
            c = ramp[2]
        else:
            c = ramp[3]
        out[(x, y)] = c
    return out


# ---------------------------------------------------------------- base character
HEAD_HW = {4: 4, 5: 6, 6: 7, 7: 8, 8: 9, 9: 9, 10: 10, 11: 10, 12: 10, 13: 10, 14: 10, 15: 10,
           16: 10, 17: 10, 18: 10, 19: 9, 20: 9, 21: 8, 22: 7, 23: 6, 24: 4}


# Left eye (the right eye is its mirror). No sclera and no catchlight: no white pixels.
EYE_OPEN = {
    (16, 15): "eye_ln", (17, 15): "eye_ln", (18, 15): "eye_ln", (19, 15): "eye_ln", (20, 15): "eye_ln",
    (17, 16): "eye_dk", (18, 16): "eye_dk", (19, 16): "eye_dk", (20, 16): "eye_dk",
    (17, 17): "eye_dk", (18, 17): "eye_md", (19, 17): "eye_md", (20, 17): "eye_dk",
    (17, 18): "eye_md", (18, 18): "eye_lt", (19, 18): "eye_lt", (20, 18): "eye_md",
    (18, 19): "eye_lt", (19, 19): "eye_lt",
}


def build_base():
    cv = Canvas()
    skin = ("skin_hi", "skin", "skin_sh", "skin_dk")
    top = ("top_hi", "top", "top_sh", "top_ln")
    pant = ("pant_hi", "pant", "pant_sh", "pant_ln")

    # ---- head (bald dome + face)
    head = [p for y, hw in HEAD_HW.items() for p in sym_row(y, hw)]
    cv.put(head, "head")
    # light mostly frontal so the face stays one flat cluster; shade only along the right/lower rim
    for p, c in shade_region(cv, head, skin, light=(-0.45, -0.55, 0.70), cx=AXIS, cy=14.5,
                             rx=10.5, ry=11, th=(0.90, 0.52, 0.22)).items():
        cv.col[p[1]][p[0]] = c
    # face (below the brow line) is one flat cluster: shade only a thin rim on the right and under the jaw
    hs = set(head)
    for x, y in head:
        if y < 12:
            continue
        right_rim = (x + 2, y) not in hs or (x + 1, y + 1) not in hs and x > AXIS
        jaw_rim = (x, y + 2) not in hs and (x, y + 1) in hs and y >= 21
        cv.col[y][x] = "skin_sh" if (right_rim and x > AXIS) or jaw_rim else "skin"
    # ears: one pixel bump on each side
    ear_l = [(13, 15), (13, 16), (13, 17)]
    cv.put(ear_l, "ear", "skin_sh")
    cv.put(mirror(ear_l), "ear", "skin_dk")

    # ---- neck (one row, in the chin's shadow)
    neck = sym_row(25, 2)
    cv.put(neck, "neck", "skin_dk")

    # ---- shoulders (skin, sleeveless) and chest/waist (top)
    shoulder = sym_row(26, 5) + sym_row(27, 6)
    cv.put(shoulder, "shoulder")
    for p, c in shade_region(cv, shoulder, skin, cx=AXIS, cy=28, rx=6.5, ry=3).items():
        cv.col[p[1]][p[0]] = c
    chest = sym_row(27, 3) + sym_row(28, 4) + sym_row(29, 4) + sym_row(30, 4) + sym_row(31, 4)
    waist = sym_row(32, 3) + sym_row(33, 3) + sym_row(34, 3)
    cv.put(chest, "chest")
    cv.put(waist, "waist")
    for p, c in shade_region(cv, chest + waist, top, cx=AXIS, rx=4.6, cyl=True,
                             th=(0.85, 0.45, 0.05)).items():
        cv.col[p[1]][p[0]] = c

    # ---- arms (hang beside the body) and hands
    arm_l = [(x, y) for y in range(28, 35) for x in (18, 19)]
    hand_l = [(18, 35), (19, 35), (17, 36), (18, 36), (19, 36), (18, 37)]
    for pts, rg, rh in ((arm_l, "arm_l", "arm_r"), (hand_l, "hand_l", "hand_r")):
        cv.put(pts, rg)
        cv.put(mirror(pts), rh)
    for pts in (arm_l + hand_l, mirror(arm_l + hand_l)):
        for p, c in shade_region(cv, pts, skin, cyl=True, th=(0.9, 0.42, 0.0)).items():
            cv.col[p[1]][p[0]] = c

    # ---- hips and legs (tight long pants), feet
    hips = sym_row(35, 4) + sym_row(36, 4) + sym_row(37, 4) + sym_row(38, 4)
    cv.put(hips, "hips")
    for p, c in shade_region(cv, hips, pant, cx=AXIS, rx=4.6, cyl=True,
                             th=(0.85, 0.45, 0.05)).items():
        cv.col[p[1]][p[0]] = c
    leg_l = [(x, y) for y in range(39, 48) for x in (20, 21, 22)] + [(23, 39), (23, 40)]
    foot_l = [(20, 48), (21, 48), (22, 48), (19, 49), (20, 49), (21, 49), (22, 49)]
    cv.put(leg_l, "leg_l")
    cv.put(mirror(leg_l), "leg_r")
    for pts in (leg_l, mirror(leg_l)):
        for p, c in shade_region(cv, pts, pant, cx=sum(q[0] for q in pts[:27]) / 27, rx=1.8,
                                 cyl=True, th=(0.9, 0.45, 0.0)).items():
            cv.col[p[1]][p[0]] = c
    cv.put(foot_l, "foot_l")
    cv.put(mirror(foot_l), "foot_r")
    for pts in (foot_l, mirror(foot_l)):
        for p, c in shade_region(cv, pts, skin, cy=48.5, ry=1.5, th=(0.9, 0.45, 0.05)).items():
            cv.col[p[1]][p[0]] = c

    # ---- selective edge: silhouette pixels on the shade side get the darkest step of their ramp
    darkest = {"skin_hi": "skin_ln", "skin": "skin_ln", "skin_sh": "skin_ln", "skin_dk": "skin_ln",
               "top_hi": "top_ln", "top": "top_ln", "top_sh": "top_ln", "top_ln": "top_ln",
               "pant_hi": "pant_ln", "pant": "pant_ln", "pant_sh": "pant_ln", "pant_ln": "pant_ln"}
    lit_edge = {"skin_hi": "skin", "skin": "skin", "skin_sh": "skin_sh", "skin_dk": "skin_dk",
                "top_hi": "top", "top": "top", "top_sh": "top_sh", "top_ln": "top_ln",
                "pant_hi": "pant", "pant": "pant", "pant_sh": "pant_sh", "pant_ln": "pant_ln"}
    snap = [row[:] for row in cv.lab]

    def empty(x, y):
        return not (0 <= x < W and 0 <= y < H) or snap[y][x] == 0

    for y in range(H):
        for x in range(W):
            if not snap[y][x]:
                continue
            c = cv.col[y][x]
            if empty(x + 1, y) or empty(x, y + 1):
                cv.col[y][x] = darkest[c]
            elif empty(x - 1, y) or empty(x, y - 1):
                cv.col[y][x] = lit_edge[c]

    # ---- inner separation lines (arm against torso, legs, chin) drawn as one ramp step darker
    for y in range(H):
        for x in range(W):
            r = REGION_NAMES.get(cv.lab[y][x])
            if r in ("chest", "waist", "hips") and REGION_NAMES.get(cv.lab[y][x - 1]) in ("arm_l", "hand_l"):
                cv.col[y][x] = {"top": "top_sh", "top_hi": "top", "top_sh": "top_ln", "top_ln": "top_ln",
                                "pant": "pant_sh", "pant_hi": "pant", "pant_sh": "pant_ln",
                                "pant_ln": "pant_ln"}[cv.col[y][x]]
            if r in ("arm_r", "hand_r") and REGION_NAMES.get(cv.lab[y][x - 1]) in ("chest", "waist", "hips"):
                cv.col[y][x] = {"skin_hi": "skin", "skin": "skin_sh", "skin_sh": "skin_dk",
                                "skin_dk": "skin_dk", "skin_ln": "skin_ln"}[cv.col[y][x]]
    # shadow the chin casts on the neck, and the waistband line
    for p in sym_row(35, 4)[1:-1]:
        cv.col[p[1]][p[0]] = "pant_sh"

    # ---- face (drawn last, only inside the head)
    brow_l = [(17, 13), (18, 13), (19, 13)]
    cv.put(brow_l, "brow", "brow")
    cv.put(mirror(brow_l), "brow", "brow")
    for (x, y), c in EYE_OPEN.items():
        cv.put([(x, y)], "eye_l", c)
        cv.put(mirror([(x, y)]), "eye_r", c)
    cv.put([(23, 22), (24, 22)], "mouth", "mouth")
    return cv


def canvas_to_image(cv):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = im.load()
    for y in range(H):
        for x in range(W):
            if cv.lab[y][x]:
                px[x, y] = PAL[cv.col[y][x]] + (255,)
    return im


def label_image(cv):
    im = Image.new("L", (W, H), 0)
    px = im.load()
    for y in range(H):
        for x in range(W):
            px[x, y] = cv.lab[y][x]
    return im


# ---------------------------------------------------------------- editing helpers (used by animation)
def load(path):
    return Image.open(path).convert("RGBA")


def region_mask(labels: Image.Image, *names):
    ids = {REGIONS[n] for n in names}
    lp = labels.load()
    return {(x, y) for y in range(labels.height) for x in range(labels.width) if lp[x, y] in ids}


def apply_edits(base: Image.Image, edits: dict, allowed: set):
    """Copy base and set pixels in `edits` ({(x,y): rgba}). Raises if an edit leaves `allowed`."""
    out = base.copy()
    px = out.load()
    for (x, y), c in edits.items():
        if (x, y) not in allowed:
            raise ValueError(f"edit at {(x, y)} is outside the allowed mask")
        px[x, y] = c
    return out


def upscale(im, n):
    return im.resize((im.width * n, im.height * n), Image.NEAREST)


def checker(w, h, s=8):
    bg = Image.new("RGBA", (w, h), (236, 236, 240, 255))
    d = ImageDraw.Draw(bg)
    for y in range(0, h, s):
        for x in range(0, w, s):
            if (x // s + y // s) % 2:
                d.rectangle([x, y, x + s - 1, y + s - 1], fill=(214, 214, 222, 255))
    return bg


def preview_sheet(items, scale=6, gap=16, label_h=18):
    """items: list of (label, image, scale or None). Labels drawn with PIL default bitmap font."""
    tiles = []
    for label, im, s in items:
        s = s or scale
        big = upscale(im, s)
        tile = checker(big.width, big.height)
        tile.alpha_composite(big)
        tiles.append((label, tile))
    tw = sum(t.width for _, t in tiles) + gap * (len(tiles) + 1)
    th = max(t.height for _, t in tiles) + label_h + gap * 2
    sheet = Image.new("RGBA", (tw, th), (255, 255, 255, 255))
    d = ImageDraw.Draw(sheet)
    x = gap
    for label, t in tiles:
        d.text((x, gap // 2), label, fill=(20, 20, 30, 255))
        sheet.alpha_composite(t, (x, gap + label_h - 4))
        x += t.width + gap
    return sheet


if __name__ == "__main__":
    cv = build_base()
    base_dir = os.path.join(ASSETS, "characters", "base")
    os.makedirs(base_dir, exist_ok=True)
    img = canvas_to_image(cv)
    img.save(os.path.join(base_dir, "base.png"))
    label_image(cv).save(os.path.join(base_dir, "base_regions.png"))
    with open(os.path.join(base_dir, "base_meta.json"), "w") as f:
        json.dump({"size": [W, H], "axis": AXIS, "baseline": BASELINE, "regions": REGIONS,
                   "palette": {k: list(v) for k, v in PAL.items()}}, f, indent=1)
    print("saved", os.path.join(base_dir, "base.png"))
