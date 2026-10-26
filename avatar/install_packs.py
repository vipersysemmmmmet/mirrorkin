"""Install downloaded MakeHuman asset packs into MPFB, using MPFB's own zip safety check.

Blender --background --python avatar/install_packs.py -- assets/makehuman_packs/*.zip
"""

import importlib
import sys
import zipfile

import bpy

MPFB = "bl_ext.blender_org.mpfb"
bpy.ops.preferences.addon_enable(module=MPFB)
AssetService = importlib.import_module(f"{MPFB}.services.assetservice").AssetService
LocationService = importlib.import_module(f"{MPFB}.services.locationservice").LocationService

target = LocationService.get_user_data()
for path in sys.argv[sys.argv.index("--") + 1:]:
    problem = AssetService.check_asset_pack_zip(path)
    if problem:
        print(f"PACK_SKIPPED {path}: {problem}")
        continue
    with zipfile.ZipFile(path) as z:
        z.extractall(target)
    print(f"PACK_INSTALLED {path}")
AssetService.update_all_asset_lists()
print("USER_DATA", target)
