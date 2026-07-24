"""Generate the app icon (a golf flag on a fairway-green roundel) as a multi-size .ico.

Run:  python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
GREEN = (20, 83, 45, 255)
GREEN_DARK = (11, 61, 32, 255)
WHITE = (255, 255, 255, 255)
AMBER = (217, 119, 6, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Roundel with a darker rim for depth.
d.ellipse([6, 6, S - 6, S - 6], fill=GREEN_DARK)
d.ellipse([12, 12, S - 12, S - 12], fill=GREEN)
d.ellipse([12, 12, S - 12, S - 12], outline=(255, 255, 255, 45), width=3)

# Ground line (the green).
d.arc([40, 150, S - 40, 250], start=200, end=340, fill=(255, 255, 255, 60), width=4)

# Flag pole.
pole_x = 104
d.line([(pole_x, 56), (pole_x, 196)], fill=WHITE, width=9)
# Cup at the base of the pole.
d.ellipse([pole_x - 20, 190, pole_x + 20, 206], fill=(235, 235, 235, 255))

# Pennant flag.
d.polygon([(pole_x + 4, 60), (pole_x + 4, 116), (188, 88)], fill=AMBER)
d.polygon([(pole_x + 4, 60), (pole_x + 4, 116), (188, 88)], outline=(146, 64, 14, 255))

# Golf ball.
d.ellipse([150, 170, 182, 202], fill=WHITE)

out = Path(__file__).parent / "icon.ico"
img.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
# Also drop a PNG for any web use.
img.save(Path(__file__).parent.parent / "app" / "static" / "icon.png", format="PNG")
print(f"wrote {out}")
