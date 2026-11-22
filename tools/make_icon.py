"""Generates assets/icons/app.ico (16-256 px) and app.png. Run: python tools/make_icon.py  (needs Pillow)."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "icons"
SIZE = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    # vertical gradient inside a rounded square
    grad = Image.new("RGBA", (SIZE, SIZE))
    top, bottom = (79, 140, 255), (52, 86, 214)
    px = grad.load()
    for y in range(SIZE):
        t = y / (SIZE - 1)
        colour = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        for x in range(SIZE):
            px[x, y] = colour
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, SIZE - 40, SIZE - 40), radius=210, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    # download arrow
    d.rectangle((452, 210, 572, 520), fill=white)
    d.polygon([(300, 480), (724, 480), (512, 720)], fill=white)
    # tray
    d.rounded_rectangle((262, 770, 762, 830), radius=30, fill=white)
    d.rectangle((262, 700, 322, 800), fill=white)
    d.rectangle((702, 700, 762, 800), fill=white)
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    image = draw()
    image.resize((512, 512), Image.LANCZOS).save(OUT / "app.png")
    image.save(OUT / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Wrote", OUT / "app.ico", "and", OUT / "app.png")
