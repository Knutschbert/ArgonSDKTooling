# -*- coding: utf-8 -*-
"""deploy.provenance - CookModManifest write/validate.

A single-mod cook is proven current by a manifest written ONLY after the final
chunk succeeds, recording the exact source files (path/size/mtime) + cooked dir
+ time. Pak-only mode rejects missing, interrupted, or source-mismatched output.
Port of cook_mod.py's provenance, behavior-preserving.
"""
from __future__ import print_function

import json
import os
import time


def _replace_file(source, destination):
    """Publish a validated file with an atomic replace where available."""
    if hasattr(os, 'replace'):
        os.replace(source, destination)
        return
    if os.name == 'nt':  # pragma: no cover - Windows fallback
        import ctypes
        move_file = ctypes.windll.kernel32.MoveFileExW
        move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move_file.restype = ctypes.c_int
        if not move_file(source, destination, 0x1 | 0x8):
            raise OSError('MoveFileExW failed while publishing {0}'.format(destination))
        return
    if os.path.exists(destination):
        os.remove(destination)
    os.rename(source, destination)


def write_manifest(path, mod, source_files, cooked_dir):
    """Publish provenance only after a completed (final-chunk) cook."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    temporary = path + '.tmp.{0}'.format(os.getpid())
    payload = {'complete': True, 'mod': mod, 'source_files': source_files,
               'cooked_dir': cooked_dir, 'completed': time.time()}
    try:
        with open(temporary, 'w') as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
        _replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def read_manifest(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (IOError, ValueError):
        return None


def validate_cooked_output(cooked_root, manifest_path, mod, current_sources):
    """Reject missing, incomplete, or source-mismatched single-mod cook output.

    Returns the manifest dict, or raises RuntimeError with a clear reason.
    """
    manifest = read_manifest(manifest_path)
    if not manifest or manifest.get('complete') is not True:
        raise RuntimeError('no completed cook provenance found at {0}'.format(manifest_path))
    if manifest.get('mod') != mod:
        raise RuntimeError('cook provenance belongs to {0}, not {1}'.format(manifest.get('mod'), mod))
    if manifest.get('source_files') != current_sources:
        raise RuntimeError('cooked output is stale: source assets changed since the recorded cook ({0})'.format(manifest_path))
    if not os.path.isdir(cooked_root):
        raise RuntimeError('cooked output directory is missing: {0}'.format(cooked_root))
    found = False
    for _root, _dirs, files in os.walk(cooked_root):
        if files:
            found = True
            break
    if not found:
        raise RuntimeError('cooked output directory is empty: {0}'.format(cooked_root))
    return manifest
