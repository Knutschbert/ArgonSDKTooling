# -*- coding: utf-8 -*-
"""deploy.markers - what the V2 mod markers (DA_ModMarker_V2) decide about a cook and its paks.

The marker tree is read through the SDK's `modmarker` module (Content/Mods/ArgonSDK/Mods/ModLoading/Python),
which uses the Asset Registry and never loads an asset. Outside the editor, or in an SDK without that module,
there is simply no marker plan and the cook keeps its old behavior (one pak, everything in the folder).

ExcludedPaths patterns (per marker, relative to the marker's folder, matched against the asset path without
extension, case-insensitive):
  - no wildcard:  a path PREFIX      "Old/"  -> everything under Old/;  "Old" -> Old, Older, Old/X ...
  - with * or ?:  "*" matches any characters including "/", "?" exactly one. The pattern must match the whole
                  path OR one of its parent folders:  "*_bak" -> Foo_bak and everything under Foo_bak/;
                  "*/Backup" -> X/Backup/Anything;  "Maps/*_old" -> Maps/Town_old.
KEEP IN SYNC with modmarker.pattern_matches (SDK side, used by Validate); both test suites share the vectors.
"""
from __future__ import print_function

import os
import re

from deploy import engine

IGNORE_ENV = 'DEPLOY_IGNORE_MOD_MARKER'


# -- ExcludedPaths matching ----------------------------------------------------

def _regex(pattern):
    return re.compile('^' + ''.join('.*' if c == '*' else '.' if c == '?' else re.escape(c) for c in pattern) + '$')


def pattern_matches(rel_path, pattern):
    """True if `rel_path` (relative to the marker folder, no extension) is excluded by one pattern."""
    rel = rel_path.strip('/').lower()
    pat = pattern.strip().replace('\\', '/').lstrip('/').lower()
    if not pat:
        return False
    if '*' not in pat and '?' not in pat:
        return rel.startswith(pat)
    regex = _regex(pat)
    parts = rel.split('/')
    return any(regex.match('/'.join(parts[:i])) for i in range(1, len(parts) + 1))


def excluded(rel_path, patterns):
    """True if any of `patterns` excludes `rel_path`."""
    return any(pattern_matches(rel_path, p) for p in (patterns or ()))


def apply_excludes(packages, folder_patterns):
    """Split /Game/... packages into (kept, skipped) by each marker folder's ExcludedPaths.

    folder_patterns: {marker folder: [patterns]}; a package is only tested against the folders it lives in.
    """
    kept, skipped = [], []
    for package in packages:
        hit = False
        for folder, patterns in folder_patterns.items():
            if patterns and package.startswith(folder.rstrip('/') + '/') and excluded(package[len(folder.rstrip('/')) + 1:], patterns):
                hit = True
                break
        (skipped if hit else kept).append(package)
    return kept, skipped


# -- the plan ------------------------------------------------------------------

def preflight(mod_dir):
    """Editor only: stamp ModVersion from mod.json on the markers, then return the pak plan for `mod_dir`.

    {'paks': [{'root','stem','separate','markers'}], 'dirs': {marker: folder}, 'excluded': {marker: [patterns]},
    'versions', 'ids', 'replaces', 'warnings'} or None (not in the editor, no `modmarker` module, no V2 marker
    below the folder, or DEPLOY_IGNORE_MOD_MARKER set). A marker tree that cannot be read raises RuntimeError.
    """
    if os.environ.get(IGNORE_ENV, '').strip().lower() in ('1', 'true', 'yes'):
        return None
    if not engine.in_editor():
        return None
    try:
        import modmarker
    except ImportError:
        return None
    if not hasattr(modmarker, 'plan') or not hasattr(modmarker, 'find_markers'):
        return None
    try:
        if not modmarker.find_markers(mod_dir):
            return None
        if hasattr(modmarker, 'stamp_all'):
            stamped = modmarker.stamp_all(mod_dir)
            if stamped['markers'] or stamped['actors']:
                print('[deploy] versions stamped: {0} marker(s), {1} mod actor class(es)'.format(
                    len(stamped['markers']), len(stamped['actors'])))
        else:
            modmarker.stamp_versions(mod_dir)
        return modmarker.plan(mod_dir)
    except Exception as error:  # noqa: BLE001
        raise RuntimeError('could not read the mod markers below {0}: {1}'.format(mod_dir, error))


def folder_excludes(pak_plan):
    """{marker folder: [ExcludedPaths patterns]} for every marker in a plan."""
    return dict((pak_plan['dirs'][m], list(p)) for m, p in (pak_plan.get('excluded') or {}).items() if p and m in pak_plan['dirs'])


def bundled_folders(pak_plan, mod_dir):
    """Marker folders of the plan that lie OUTSIDE `mod_dir` (bundled markers from other directories)."""
    prefix = mod_dir.rstrip('/') + '/'
    return sorted(set(f for f in pak_plan['dirs'].values() if f != mod_dir.rstrip('/') and not f.startswith(prefix)))


def manifest_plan(pak_plan):
    """The part of a pak plan the cook records in CookModManifest.json (JSON-safe, what the pak step needs)."""
    return {'paks': [{'stem': p['stem'], 'root': p['root'], 'separate': bool(p['separate']), 'markers': list(p['markers'])}
                     for p in pak_plan['paks']],
            'dirs': dict(pak_plan['dirs']), 'excluded': dict((k, list(v)) for k, v in (pak_plan.get('excluded') or {}).items()),
            'warnings': list(pak_plan.get('warnings') or [])}
