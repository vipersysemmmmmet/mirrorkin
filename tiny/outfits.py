"""Turn look settings into installed MakeHuman assets for the Blender build (avatar/build_tiny.py).

Shared by the desktop app and the server so a look builds the same tiny everywhere.
Colours are applied in the build script by tinting each asset's own texture, so fabric
and hair keep their detail.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HAIR = {
    "short": "short02", "long": "long01", "curly": "o4saken_curly01",
    "long curly": "punkduck_alpha7_curly", "afro": "afro01", "bun": "elvs_braid_bun",
    "updo": "elvs_50s_updo", "ponytail": "ponytail01", "bob": "bob02", "braids": "braid01",
    "cornrows": "elvs_braided_rows", "buzz": "short04", "bald": None,
}

# Separate tops worn over the bottoms
FEMININE_TOPS = {
    "t-shirt": "toigo_basic_tucked_t-shirt", "shirt": "toigo_basic_tucked_t-shirt",
    "polo": "toigo_basic_tucked_t-shirt",
    "long sleeves": "toigo_fisherman_sweater", "sweater": "toigo_fisherman_sweater",
    "jacket": "toigo_fisherman_sweater", "suit": "toigo_fisherman_sweater",
    "tank top": "toigo_camisole_top", "camisole": "toigo_camisole_top",
    "turtleneck": "toigo_turtleneck_halter_top", "corset top": "toigo_bodice-style_top",
    "tube top": "skalldyrssuppe_tube_top_funky_colors",
}
# The installed packs only have trousers inside full suits; the sports suit's crop top sits
# under any separate top without showing through
FEMININE_BOTTOMS = {
    "pants": "female_sportsuit01", "jeans": "female_sportsuit01", "shorts": "female_casualsuit02",
    "skirt": "toigo_skirt_with_lace_ruffle", "mini skirt": "toigo_tiered_mini_skirt",
    "long skirt": "toigo_long_full_skirt",
}
# Masculine outfits are whole suits (top and trousers in one garment)
MASCULINE_SUITS = {
    "t-shirt": "male_casualsuit06", "polo": "male_casualsuit04", "tank top": "male_casualsuit06",
    "long sleeves": "male_casualsuit02", "sweater": "male_casualsuit02", "turtleneck": "male_casualsuit02",
    "shirt": "male_casualsuit03", "jacket": "male_casualsuit05", "suit": "male_elegantsuit01",
}

# Skin textures from dark to light, picked by the skin colour measured from the photo.
# The build then shifts the texture to that exact colour; this only picks the closest detail.
SKINS = {
    "feminine": [(0.55, "young_african_female"), (0.66, "cutoff3d_indian_female_skin"),
                 (0.74, "toigo_light_skin_female_bronze"), (1.01, "young_caucasian_female")],
    "masculine": [(0.55, "young_african_male"), (0.72, "young_asian_male"), (1.01, "young_caucasian_male")],
}

SHOES = "shoes03"


# MakeHuman body sliders for each build
BUILDS = {
    "slim": {"weight": 0.3, "cupsize": 0.45},
    "average": {"weight": 0.45, "cupsize": 0.5},
    "curvy": {"weight": 0.62, "cupsize": 0.68, "proportions": 0.8},
    "plus-size": {"weight": 0.82, "cupsize": 0.72},
}


def body_for(look: dict) -> dict:
    body = json.loads((ROOT / "avatar" / "body.json").read_text())
    body["gender"] = 1.0 if look.get("body") == "masculine" else 0.0
    return {**body, **BUILDS.get(look.get("build"), {})}


def brightness(hex_colour: str) -> float:
    r, g, b = (int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _close(a: str, b: str) -> bool:
    ca = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5 < 70


def outfit_for(look: dict) -> dict:
    """Assets for a look. `bottom_color: None` means keep a suit's own colours."""
    base = json.loads((ROOT / "avatar" / "outfit.json").read_text())
    body = "masculine" if look.get("body") == "masculine" else "feminine"
    tone = brightness(look.get("skin", "#e0ac7e"))
    skin = next(name for limit, name in SKINS[body] if tone < limit)
    top_style = look.get("top_style", "t-shirt").strip().lower()
    bottom_style = look.get("bottom_style", "pants").strip().lower()
    outfit = {**base, "skin": skin, "hair": HAIR.get(look.get("hair_style"), base["hair"]),
              "shoes": SHOES, "tint_bottom": True}
    if body == "masculine" and "skirt" not in bottom_style:
        outfit["top"] = None
        outfit["bottom"] = MASCULINE_SUITS.get(top_style, top_style)
        if top_style not in MASCULINE_SUITS and not top_style:
            raise ValueError("Enter a clothing style or choose one of the listed styles.")
        # One garment can only take one colour: tint it when top and bottoms match, else keep its own
        outfit["tint_bottom"] = _close(look.get("top_color", "#000000"), look.get("bottom_color", "#000000"))
    else:
        outfit["top"] = FEMININE_TOPS.get(top_style, top_style)
        outfit["bottom"] = FEMININE_BOTTOMS.get(bottom_style, bottom_style)
        if not top_style or not bottom_style:
            raise ValueError("Enter both a top and a bottoms style.")
    return outfit
