"""Creates the illustrated sample portraits that ship with the project (no real people are shown).
    python scripts/make_portraits.py
Candidates -> app/static/people/<name>.jpg      Sample voters -> seed/voters/<username>.jpg"""
import random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
S = 4
W = 480

SKIN = ["#f1c7a0", "#e0a97c", "#c68a5d", "#a86d44", "#8a5636", "#f6d5b5"]
HAIR = ["#1b1410", "#2c1d14", "#4a3020", "#0e0e10", "#6b4a2b", "#3a3a3d"]
CLOTH = ["#2d4a3e", "#33415c", "#7a2e2e", "#4a3a6b", "#1f5f6b", "#8a6a1f", "#3b3b3b", "#a8483a"]
BG = [("#cfe3d0", "#7fa88a"), ("#d9dff0", "#8392c4"), ("#f0dcc8", "#c9966a"), ("#e4d3ea", "#9879ab"), ("#f3e8b8", "#c4a93e"), ("#d3e8ea", "#6aa0a8")]


def portrait(seed, female, path):
    rnd = random.Random(seed)
    im = Image.new("RGB", (W * S, W * S))
    d = ImageDraw.Draw(im)
    c1, c2 = rnd.choice(BG)
    for y in range(W * S):                       # vertical gradient
        t = y / (W * S)
        a = [int(int(c1[i:i + 2], 16) * (1 - t) + int(c2[i:i + 2], 16) * t) for i in (1, 3, 5)]
        d.line([(0, y), (W * S, y)], fill=tuple(a))
    for _ in range(rnd.randint(5, 9)):            # soft circles for texture / unique hashes
        r = rnd.randint(40, 150) * S
        x, y = rnd.randint(0, W) * S, rnd.randint(0, W) * S
        col = rnd.choice([c1, c2, "#ffffff"])
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).ellipse([x - r, y - r, x + r, y + r], fill=col + "30")
        im.paste(ov, (0, 0), ov)
        d = ImageDraw.Draw(im)
    skin, hair, cloth = rnd.choice(SKIN), rnd.choice(HAIR), rnd.choice(CLOTH)
    cx = W // 2 * S + rnd.randint(-12, 12) * S
    # long hair behind the head
    if female:
        d.rounded_rectangle([cx - 150 * S, 110 * S, cx + 150 * S, 400 * S], radius=110 * S, fill=hair)
    # shoulders
    d.ellipse([cx - 230 * S, 370 * S, cx + 230 * S, 640 * S], fill=cloth)
    d.polygon([(cx - 40 * S, 372 * S), (cx, 430 * S), (cx + 40 * S, 372 * S)], fill=skin)
    # neck + head
    d.rounded_rectangle([cx - 44 * S, 300 * S, cx + 44 * S, 395 * S], radius=30 * S, fill=skin)
    d.ellipse([cx - 108 * S, 110 * S, cx + 108 * S, 340 * S], fill=skin)
    # ears
    for sx in (-1, 1):
        d.ellipse([cx + sx * 108 * S - 14 * S, 215 * S, cx + sx * 108 * S + 14 * S, 262 * S], fill=skin)
    # hair top
    if female:
        d.pieslice([cx - 118 * S, 92 * S, cx + 118 * S, 300 * S], 180, 360, fill=hair)
        d.ellipse([cx - 112 * S, 118 * S, cx - 40 * S, 230 * S], fill=hair)
        d.ellipse([cx + 40 * S, 118 * S, cx + 112 * S, 230 * S], fill=hair)
        d.ellipse([cx - 84 * S, 128 * S, cx + 84 * S, 330 * S], fill=skin)
    else:
        d.pieslice([cx - 116 * S, 96 * S, cx + 116 * S, 290 * S], 180, 360, fill=hair)
        if rnd.random() < .5:
            d.rectangle([cx - 100 * S, 150 * S, cx + 100 * S, 175 * S], fill=hair)
    # face
    ey = 228 * S
    for sx in (-1, 1):
        ex = cx + sx * 44 * S
        d.ellipse([ex - 16 * S, ey - 10 * S, ex + 16 * S, ey + 10 * S], fill="#ffffff")
        d.ellipse([ex - 7 * S, ey - 7 * S, ex + 7 * S, ey + 7 * S], fill="#2a1a10")
        d.line([ex - 22 * S, ey - 28 * S, ex + 20 * S, ey - 32 * S + sx * 2 * S], fill=hair, width=6 * S)
    d.line([cx, 235 * S, cx - 6 * S, 270 * S, cx + 6 * S, 272 * S], fill="#00000030", width=3 * S)
    d.arc([cx - 36 * S, 262 * S, cx + 36 * S, 312 * S], 20, 160, fill="#7a3b30", width=5 * S)
    if rnd.random() < .35:                          # glasses
        for sx in (-1, 1):
            ex = cx + sx * 44 * S
            d.ellipse([ex - 26 * S, ey - 20 * S, ex + 26 * S, ey + 20 * S], outline="#222", width=4 * S)
        d.line([cx - 18 * S, ey, cx + 18 * S, ey], fill="#222", width=4 * S)
    if not female and rnd.random() < .4:            # light stubble
        d.arc([cx - 92 * S, 222 * S, cx + 92 * S, 337 * S], 20, 160, fill=tuple(int(int(skin[i:i + 2], 16) * .72) for i in (1, 3, 5)), width=8 * S)
    im = im.resize((W, W), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.4))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=90)


CANDS = [("arjun", 0), ("kavita", 1), ("rohan", 0), ("sneha", 1), ("vikram", 0), ("divya", 1)]
VOTERS = [("aarav01", 0), ("priya02", 1), ("karthik03", 0), ("ananya04", 1), ("farhan05", 0), ("lakshmi06", 1), ("rahul07", 0), ("neha08", 1)]

if __name__ == "__main__":
    for n, f in CANDS:
        portrait("cand-" + n, f, ROOT / "app" / "static" / "people" / f"{n}.jpg")
    for n, f in VOTERS:
        portrait("voter-" + n, f, ROOT / "seed" / "voters" / f"{n}.jpg")
    print("portraits written")
