"""Generate the Portage Horse Race app icon (PNG + multi-resolution ICO).

Drawn at 4x supersampling and downscaled with LANCZOS for crisp anti-aliased
edges. A fairway-green disc with a subtle top-lit gradient, a white ring, a
gentle putting-green contour, the hole, a golf ball, and an amber pennant flag
— matching the app's brand palette.

Run:  python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_PNG = Path(__file__).resolve().parent.parent / "app" / "static" / "icon.png"
OUT_ICO = Path(__file__).resolve().parent / "icon.ico"

S = 4              # supersample factor
N = 256            # final size
D = N * S          # working canvas size

GREEN_TOP = (24, 90, 52)   # #185a34 (lighter, top-lit)
GREEN_BOT = (11, 61, 32)   # #0b3d20 (deeper, bottom)
WHITE = (255, 255, 255, 255)
FLAG = (224, 126, 27)      # warm amber pennant
FLAG_DK = (183, 96, 15)    # shading edge


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make():
    img = Image.new("RGBA", (D, D), (0, 0, 0, 0))

    # circular mask for the disc (leave a hair of transparent margin)
    mask = Image.new("L", (D, D), 0)
    mdraw = ImageDraw.Draw(mask)
    margin = int(D * 0.012)
    mdraw.ellipse([margin, margin, D - margin, D - margin], fill=255)

    # --- disc with vertical gradient -------------------------------------
    grad = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    gpx = grad.load()
    for y in range(D):
        t = (y / (D - 1)) ** 1.15   # deepen toward the base
        col = lerp(GREEN_TOP, GREEN_BOT, t)
        for x in range(D):
            gpx[x, y] = (col[0], col[1], col[2], 255)
    img.paste(grad, (0, 0), mask)

    # --- soft top highlight for depth ------------------------------------
    hi = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    hd.ellipse([D * 0.18, D * 0.06, D * 0.82, D * 0.6], fill=(255, 255, 255, 26))
    hi = hi.filter(ImageFilter.GaussianBlur(D * 0.04))
    img = Image.alpha_composite(
        img, Image.composite(hi, Image.new("RGBA", (D, D), (0, 0, 0, 0)), mask))

    draw = ImageDraw.Draw(img)

    # --- white outer ring ------------------------------------------------
    ring_w = int(D * 0.05)
    r0 = int(D * 0.045)
    draw.ellipse([r0, r0, D - r0, D - r0], outline=WHITE, width=ring_w)

    # --- putting-green contour (gentle arc) ------------------------------
    ground_w = max(2, int(D * 0.014))
    draw.arc([D * 0.12, D * 0.52, D * 0.88, D * 0.98],
             start=200, end=340, fill=(255, 255, 255, 235), width=ground_w)

    # --- hole ------------------------------------------------------------
    hx, hy = D * 0.40, D * 0.70
    hrw, hrh = D * 0.075, D * 0.032
    draw.ellipse([hx - hrw, hy - hrh, hx + hrw, hy + hrh],
                 fill=(9, 46, 25, 255), outline=(255, 255, 255, 235),
                 width=max(2, int(D * 0.008)))

    # --- flagpole --------------------------------------------------------
    pole_w = max(3, int(D * 0.016))
    pole_x = hx
    pole_top = D * 0.24
    draw.line([pole_x, pole_top, pole_x, hy - hrh * 0.2], fill=WHITE, width=pole_w)
    fr = pole_w * 0.9
    draw.ellipse([pole_x - fr, pole_top - fr, pole_x + fr, pole_top + fr], fill=WHITE)

    # --- pennant flag (waving triangle) ----------------------------------
    fx = pole_x + pole_w * 0.4
    fy = pole_top + D * 0.008
    draw.polygon([(fx, fy), (fx + D * 0.30, fy + D * 0.075), (fx, fy + D * 0.15)], fill=FLAG)
    draw.polygon([(fx, fy + D * 0.15), (fx + D * 0.30, fy + D * 0.075),
                  (fx + D * 0.16, fy + D * 0.115)], fill=FLAG_DK)

    # --- golf ball -------------------------------------------------------
    bx, by = D * 0.60, D * 0.74
    br = D * 0.055
    draw.ellipse([bx - br, by - br, bx + br, by + br], fill=WHITE)
    draw.ellipse([bx - br, by - br, bx + br, by + br],
                 outline=(210, 216, 210, 255), width=max(1, int(D * 0.004)))

    return img.resize((N, N), Image.LANCZOS)


def main():
    icon = make()
    icon.save(OUT_PNG, format="PNG")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon.save(OUT_ICO, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {OUT_PNG}")
    print(f"wrote {OUT_ICO} ({', '.join(str(s) for s in sizes)})")


if __name__ == "__main__":
    main()
