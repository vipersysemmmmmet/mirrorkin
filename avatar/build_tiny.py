"""Build a realistic tiny you in Blender with MPFB, and render it.

Run headless:
  Blender --background --python avatar/build_tiny.py -- --look data/look.json --body avatar/body.json --out data/avatar

Reads the same look settings the desktop app uses (skin, hair, glasses, clothes),
plus body settings (MPFB macro sliders). Asset packs are used when installed; anything
missing falls back to simple materials, so the script always produces a render.
"""

import argparse
import json
import math
import os
import sys

import bpy

MPFB = "bl_ext.blender_org.mpfb"


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--look", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--outfit", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--sprites", action="store_true", help="also render the desktop animation frames")
    return parser.parse_args(argv)


def hex_to_rgba(value: str) -> tuple:
    value = value.lstrip("#")
    srgb = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    # Blender colours are linear
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    return (*linear, 1.0)


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=MPFB)


def make_human(body: dict):
    import importlib
    HumanService = importlib.import_module(f"{MPFB}.services.humanservice").HumanService
    return HumanService.create_human(macro_detail_dict=body, scale=0.1)


def mpfb(name: str):
    import importlib
    module = importlib.import_module(f"{MPFB}.services.{name.lower()}")
    return getattr(module, name)


def asset_path(kind: str, name: str, ext: str = "mhclo") -> str | None:
    """Find an installed asset by name, e.g. ("hair", "punkduck_alpha7_curly")."""
    root = mpfb("LocationService").get_user_data()
    path = os.path.join(root, kind, name, f"{name}.{ext}")
    return path if os.path.exists(path) else None


def add_asset(human, kind: str, name: str, asset_type: str):
    """Attach hair, eyes, clothes etc. Returns the new object, or None if not installed."""
    path = asset_path(kind, name)
    if not path:
        print(f"TINY_MISSING {kind}/{name}")
        return None
    before = set(bpy.data.objects)
    mpfb("HumanService").add_mhclo_asset(path, human, asset_type=asset_type, subdiv_levels=0)
    added = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    return added[0] if added else None


def colour_mix(nodes):
    """A Multiply mix node set to colour mode, with its colour sockets (it has same-named
    number and vector sockets too, so they can't be looked up by name)."""
    mix = nodes.new("ShaderNodeMix")
    mix.data_type, mix.blend_type = "RGBA", "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    a, b = [i for i in mix.inputs if i.type == "RGBA"][:2]
    result = next(o for o in mix.outputs if o.type == "RGBA")
    return mix, a, b, result


def tint(obj, color: str) -> None:
    """Colour hair or clothing while keeping its texture's detail (weave, strands, folds).

    The texture is turned to grey, then multiplied by the colour, scaled so the texture's
    average brightness lands on the requested colour."""
    for mat in obj.data.materials:
        if not mat or not mat.use_nodes:
            continue
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        tex = nodes.get("diffuseTexture")
        targets = [l.to_socket for l in tex.outputs["Color"].links] if tex else []
        if not targets:  # untextured: plain colour
            bsdf = nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Base Color"].default_value = hex_to_rgba(color)
            continue
        mean = max(0.08, brightness(average_colour(tex.image)))
        gray = nodes.new("ShaderNodeRGBToBW")
        cap = nodes.new("ShaderNodeMath")  # printed logos and white trim would glow through dark colours
        cap.operation = "MINIMUM"
        cap.inputs[1].default_value = mean * 1.4
        _, a_in, b_in, result = colour_mix(nodes)
        r, g, b, _ = hex_to_rgba(color)
        b_in.default_value = (min(r / mean, 4), min(g / mean, 4), min(b / mean, 4), 1)
        links.new(tex.outputs["Color"], gray.inputs["Color"])
        links.new(gray.outputs["Val"], cap.inputs[0])
        links.new(cap.outputs["Value"], a_in)
        for socket in targets:
            links.new(result, socket)


def brightness(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def average_colour(image) -> tuple:
    """Mean linear RGB of alpha-visible pixels, sampled on an even 2D grid."""
    if image is None:
        raise ValueError("Skin texture image is missing.")
    if not image.has_data and image.source == "FILE" and image.filepath:
        image.reload()
    width, height = image.size
    if not image.has_data or not width or not height:
        raise ValueError(f"Skin texture '{image.name}' has no pixel data.")

    px = image.pixels[:]
    step_x = max(1, width // 160)
    step_y = max(1, height // 160)
    total, weight = [0.0, 0.0, 0.0], 0.0
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            i = (y * width + x) * 4
            alpha = px[i + 3]
            if alpha <= 0:
                continue
            for c in range(3):
                total[c] += px[i + c] * alpha
            weight += alpha
    if not weight:
        raise ValueError(f"Skin texture '{image.name}' contains no visible pixels.")
    return tuple(channel / weight for channel in total)


RENDER_LIGHT = (0.59, 0.47, 0.44)  # per-channel calibration through the stage lights and colour management


def match_skin(human, color: str) -> None:
    """Shift a skin texture's overall tone to the look's skin colour, keeping its detail
    (pores, lips, shading): each channel is scaled so the texture's average becomes the colour."""
    for mat in human.data.materials:
        group = next((n for n in mat.node_tree.nodes if n.type == "GROUP"), None) if mat and mat.use_nodes else None
        tree = group.node_tree if group else None
        tex = tree.nodes.get("DiffuseTexture") if tree else None
        if not tex or tree.nodes.get("skinTone"):
            continue  # one group is shared by every body material: adjust it once
        if tex.image is None:
            raise ValueError(f"Skin material '{mat.name}' has no diffuse texture.")
        targets = [l.to_socket for l in tex.outputs["Color"].links if l.to_node.type != "BSDF_PRINCIPLED"]
        avg = average_colour(tex.image)
        # Compensate per channel for the stage lights and render colour management.
        want = [c / RENDER_LIGHT[i] for i, c in enumerate(hex_to_rgba(color)[:3])]
        balance, a_in, b_in, result = colour_mix(tree.nodes)
        balance.name = "skinTone"
        b_in.default_value = (*(min(want[c] / max(avg[c], 0.01), 4) for c in range(3)), 1)
        tree.links.new(tex.outputs["Color"], a_in)
        for socket in targets:
            tree.links.new(result, socket)


def dress(human, look: dict, outfit: dict) -> None:
    for name in (outfit.get("bottom"), outfit.get("top"), outfit.get("shoes")):
        if name and not asset_path("clothes", name):
            raise ValueError(f"Clothing style '{name}' is not installed. Enter an installed asset name.")
    if asset_path("skins", outfit["skin"], "mhmat"):
        mpfb("HumanService").set_character_skin(asset_path("skins", outfit["skin"], "mhmat"), human)
        match_skin(human, look["skin"])
    else:
        simple_skin(human, look["skin"])
    add_asset(human, "eyes", outfit["eyes"], "Eyes")
    add_asset(human, "eyebrows", outfit["eyebrows"], "Eyebrows")
    add_asset(human, "eyelashes", outfit["eyelashes"], "Eyelashes")
    if outfit.get("hair"):
        hair = add_asset(human, "hair", outfit["hair"], "Hair")
        if hair:
            tint(hair, look["hair_color"])
    if look.get("glasses") and outfit.get("glasses"):
        add_asset(human, "clothes", outfit["glasses"], "Clothes")
    bottom = add_asset(human, "clothes", outfit["bottom"], "Clothes")
    if bottom and outfit.get("tint_bottom", True):
        tint(bottom, look["bottom_color"])
    # No separate top when the bottom is a full suit with its own (two layers clash)
    top = add_asset(human, "clothes", outfit["top"], "Clothes") if outfit.get("top") else None
    if top:
        tint(top, look["top_color"])
        # Smooth before lifting so the fabric fit does not pull back into the skin.
        smooth = top.modifiers.new("fabric", "SMOOTH")
        smooth.factor, smooth.iterations = 0.6, 8
        lift = top.modifiers.new("lift", "DISPLACE")
        lift.direction, lift.strength, lift.mid_level = "NORMAL", 0.02, 0.0
    if outfit.get("shoes"):
        add_asset(human, "clothes", outfit["shoes"], "Clothes")


def relax_pose(human, arm_angle: float = 32) -> None:
    """Bring the arms down from the default A-pose to rest at the sides."""
    rig = human.parent
    for side, sign in (("L", -1), ("R", 1)):
        bone = rig.pose.bones[f"upperarm01.{side}"]
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = (0, 0, math.radians(arm_angle) * sign)


def simple_skin(basemesh, color: str) -> None:
    """Skin shader with subsurface scattering, used until a skin texture is applied."""
    mat = bpy.data.materials.new("tiny_skin")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = hex_to_rgba(color)
    bsdf.inputs["Roughness"].default_value = 0.5
    bsdf.inputs["Subsurface Weight"].default_value = 0.15
    bsdf.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.2)
    basemesh.data.materials.clear()
    basemesh.data.materials.append(mat)


def setup_stage(height: float, size: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {
        e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} else "BLENDER_EEVEE"
    scene.render.film_transparent = True  # transparent background for the desktop
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    # "Standard" keeps colours true to the look settings (AgX washes skin tones out)
    scene.view_settings.view_transform = "Standard"

    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.8, 0.8, 0.85, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.25
    scene.world = world

    # Soft three-point lighting
    for name, energy, loc, size_m in (("key", 30, (1.2, -1.6, 2.0), 1.2),
                                      ("fill", 12, (-1.6, -1.2, 1.4), 1.5),
                                      ("rim", 25, (0.2, 1.8, 2.0), 0.8)):
        light = bpy.data.lights.new(name, "AREA")
        light.energy = energy * height
        light.size = size_m
        obj = bpy.data.objects.new(name, light)
        obj.location = loc
        scene.collection.objects.link(obj)
        point_at(obj, (0, 0, height * 0.6))

    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = height * 1.12
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, -4, height * 0.53)
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.collection.objects.link(cam)
    scene.camera = cam


def point_at(obj, target) -> None:
    from mathutils import Vector
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


WALK_FRAMES = 8
LID_CLOSED = -45   # degrees on the upper-lid bones' X axis; tested
LEG_SWING = 24     # degrees; positive X swings a leg backwards
WALK_TURN = 35     # degrees the body turns toward the direction of travel


def set_rot(rig, bone: str, x: float = 0, y: float = 0, z: float = 0) -> None:
    b = rig.pose.bones[bone]
    b.rotation_mode = "XYZ"
    b.rotation_euler = (math.radians(x), math.radians(y), math.radians(z))


def eyes(rig, closed: bool) -> None:
    for side in ("L", "R"):
        set_rot(rig, f"orbicularis03.{side}", x=LID_CLOSED if closed else 0)


def walk_pose(rig, phase: float) -> None:
    """One moment of a walk cycle; phase runs 0..1."""
    swing = math.sin(phase * 2 * math.pi)
    set_rot(rig, "upperleg01.L", x=-LEG_SWING * swing)
    set_rot(rig, "upperleg01.R", x=LEG_SWING * swing)
    # Bend the knee of whichever leg is swinging back
    set_rot(rig, "lowerleg01.L", x=max(0, swing) * 30)
    set_rot(rig, "lowerleg01.R", x=max(0, -swing) * 30)


def stand(rig) -> None:
    for bone in ("upperleg01.L", "upperleg01.R", "lowerleg01.L", "lowerleg01.R"):
        set_rot(rig, bone)
    rig.rotation_euler = (0, 0, 0)


def render_to(path: str) -> None:
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_sprites(human, out: str) -> None:
    """Frames the desktop app plays: idle, blink, sleep, and a walk cycle facing right.
    Walking left is the same frames mirrored."""
    rig = human.parent
    os.makedirs(out, exist_ok=True)
    stand(rig)
    eyes(rig, False)
    render_to(os.path.join(out, "idle.png"))
    eyes(rig, True)
    render_to(os.path.join(out, "blink.png"))
    render_to(os.path.join(out, "sleep.png"))
    eyes(rig, False)
    rig.rotation_euler = (0, 0, math.radians(WALK_TURN))
    for i in range(WALK_FRAMES):
        walk_pose(rig, i / WALK_FRAMES)
        render_to(os.path.join(out, f"walk_{i}.png"))
    stand(rig)
    print(f"TINY_SPRITES {out}")


def main() -> None:
    opts = args()
    look = json.load(open(opts.look))
    body = json.load(open(opts.body))
    clear_scene()
    human = make_human(body)
    if opts.outfit:
        mpfb("HumanService").add_builtin_rig(human, "default")
        dress(human, look, json.load(open(opts.outfit)))
        relax_pose(human)
    else:
        simple_skin(human, look["skin"])
    height = human.dimensions.z
    setup_stage(height, opts.size)
    os.makedirs(opts.out, exist_ok=True)
    render_to(os.path.join(opts.out, "front.png"))
    print(f"TINY_RENDERED {opts.out}/front.png height={height:.2f}")
    if opts.sprites and opts.outfit:
        render_sprites(human, os.path.join(opts.out, "sprites"))


if __name__ == "__main__":
    main()
