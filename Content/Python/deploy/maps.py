# -*- coding: utf-8 -*-
"""deploy.maps - the map marker's cook plan (editor only).

A mod folder may hold a map marker (DA_MapMarker_V2 or a subclass) that lists the BASE maps the mod
ships, plus extra maps and the sublevel "layers" those maps need. When one is present the cooker cooks
only those maps (every other asset in the folder is still cooked). The marker is read through the
SDK's `mapmarker` module (Content/Mods/ArgonSDK/Mods/ModLoading/Python), which uses the Asset Registry
and never loads a map.

Outside the editor there is no `unreal` module to read the marker with, and an SDK without the map
marker scripts has no `mapmarker`; both mean "no plan" and the cook keeps its old behavior (every map).
"""
from __future__ import print_function

import os

from deploy import engine

IGNORE_ENV = 'DEPLOY_IGNORE_MAP_MARKER'


def marker_plan(mod_dir):
    """{'markers', 'shipped', 'extra', 'layers', 'maps'} (package names) for `mod_dir`, or None.

    None means: not in the editor, the SDK has no mapmarker module, the folder has no map marker, or
    DEPLOY_IGNORE_MAP_MARKER is set. A marker that exists but cannot be read raises RuntimeError - cooking
    everything instead would silently ship maps the owner excluded.
    """
    if os.environ.get(IGNORE_ENV, '').strip().lower() in ('1', 'true', 'yes'):
        return None
    if not engine.in_editor():
        return None
    try:
        import mapmarker
    except ImportError:
        return None
    if not hasattr(mapmarker, 'cook_plan'):
        return None
    try:
        return mapmarker.cook_plan(mod_dir)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError('could not read the map marker below {0}: {1}'.format(mod_dir, error))
