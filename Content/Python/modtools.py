# -*- coding: utf-8 -*-
"""modtools - create mod assets from the editor (the DA_ModMarker / ArgonSDKModBase creators).

Port of the SDK's Content/Python/modtools.py into the ArgonSDKTooling plugin.
Editor-only (uses the `unreal` module); imported by NewModWidget. The parent
class references are content paths (shared SDK Blueprints) so they are portable
across SDKs - no filesystem paths are hardcoded here.

    create_mod(name, path)         -> (CDO, Asset) of an ArgonSDKModBase actor
    create_mod_marker(name, path)  -> (CDO, Asset) of a DA_ModMarker data asset
    get_object_cdo(object)         -> class default object
"""
import unreal
from unreal import WorldFactory, AssetToolsHelpers  # noqa: F401  (kept for parity)

ASSET_TOOLS = unreal.AssetToolsHelpers.get_asset_tools()  # type: unreal.AssetTools


def get_object_cdo(object):
    # type: (unreal.Object) -> unreal.Object
    da_class = unreal.load_object(None, object.get_path_name())
    cdo = unreal.get_default_object(da_class)
    return cdo


DA_FACTORY = unreal.DataAssetFactory()
da_parent_class = unreal.get_default_object(unreal.load_object(
    None, '/Game/Mods/ArgonSDK/Mods/ModLoading/DA_ModMarker.DA_ModMarker_C')).get_class()
DA_FACTORY.set_editor_property("DataAssetClass", da_parent_class)


def create_mod_marker(name, path):
    # type: (str, str) -> tuple
    new_item = ASSET_TOOLS.create_asset(
        "{}_Marker".format(name),
        "{}/{}".format(path, name),
        unreal.DataAsset,
        DA_FACTORY
    )  # type: unreal.Object
    da_class = unreal.load_object(None, "{0}/{1}/{1}.{1}_C".format(path, name))
    cdo = unreal.get_default_object(da_class)
    unreal.EditorAssetLibrary.save_loaded_asset(new_item)
    return cdo, new_item


BP_ASSET_FACTORY = unreal.BlueprintFactory()
parent_class = unreal.get_default_object(unreal.load_object(
    None, '/Game/Mods/ArgonSDK/Mods/ArgonSDKModBase.ArgonSDKModBase_C')).get_class()
BP_ASSET_FACTORY.set_editor_property("ParentClass", parent_class)


def create_mod(name, path):
    # type: (str, str) -> tuple
    new_item = ASSET_TOOLS.create_asset(
        name,
        "{}/{}".format(path, name),
        unreal.Blueprint,
        BP_ASSET_FACTORY
    )  # type: unreal.Object
    bp_class = unreal.load_object(None, "{0}/{1}/{1}.{1}_C".format(path, name))
    cdo = unreal.get_default_object(bp_class)
    # cdo.set_editor_property('bClientside', True)
    unreal.EditorAssetLibrary.save_loaded_asset(new_item)
    return cdo, new_item


# -- V2 marker (vje.15 schema) -------------------------------------------------

# The DA_ModMarkerV2 DataAsset class MUST exist first - create it to the vje.15
# spec, then this helper creates/updates STABLE per-mod instances of it.
DA_V2_CLASS_PATH = '/Game/Mods/ArgonSDK/Mods/ModLoading/DA_ModMarkerV2'


def _set_soft_array(obj, property_name, values, unreal_module=unreal):
    """Best-effort set of a TArray<FSoft*Path> property from path strings.

    The exact Python representation for soft-path arrays can vary by UE version;
    this is wrapped so a failed set is reported rather than silent, and the
    format may need confirming once in the editor.
    """
    if values is None:
        return 0
    try:
        obj.set_editor_property(property_name, values)
        return 1
    except Exception as error:
        raise RuntimeError('could not set {0} on V2 marker ({1}): {2}'.format(
            property_name, DA_V2_CLASS_PATH, error))


def create_mod_marker_v2(mod_id, path, mod_version=None, class_paths=None,
                         assets=None, referenced_markers=None, exclude_paths=None):
    """Create (or update, idempotently) the DA_ModMarkerV2 in <path>/DA_ModMarkerV2.

    Editor-only, per vje.15: one V2 marker per mod root, deterministic name,
    base directory = `path`. Re-runs sync, never duplicate. Only create after the
    DA_ModMarkerV2 class exists in the SDK.
    """
    v2_class = unreal.load_object(None, DA_V2_CLASS_PATH)
    if v2_class is None:
        raise RuntimeError('DA_ModMarkerV2 class not found at {0} - create it to spec first'.format(DA_V2_CLASS_PATH))
    asset_path = '{0}/DA_ModMarkerV2'.format(path)
    asset = unreal.EditorAssetLibrary.load_asset(asset_path)
    if asset is None:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("DataAssetClass", v2_class)
        asset = ASSET_TOOLS.create_asset('DA_ModMarkerV2', path, unreal.DataAsset, factory)
    if asset is None:
        raise RuntimeError('could not create/load V2 marker at {0}'.format(asset_path))

    asset.set_editor_property('schema_version', 1)
    asset.set_editor_property('mod_id', mod_id)
    if mod_version:
        asset.set_editor_property('mod_version', mod_version)
    if class_paths is not None:
        _set_soft_array(asset, 'class_paths', class_paths)
    if assets is not None:
        _set_soft_array(asset, 'assets', assets)
    if referenced_markers is not None:
        _set_soft_array(asset, 'referenced_markers', referenced_markers)
    if exclude_paths is not None:
        asset.set_editor_property('exclude_paths', exclude_paths)

    unreal.EditorAssetLibrary.save_loaded_asset(asset)
    return unreal.get_default_object(v2_class), asset

