"""Regenerate frontend/public icons from the SVG masters in this directory.

Oneshot tooling, not a runtime dependency (requires: pip install pillow cairosvg):

    python3 frontend/icon-src/render.py

Sources:
  favicon.svg    64px master (browser tab SVG, header logo, large PNGs)
  favicon-32.svg 32px pixel-fitted master (chunkier holes/strokes)
  favicon-16.svg 16px pixel-fitted master (no holes, bold J+play)
  logo.svg       horizontal lockup for dark backgrounds (header备用)
"""
import os
import shutil

import cairosvg
from PIL import Image

SRC = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.normpath(os.path.join(SRC, "..", "public"))
os.makedirs(PUB, exist_ok=True)

for name in ("favicon.svg", "logo.svg"):
    shutil.copy(os.path.join(SRC, name), os.path.join(PUB, name))

cairosvg.svg2png(url=os.path.join(SRC, "favicon-16.svg"),
                 write_to=os.path.join(PUB, "favicon-16x16.png"),
                 output_width=16, output_height=16)
cairosvg.svg2png(url=os.path.join(SRC, "favicon-32.svg"),
                 write_to=os.path.join(PUB, "favicon-32x32.png"),
                 output_width=32, output_height=32)
for name, size in (("apple-touch-icon.png", 180), ("icon-512.png", 512)):
    cairosvg.svg2png(url=os.path.join(SRC, "favicon.svg"),
                     write_to=os.path.join(PUB, name),
                     output_width=size, output_height=size)

Image.open(os.path.join(PUB, "favicon-32x32.png")).convert("RGBA").save(
    os.path.join(PUB, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
print("public icons regenerated from", SRC)
