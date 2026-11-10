"""PETUNIA, built in 3D from bible/sheets/PETUNIA_sheet.png.

Colours were sampled from the sheet. Every part hangs on a bone of her skeleton,
so animation moves her like a real toy and she looks identical in every shot.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib3d import attach, ball, capsule, helix, make_armature, material, rounded_box, tube  # noqa: E402

import bpy  # noqa: E402

SKIN = (196, 112, 66)
SKIN_DARK = (176, 94, 52)
HAIR = (92, 46, 26)
HAIR_HI = (128, 68, 40)
MAGENTA = (198, 46, 88)
LAVENDER = (190, 128, 178)
PINK_CUFF = (240, 140, 168)
BUTTER = (253, 216, 140)
YELLOW = (252, 200, 40)
TEAL = (78, 168, 164)
PLUM = (128, 44, 92)
CREAM = (242, 226, 196)
IRIS = (128, 116, 42)
BROW = (70, 34, 20)
MOUTH = (110, 30, 36)
FRECKLE = (172, 78, 36)

HEAD_C = (0.0, 0.0, 0.78)
HEAD_R = (0.125, 0.115, 0.125)
JAW_C = (0.0, -0.01, 0.735)
JAW_R = (0.112, 0.098, 0.085)


def face_y(x, z, inset=0.0):
    """Front surface of the face at (x, z) so details sit exactly on the skin."""
    best = 0.0
    for c, r in ((HEAD_C, HEAD_R),):
        k = 1 - ((x - c[0]) / r[0]) ** 2 - ((z - c[2]) / r[2]) ** 2
        if k > 0:
            best = min(best, c[1] - r[1] * math.sqrt(k))
    return best + inset


def build():
    skin = material("p_skin", SKIN, 0.5, sss=0.25)
    skin_dk = material("p_skin_dark", SKIN_DARK, 0.5, sss=0.2)
    hair = material("p_hair", HAIR, 0.55, sheen=0.4)
    hair_hi = material("p_hair_hi", HAIR_HI, 0.5, sheen=0.5)
    fab = lambda n, c: material(n, c, 0.8, sheen=0.35)  # noqa: E731
    magenta, lavender, cuff = fab("p_magenta", MAGENTA), fab("p_lavender", LAVENDER), fab("p_cuff", PINK_CUFF)
    butter, yellow, teal = fab("p_tee", BUTTER), fab("p_sock", YELLOW), fab("p_teal", TEAL)
    plum, cream = fab("p_plum", PLUM), material("p_cream", CREAM, 0.6)
    white = material("p_eye_white", (250, 248, 244), 0.25, coat=0.6)
    iris = material("p_iris", IRIS, 0.3, coat=1.0)
    dark = material("p_pupil", (18, 10, 6), 0.2, coat=1.0)
    shine = material("p_shine", (255, 255, 255), 0.1, emit=1.5)
    brow = material("p_brow", BROW, 0.6)
    mouth = material("p_mouth", MOUTH, 0.5)
    tooth = material("p_tooth", (252, 250, 240), 0.3, coat=0.5)
    freckle = material("p_freckle", FRECKLE, 0.6)
    button = material("p_button", (246, 196, 50), 0.35, coat=0.6)

    parts = {k: [] for k in ["head", "neck", "chest", "hips", "upper_arm.L", "fore_arm.L", "hand.L",
                             "upper_arm.R", "fore_arm.R", "hand.R", "thigh.L", "shin.L", "foot.L",
                             "thigh.R", "shin.R", "foot.R", "pigtail.L", "pigtail.R", "lid.L", "lid.R",
                             "brow.L", "brow.R", "mouth"]}

    # ---------------- head + face
    H = parts["head"]
    head = ball("head", skin, HEAD_C, HEAD_R, subdiv=2, segs=40)
    for v in head.data.vertices:   # chubby cheeks, softer chin: widen the lower face a little
        z = v.co.z
        if z < 0:
            v.co.x *= 1 + 0.06 * min(1, -z * 2.2) - 0.05 * max(0, -z - 0.6) * 3
    H.append(head)
    for s in (-1, 1):
        H.append(ball(f"ear{s}", skin, (s * 0.121, 0.0, 0.772), (0.02, 0.014, 0.027)))
        ex, ez = s * 0.048, 0.786
        ey = face_y(ex, ez, 0.022)
        H.append(ball(f"eye{s}", white, (ex, ey, ez), (0.034, 0.022, 0.039), subdiv=2))
        H.append(ball(f"iris{s}", iris, (ex + s * 0.002, ey - 0.018, ez - 0.003), (0.023, 0.008, 0.026), subdiv=2))
        H.append(ball(f"pupil{s}", dark, (ex + s * 0.002, ey - 0.024, ez - 0.003), (0.012, 0.005, 0.0135)))
        H.append(ball(f"shine{s}", shine, (ex - 0.008, ey - 0.028, ez + 0.009), (0.004, 0.002, 0.004)))
        side = "L" if s > 0 else "R"
        # upper eyelid (skin) + lashes: rest position shows the whole eye; blink slides it down
        lid = ball(f"lid{s}", skin, (ex, ey + 0.004, ez + 0.041), (0.036, 0.023, 0.009), subdiv=2)
        lash = tube(f"lash{s}", dark, [(ex - s * 0.034, ey - 0.008, ez + 0.03), (ex, ey - 0.02, ez + 0.039),
                                       (ex + s * 0.036, ey - 0.008, ez + 0.032), (ex + s * 0.045, ey - 0.003, ez + 0.04)],
                    0.002)
        parts[f"lid.{side}"] += [lid, lash]
        by = face_y(ex, 0.845, -0.004)
        parts[f"brow.{side}"].append(tube(f"brow{s}", brow, [(ex - s * 0.028, by + 0.004, 0.838),
                                                             (ex, by - 0.002, 0.848),
                                                             (ex + s * 0.03, by + 0.006, 0.843)], 0.0045))
        # freckles across the cheeks
        for i, (fx, fz) in enumerate([(0.066, 0.752), (0.08, 0.745), (0.072, 0.738), (0.058, 0.743), (0.088, 0.756)]):
            x = s * fx
            H.append(ball(f"freckle{s}{i}", freckle, (x, face_y(x, fz, 0.001), fz), (0.0035, 0.002, 0.0035), subdiv=0, segs=8))
    H.append(ball("nose", skin_dk, (0, face_y(0, 0.755, 0.004), 0.755), (0.016, 0.012, 0.012), subdiv=2))
    # gap-tooth grin
    my = face_y(0, 0.715, 0.003)
    M = parts["mouth"]
    M.append(ball("mouth_open", mouth, (0, my, 0.716), (0.03, 0.006, 0.011), subdiv=2))
    for s in (-1, 1):
        M.append(rounded_box(f"tooth{s}", tooth, (s * 0.0075, my - 0.005, 0.721), (0.0058, 0.002, 0.0062), 0.4))
    M.append(tube("smile", mouth, [(-0.042, face_y(-0.042, 0.727, 0.002), 0.727), (-0.03, my - 0.001, 0.717),
                                   (0, my - 0.002, 0.709), (0.03, my - 0.001, 0.717),
                                   (0.042, face_y(0.042, 0.727, 0.002), 0.727)], 0.0028))

    # ---------------- hair: cap, curly fringe, pigtails with ringlets
    cap = ball("hair_cap", hair, (0, 0.022, 0.818), (0.132, 0.121, 0.117), subdiv=2, segs=40)
    tex = bpy.data.textures.new("curls", "VORONOI")
    tex.noise_scale = 0.018
    dm = cap.modifiers.new("curly", "DISPLACE")
    dm.texture, dm.strength, dm.mid_level = tex, 0.012, 0.6
    H.append(cap)
    import random
    rnd = random.Random(7)
    for i in range(13):   # little coils along the hairline
        a = math.radians(-66 + i * 11)
        x, z = 0.11 * math.sin(a), 0.858 + 0.05 * math.cos(a)
        y = face_y(x, z, 0.004) if abs(x) < 0.11 else -0.02
        H.append(helix(f"fringe{i}", hair if i % 3 else hair_hi, (x, y, z + 0.01), 0.022, 1.4, 0.011, 0.0065,
                       phase=i * 1.3))
    for s in (-1, 1):
        side = "L" if s > 0 else "R"
        P = parts[f"pigtail.{side}"]
        cx, cz = s * 0.17, 0.895
        P.append(ball(f"puffcore{s}", hair, (cx, 0.01, cz), (0.04, 0.036, 0.038), subdiv=2))
        for i in range(56):   # springy ringlets bursting out of the tie in every direction
            u, v = rnd.uniform(0, 2 * math.pi), rnd.uniform(-0.9, 0.6)
            d = (math.cos(u) * math.sqrt(1 - v * v), math.sin(u) * math.sqrt(1 - v * v) * 0.8, v)
            if d[0] * s < -0.2:
                d = (-d[0], d[1], d[2])
            start = (cx + d[0] * 0.035, 0.01 + d[1] * 0.03, cz + d[2] * 0.03)
            length = 0.06 + 0.07 * rnd.random() + (0.05 if v < -0.4 else 0)
            pts, n, ph = [], 26, rnd.uniform(0, 6.28)
            for k in range(n):
                t = k / (n - 1)
                ang = ph + t * 3.2 * 2 * math.pi
                r = 0.011 * (1 - 0.3 * t)
                # coil axis follows the direction d, drooping with gravity
                ax = (start[0] + d[0] * length * t, start[1] + d[1] * length * t,
                      start[2] + d[2] * length * t - 0.05 * t * t)
                pts.append((ax[0] + r * math.cos(ang), ax[1] + r * math.sin(ang), ax[2] + r * math.sin(ang + 1.0) * 0.6))
            P.append(tube(f"coil{s}{i}", hair if i % 3 else hair_hi, pts, 0.0072,
                          taper=[1.0 - 0.35 * (k / (n - 1)) for k in range(n)]))
        bpy.ops.mesh.primitive_torus_add(major_radius=0.026, minor_radius=0.011, location=(s * 0.122, 0.01, 0.9),
                                         rotation=(0, math.radians(90 - s * 25), 0))
        tie = bpy.context.object
        tie.name = f"tie{s}"
        tie.data.materials.append(material("p_tie", MAGENTA, 0.5, sheen=0.3))
        P.append(tie)
        H.append(helix(f"tendril{s}", hair, (s * 0.108, -0.06, 0.835), 0.07, 1.6, 0.012, 0.007, drift=(s * 0.01, 0)))

    # ---------------- body
    parts["neck"] += capsule("neck", skin, (0, 0, 0.62), (0, 0, 0.68), 0.033)
    C = parts["chest"]
    C.append(ball("tee", butter, (0, 0, 0.55), (0.105, 0.078, 0.09), subdiv=2))
    C.append(rounded_box("bib", magenta, (0, -0.07, 0.522), (0.074, 0.014, 0.058), 0.35))
    bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.042, depth=0.006, location=(0.012, -0.086, 0.54),
                                    rotation=(math.radians(90), 0, math.radians(180)))
    pocket = bpy.context.object
    pocket.name = "pocket"
    pocket.data.materials.append(teal)
    C.append(pocket)
    for s in (-1, 1):
        C.append(tube(f"strap{s}", magenta, [(s * 0.058, -0.078, 0.575), (s * 0.072, -0.064, 0.625),
                                              (s * 0.078, 0.0, 0.645), (s * 0.07, 0.07, 0.6), (s * 0.05, 0.082, 0.5)], 0.011))
        C.append(ball(f"button{s}", button, (s * 0.058, -0.086, 0.575), (0.012, 0.006, 0.012)))
        C.append(ball(f"sleeve{s}", butter, (s * 0.118, 0.0, 0.585), (0.046, 0.046, 0.042), subdiv=2))
        zig = [(s * 0.118 + 0.047 * math.cos(t / 12 * 2 * math.pi), 0.047 * math.sin(t / 12 * 2 * math.pi),
                0.558 + (0.008 if t % 2 else -0.004)) for t in range(13)]
        C.append(tube(f"zig{s}", plum, zig, 0.0035))
    P = parts["hips"]
    for s, mat in ((-1, magenta), (1, lavender)):
        P.append(rounded_box(f"waist{s}", mat, (s * 0.056, 0, 0.47), (0.058, 0.08, 0.036), 0.5))
        P.append(rounded_box(f"shorts{s}", mat, (s * 0.056, 0, 0.42), (0.06, 0.077, 0.05), 0.5))
        bpy.ops.mesh.primitive_torus_add(major_radius=0.047, minor_radius=0.016, location=(s * 0.056, 0, 0.372))
        t = bpy.context.object
        t.name = f"cuff{s}"
        t.scale = (1.1, 1.35, 1.0)
        t.data.materials.append(cuff)
        P.append(t)

    # ---------------- arms and legs
    for s in (-1, 1):
        side = "L" if s > 0 else "R"
        sh, el, wr = (s * 0.13, 0, 0.585), (s * 0.152, -0.006, 0.47), (s * 0.158, -0.012, 0.37)
        parts[f"upper_arm.{side}"] += capsule(f"uparm{s}", skin, sh, el, 0.028, 0.025)
        parts[f"fore_arm.{side}"] += capsule(f"forearm{s}", skin, el, wr, 0.025, 0.022)
        parts[f"hand.{side}"].append(ball(f"hand{s}", skin, (s * 0.16, -0.014, 0.338), (0.024, 0.017, 0.034), subdiv=2))
        parts[f"hand.{side}"].append(ball(f"thumb{s}", skin, (s * 0.15, -0.03, 0.35), (0.009, 0.009, 0.016)))
        hip, knee, ank = (s * 0.052, 0, 0.4), (s * 0.052, 0, 0.25), (s * 0.052, 0, 0.12)
        parts[f"thigh.{side}"] += capsule(f"thigh{s}", skin, hip, knee, 0.036, 0.032)
        parts[f"shin.{side}"] += capsule(f"shin{s}", skin, knee, (s * 0.052, 0, 0.16), 0.032, 0.03)
        parts[f"shin.{side}"] += capsule(f"sock{s}", yellow, (s * 0.052, 0, 0.065), (s * 0.052, 0, 0.16), 0.035, 0.036)
        zig = [(s * 0.052 + 0.038 * math.cos(t / 12 * 2 * math.pi), 0.038 * math.sin(t / 12 * 2 * math.pi),
                0.148 + (0.007 if t % 2 else -0.004)) for t in range(13)]
        parts[f"shin.{side}"].append(tube(f"sockzig{s}", plum, zig, 0.0032))
        F = parts[f"foot.{side}"]
        F.append(rounded_box(f"shoe{s}", plum, (s * 0.054, -0.022, 0.04), (0.042, 0.072, 0.034), 0.6))
        F.append(rounded_box(f"sole{s}", cream, (s * 0.054, -0.024, 0.012), (0.045, 0.076, 0.012), 0.6))
        F.append(ball(f"toe{s}", cream, (s * 0.054, -0.078, 0.03), (0.038, 0.026, 0.024), subdiv=2))
        for i in range(3):
            F.append(tube(f"lace{s}{i}", cream, [(s * 0.054 - 0.022, -0.045 + i * 0.018, 0.074 - i * 0.002),
                                                  (s * 0.054 + 0.022, -0.045 + i * 0.018, 0.074 - i * 0.002)], 0.004))
        F.append(tube(f"shoezig{s}", material("p_teal_zig", TEAL, 0.6),
                      [(s * (0.054 + 0.044), -0.07 + i * 0.022, 0.03 + (0.008 if i % 2 else 0)) for i in range(5)], 0.004))

    # ---------------- skeleton
    bones = [("root", None, (0, 0, 0), (0, 0, 0.1)),
             ("hips", "root", (0, 0, 0.4), (0, 0, 0.5)),
             ("chest", "hips", (0, 0, 0.5), (0, 0, 0.62)),
             ("neck", "chest", (0, 0, 0.62), (0, 0, 0.68)),
             ("head", "neck", (0, 0, 0.68), (0, 0, 0.92)),
             ("mouth", "head", (0, -0.1, 0.716), (0, -0.14, 0.716))]
    for s, side in ((1, "L"), (-1, "R")):
        bones += [(f"upper_arm.{side}", "chest", (s * 0.13, 0, 0.585), (s * 0.152, -0.006, 0.47)),
                  (f"fore_arm.{side}", f"upper_arm.{side}", (s * 0.152, -0.006, 0.47), (s * 0.158, -0.012, 0.37)),
                  (f"hand.{side}", f"fore_arm.{side}", (s * 0.158, -0.012, 0.37), (s * 0.16, -0.014, 0.3)),
                  (f"thigh.{side}", "hips", (s * 0.052, 0, 0.4), (s * 0.052, 0, 0.25)),
                  (f"shin.{side}", f"thigh.{side}", (s * 0.052, 0, 0.25), (s * 0.052, 0, 0.065)),
                  (f"foot.{side}", f"shin.{side}", (s * 0.052, 0, 0.065), (s * 0.052, -0.08, 0.02)),
                  (f"pigtail.{side}", "head", (s * 0.12, 0.01, 0.9), (s * 0.19, 0.01, 0.89)),
                  (f"lid.{side}", "head", (s * 0.048, -0.09, 0.82), (s * 0.048, -0.13, 0.82)),
                  (f"brow.{side}", "head", (s * 0.048, -0.1, 0.835), (s * 0.048, -0.14, 0.835))]
    rig = make_armature("PETUNIA", bones)
    for bone, objs in parts.items():
        attach(objs, rig, bone)
    return rig


if __name__ == "__main__":
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build()
    out = Path(__file__).resolve().parent / "petunia.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    print("saved", out)
