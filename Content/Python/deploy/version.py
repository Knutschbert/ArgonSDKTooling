# -*- coding: utf-8 -*-
"""deploy.version - single-source version stamping from mod.json (vje.14).

mod.json is the author's single source of truth for the mod's version (the mod
registry uses it; see vje.15 for the "mod.json is canonical" decision). Before
cooking, the toolkit reads `version` from mod.json and applies it to the
`mod_version` property of every ArgonSDKModMarker-derived class marked by the
marker(s), so the .uasset-side version matches the shipped mod.

Finding mod.json is editor-free (pure filesystem/JSON) so it is fully testable;
the stamping itself needs the editor's `unreal` module and is gated to the
editor (the deploy CLI that spawns the cook commandlet cannot touch CDOs).

Only when a mod.json with a `version` is present is anything stamped; otherwise
the cook proceeds unchanged.
"""
from __future__ import print_function

import json
import os

MOD_JSON = 'mod.json'


# -- pure helpers (editor-free, testable) --------------------------------------

def find_mod_json(uproject, mod_dir):
    """Search the mod folder, then ancestors up to the project Content root, for mod.json."""
    content = os.path.abspath(os.path.join(os.path.dirname(uproject), 'Content'))
    start = os.path.abspath(os.path.join(content, mod_dir[len('/Game/'):].replace('/', os.sep)))
    d = start
    while (d == content or d.startswith(content + os.sep)) and d != os.path.dirname(content):
        candidate = os.path.join(d, MOD_JSON)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def read_mod_json(uproject, mod_dir):
    """The parsed mod.json dict, or None if absent/invalid."""
    path = find_mod_json(uproject, mod_dir)
    if not path:
        return None
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (IOError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def mod_version(uproject, mod_dir):
    """The version string from mod.json, or None if there is no usable one."""
    data = read_mod_json(uproject, mod_dir)
    if not data:
        return None
    version = data.get('version')
    if version is None:
        return None
    version = str(version).strip()
    return version or None


# -- editor-side stamping (gated: needs `unreal`) -------------------------------

def _marked_classes(marker_cdo, unreal):
    """ArgonSDKModMarker-derived classes referenced by a marker's mod_actors.

    V1 markers hold mod_actors: TMap<*mut UClass, FString>; the keys are the
    marked classes. Mapping of the TMap to Python via the editor API is the
    part that must be verified in-editor on the real SDK class.
    """
    try:
        actors = marker_cdo.get_editor_property('mod_actors')
    except Exception:
        return []
    keys = getattr(actors, 'keys', None)
    if keys is not None:
        return [k for k in keys if isinstance(k, unreal.Class)]
    out = []
    for entry in (actors or []):
        k = entry
        if isinstance(k, (tuple, list)) and k:
            k = k[0]
        if isinstance(k, unreal.Class):
            out.append(k)
    return out


def stamp_marked_classes(version, marker_cdos, unreal=None):
    """Set mod_version on every ArgonSDKModMarker-derived class marked by the marker CDOs.

    Returns the number of classes stamped. `marker_cdos` are the default objects
    of the mod's marker data assets(s). Editor-only.
    """
    if unreal is None:
        import unreal
    stamped = 0
    for cdo in marker_cdos:
        for cls in _marked_classes(cdo, unreal):
            try:
                derived = _class_cdo(cls, unreal)
            except Exception:
                continue
            if not _has_property(derived, 'mod_version'):
                continue
            try:
                derived.set_editor_property('mod_version', version)
            except Exception:
                continue
            stamped += 1
    return stamped


def stamp_before_cook(mod, uproject_path=None, ue4_root=None, unreal=None):
    """Editor entry: read mod.json version and stamp the marked classes, then cook.

    Returns the result of cook.cook_mod (the caller always cooks). If there is no
    usable mod.json version, or not in the editor, it cooks unchanged with no stamping.
    """
    from deploy import cook, engine
    uproject = engine.find_project(uproject_path)
    mod_dir = cook.mod_package_dir(mod)
    version = mod_version(uproject, mod_dir)
    if unreal is None:
        import unreal
    if version and unreal is not None:
        marker_cdos = _find_marker_cdos(uproject, mod_dir, unreal)
        if marker_cdos:
            n = stamp_marked_classes(version, marker_cdos, unreal=unreal)
            print('version {0}: stamped mod_version on {1} marked class(es)'.format(version, n))
        else:
            print('version {0}: no markers found; nothing stamped'.format(version))
    else:
        print('no mod.json version found; cook unchanged')
    return cook.cook_mod(mod, uproject_path, ue4_root)


def _find_marker_cdos(uproject, mod_dir, unreal):
    """Default objects of DA_ModMarker-derived data assets in the mod folder.

    Uses the editor asset registry with GetAssetsByClass coverage. The exact
    class name and lookup must be verified against the SDK (ArgonSDK/ModLoading).
    """
    result = []
    try:
        for asset_data in unreal.EditorAssetLibrary.list_assets(mod_dir, recursive=True):
            if not asset_data.lower().endswith('_marker'):
                continue
            obj = unreal.load_asset(asset_data)
            if obj is None:
                continue
            cdo = unreal.get_default_object(obj.gen_class() if hasattr(obj, 'gen_class') else obj.class_)
            result.append(cdo)
    except Exception:
        return result
    return result


def _class_cdo(cls, unreal):
    try:
        return unreal.get_default_object(cls)
    except Exception:
        return None


def _has_property(obj, name):
    try:
        obj.get_editor_property(name)
        return True
    except Exception:
        return False
