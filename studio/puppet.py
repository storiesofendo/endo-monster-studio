#!/usr/bin/env python3
"""Puppet mode: every character is a fixed pack of images drawn ONCE from the
reference picture, so characters never drift between shots or episodes.

Pack pieces (bible/characters/<NAME>/raw/):
    body.png                      full figure, neutral face, mouth closed  (= neutral + closed)
    face_<expr>.png  x5           same drawing, only the eyes/brows/face changed
    mouth_<shape>.png x3          same drawing, only the mouth changed
Pack pieces are lined up against body.png and cut down to just what changed, so
at render time we stack: body -> face patch -> mouth patch.

    python3 studio/puppet.py prompts all            # write prompts.md for every character
    python3 studio/puppet.py generate PETUNIA       # draw pieces with the image service (needs reference.png)
    python3 studio/puppet.py placeholder all        # stand-in cartoon pieces for testing
    python3 studio/puppet.py pack all               # remove backgrounds, line up, write pack.json
    python3 studio/puppet.py lineup                 # bible/lineup.png: everyone side by side, every face
    python3 studio/puppet.py background puddle      # paint a background in the book style
    python3 studio/puppet.py build ep01             # render episodes/ep01/ep01.mp4
"""
import argparse
import base64
import io
import json
import math
import zlib
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (BGS, BIBLE, CHARS, EXPRESSIONS, MOUTHS, ep_dir, load_cast,  # noqa: E402
                    load_env, load_episode, need_key, slug)

W, H = 1280, 720
GROUND = 0.94          # feet line, fraction of frame height
LINE_GAP = 0.35        # silence between lines (s)
SCENE_PAD = 0.7        # silence at scene start/end (s)
TITLE_SECS = 3.0

FACE_PROMPTS = {
    "happy": "a big warm happy smile and cheerful, smiling eyes",
    "sad": "a sad face: eyebrows tilted up in the middle, droopy eyes, a small frown",
    "worried": "a worried face: worried eyebrows pinched together, wide nervous eyes, a wobbly little mouth",
    "surprised": "a surprised face: eyebrows raised high, eyes wide open, a small round open mouth",
    "hurting": "a face in tummy pain, kid-friendly: eyes squeezed half shut, scrunched eyebrows, a grimace",
    "blink": "the eyes gently closed mid-blink (eyelids down), eyebrows and mouth exactly as they are now",
}
MOUTH_PROMPTS = {
    "small": "slightly open, as if saying 'eh' (a small gap, teeth barely showing)",
    "open": "wide open, as if saying 'ah' (tongue visible)",
    "round": "a small round 'oo' shape, lips pushed forward",
}
BG_PROMPTS = {
    "puddle": "a quiet neighborhood sidewalk after the rain with big shiny puddles, soft clouds clearing, "
              "a picket fence and flowers, puddles in the foreground",
    "kitchen": "a cozy family kitchen in the evening, warm lamp light, a wooden table with two chairs, "
               "a fridge with children's drawings, a window with a sunset",
    "doctor": "a friendly children's doctor's office, an exam bed with paper cover, a poster of a smiling "
              "tummy, a plant, soft daylight, calm and welcoming, not clinical or scary",
}


def char_dir(name):
    return CHARS / slug(name)


def on_screen_names(cast):
    return [n for n, c in cast["characters"].items() if c.get("on_screen", True)]


def names_arg(arg, cast):
    if arg.lower() == "all":
        return on_screen_names(cast)
    name = arg.upper()
    if name not in cast["characters"]:
        sys.exit(f"Unknown character {name}")
    return [name]


def style_text():
    p = BIBLE / "style.md"
    if not p.exists():
        return ""
    look = p.read_text().split("## Look", 1)[-1].split("##", 1)[0]
    return " ".join(ln.strip("- ").strip() for ln in look.splitlines() if ln.strip())


# ---------------------------------------------------------------- prompts

def piece_prompts(name, cast):
    c = cast["characters"][name]
    style = style_text()
    keep = ("Keep EVERYTHING else pixel-identical: same body, pose, outfit, head position and tilt, size, "
            "framing, colors, outline thickness and painting style. Transparent background, no shadow, no text.")
    pieces = [{
        "piece": "body", "from": "reference",
        "prompt": (f"Redraw the character in the attached reference picture ({name}: {c.get('role', '')}) as the "
                   "exact same character - identical face shape, proportions, colors, hair, outfit and line style. "
                   "Full body, standing, facing the viewer, arms relaxed, the whole figure visible from head to toe "
                   "with a small margin, centered. Calm neutral expression, eyes open looking at the viewer, mouth "
                   f"gently closed. Plain transparent background, no shadow, no props, no text. Style: {style}"),
    }]
    for expr, desc in FACE_PROMPTS.items():
        pieces.append({"piece": f"face_{expr}", "from": "body",
                       "prompt": f"Edit this picture of {name}. Change ONLY the face to show {desc}. {keep}"})
    for shape, desc in MOUTH_PROMPTS.items():
        pieces.append({"piece": f"mouth_{shape}", "from": "body",
                       "prompt": (f"Edit this picture of {name}. Change ONLY the mouth so it is {desc}. The eyes "
                                  f"and eyebrows stay exactly as they are (neutral). {keep}")})
    return pieces


def cmd_prompts(args):
    cast = load_cast()
    for name in names_arg(args.char, cast):
        d = char_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        pieces = piece_prompts(name, cast)
        (d / "prompts.json").write_text(json.dumps(pieces, indent=2))
        md = [f"# {name} puppet pieces\n",
              "Reference picture: `reference.png` (put it in this folder).",
              "`body` is drawn from the reference; every other piece is an edit of `raw/body.png`.",
              "neutral face = body, closed mouth = body.\n"]
        for p in pieces:
            md.append(f"## raw/{p['piece']}.png  (from {p['from']})\n\n{p['prompt']}\n")
        (d / "prompts.md").write_text("\n".join(md))
        ref = "found" if (d / "reference.png").exists() else "MISSING - add reference.png"
        print(f"{name}: {len(pieces)} prompts -> {d / 'prompts.md'}  (reference: {ref})")


# ---------------------------------------------------------------- image service

def openai_image(prompt, refs, size, transparent):
    """OpenAI image model. refs = list of image paths (empty -> pure generation)."""
    import requests
    key = need_key("OPENAI_API_KEY")
    model = os.environ.get("IMAGE_MODEL", "gpt-image-1")
    data = {"model": model, "prompt": prompt, "size": size, "quality": "high", "n": "1"}
    if transparent:
        data["background"] = "transparent"
    if refs:
        data["input_fidelity"] = "high"
        files = [("image[]", (Path(r).name, open(r, "rb"), "image/png")) for r in refs]
        r = requests.post("https://api.openai.com/v1/images/edits", headers={"Authorization": f"Bearer {key}"},
                          data=data, files=files, timeout=600)
    else:
        r = requests.post("https://api.openai.com/v1/images/generations", timeout=600,
                          headers={"Authorization": f"Bearer {key}"}, json=data)
    if r.status_code >= 400:
        sys.exit(f"Image service error ({r.status_code}): {r.text[:500]}")
    return Image.open(io.BytesIO(base64.b64decode(r.json()["data"][0]["b64_json"])))


def to_png(path):
    """Image models want PNG; convert reference.jpg etc."""
    path = Path(path)
    if path.suffix.lower() == ".png":
        return path
    out = path.with_suffix(".png")
    Image.open(path).convert("RGBA").save(out)
    return out


def find_reference(d):
    for ext in ("png", "jpg", "jpeg", "webp"):
        if (d / f"reference.{ext}").exists():
            return to_png(d / f"reference.{ext}")
    return None


def cmd_generate(args):
    load_env()
    cast = load_cast()
    for name in names_arg(args.char, cast):
        d = char_dir(name)
        ref = find_reference(d)
        if not ref:
            sys.exit(f"{name}: put the reference picture at {d}/reference.png first")
        (d / "raw").mkdir(parents=True, exist_ok=True)
        for p in piece_prompts(name, cast):
            out = d / "raw" / f"{p['piece']}.png"
            if args.only and p["piece"] not in args.only:
                continue
            if out.exists() and not args.force:
                print(f"  {name}/{p['piece']}: exists (use --force to redraw)")
                continue
            if p["from"] == "body" and not (d / "raw" / "body.png").exists():
                sys.exit(f"{name}: body.png is missing; generate it first")
            if p["piece"] == "body" and not args.redraw_body:
                # Your drawn front view IS the body: zero drift. (--redraw-body asks the image service instead.)
                Image.open(ref).convert("RGB").save(out)
                print(f"  {name}/body: using your reference drawing as the body")
                continue
            src = [ref] if p["from"] == "reference" else [d / "raw" / "body.png"]
            print(f"  {name}/{p['piece']}: drawing...", flush=True)
            openai_image(p["prompt"], src, "1024x1536", transparent=True).save(out)
        print(f"{name}: pieces in {d / 'raw'}. Next: puppet.py pack {name}")


def cmd_background(args):
    load_env()
    BGS.mkdir(parents=True, exist_ok=True)
    desc = args.prompt or BG_PROMPTS.get(args.scene)
    if not desc:
        sys.exit(f"No default description for '{args.scene}'; pass --prompt \"...\"")
    prompt = (f"A background painting for a children's animated picture-book story: {desc}. "
              f"Style: {style_text()} No people, no animals, no characters, no text. Wide landscape framing at "
              "child eye level; leave open, uncluttered floor space across the lower middle where characters "
              "will stand, with the ground line near the bottom tenth of the picture.")
    refs = [p for p in [BIBLE / "style_reference.png"] if p.exists()]
    if refs:
        prompt += " Match the painting technique, palette and texture of the attached book page exactly."
    out = BGS / f"{args.scene}.png"
    openai_image(prompt, refs, "1536x1024", transparent=False).convert("RGB").save(out)
    print(f"Wrote {out}")


# ---------------------------------------------------------------- placeholder pieces (for testing)

def draw_placeholder(name, face="neutral", mouth="closed", jitter=(0, 0, 1.0)):
    """Simple stand-in cartoon on a white page, so the whole pipeline can be tested."""
    rng = zlib.crc32(name.encode()) % 360
    S = 4  # supersample
    w, h = 600, 1000
    img = Image.new("RGB", (w * S, h * S), "white")
    d = ImageDraw.Draw(img)
    col = {"PETUNIA": (236, 128, 160), "FRIEND": (110, 180, 220), "MOM": (170, 130, 210),
           "ENDO-MONSTER": (120, 190, 90)}.get(name, ImageColor_hsv(rng))
    dark = tuple(int(c * 0.35) for c in col)
    lw = 7 * S

    def e(box, **kw):
        d.ellipse([v * S for v in box], **kw)

    monster = name == "ENDO-MONSTER"
    if monster:
        d.polygon([(200 * S, 330 * S), (170 * S, 200 * S), (260 * S, 300 * S)], fill=col, outline=dark, width=lw)
        d.polygon([(400 * S, 330 * S), (430 * S, 200 * S), (340 * S, 300 * S)], fill=col, outline=dark, width=lw)
        e((120, 260, 480, 900), fill=col, outline=dark, width=lw)
        for x in (190, 300, 410):
            e((x - 30, 870, x + 30, 960), fill=col, outline=dark, width=lw)
        head_c = (300, 470)
    else:
        e((180, 420, 420, 900), fill=col, outline=dark, width=lw)          # body
        d.rectangle([230 * S, 860 * S, 270 * S, 970 * S], fill=dark)         # legs
        d.rectangle([330 * S, 860 * S, 370 * S, 970 * S], fill=dark)
        e((150, 100, 450, 420), fill=(250, 220, 195), outline=dark, width=lw)  # head
        d.chord([140 * S, 80 * S, 460 * S, 330 * S], 180, 360, fill=col, outline=dark, width=lw)  # hair
        head_c = (300, 300)
    cx, cy = head_c
    # eyes + brows by expression
    eye_h = {"surprised": 42, "hurting": 8, "happy": 22}.get(face, 30)
    for sx in (-60, 60):
        if face == "blink":
            d.line([((cx + sx - 22) * S, (cy - 30) * S), ((cx + sx + 22) * S, (cy - 30) * S)], fill=dark, width=lw)
        elif face == "happy":
            d.arc([(cx + sx - 26) * S, (cy - 50) * S, (cx + sx + 26) * S, (cy - 10) * S], 200, 340, fill=dark, width=lw)
        else:
            e((cx + sx - 22, cy - 30 - eye_h / 2, cx + sx + 22, cy - 30 + eye_h / 2), fill="white", outline=dark, width=lw // 2)
            e((cx + sx - 9, cy - 39, cx + sx + 9, cy - 21), fill=dark)
        tilt = {"sad": 14, "worried": 12, "surprised": -6, "hurting": -14}.get(face, 0) * (1 if sx < 0 else -1)
        lift = {"surprised": -18}.get(face, 0)
        d.line([((cx + sx - 30) * S, (cy - 72 + lift + tilt) * S), ((cx + sx + 30) * S, (cy - 72 + lift - tilt) * S)],
               fill=dark, width=lw)
    # mouth: face expression mouth unless a speaking shape is given
    my = cy + 60
    if mouth == "closed":
        if face == "happy":
            d.arc([(cx - 50) * S, (my - 40) * S, (cx + 50) * S, (my + 20) * S], 20, 160, fill=dark, width=lw)
        elif face in ("sad", "hurting"):
            d.arc([(cx - 40) * S, (my) * S, (cx + 40) * S, (my + 40) * S], 200, 340, fill=dark, width=lw)
        elif face == "surprised":
            e((cx - 16, my - 16, cx + 16, my + 16), fill=(120, 30, 40), outline=dark, width=lw // 2)
        elif face == "worried":
            d.line([((cx - 30) * S, my * S), ((cx - 10) * S, (my - 6) * S), ((cx + 10) * S, (my + 4) * S),
                    ((cx + 30) * S, (my - 4) * S)], fill=dark, width=lw)
        else:
            d.line([((cx - 30) * S, my * S), ((cx + 30) * S, my * S)], fill=dark, width=lw)
    else:
        mw, mh = {"small": (30, 12), "open": (36, 34), "round": (18, 22)}[mouth]
        e((cx - mw, my - mh, cx + mw, my + mh), fill=(120, 30, 40), outline=dark, width=lw // 2)
    img = img.resize((w, h), Image.LANCZOS)
    dx, dy, sc = jitter
    if sc != 1.0 or dx or dy:  # imitate an image model that nudges the drawing a little
        big = img.resize((int(w * sc), int(h * sc)), Image.LANCZOS)
        page = Image.new("RGB", (w, h), "white")
        page.paste(big, (int((w - big.width) / 2 + dx), int((h - big.height) / 2 + dy)))
        img = page
    return img


def ImageColor_hsv(hue):
    import colorsys
    return tuple(int(v * 255) for v in colorsys.hsv_to_rgb(hue / 360, 0.45, 0.85))


def cmd_placeholder(args):
    cast = load_cast()
    for name in names_arg(args.char, cast):
        raw = char_dir(name) / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        draw_placeholder(name).save(raw / "body.png")
        for i, expr in enumerate([e for e in EXPRESSIONS if e != "neutral"] + ["blink"]):
            draw_placeholder(name, face=expr, jitter=(5 - i * 3, i * 2 - 4, 1.0 + 0.008 * (i - 2))).save(raw / f"face_{expr}.png")
        for i, m in enumerate(x for x in MOUTHS if x != "closed"):
            draw_placeholder(name, mouth=m, jitter=(i * 3 - 3, 4 - i * 3, 1.0)).save(raw / f"mouth_{m}.png")
        print(f"{name}: placeholder pieces in {raw}")


# ---------------------------------------------------------------- pack: clean, line up, cut patches

def remove_background(img, tol=38):
    """Return RGBA. Keeps real transparency; otherwise removes the paper colour touching the border,
    the soft ground smudge drawn under the feet, and stray marks (sparkles, labels) not touching the figure."""
    img = img.convert("RGBA")
    a = np.asarray(img)
    if (a[..., 3] < 250).mean() > 0.02:
        return keep_main_figure(img)
    rgb = a[..., :3].astype(int)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    near = np.abs(rgb - bg).sum(axis=2) < tol
    hgt, wid = near.shape
    # ground smudge: the pale lavender patch painted under the feet (bottom 15% only)
    band = np.zeros_like(near)
    band[int(hgt * 0.85):] = True
    r_, g_, b_ = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lavender = (b_ > g_ + 12) & (r_ > g_ - 5) & (rgb.mean(axis=2) > 135)
    near |= band & lavender
    m = Image.fromarray(near.astype(np.uint8) * 255, "L").copy()  # copy: Pillow won't flood-fill array-backed images
    for x in range(0, wid, max(1, wid // 40)):
        for y in (0, hgt - 1):
            if m.getpixel((x, y)) == 255:
                ImageDraw.floodfill(m, (x, y), 128)
    for y in range(0, hgt, max(1, hgt // 40)):
        for x in (0, wid - 1):
            if m.getpixel((x, y)) == 255:
                ImageDraw.floodfill(m, (x, y), 128)
    clear = np.asarray(m) == 128
    # enclosed paper (between legs, inside a pigtail loop): a tight colour match, so cream clothes stay
    paper = (np.abs(rgb - bg).sum(axis=2) < 30) & ~clear
    f = 3
    sm = np.asarray(Image.fromarray(paper.astype(np.uint8) * 255).resize((wid // f, hgt // f), Image.BOX)) > 200
    for area, blob in components(sm, min_area=40):
        full = np.asarray(Image.fromarray(blob.astype(np.uint8) * 255).resize((wid, hgt), Image.NEAREST)) > 0
        region = rgb[full & paper]
        if len(region) and region.std(axis=0).mean() < 9:      # flat paper, not textured cloth
            clear |= np.asarray(Image.fromarray((full & paper).astype(np.uint8) * 255)
                                .filter(ImageFilter.MaxFilter(3))) > 0
    alpha = Image.fromarray(np.where(clear, 0, 255).astype(np.uint8), "L")
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    img.putalpha(alpha)
    return keep_main_figure(img)


def keep_main_figure(img):
    """Drop small separate blobs (sparkle marks, label letters) that are not part of the figure."""
    al = np.asarray(img.getchannel("A")) > 40
    f = 4
    small = np.asarray(Image.fromarray(al.astype(np.uint8) * 255).resize(
        (max(1, al.shape[1] // f), max(1, al.shape[0] // f)), Image.BOX)) > 60
    blobs = components(small, min_area=1)
    if len(blobs) < 2:
        return img
    biggest = blobs[0][0]
    keep = np.any([b for area, b in blobs if area >= biggest * 0.05], axis=0)
    keep = np.asarray(Image.fromarray(keep.astype(np.uint8) * 255).resize(img.size, Image.NEAREST)
                      .filter(ImageFilter.MaxFilter(2 * f + 1)))
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), Image.fromarray(keep)))
    return out


def alpha_bbox(img):
    return img.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()


def phase_shift(a, b):
    """Integer (dy, dx) that best moves b onto a."""
    fa, fb = np.fft.fft2(a), np.fft.fft2(b)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    corr = np.fft.ifft2(r).real
    dy, dx = np.unravel_index(np.argmax(corr), corr.shape)
    if dy > a.shape[0] // 2:
        dy -= a.shape[0]
    if dx > a.shape[1] // 2:
        dx -= a.shape[1]
    return int(dy), int(dx)


def align_to(body, piece, body_box):
    """Scale/translate piece so its figure sits exactly on body's figure. Returns RGBA same size as body."""
    pbox = alpha_bbox(piece)
    crop = piece.crop(pbox)
    bw, bh = body_box[2] - body_box[0], body_box[3] - body_box[1]
    # match height (the figure's height is the most stable measure), keep aspect
    scale = bh / crop.height
    crop = crop.resize((max(1, round(crop.width * scale)), bh), Image.LANCZOS)
    out = Image.new("RGBA", body.size, (0, 0, 0, 0))
    cx = body_box[0] + bw / 2
    out.paste(crop, (round(cx - crop.width / 2), body_box[1]), crop)
    # refine with phase correlation on line art (greyscale * alpha)
    def lum(im):
        a = np.asarray(im.convert("RGBA")).astype(float)
        return (255 - a[..., :3].mean(axis=2)) * (a[..., 3] / 255)
    dy, dx = phase_shift(lum(body), lum(out))
    lim = 0.06 * bh
    if abs(dy) <= lim and abs(dx) <= lim and (dx or dy):
        out = ImageChops.offset(out, dx, dy)
    return out


def components(mask, min_area):
    """Label 4-connected blobs in a small boolean array. Returns list of (area, bool-array)."""
    hgt, wid = mask.shape
    seen = np.zeros_like(mask, bool)
    blobs = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if seen[y0, x0]:
            continue
        stack, pts = [(y0, x0)], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            pts.append((y, x))
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < hgt and 0 <= nx < wid and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(pts) >= min_area:
            b = np.zeros_like(mask, bool)
            ys, xs = zip(*pts)
            b[list(ys), list(xs)] = True
            blobs.append((len(pts), b))
    return sorted(blobs, key=lambda t: -t[0])


def change_patch(body, piece, region, largest_only):
    """Cut the part of `piece` that differs from `body` inside `region` (x0,y0,x1,y1), feathered."""
    a = np.asarray(body).astype(int)
    b = np.asarray(piece).astype(int)
    diff = np.abs(a[..., :3] * (a[..., 3:] / 255) - b[..., :3] * (b[..., 3:] / 255)).sum(axis=2)
    diff += np.abs(a[..., 3] - b[..., 3])
    m = diff > 60
    keep = np.zeros_like(m)
    x0, y0, x1, y1 = region
    keep[y0:y1, x0:x1] = True
    m &= keep
    # blob filtering at quarter resolution
    small = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((m.shape[1] // 4, m.shape[0] // 4),
                                                                         Image.BOX)) > 40
    small = np.asarray(Image.fromarray(small.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0
    blobs = components(small, min_area=6)
    if not blobs:
        return None, None
    chosen = blobs[:1] if largest_only else blobs
    sm = np.any([bl for _, bl in chosen], axis=0)
    mask = Image.fromarray(sm.astype(np.uint8) * 255).resize(body.size, Image.NEAREST)
    mask = Image.fromarray((np.asarray(mask) > 0).astype(np.uint8) * 255 & keep.astype(np.uint8) * 255)
    mask = mask.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(4))
    patch = piece.copy()
    patch.putalpha(ImageChops.multiply(piece.getchannel("A"), mask))
    return patch, mask.getbbox()


def cmd_pack(args):
    cast = load_cast()
    for name in names_arg(args.char, cast):
        d = char_dir(name)
        raw, out = d / "raw", d / "pack"
        if not (raw / "body.png").exists():
            sys.exit(f"{name}: {raw}/body.png missing (run generate or placeholder)")
        out.mkdir(exist_ok=True)
        old = json.loads((out / "pack.json").read_text()) if (out / "pack.json").exists() else {}
        body = remove_background(Image.open(raw / "body.png"))
        box = alpha_bbox(body)
        m = 12
        body = body.crop((max(0, box[0] - m), max(0, box[1] - m), min(body.width, box[2] + m), min(body.height, box[3] + m)))
        if body.height > 1100:  # keep packs a sensible size
            body = body.resize((round(body.width * 1100 / body.height), 1100), Image.LANCZOS)
        box = alpha_bbox(body)
        fh = box[3] - box[1]
        head = old.get("head_box") or [box[0], box[1], box[2], box[1] + round(fh * float(cast["characters"][name].get("head_fraction", 0.45)))]
        body.save(out / "body.png")
        pack = {"name": name, "canvas": list(body.size), "figure_box": list(box), "head_box": head,
                "faces": {"neutral": None}, "mouths": {"closed": None}, "notes": []}
        mouth_boxes = []
        for expr in EXPRESSIONS[1:] + ["blink"]:
            p = raw / f"face_{expr}.png"
            if not p.exists():
                pack["notes"].append(f"face_{expr} missing (uses neutral)")
                continue
            piece = align_to(body, remove_background(Image.open(p)), box)
            patch, pb = change_patch(body, piece, head, largest_only=False)
            if patch is None:
                pack["notes"].append(f"face_{expr}: no change found inside head_box")
                continue
            patch.save(out / f"face_{expr}.png")
            pack["faces"][expr] = f"face_{expr}.png"
        for shape in MOUTHS[1:]:
            p = raw / f"mouth_{shape}.png"
            if not p.exists():
                pack["notes"].append(f"mouth_{shape} missing (uses closed)")
                continue
            piece = align_to(body, remove_background(Image.open(p)), box)
            patch, pb = change_patch(body, piece, head, largest_only=True)
            if patch is None:
                pack["notes"].append(f"mouth_{shape}: no change found inside head_box")
                continue
            patch.save(out / f"mouth_{shape}.png")
            pack["mouths"][shape] = f"mouth_{shape}.png"
            mouth_boxes.append(pb)
        if mouth_boxes:
            pack["mouth_box"] = [min(b[0] for b in mouth_boxes), min(b[1] for b in mouth_boxes),
                                 max(b[2] for b in mouth_boxes), max(b[3] for b in mouth_boxes)]
        (out / "pack.json").write_text(json.dumps(pack, indent=2))
        sheet = contact_sheet(name)
        sheet.save(d / "sheet.png")
        print(f"{name}: pack ready ({len([v for v in pack['faces'].values()])} faces, "
              f"{len(pack['mouths'])} mouths) -> {out}  sheet: {d / 'sheet.png'}")
        for n in pack["notes"]:
            print(f"   note: {n}")


# ---------------------------------------------------------------- loading packs + compositing

class Puppet:
    def __init__(self, name):
        self.name = name
        d = char_dir(name) / "pack"
        if not (d / "pack.json").exists():
            raise FileNotFoundError(f"{name} has no pack. Run: puppet.py pack {name}  (or placeholder {name} first)")
        self.meta = json.loads((d / "pack.json").read_text())
        self.body = Image.open(d / "body.png").convert("RGBA")
        self.faces = {k: Image.open(d / v).convert("RGBA") for k, v in self.meta["faces"].items() if v}
        self.mouths = {k: Image.open(d / v).convert("RGBA") for k, v in self.meta["mouths"].items() if v}
        self.box = self.meta["figure_box"]
        self._cache = {}

    def frame(self, expr="neutral", mouth=None):
        key = (expr, mouth)
        if key not in self._cache:
            im = self.body.copy()
            if expr in self.faces:
                im.alpha_composite(self.faces[expr])
            if mouth in self.mouths:
                im.alpha_composite(self.mouths[mouth])
            self._cache[key] = im
        return self._cache[key]

    def scaled(self, expr, mouth, height_px):
        key = (expr, mouth, height_px)
        if key not in self._cache:
            im = self.frame(expr, mouth)
            fig_h = self.box[3] - self.box[1]
            s = height_px / fig_h
            self._cache[key] = (im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS), s)
        return self._cache[key]


def font(size, bold=True):
    for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def contact_sheet(name):
    p = Puppet(name)
    cell = 260
    rows = [[(e, None) for e in EXPRESSIONS], [("neutral", m) for m in MOUTHS]]
    sheet = Image.new("RGB", (cell * 6, (cell + 30) * 2 + 50), (245, 240, 235))
    d = ImageDraw.Draw(sheet)
    d.text((10, 10), name, fill=(60, 40, 50), font=font(28))
    hb = p.meta["head_box"]
    for r, row in enumerate(rows):
        for c, (e, m) in enumerate(row):
            im = p.frame(e, m).crop(hb)
            im.thumbnail((cell - 20, cell - 20))
            x, y = c * cell + (cell - im.width) // 2, 50 + r * (cell + 30) + 10
            sheet.paste(im, (x, y), im)
            d.text((c * cell + 10, 50 + r * (cell + 30) + cell - 5), m or e, fill=(80, 60, 70), font=font(18, False))
    return sheet


def cmd_lineup(args):
    cast = load_cast()
    names = [n for n in on_screen_names(cast) if (char_dir(n) / "pack" / "pack.json").exists()]
    if not names:
        sys.exit("No packs yet.")
    puppets = [Puppet(n) for n in names]
    tall = 560
    bg = Image.new("RGB", (len(names) * 330 + 40, tall + 90 + 6 * 0), (250, 246, 240))
    d = ImageDraw.Draw(bg)
    for i, p in enumerate(puppets):
        hpx = round(tall * cast["characters"][p.name].get("height", 0.6) / 0.86)
        im, s = p.scaled("neutral", None, hpx)
        x = 40 + i * 330 + 145 - im.width // 2
        y = tall + 20 - round(p.box[3] * s)
        bg.paste(im, (x, y), im)
        d.text((40 + i * 330 + 145, tall + 50), p.name, fill=(70, 40, 60), font=font(24), anchor="mm")
    sheets = [contact_sheet(n) for n in names]
    full = Image.new("RGB", (max(bg.width, sheets[0].width), bg.height + sum(s.height for s in sheets)), (250, 246, 240))
    full.paste(bg, (0, 0))
    y = bg.height
    for s in sheets:
        full.paste(s, (0, y))
        y += s.height
    out = BIBLE / "lineup.png"
    full.save(out)
    print(f"Wrote {out}")


# ---------------------------------------------------------------- lip sync

VOWEL_OPEN = set("aeiæáàâäãéèêëíìîï")
VOWEL_ROUND = set("ouwóòôöõúùûüq")
LIPS_SHUT = set("mbp")


def viseme(ch):
    ch = ch.lower()
    if ch in LIPS_SHUT or not ch.isalpha():
        return "closed"
    if ch in VOWEL_OPEN:
        return "open"
    if ch in VOWEL_ROUND:
        return "round"
    return "small"


def mouth_track(meta, fps, min_hold):
    """Per-frame mouth shapes for one line, from ElevenLabs character timestamps."""
    al = meta["alignment"]
    n = max(1, math.ceil(meta["duration"] * fps))
    chars, starts, ends = al["chars"], al["starts"], al["ends"]
    track, j = [], 0
    for f in range(n):
        t = (f + 0.5) / fps
        while j < len(chars) - 1 and ends[j] <= t:
            j += 1
        if not chars or t < starts[0] or t > ends[-1]:
            track.append("closed")
        else:
            track.append(viseme(chars[j]))
    # hold each shape at least `min_hold` frames, so the mouth doesn't flicker
    out, held = [], 0
    for s in track:
        if out and s != out[-1] and held < min_hold:
            out.append(out[-1]); held += 1
        else:
            held = held + 1 if out and s == out[-1] else 1
            out.append(s)
    # a closed blip of one frame between two open shapes reads as chatter; smooth it
    for i in range(1, len(out) - 1):
        if out[i] == "closed" and out[i - 1] != "closed" and out[i + 1] != "closed":
            out[i] = "small"
    return out


# ---------------------------------------------------------------- build

def load_background(name):
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = BGS / f"{name}.{ext}"
        if p.exists():
            return ImageOps.fit(Image.open(p).convert("RGB"), (W * 2, H * 2), Image.LANCZOS)
    print(f"  warning: no background bible/backgrounds/{name}.png - using a plain stand-in")
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    top, bot = {"puddle": ((170, 205, 230), (150, 190, 140)), "kitchen": ((245, 220, 180), (200, 160, 120)),
                "doctor": ((215, 235, 235), (190, 210, 215))}.get(name, ((220, 220, 230), (200, 200, 190)))
    for y in range(H):
        t = y / H
        c = top if t < 0.7 else bot
        d.line([(0, y), (W, y)], fill=tuple(int(v * (1 - 0.12 * t)) for v in c))
    d.text((W - 20, 20), f"[{name}]", fill=(90, 90, 90), font=font(22, False), anchor="ra")
    return img.resize((W * 2, H * 2), Image.BILINEAR)


def decode_audio(path, sr=44100):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16)


MOVE_SECS = {"enter": 1.2, "exit": 1.1, "sneak": 2.4, "peek": 2.4, "hop": 0.55, "shake": 0.8}
BLOCKING = {"enter": 0.8, "exit": 0.6, "sneak": 0.85, "peek": 0.9, "hop": 0.0, "shake": 0.0}


def plan_episode(ep, script, cast, fps):
    """Lay every line and motion on a timeline. Returns (shots, total_seconds)."""
    audio_dir = ep_dir(ep) / "audio"
    t = TITLE_SECS
    shots = []
    for s in script["scenes"]:
        shot = {"bg": s["bg"], "stage": s["stage"], "start": t, "events": []}
        t += SCENE_PAD
        for e in s["events"]:
            if e["type"] == "face":
                shot["events"].append({**e, "t": t})
                continue
            if e["type"] == "move":
                dur = MOVE_SECS[e["action"]]
                side = e["side"] or ("left" if s["stage"][e["char"]] < 0.5 else "right")
                shot["events"].append({**e, "side": side, "t": t, "dur": dur})
                t += dur * BLOCKING[e["action"]]
                continue
            meta_p = audio_dir / f"{e['id']:03d}.json"
            if not meta_p.exists():
                sys.exit(f"Line {e['id']} has no audio. Run: studio.py voices {ep}")
            meta = json.loads(meta_p.read_text())
            if meta["text"] != e["text"]:
                sys.exit(f"Line {e['id']} audio is out of date (script changed). Run: studio.py voices {ep}")
            shot["events"].append({**e, "t": t, "dur": meta["duration"], "audio": audio_dir / f"{e['id']:03d}.mp3",
                                   "mouths": mouth_track(meta, fps, cast.get("lip_sync", {}).get("min_hold_frames", 2)),
                                   "offline": meta.get("offline")})
            t += meta["duration"] + LINE_GAP
        t += SCENE_PAD - LINE_GAP
        shot["end"] = t
        # camera: wide to open each scene, then close-ups on whoever talks, wide again every few lines
        n = 0
        for e in shot["events"]:
            if e["type"] == "line":
                on_stage = e["char"] in shot["stage"]
                e["shot"] = "close" if on_stage and len(shot["stage"]) > 1 and n % 3 != 0 and e["dur"] > 1.4 else "wide"
                n += 1
        shots.append(shot)
    return shots, t + 0.5


def ease(p):
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def blink_now(name, t):
    h = zlib.crc32(name.encode())
    period = 2.7 + (h % 19) / 10
    return (t + (h % 97) / 31) % period < 0.13


def state_at(shot, t, fps):
    """Faces, who is talking (and mouth shape), and each character's motion at time t."""
    faces = {c: "neutral" for c in shot["stage"]}
    talking, mouth, line = None, None, None
    for e in shot["events"]:
        if e["t"] > t:
            break
        if e["type"] == "face" or e.get("expr"):
            faces[e["char"]] = e["expr"]
        if e["type"] == "line" and t < e["t"] + e["dur"]:
            talking, line = e["char"], e
            f = int((t - e["t"]) * fps)
            mouth = e["mouths"][min(f, len(e["mouths"]) - 1)]
    motion = {}
    for name, x in shot["stage"].items():
        moves = [e for e in shot["events"] if e["type"] == "move" and e["char"] == name]
        first = moves[0]["action"] if moves else None
        st = {"x": x * W, "present": first not in ("enter", "sneak", "peek"), "dy": 0.0, "dx": 0.0,
              "squash": 1.0, "lean": 0.0, "walk": None}
        for e in moves:
            if e["t"] > t:
                break
            p = (t - e["t"]) / e["dur"]
            off = -0.18 * W if e["side"] == "left" else 1.18 * W
            home = x * W
            if e["action"] in ("enter", "sneak"):
                st["present"] = True
                if p < 1:
                    st["x"] = off + (home - off) * ease(p)
                    st["walk"] = e["action"]
            elif e["action"] == "exit":
                if p < 1:
                    st["x"] = home + (off - home) * ease(p)
                    st["walk"] = "enter"
                else:
                    st["present"] = False
            elif e["action"] == "peek":
                edge = 0.10 * W if e["side"] == "left" else 0.90 * W
                if p < 1:
                    st["present"] = True
                    k = ease(p / 0.3) if p < 0.3 else (1.0 if p < 0.7 else 1 - ease((p - 0.7) / 0.3))
                    st["x"] = off + (edge - off) * k
                    st["walk"] = "sneak" if (p < 0.3 or p > 0.7) else None
                else:
                    st["present"] = False
            elif e["action"] == "hop" and p < 1:
                st["dy"] -= math.sin(math.pi * min(p / 0.85, 1)) * 0.07 * H
                if p > 0.85:
                    st["squash"] = 0.9
            elif e["action"] == "shake" and p < 1:
                st["dx"] += math.sin(t * 55) * 5 * (1 - p)
        motion[name] = st
    return faces, talking, mouth, line, motion


def camera_at(shot, t, line, motion, cast):
    """(zoom, centre x, centre y) in frame coordinates."""
    moving = any(m["walk"] for m in motion.values())
    if line is None or moving or line.get("shot") != "close" or line["char"] not in motion:
        return 1.0, W / 2, H / 2
    p = (t - line["t"]) / max(line["dur"], 0.1)
    z = 1.42 * (1 + 0.035 * ease(p))  # gentle push-in while they talk
    hpx = H * cast["characters"][line["char"]].get("height", 0.6)
    cx = motion[line["char"]]["x"]
    cy = H * GROUND - hpx * 0.72
    return z, cx, cy


def lean_sprite(im, s, box, lean_deg, squash):
    """Shear (lean) and squash a sprite around its feet. Returns (image, dx, dy) paste offsets."""
    if abs(lean_deg) < 0.05 and abs(squash - 1) < 0.002:
        return im, 0, 0
    k = math.tan(math.radians(lean_deg))
    sy, sx = squash, 1 / math.sqrt(squash)
    cx = (box[0] + box[2]) / 2 * s
    by = box[3] * s
    m = int(abs(k) * im.height) + 4
    mt = int(max(0, (sy - 1)) * im.height) + 2
    w2, h2 = im.width + 2 * m, im.height + mt
    a, b_ = 1 / sx, k / sx
    c = cx - (m + cx) / sx - k * (by + mt) / sx
    e_, f = 1 / sy, by - (mt + by) / sy
    out = im.transform((w2, h2), Image.AFFINE, (a, b_, c, 0, e_, f), resample=Image.BICUBIC)
    return out, -m, -mt


def render_frame(shot, t, fps, bg, puppets, cast, captions):
    """bg is the high-res background (2x). Characters are drawn at camera scale so close-ups stay sharp."""
    faces, talking, mouth, line, motion = state_at(shot, t, fps)
    z, ccx, ccy = camera_at(shot, t, line, motion, cast)
    vw, vh = W / z, H / z
    x0 = min(max(ccx - vw / 2, 0), W - vw)
    y0 = min(max(ccy - vh / 2, 0), H - vh)
    k = bg.width / W
    frame = bg.crop((round(x0 * k), round(y0 * k), round((x0 + vw) * k), round((y0 + vh) * k))).resize((W, H), Image.BILINEAR)
    zq = round(z * 50) / 50
    present = [n for n, m in motion.items() if m["present"]]
    for name in sorted(present, key=lambda n: cast["characters"][n].get("height", 0.6)):
        if name not in puppets:
            continue
        p, st = puppets[name], motion[name]
        expr = faces.get(name, "neutral")
        m = mouth if name == talking and mouth != "closed" else None
        if m is None and expr == "neutral" and "blink" in p.faces and blink_now(name, t) and not st["walk"]:
            expr = "blink"
        hpx = round(H * cast["characters"][name].get("height", 0.6) * zq)
        if len(p._cache) > 400:
            p._cache.clear()
        im, s = p.scaled(expr, m, hpx)
        phase = (zlib.crc32(name.encode()) % 100) / 100 * 6.28
        lean, squash, dy = 0.0, st["squash"], st["dy"]
        if st["walk"] == "sneak":          # tiptoe: crouched, leaning forward, slow sneaky steps
            squash *= 0.93
            dy -= abs(math.sin(t * 5.0 + phase)) * 7
            lean = 7 if st["x"] < shot["stage"][name] * W else -7
        elif st["walk"]:                   # bouncy walk
            dy -= abs(math.sin(t * 9.0 + phase)) * 11
            lean = 3 if st["x"] < shot["stage"][name] * W else -3
        elif name == talking:              # lean toward whoever they are talking to, bounce on open vowels
            others = [motion[o]["x"] for o in present if o != name]
            target = sum(others) / len(others) if others else W / 2
            lean = 2.2 * (1 if target > st["x"] else -1) * (0.6 + 0.4 * math.sin(t * 2.1 + phase))
            dy -= 4 if m == "open" else (2 if m else 0)
            squash *= 1.012 if m == "open" else 1.0
        else:                              # breathing
            squash *= 1 + 0.006 * math.sin(t * 1.7 + phase)
            lean = 0.6 * math.sin(t * 0.7 + phase)
        im2, ox, oy = lean_sprite(im, s, p.box, lean, squash)
        fx0, fx1 = p.box[0] * s, p.box[2] * s
        sx_ = (st["x"] + st["dx"] - x0) * z
        feet = (H * GROUND - y0) * z
        # soft contact shadow so they stand on the ground
        sh_w = (fx1 - fx0) * 0.8
        lift = max(0.0, -dy) / (0.07 * H * z + 1e-6)
        shadow = Image.new("L", (int(sh_w) + 20, 40), 0)
        ImageDraw.Draw(shadow).ellipse((10, 10, 10 + sh_w * (1 - 0.3 * lift), 30), fill=int(90 * (1 - 0.5 * lift)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(6))
        frame.paste((30, 20, 25), (round(sx_ - sh_w / 2 - 10), round(feet - 20)), shadow)
        px = round(sx_ - (fx0 + fx1) / 2 + ox)
        py = round(feet - p.box[3] * s + dy * z + oy)
        frame.paste(im2, (px, py), im2)
    if captions:
        for e in shot["events"]:
            if e["type"] == "line" and e["t"] <= t < e["t"] + e["dur"] + LINE_GAP * 0.8:
                draw_caption(frame, e["text"])
    return frame


def draw_caption(frame, text):
    d = ImageDraw.Draw(frame)
    f = font(30)
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if d.textlength(test, font=f) > W * 0.8 and cur:
            lines.append(cur); cur = w_
        else:
            cur = test
    lines.append(cur)
    y = H - 40 - 40 * len(lines)
    for ln in lines:
        d.text((W // 2, y), ln, font=f, fill="white", anchor="mt", stroke_width=4, stroke_fill=(40, 20, 40))
        y += 40


def title_frame(bg, title, t):
    bg = bg.resize((W, H), Image.BILINEAR)
    frame = bg.filter(ImageFilter.GaussianBlur(6))
    fade = min(1.0, t / 0.6, max(0.0, (TITLE_SECS - t) / 0.6))
    overlay = Image.new("RGB", (W, H), (255, 240, 245))
    frame = Image.blend(frame, overlay, 0.35)
    d = ImageDraw.Draw(frame)
    f = font(56)
    words, lines, cur = title.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if d.textlength(test, font=f) > W * 0.8 and cur:
            lines.append(cur); cur = w_
        else:
            cur = test
    lines.append(cur)
    y = H // 2 - 35 * len(lines)
    for ln in lines:
        d.text((W // 2, y), ln, font=f, fill=(190, 60, 110), anchor="mt", stroke_width=5, stroke_fill="white")
        y += 70
    return Image.blend(Image.new("RGB", (W, H), "black"), frame, fade)


def cmd_build(args):
    cast = load_cast()
    fps = cast.get("lip_sync", {}).get("fps", 24)
    script = load_episode(args.ep)
    shots, total = plan_episode(args.ep, script, cast, fps)
    needed = sorted({c for s in shots for c in s["stage"]})
    puppets = {}
    for n in needed:
        try:
            puppets[n] = Puppet(n)
        except FileNotFoundError as e:
            sys.exit(str(e))
    offline = any(e.get("offline") for s in shots for e in s["events"])
    if offline:
        print("  note: some lines use silent --offline stand-in audio")
    # audio track
    sr = 44100
    track = np.zeros(int((total + 1) * sr), np.int32)
    for s in shots:
        for e in s["events"]:
            if e["type"] == "line":
                a = decode_audio(e["audio"], sr)
                i = int(e["t"] * sr)
                track[i:i + len(a)] += a[: len(track) - i]
    track = np.clip(track, -32768, 32767).astype(np.int16)
    out_dir = ep_dir(args.ep)
    wav = out_dir / "audio" / "_mix.wav"
    import wave
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(track.tobytes())
    out = out_dir / f"{args.ep}.mp4"
    nframes = int(total * fps)
    bgs = {s["bg"]: load_background(s["bg"]) for s in shots}
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(fps), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "medium",
                           "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest",
                           "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    stills, want = [], set(np.linspace(fps * TITLE_SECS + 5, nframes - 5, 12).astype(int))
    si = 0
    for f in range(nframes):
        t = f / fps
        if t < TITLE_SECS:
            frame = title_frame(bgs[shots[0]["bg"]], script["title"], t)
        else:
            while si < len(shots) - 1 and t >= shots[si]["end"]:
                si += 1
            frame = render_frame(shots[si], t, fps, bgs[shots[si]["bg"]], puppets, cast, args.captions)
            fade_out = (total - t) / 0.5
            if fade_out < 1:
                frame = Image.blend(Image.new("RGB", (W, H), "black"), frame, max(0.0, fade_out))
        ff.stdin.write(frame.tobytes())
        if f in want:
            stills.append((t, frame.resize((W // 4, H // 4))))
        if f % (fps * 10) == 0:
            print(f"  rendering {t:5.1f}s / {total:.1f}s", flush=True)
    ff.stdin.close()
    if ff.wait() != 0:
        sys.exit("ffmpeg failed")
    sheet = Image.new("RGB", (W // 4 * 4, H // 4 * 3), "black")
    d = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(stills):
        sheet.paste(im, ((i % 4) * W // 4, (i // 4) * H // 4))
        d.text(((i % 4) * W // 4 + 6, (i // 4) * H // 4 + 4), f"{t:.1f}s", fill="white", font=font(14))
    sheet.save(out_dir / "contact.png")
    print(f"Built {out}  ({total:.1f}s, {nframes} frames)  contact sheet: {out_dir / 'contact.png'}")


def cmd_still(args):
    """Render one frame at time --t for close inspection."""
    cast = load_cast()
    fps = cast.get("lip_sync", {}).get("fps", 24)
    script = load_episode(args.ep)
    shots, total = plan_episode(args.ep, script, cast, fps)
    shot = next((s for s in shots if s["start"] <= args.t < s["end"]), shots[-1])
    puppets = {n: Puppet(n) for n in shot["stage"]}
    frame = render_frame(shot, args.t, fps, load_background(shot["bg"]), puppets, cast, args.captions)
    out = ep_dir(args.ep) / f"still_{args.t:.1f}.png"
    frame.save(out)
    print(f"Wrote {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prompts"); s.add_argument("char", nargs="?", default="all"); s.set_defaults(f=cmd_prompts)
    s = sub.add_parser("generate"); s.add_argument("char"); s.add_argument("--only", nargs="*")
    s.add_argument("--force", action="store_true")
    s.add_argument("--redraw-body", action="store_true", help="have the image service redraw the body too")
    s.set_defaults(f=cmd_generate)
    s = sub.add_parser("placeholder"); s.add_argument("char", nargs="?", default="all"); s.set_defaults(f=cmd_placeholder)
    s = sub.add_parser("pack"); s.add_argument("char", nargs="?", default="all"); s.set_defaults(f=cmd_pack)
    s = sub.add_parser("lineup"); s.set_defaults(f=cmd_lineup)
    s = sub.add_parser("background"); s.add_argument("scene"); s.add_argument("--prompt"); s.set_defaults(f=cmd_background)
    s = sub.add_parser("build"); s.add_argument("ep"); s.add_argument("--captions", action="store_true"); s.set_defaults(f=cmd_build)
    s = sub.add_parser("still"); s.add_argument("ep"); s.add_argument("--t", type=float, required=True)
    s.add_argument("--captions", action="store_true"); s.set_defaults(f=cmd_still)
    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    main()
