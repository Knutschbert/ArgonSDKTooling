# -*- coding: utf-8 -*-
"""deploy.pak - UnrealPak filelist (incl. _Content mount) + validated publish.

Port of cook_mod.py's pak path, behavior-preserving:
- write_filelist: normal files mount at ../../../TBL/Content/<moddir>/<rel>;
  files under <cooked>/_Content/ mount at ../../../TBL/Content/<inner>
  (content replacement at the game root, not inside the mod folder).
- UnrealPak create + -List are validated (deploy.unrealpak), then the pak(s) and
  listing(s) are published ATOMICALLY (os.replace / MoveFileExW). A failed
  create or list can never masquerade as a good old/partial pak.

Optional PER-SIZE SPLITTING (vje.13): when max_pak_size is set and the cooked
output exceeds it, the response file is partitioned by destination PACKAGE (base
path - a package's .uasset/.uexp/.ubulk stay together) into N parts, published
as <mod>_part1.pak, <mod>_part2.pak, ... A single package larger than the
threshold keeps its own (oversized) part - we never split a package. If the
output is within the threshold, an ordinary single <mod>.pak is produced.
"""
from __future__ import print_function

import os
import time
from collections import OrderedDict

from deploy import engine, provenance, unrealpak

PROJECT_NAME = 'TBL'

# Step timing for one pak, collected on the worker thread. Timestamps are
# recorded, and printed ONCE at the end as deltas (avoids per-line print/flush
# lag on the worker thread skewing the numbers).
try:
    _STEPS
except NameError:  # pragma: no cover - module reload in the editor
    _STEPS = []



def _step(label):
    return _STEPS.append((label, _mono()))


def _mono():
    return time.time()


def _dump_steps(mod):
    """Print one consolidated timing line for the whole pak (labels in order)."""
    if not _STEPS:
        return
    t0 = _STEPS[0][1]
    deltas = []
    prev = t0
    for label, ts in _STEPS:
        deltas.append((label, ts - prev))
        prev = ts
    total = prev - t0
    parts = ' ; '.join('{0}={1:.2f}s'.format(k, v) for k, v in deltas)
    print('[deploy-step] pak {0}: total={1:.2f}s {{{2}}}'.format(mod, total, parts))
    del _STEPS[:]


_MULTIPLIERS = {'kb': 1024 ** 1, 'mb': 1024 ** 2, 'gb': 1024 ** 3,
                'k': 1024 ** 1, 'm': 1024 ** 2, 'g': 1024 ** 3, 'b': 1}


def parse_size(text):
    """Parse '200MB' / '1g' / '52428800' into bytes (case-insensitive)."""
    text = str(text).strip().lower()
    suffix = None
    for suf in ('kb', 'mb', 'gb', 'k', 'm', 'g', 'b'):
        if text.endswith(suf):
            suffix = suf
            break
    number = text[:-len(suffix)] if suffix else text
    number = number.strip()
    try:
        value = float(number) if number else 0.0
    except ValueError:
        raise ValueError('cannot parse size: {0!r}'.format(text))
    if value < 0 or not number:
        raise ValueError('cannot parse size: {0!r}'.format(text))
    return int(value * _MULTIPLIERS.get(suffix, 1))


# -- filelist ------------------------------------------------------------------

def _build_entries(cooked_dir, mod_dir):
    """[(source, target, size)] for every file, mount target + size for splitting.

    Mount: normal files -> ../../../TBL/Content/<moddir>/<rel>; files under
    <cooked>/_Content -> ../../../TBL/Content/<inner> (content replacement).
    """
    content_rel = mod_dir[len('/Game/'):]
    destination_content = '../../../{0}/Content'.format(PROJECT_NAME)
    replacement = os.path.join(cooked_dir, '_Content')
    entries = []
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
            try:
                size = os.path.getsize(source)
            except OSError:
                size = 0
            entries.append((source, target, size))
    entries.sort(key=lambda entry: entry[1])
    return entries


def write_entries(entries, filelist_path):
    """Write UnrealPak response entries. Returns the file count."""
    with open(filelist_path, 'w') as filelist:
        for source, target, _size in entries:
            filelist.write('"{0}" "{1}"\n'.format(source, target))
    return len(entries)


def write_filelist(cooked_dir, mod_dir, filelist_path):
    """Compatibility path: build entries for a cooked mod folder and write them."""
    return write_entries(_build_entries(cooked_dir, mod_dir), filelist_path)


def _partition_entries(entries, max_bytes):
    """Partition entries by destination PACKAGE (base path) at <= max_bytes each.

    A package's files (.uasset/.uexp/.ubulk share one base) are never split
    across parts. A package larger than max_bytes keeps its own oversized part.
    Returns a list of parts, each a list of (source, target, size).
    """
    groups = OrderedDict()
    for entry in entries:
        _source, target, _size = entry
        base = os.path.splitext(target)[0]
        groups.setdefault(base, []).append(entry)
    parts, current, current_size = [], [], 0
    for _base, group in groups.items():
        group_size = sum(e[2] for e in group)
        if current and current_size + group_size > max_bytes:
            parts.append(current)
            current, current_size = [], 0
        current.extend(group)
        current_size += group_size
    if current:
        parts.append(current)
    return parts or [entries]


# -- orchestration -------------------------------------------------------------

def pak_mod(result, dest_dir, ue4_root=None, compress=True, max_pak_size=None):
    """Create/validate <dest>/<mod>.pak from a successful cook result, optionally split by size.

    Returns the pak path (str) when not split, or a list of part pak paths when
    split into multiple parts.
    """
    if not result.get('success'):
        raise RuntimeError('The cook of {0} failed: nothing to pak'.format(result.get('mod')))
    _step('find_engine')
    engine_root = engine.find_engine(ue4_root)
    _step('makedirs')
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    # The mod's REAL /Game/... root (resolved at cook time). Fall back to AgMods
    # for older result dicts.
    mod_dir = result.get('mod_dir') or ('/Game/Mods/AgMods/' + result['mod'])
    _step('build_entries')
    entries = _build_entries(result['cooked_dir'], mod_dir)
    _step('parse_partition')
    if not entries:
        raise RuntimeError('no cooked files were found below {0}'.format(result['cooked_dir']))

    total_size = sum(e[2] for e in entries)
    threshold = parse_size(max_pak_size) if max_pak_size else None
    if threshold is not None and total_size > threshold:
        parts = _partition_entries(entries, threshold)
    else:
        parts = [entries]
    multi = len(parts) > 1

    _step('discover_unrealpak')
    unrealpak_run = unrealpak.discover_unrealpak(engine_root=engine_root)
    token = '{0}.{1}.{2}'.format(result['mod'], os.getpid(), int(time.time() * 1000))
    temps, published = [], []
    try:
        for idx, part in enumerate(parts, start=1):
            stem = '{0}_part{1}'.format(result['mod'], idx) if multi else result['mod']
            suffix = '_' + str(idx)
            filelist = os.path.join(dest_dir, '.filelist_{0}{1}.txt'.format(token, suffix))
            temp_pak = os.path.join(dest_dir, '.{0}{1}.pak.tmp'.format(token, suffix))
            temp_listing = os.path.join(dest_dir, '.{0}{1}.txt.tmp'.format(token, suffix))
            article = os.path.join(dest_dir, stem + '.pak')
            listing_path = os.path.join(dest_dir, stem + '.txt')
            _step('write_filelist')
            write_entries(part, filelist)
            _step('run_create')
            unrealpak.run_create(unrealpak_run, temp_pak, filelist, compress=compress)
            _step('run_list')
            unrealpak.run_list(unrealpak_run, temp_pak, log_path=temp_listing)
            _step('publish')
            provenance._replace_file(temp_pak, article)
            provenance._replace_file(temp_listing, listing_path)
            _step('part_done')
            print('pak part: {0} ({1} files, {2} bytes)'.format(
                article, len(part), sum(e[2] for e in part)))
            published.append(article)
            temps.extend([filelist, temp_pak, temp_listing,
                          temp_pak + '.create.log', temp_pak + '.list.log'])
    finally:
        for path in temps:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    print('pak: {0} ({1} asset(s), {2} part(s))'.format(
        ', '.join(published), len(entries), len(published)))
    _dump_steps(result['mod'])
    return published[0] if len(published) == 1 else published


def cook_and_pak(mod, dest_dir, uproject_path=None, ue4_root=None, max_chunk=250, max_pak_size=None):
    """Cook the mod folder, then pak it into dest_dir. Returns (result, pak path(s) or None)."""
    from deploy import cook
    result = cook.cook_mod(mod, uproject_path, ue4_root, max_chunk=max_chunk)
    return result, (pak_mod(result, dest_dir, ue4_root, max_pak_size=max_pak_size) if result['success'] else None)
