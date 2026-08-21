#!/usr/bin/env python3
"""
Build the contributor portraits from their source photos.

The two source photos are nothing alike — one is a tight studio headshot on
neutral grey, the other a wide environmental shot in a forest. Side by side
they read as a mistake. So both get cropped to the same head-and-shoulders
framing and mapped to the same two-tone ink/paper ramp, which puts them in a
single tonal world and makes the pair look deliberate.

Usage:
    python3 tools/portraits.py                          # rebuild from defaults
    python3 tools/portraits.py --colour                 # skip the toning
    python3 tools/portraits.py --abrar photo.jpg        # swap one source photo
    python3 tools/portraits.py --james photo.jpg --crop 40,10,360,330

Requires Pillow. Source photos are not in the repo — pass your own with the
--james / --abrar flags. A new photo almost certainly needs a new --crop: aim
for head-and-shoulders, with both heads about the same size in frame.
"""

import sys
from PIL import Image, ImageOps, ImageEnhance, ImageFilter

# Ink and paper, matching the light-theme palette in assets/styles.css
INK = (0x16, 0x13, 0x0F)
PAPER = (0xFA, 0xF7, 0xF2)

SIZE = 256  # displayed at 6.5rem, so this is ~2.5x for high-density screens

# (source path, crop box on the 400x400 original, unsharp amount)
# The crops are chosen so both heads occupy roughly the same share of frame.
SOURCES = {
    "james-hartt": (
        "/home/jameshartt/Desktop/1649933828634.jpeg",
        (0, 0, 400, 400),
        0.0,
    ),
    "abrar-mahmood": (
        "/home/jameshartt/Desktop/1758875428568.jpeg",
        (122, 72, 278, 228),
        0.70,  # this one is upscaled from a 156px crop, so it needs the help
    ),
}


def duotone(im):
    """Map luminance onto an ink -> paper ramp, keeping midtone modelling."""
    g = ImageOps.grayscale(im)
    g = ImageOps.autocontrast(g, cutoff=(1, 1))
    g = ImageEnhance.Contrast(g).enhance(1.06)

    lut = []
    for channel in range(3):
        for i in range(256):
            t = i / 255.0
            # a gentle S-curve; a straight ramp flattens the faces
            t = t * t * (3 - 2 * t) * 0.35 + t * 0.65
            lut.append(round(INK[channel] + (PAPER[channel] - INK[channel]) * t))

    return Image.merge("RGB", (g, g, g)).point(lut)


def build(name, path, box, sharpen, colour=False):
    im = Image.open(path).convert("RGB").crop(box)
    im = im.resize((SIZE, SIZE), Image.LANCZOS)
    if sharpen:
        im = im.filter(ImageFilter.UnsharpMask(1.2, int(sharpen * 100), 3))
    if not colour:
        im = duotone(im)
    out = f"assets/{name}.jpg"
    im.save(out, quality=92, subsampling=0, optimize=True, progressive=True)
    print(f"  {out}")


def arg(flag):
    """Value following --flag on the command line, or None."""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        sys.exit(f"{flag} needs a value")
    return None


def main():
    colour = "--colour" in sys.argv or "--color" in sys.argv

    sources = {name: list(v) for name, v in SOURCES.items()}

    # --james / --abrar swap a source photo; --crop overrides the crop box
    for who, key in (("--james", "james-hartt"), ("--abrar", "abrar-mahmood")):
        path = arg(who)
        if path:
            sources[key][0] = path
            # a different photo will not match the old crop, so start from the
            # whole frame unless told otherwise
            sources[key][1] = None

    crop = arg("--crop")
    if crop:
        box = tuple(int(n) for n in crop.split(","))
        if len(box) != 4:
            sys.exit("--crop wants left,top,right,bottom")
        for who, key in (("--james", "james-hartt"), ("--abrar", "abrar-mahmood")):
            if arg(who):
                sources[key][1] = box

    print("building portraits" + (" (colour)" if colour else " (duotone)"))
    for name, (path, box, sharpen) in sources.items():
        if box is None:
            with Image.open(path) as probe:
                w, h = probe.size
            side = min(w, h)
            box = ((w - side) // 2, (h - side) // 2,
                   (w - side) // 2 + side, (h - side) // 2 + side)
            print(f"  {name}: no --crop given, using centre square {box}")
        build(name, path, box, sharpen, colour)


if __name__ == "__main__":
    main()
