# -*- coding: utf-8 -*-
"""deploy.pak - UnrealPak filelist (incl. _Content mount) + validated publish.

Port of cook_mod.py's pak path, behavior-preserving:
- write_filelist: normal files mount at ../../../TBL/Content/<moddir>/<rel>;
  files under <cooked>/_Content/ mount at ../../../TBL/Content/<inner>
  (content replacement at the game root, not inside the mod folder).
- UnrealPak create + -List are validated (deploy.unrealpak), then the pak and
  listing are published ATOMICALLY (os.replace / MoveFileExW). A failed create
  or list can never masquerade as a good old/partial pak.
"""
from __future__ import print_function

import os
import time

from deploy import engine, provenance, unrealpak

PROJECT_NAME = 'TBL'


# -- filelist ------------------------------------------------------------------

def write_filelist(cooked_dir, mod_dir, filelist_path):
    """UnrealPak response file for the cooked mod folder. Returns the file count."""
    content_rel = mod_dir[len('/Game/'):]
    destination_content = '../../../{0}/Content'.format(PROJECT_NAME)
    replacement = os.path.join(cooked_dir, '_Content')
    count = 0
    with open(filelist_path, 'w') as filelist:
        for root, _dirs, files in os.walk(cooked_dir):
            rel = os.path.relpath(root, cooked_dir).replace(os.sep, '/')
            rel = '' if rel == '.' else rel
            for name in files:
                source = os.path.join(root, name)
                if os.path.exists(replacement) and (root == replacement or root.startswith(replacement + os.sep)):
                    inner = os.path.relpath(root, replacement).replace(os.sep, '/')
                    target = '{0}/{1}/{2}'.format(destination_content, inner, name) if inner != '.' else '{0}/{1}'.format(destination_content, name)
                else:
                    target = '{0}/{1}/{2}{3}'.format(destination_content, content_rel, rel + '/' if rel else '', name)
                filelist.write('"{0}" "{1}"\n'.format(source, target))
                count += 1
    return count


# -- orchestration -------------------------------------------------------------

def pak_mod(result, dest_dir, ue4_root=None, compress=True):
    """Create and validate <dest>/<mod>.pak from a successful cook result. Returns its path."""
    if not result.get('success'):
        raise RuntimeError('The cook of {0} failed: nothing to pak'.format(result.get('mod')))
    engine_root = engine.find_engine(ue4_root)
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    mod_dir = '/Game/Mods/AgMods/' + result['mod']
    token = '{0}.{1}.{2}'.format(result['mod'], os.getpid(), int(time.time() * 1000))
    filelist = os.path.join(dest_dir, '.filelist_{0}.txt'.format(token))
    temp_pak = os.path.join(dest_dir, '.{0}.pak.tmp'.format(token))
    temp_listing = os.path.join(dest_dir, '.{0}.txt.tmp'.format(token))
    pak = os.path.join(dest_dir, result['mod'] + '.pak')
    listing_path = os.path.join(dest_dir, result['mod'] + '.txt')
    try:
        count = write_filelist(result['cooked_dir'], mod_dir, filelist)
        if count <= 0:
            raise RuntimeError('no cooked files were found below {0}'.format(result['cooked_dir']))
        unrealpak_run = unrealpak.discover_unrealpak(engine_root=engine_root)
        unrealpak.run_create(unrealpak_run, temp_pak, filelist, compress=compress)
        unrealpak.run_list(unrealpak_run, temp_pak, log_path=temp_listing)
        provenance._replace_file(temp_pak, pak)
        provenance._replace_file(temp_listing, listing_path)
        print('pak: {0}'.format(pak))
        print('packaged assets: {0}'.format(count))
        return pak
    finally:
        for path in (filelist, temp_pak, temp_listing, temp_pak + '.create.log', temp_pak + '.list.log'):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


def cook_and_pak(mod, dest_dir, uproject_path=None, ue4_root=None, max_chunk=250):
    """Cook the mod folder, then pak it into dest_dir. Returns (result, pak path or None)."""
    from deploy import cook
    result = cook.cook_mod(mod, uproject_path, ue4_root, max_chunk=max_chunk)
    return result, (pak_mod(result, dest_dir, ue4_root) if result['success'] else None)
