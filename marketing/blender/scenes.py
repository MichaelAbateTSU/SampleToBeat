"""ChopLab trailer 3D shots, rendered headless with Blender's Python module (bpy) and Cycles on CPU.

  python scenes.py <shot> <out_dir> [--part K/N] [--range A:B:STEP] [--still FRAME] [--samples S] [--scale PCT]

Shots:  chop  - a waveform of glowing bars is sliced by a blade of light and falls into chops
        logo  - the four bars of the ChopLab mark rise out of a dark stage
Frames are set procedurally (no keyframes), so any frame can be rendered on its own and the work can be
split across processes with --part."""
import math
import sys

import bpy  # must come first: it provides addon_utils
import addon_utils  # noqa: E402,I001

FPS = 24
SHOTS = {"chop": 84, "logo": 108}  # frames
AMBER, EMBER = (1.0, 0.54, 0.035), (1.0, 0.10, 0.04)  # linear-light values of #ffc233 and #ff5a36


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("cycles")
    s = bpy.context.scene
    s.render.engine = "CYCLES"
    s.cycles.device = "CPU"
    s.cycles.use_denoising = True
    s.cycles.max_bounces = 4
    s.render.fps = FPS
    s.render.image_settings.file_format = "PNG"
    s.view_settings.view_transform = "Standard"  # keep the brand colours saturated instead of rolling off to white
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.0, 0.0, 0.0, 1)
    s.world = world
    return s


def material(name, color, emission=0.0, rough=0.4, metallic=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metallic
    if emission:
        b.inputs["Emission Color"].default_value = (*color, 1)
        b.inputs["Emission Strength"].default_value = emission
    return m


class Pill:
    """A bar with fully rounded ends (the shape in the ChopLab mark): a cylinder capped by two spheres, so it
    can change height every frame without its caps stretching."""

    def __init__(self, name, mat, radius):
        self.r = radius
        bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=1, vertices=48)
        self.body = bpy.context.object
        self.caps = []
        for _ in range(2):
            bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=48, ring_count=24)
            self.caps.append(bpy.context.object)
        for o in (self.body, *self.caps):
            o.name = name
            o.data.materials.append(mat)
            for poly in o.data.polygons:
                poly.use_smooth = True

    def set(self, x, y, base, height):
        h = max(2 * self.r + 1e-3, height)
        self.body.location = (x, y, base + h / 2)
        self.body.scale = (1, 1, h - 2 * self.r)
        self.caps[0].location = (x, y, base + self.r)
        self.caps[1].location = (x, y, base + h - self.r)


def stage(s):
    bpy.ops.mesh.primitive_plane_add(size=200)
    floor = bpy.context.object
    floor.data.materials.append(material("floor", (0.03, 0.024, 0.02), rough=0.14, metallic=0.85))
    bpy.ops.object.light_add(type="AREA", location=(0, -6, 9))
    key = bpy.context.object
    key.data.energy, key.data.size = 60, 9
    key.data.color = (1.0, 0.8, 0.6)
    key.rotation_euler = (math.radians(35), 0, 0)
    bpy.ops.object.camera_add()
    cam = bpy.context.object
    cam.data.lens = 40
    cam.data.dof.use_dof = True
    cam.data.dof.aperture_fstop = 2.2
    s.camera = cam
    bpy.ops.object.empty_add(location=(0, 0, 1))
    target = bpy.context.object
    con = cam.constraints.new("TRACK_TO")
    con.target, con.track_axis, con.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
    cam.data.dof.focus_object = target
    return cam, target


def wave(i: int, n: int) -> float:
    x = i / n
    env = 0.35 + 0.65 * abs(math.sin(math.pi * (x * 2.0 + 0.1))) ** 0.8
    return max(0.08, env * (0.55 + 0.45 * math.sin(i * 1.7) * math.sin(i * 0.37 + 1.3)) ** 2 * 3.2 + 0.25)


def build_chop(s):
    cam, target = stage(s)
    n, pitch = 56, 0.34
    bars = []
    for i in range(n):
        m = material(f"bar{i}", lerp(AMBER, EMBER, i / (n - 1)), emission=1.0, rough=0.3)
        bars.append(Pill(f"bar{i}", m, 0.105))
    bpy.ops.mesh.primitive_plane_add(size=1)
    blade = bpy.context.object
    blade.scale = (0.05, 3.0, 9)
    blade.rotation_euler = (math.radians(90), 0, math.radians(90))
    blade.data.materials.append(material("blade", (1, 0.95, 0.85), emission=25))
    chunk = 7  # bars per chop
    total = SHOTS["chop"]

    def frame(f):
        t = f / (total - 1)
        cut = -1.2 + ease((t - 0.12) / 0.62) * (n * pitch + 2.4)  # blade x, relative to the left edge
        left = -n * pitch / 2
        blade.location = (left + cut, 0, 2.5)
        for i, o in enumerate(bars):
            x0 = left + i * pitch
            passed = ease((cut - i * pitch) / 1.6)  # 0 before the blade, 1 well after
            c = i // chunk
            gap = (c - (n // chunk) / 2) * 0.42 * passed  # chops drift apart
            hop = math.sin(min(1.0, max(0.0, (cut - i * pitch) / 2.4)) * math.pi) * 0.55
            pulse = 1 + 0.10 * math.sin(f * 0.55 + i * 0.9) * (1 - passed) + 0.06 * math.sin(f * 0.9 + c * 2.1) * passed
            o.set(x0 + gap, 0, 0.04 + hop, wave(i, n) * pulse)
        a = math.radians(-58 + 34 * ease(t))
        r = 15.5 - 3.5 * ease(t)
        cam.location = (r * math.sin(a) * 0.55, -r * math.cos(a * 0.4), 2.1 + 1.4 * ease(t))
        target.location = (left + min(max(cut, 3.0), n * pitch - 3.0) * 0.55 + 2.2, 0, 1.35)
    return frame


LOGO = [(-2.1, 1.6, 0.7), (-0.7, 4.2, -0.6), (0.9, 2.8, 0.6), (2.3, 3.6, -0.2)]  # x, height, base lift (mark proportions)


def build_logo(s):
    cam, target = stage(s)
    bars = [Pill(f"logo{i}", material(f"logo{i}", lerp(AMBER, EMBER, i / 3), emission=1.0, rough=0.25), 0.45) for i in range(4)]
    total = SHOTS["logo"]

    def frame(f):
        t = f / (total - 1)
        for i, (o, (x, h, lift)) in enumerate(zip(bars, LOGO)):
            k = (f - 6 - i * 7) / 26
            g = max(0.0, min(1.0, k))
            spring = 1 - (1 - g) ** 3 * math.cos(g * 9.0) if g < 1 else 1.0
            breathe = 1 + 0.025 * math.sin(f * 0.22 + i * 1.3) * ease((t - 0.5) * 4)
            o.set(x, 0, 0.25 + (lift + 0.6) * ease(g), 0.9 + (h - 0.9) * spring * breathe)
        a = math.radians(38 * (1 - ease(t / 0.7)))
        r = 21 - 5.5 * ease(t / 0.8)
        cam.location = (r * math.sin(a), -r * math.cos(a), 1.6 + 2.6 * (1 - ease(t / 0.7)))
        target.location = (0.1, 0, 2.3)
    return frame


def main():
    args = sys.argv[1:]
    shot, out = args[0], args[1]
    opt = {args[i]: args[i + 1] for i in range(2, len(args) - 1, 2)}
    s = reset()
    frame = {"chop": build_chop, "logo": build_logo}[shot](s)
    s.cycles.samples = int(opt.get("--samples", 20))
    s.render.resolution_x, s.render.resolution_y = 1920, 1080
    s.render.resolution_percentage = int(opt.get("--scale", 100))
    s.render.threads_mode = "FIXED"
    s.render.threads = int(opt.get("--threads", 2))
    frames = [int(opt["--still"])] if "--still" in opt else list(range(SHOTS[shot]))
    if "--range" in opt:  # start:stop:step, e.g. to render every other frame of a slow passage
        lo, hi, st = map(int, opt["--range"].split(":"))
        frames = list(range(lo, hi, st))
    if "--part" in opt:
        k, n = map(int, opt["--part"].split("/"))
        frames = frames[k::n]
    for f in frames:
        frame(f)
        s.render.filepath = f"{out}/{shot}_{f:04d}.png"
        bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
