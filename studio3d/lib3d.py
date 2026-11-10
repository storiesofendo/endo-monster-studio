"""Small helpers for building cartoon characters in Blender with code.

Characters are built from rounded parts, each part rigidly attached to one bone of a
skeleton, like a well-made toy. The same model is reused in every shot, so a character
can never drift.
"""
import math

import bpy
from mathutils import Vector


def srgb(r, g, b):
    """0-255 sRGB (as sampled from the character sheet) -> Blender linear colour."""
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (lin(r), lin(g), lin(b), 1.0)


_MATS = {}


def material(name, rgb, rough=0.55, sss=0.0, sheen=0.0, coat=0.0, emit=0.0):
    key = (name, rgb)
    if key in _MATS:
        return _MATS[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = srgb(*rgb)
    b.inputs["Roughness"].default_value = rough
    if sss:
        b.inputs["Subsurface Weight"].default_value = sss
        b.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.2)
        b.inputs["Subsurface Scale"].default_value = 0.02
    if sheen:
        b.inputs["Sheen Weight"].default_value = sheen
    if coat:
        b.inputs["Coat Weight"].default_value = coat
    if emit:
        b.inputs["Emission Color"].default_value = srgb(*rgb)
        b.inputs["Emission Strength"].default_value = emit
    _MATS[key] = m
    return m


def _finish(o, mat, name, smooth=True, subdiv=2):
    o.name = name
    if mat:
        o.data.materials.append(mat)
    if smooth:
        for p in o.data.polygons:
            p.use_smooth = True
    if subdiv:
        mod = o.modifiers.new("smooth", "SUBSURF")
        mod.levels = 1
        mod.render_levels = subdiv
    return o


def ball(name, mat, loc, size, rot=(0, 0, 0), subdiv=1, segs=24):
    """Ellipsoid. size = (x, y, z) radii."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segs, ring_count=segs // 2, radius=1, location=loc,
                                         rotation=rot)
    o = bpy.context.object
    o.scale = size
    return _finish(o, mat, name, subdiv=subdiv)


def rounded_box(name, mat, loc, size, bevel=0.3, rot=(0, 0, 0)):
    """Box with soft edges. size = (x, y, z) half-extents, bevel as fraction of the smallest size."""
    bpy.ops.mesh.primitive_cube_add(size=2, location=loc, rotation=rot)
    o = bpy.context.object
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    bv = o.modifiers.new("bevel", "BEVEL")
    bv.width = min(size) * bevel
    bv.segments = 4
    return _finish(o, mat, name, subdiv=2)


def capsule(name, mat, a, b, r1, r2=None, segs=20):
    """Tapered limb from point a to point b (a cone with rounded ends)."""
    r2 = r1 if r2 is None else r2
    a, b = Vector(a), Vector(b)
    d = b - a
    bpy.ops.mesh.primitive_cone_add(vertices=segs, radius1=r1, radius2=r2, depth=d.length,
                                    location=(a + b) / 2)
    o = bpy.context.object
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d)
    _finish(o, mat, name, subdiv=2)
    # round caps
    caps = [ball(name + "_capA", mat, a, (r1, r1, r1)), ball(name + "_capB", mat, b, (r2, r2, r2))]
    return [o] + caps


def tube(name, mat, points, radius, taper=None):
    """Smooth tube along points (for curls, eyebrows, straps, smiles)."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = 4
    cu.use_fill_caps = True
    sp = cu.splines.new("NURBS")
    sp.points.add(len(points) - 1)
    for i, p in enumerate(points):
        sp.points[i].co = (*p, 1)
        if taper:
            sp.points[i].radius = taper[i]
    sp.use_endpoint_u = True
    sp.order_u = min(4, len(points))
    o = bpy.data.objects.new(name, cu)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(mat)
    return o


def helix(name, mat, base, length, turns, radius, wire, drift=(0, 0), phase=0.0):
    """A hanging ringlet curl."""
    pts, n = [], int(turns * 12) + 2
    for i in range(n):
        t = i / (n - 1)
        ang = phase + t * turns * 2 * math.pi
        r = radius * (1 - 0.35 * t)
        pts.append((base[0] + r * math.cos(ang) + drift[0] * t,
                    base[1] + r * math.sin(ang) + drift[1] * t,
                    base[2] - length * t))
    return tube(name, mat, pts, wire, taper=[1.0 - 0.4 * (i / (n - 1)) for i in range(n)])


# ---------------------------------------------------------------- skeleton

def make_armature(name, bones):
    """bones: list of (bone, parent, head xyz, tail xyz)."""
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for bname, parent, h, t in bones:
        eb = arm.edit_bones.new(bname)
        eb.head, eb.tail = h, t
        if parent:
            eb.parent = arm.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def attach(objs, rig, bone):
    """Rigidly attach parts to a bone (keeps their current world position)."""
    for o in objs if isinstance(objs, (list, tuple)) else [objs]:
        mw = o.matrix_world.copy()
        o.parent = rig
        o.parent_type = "BONE"
        o.parent_bone = bone
        o.matrix_world = mw


# ---------------------------------------------------------------- staging

def studio_lights_and_camera(target=(0, 0, 0.55), dist=3.0, lens=55, height=0.75, floor=True, bg=(0.93, 0.9, 0.86)):
    sc = bpy.context.scene
    if floor:
        bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
        f = bpy.context.object
        f.name = "floor"
        f.data.materials.append(material("floor", (236, 226, 214), 0.9))
    def area(name, loc, energy, size, color=(1, 1, 1)):
        bpy.ops.object.light_add(type="AREA", location=loc)
        L = bpy.context.object
        L.name = name
        L.data.energy, L.data.size, L.data.color = energy, size, color
        tc = L.constraints.new("TRACK_TO")
        tc.target = tgt
        return L
    bpy.ops.object.empty_add(location=target)
    tgt = bpy.context.object
    tgt.name = "look_at"
    area("key", (-2.2, -2.8, 3.0), 160, 2.5, (1.0, 0.95, 0.88))
    area("fill", (2.6, -2.0, 1.6), 45, 3.0, (0.85, 0.9, 1.0))
    area("rim", (1.2, 2.8, 2.6), 140, 1.5, (1.0, 0.85, 0.95))
    w = bpy.data.worlds.new("world")
    sc.world = w
    w.use_nodes = True
    w.node_tree.nodes["Background"].inputs[0].default_value = srgb(*[int(c * 255) for c in bg])
    w.node_tree.nodes["Background"].inputs[1].default_value = 0.25
    bpy.ops.object.camera_add(location=(0, -dist, height))
    cam = bpy.context.object
    cam.data.lens = lens
    tc = cam.constraints.new("TRACK_TO")
    tc.target = tgt
    sc.camera = cam
    return cam, tgt


def render_settings(w=960, h=540, samples=48):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    sc.view_settings.view_transform = "Standard"   # keeps the sheet's exact, saturated colours
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0.0
