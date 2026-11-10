"""Render a character from 4 angles next to its sheet, for approval.
   python3 studio3d/turnaround.py petunia out.png"""
import importlib, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent)); sys.path.insert(0, str(Path(__file__).resolve().parent / "characters"))
import bpy
from lib3d import render_settings, studio_lights_and_camera
name, out = sys.argv[1], sys.argv[2]
samples = int(sys.argv[3]) if len(sys.argv) > 3 else 32
bpy.ops.wm.read_factory_settings(use_empty=True)
rig = importlib.import_module(name).build()
cam, tgt = studio_lights_and_camera(target=(0, 0, 0.47), dist=1.75, lens=50, height=0.55)
render_settings(480, 720, samples)
frames = []
for i, ang in enumerate((0, 35, 90, 180)):
    rig.rotation_euler = (0, 0, math.radians(ang))
    p = f"{out}_{i}.png"; bpy.context.scene.render.filepath = p
    bpy.ops.render.render(write_still=True); frames.append(p)
from PIL import Image
ims = [Image.open(p) for p in frames]
sheet = Image.open(str(Path(__file__).resolve().parent.parent / "bible" / "sheets" / f"{name.upper()}_sheet.png"))
sheet = sheet.crop((0, 0, sheet.width, int(sheet.height * 0.68))); sheet.thumbnail((10000, 720))
W = sheet.width + 480 * 4
canvas = Image.new("RGB", (max(W, 1), 720), "white")
x = 0
for im in ims:
    canvas.paste(im.convert("RGB"), (x, 0)); x += 480
canvas.paste(sheet.convert("RGB"), (x, 0))
canvas.save(out); print("wrote", out)
