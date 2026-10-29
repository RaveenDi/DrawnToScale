"""Draw a synthetic hand-drawn-looking Negombo sketch (5 real OSM landmark names).

Writes evals/fixtures/demo-sketch.png and demo-sketch.json (the layout the mock
SketchReader returns). Synthetic: positions are projected from OSM coordinates with
a uniform scale, small hand-wobble and a rough road; this is a plumbing fixture,
not evidence of sketch-reading performance.
"""
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "fixtures"
W, H = 1200, 900
# (sketch label as written by hand, OSM id it is meant to be, type) - positions looked up in the fixture
PICKS = [("Dutch Fort", "osm:way/196057979", "fort"),
         ("Arpico Super Market", "osm:way/581075989", "supermarket"),
         ("Maris Stella", "osm:node/1268340441", "school"),
         ("Bus Terminal", "osm:way/97913613", "bus_station"),
         ("St Sebastian Church", "osm:way/1472244684", "church")]


def font(size):
    for name in ("segoepr.ttf", "comic.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def wobble_line(draw, a, b, rng, width=5, fill=(70, 60, 50)):
    n = 14
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append((a[0] + (b[0] - a[0]) * t + rng.uniform(-3, 3), a[1] + (b[1] - a[1]) * t + rng.uniform(-3, 3)))
    draw.line(pts, fill=fill, width=width, joint="curve")


def main():
    rng = random.Random(7)
    cands = {c["id"]: c for c in json.loads((OUT / "negombo-candidates.json").read_text(encoding="utf-8"))["candidates"]}
    lon0 = sum(cands[p[1]]["longitude"] for p in PICKS) / len(PICKS)
    lat0 = sum(cands[p[1]]["latitude"] for p in PICKS) / len(PICKS)
    mx, my = 111320 * math.cos(math.radians(lat0)), 110574  # metres per degree
    scale = 0.44  # pixels per metre, uniform
    landmarks = []
    for i, (label, cid, kind) in enumerate(PICKS, 1):
        c = cands[cid]
        x = W / 2 + (c["longitude"] - lon0) * mx * scale + rng.uniform(-9, 9)
        y = H / 2 - (c["latitude"] - lat0) * my * scale + rng.uniform(-9, 9)
        landmarks.append({"id": f"p{i}", "label": label, "type": kind, "x": round(x / W, 4), "y": round(y / H, 4),
                          "evidence": f'Handwritten label "{label}" next to a drawn marker.'})
    paper = Image.new("RGB", (W, H), (240, 234, 217))
    px = paper.load()
    for _ in range(40000):
        px[rng.randrange(W), rng.randrange(H)] = (228, 221, 202)
    d = ImageDraw.Draw(paper)
    pts = [(l["x"] * W, l["y"] * H) for l in landmarks]
    for a, b in [(0, 1), (1, 2), (1, 3), (1, 4)]:  # rough connecting roads
        wobble_line(d, pts[a], pts[b], rng, width=9, fill=(176, 168, 150))
        wobble_line(d, pts[a], pts[b], rng, width=3, fill=(120, 110, 95))
    f, fs = font(34), font(26)
    for (x, y), l in zip(pts, landmarks):
        d.ellipse([x - 15, y - 15, x + 15, y + 15], outline=(60, 50, 40), width=5, fill=(250, 205, 90))
        d.text((x + 24, y - 20), l["label"], font=f, fill=(40, 40, 70))
    d.text((W - 110, 40), "N", font=font(48), fill=(60, 50, 40))
    d.line([(W - 88, 110), (W - 88, 70)], fill=(60, 50, 40), width=5)
    d.polygon([(W - 88, 62), (W - 100, 84), (W - 76, 84)], fill=(60, 50, 40))
    d.text((40, H - 60), "Negombo - from memory", font=fs, fill=(110, 100, 85))
    paper = paper.filter(ImageFilter.GaussianBlur(0.6))
    paper.save(OUT / "demo-sketch.png")
    (OUT / "demo-sketch.json").write_text(json.dumps({
        "synthetic": True, "image": {"width": W, "height": H}, "north_direction": "up",
        "intended_osm_ids": {f"p{i}": p[1] for i, p in enumerate(PICKS, 1)},
        "landmarks": landmarks}, indent=2), encoding="utf-8")
    print("wrote", OUT / "demo-sketch.png")


if __name__ == "__main__":
    main()
