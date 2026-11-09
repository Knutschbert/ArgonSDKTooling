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
import shutil
import subprocess
import time
from collections import OrderedDict

from deploy import engine, provenance, unrealpak

PROJECT_NAME = 'TBL'

# Step timing for one pak, collected on the worker thread. Timestamps are
# recorded, and printed ONCE at the end as deltas (avoids per-line print/flush
# lag on the worker thread skewing the numbers). Each entry records BOTH wall
# time and the calling thread's CPU time, so we can tell "thread was descheduled
# / GIL-starved" (wall high, cpu low) apart from "actually busy or blocking".
try:
    _STEPS
except NameError:  # pragma: no cover - module reload in the editor
    _STEPS = []

try:
    _TCPU = time.thread_time
except AttributeError:  # Python 2.7 safe fallback
    _TCPU = time.time


def _step(label):
    return _STEPS.append([label, _mono(), _TCPU()])


def _mono():
    return time.time()


def stall_probe(tag):
    """Busy pure-Python burst whose wall duration exposes scheduling starvation.

    A healthy interpreter completes this in a fraction of a second. If the same
    burst takes multiple seconds while running inside the editor, the worker
    thread is starved (UE 4.25 editor Python holds the GIL on the game thread;
    deploy's async pak runs on a background thread).
    """
    started = _mono()
    total = 0
    i = 0
    while i < 3000000:
        total += i
        i += 1
    print('[deploy-step] stall {0}: busy-burst w{1:.2f}s'.format(tag, _mono() - started))


def _dump_steps(mod):
    """Print one consolidated timing line for the whole pak (labels in order)."""
    if not _STEPS:
        return
    w0, c0 = _STEPS[0][1], _STEPS[0][2]
    deltas, prev_w, prev_c = [], w0, c0
    for label, ts, tcpu in _STEPS:
        deltas.append((label, ts - prev_w, tcpu - prev_c))
        prev_w, prev_c = ts, tcpu
    total, tcpuf = prev_w - w0, prev_c - c0
    parts = ' ; '.join('{0}=w{1:.2f}c{2:.2f}'.format(k, w, c) for k, w, c in deltas)
    print('[deploy-step] pak {0}: total=w{1:.2f}s cpu={2:.2f}s {{{3}}}'.format(mod, total, tcpuf, parts))
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
            # The runtime registry is emitted beside TBL/Content, not as part
            # of the mod content tree. If a caller points cooked_dir at a
            # broader cooked root, do not package it under its source name;
            # _mod_asset_registry_entry adds the intentional renamed entry.
            if root == os.path.abspath(cooked_dir) and name == 'AssetRegistry.bin':
                continue
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


def _runtime_registry_path(cooked_dir, mod_dir):
    """Return the cooked runtime AssetRegistry.bin beside the cooked TBL root."""
    content_dir = os.path.dirname(os.path.normpath(cooked_dir))
    while os.path.basename(content_dir).lower() != 'content':
        parent = os.path.dirname(content_dir)
        if parent == content_dir:
            return os.path.abspath(os.path.join(cooked_dir, 'AssetRegistry.bin'))
        content_dir = parent
    return os.path.abspath(os.path.join(os.path.dirname(content_dir), 'AssetRegistry.bin'))


def _stage_mod_asset_registry(cooked_dir, mod_dir):
    """Stage the runtime registry under its final pak basename, if present.

    The cooked runtime registry lives at ``<cook-output>/TBL/AssetRegistry.bin``.
    UnrealPak's response-file target handling varies between builds, so do not
    rely on it to rename this file. Copy it into the cooked mod folder with its
    final name; normal filelist handling then supplies both the desired path and
    basename. The caller removes the temporary staged copy after paking.
    """
    source = _runtime_registry_path(cooked_dir, mod_dir)
    if not os.path.isfile(source):
        return None
    staged = os.path.join(cooked_dir, 'ModAssetRegistry.bin')
    if os.path.exists(staged):
        raise RuntimeError('refusing to overwrite reserved staged registry path: {0}'.format(staged))
    if not os.path.isdir(cooked_dir):
        os.makedirs(cooked_dir)
    shutil.copyfile(source, staged)
    return staged


def write_entries(entries, filelist_path):
    """Write UnrealPak response entries. Returns the file count."""
    with open(filelist_path, 'w') as filelist:
        for source, target, _size in entries:
            filelist.write('"{0}" "{1}"\n'.format(source, target))
    return len(entries)


def write_listing(entries, listing_path):
    """Write a man-readable pak listing (target mount paths) WITHOUT spawning UnrealPak.

    Replaces the UnrealPak `-List` pass, which is a whole second subprocess launch
    just to echo back what -create just wrote. The targets are already known from
    the entries we built, so we write them directly. Returns the target count.
    """
    with open(listing_path, 'w') as listing:
        for _source, target, _size in entries:
            listing.write(target + '\n')
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

def pak_mod(result, dest_dir, ue4_root=None, compress=True, max_pak_size=None, run_list=True,
            include_mod_asset_registry=True):
    """Create/validate <dest>/<mod>.pak from a successful cook result, optionally split by size.

    ue4_root should be a RESOLVED engine path when called from a worker thread
    (find_engine returns a provided path without touching the Unreal API).
    run_list=True (default) additionally runs an UnrealPak -List pass to validate
    the pak's contents. run_list=False emits the .txt listing locally from the
    entries (no second UnrealPak subprocess) — faster, still produces the .txt.
    include_mod_asset_registry=True stages the cooked runtime registry as
    ``<mod content path>/ModAssetRegistry.bin`` when it exists.

    Returns the pak path (str) when not split, or a list of part pak paths when
    split into multiple parts.
    """
    if not result.get('success'):
        raise RuntimeError('The cook of {0} failed: nothing to pak'.format(result.get('mod')))
    # Busy-burst probes bracket the whole pak: if the END burst also takes many
    # seconds, the worker thread is starved and the step wall times below are
    # scheduling artifacts, not disk/UnrealPak reality. (The entry burst lands
    # on whatever the worker thread was doing before pak_mod.)
    stall_probe('pak-entry')
    _step('find_engine')
    engine_root = engine.find_engine(ue4_root)
    _step('makedirs')
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    # The mod's REAL /Game/... root (resolved at cook time). Fall back to AgMods
    # for older result dicts.
    mod_dir = result.get('mod_dir') or ('/Game/Mods/AgMods/' + result['mod'])
    # Everything after engine/dest setup sits inside try/finally below so ANY
    # failure (bad max-pak-size string, no cooked files, UnrealPak discovery,
    # create/list validation) still un-stages the temporary registry copy.
    temps, published = [], []
    staged_registry = None
    try:
        if include_mod_asset_registry:
            _step('stage_asset_registry')
            staged_registry = _stage_mod_asset_registry(result['cooked_dir'], mod_dir)
        _step('build_entries')
        entries = _build_entries(result['cooked_dir'], mod_dir)
        _step('parse_partition')
        if not entries:
            raise RuntimeError('no cooked files were found below {0}'.format(result['cooked_dir']))
        _step('partition_total')

        total_size = sum(e[2] for e in entries)
        threshold = parse_size(max_pak_size) if max_pak_size else None
        _step('partition_plan')
        if threshold is not None and total_size > threshold:
            parts = _partition_entries(entries, threshold)
        else:
            parts = [entries]
        multi = len(parts) > 1
        _step('discover_unrealpak')
        unrealpak_run = unrealpak.discover_unrealpak(engine_root=engine_root)
        token = '{0}.{1}.{2}'.format(result['mod'], os.getpid(), int(time.time() * 1000))
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
            if run_list:
                unrealpak.run_list(unrealpak_run, temp_pak, log_path=temp_listing)
            else:
                write_listing(part, temp_listing)
            _step('publish_pak')
            provenance._replace_file(temp_pak, article)
            _step('publish_listing')
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
        if staged_registry:
            try:
                if os.path.exists(staged_registry):
                    os.remove(staged_registry)
            except OSError:
                pass

    print('pak: {0} ({1} asset(s), {2} part(s))'.format(
        ', '.join(published), len(entries), len(published)))
    stall_probe('pak-exit')
    _dump_steps(result['mod'])
    return published[0] if len(published) == 1 else published


# -- non-blocking pak (editor) ---------------------------------------------------

class PakJob(object):
    """A running pak driven by poll() - NO worker thread.

    The editor's embedded Python 2.7 starves background threads (the worker never
    got the GIL, so the async pak sat forever before its first line). So, like
    cook.CookJob, all Python runs on the caller's (game) thread and only the
    UnrealPak subprocess runs concurrently. Preparation (stage registry, entry
    list, filelist) is quick file work done in __init__; poll() returns None while
    UnrealPak runs, then the published pak path (str, or list when split) - or
    raises RuntimeError on failure (temp files and the staged registry are always
    cleaned up first).
    """

    def __init__(self, result, dest_dir, ue4_root=None, compress=True, max_pak_size=None,
                 run_list=False, include_mod_asset_registry=True):
        if not result.get('success'):
            raise RuntimeError('The cook of {0} failed: nothing to pak'.format(result.get('mod')))
        self.result, self.dest_dir, self.compress, self.run_list = result, dest_dir, compress, run_list
        self.temps, self.published = [], []
        self.staged_registry = None
        self.process, self._log_handle, self._part = None, None, None
        self.index = -1
        self.path = None
        self._done = False
        try:
            _step('find_engine')
            engine_root = engine.find_engine(ue4_root)
            if not os.path.isdir(dest_dir):
                os.makedirs(dest_dir)
            mod_dir = result.get('mod_dir') or ('/Game/Mods/AgMods/' + result['mod'])
            if include_mod_asset_registry:
                _step('stage_asset_registry')
                self.staged_registry = _stage_mod_asset_registry(result['cooked_dir'], mod_dir)
            _step('build_entries')
            entries = _build_entries(result['cooked_dir'], mod_dir)
            if not entries:
                raise RuntimeError('no cooked files were found below {0}'.format(result['cooked_dir']))
            threshold = parse_size(max_pak_size) if max_pak_size else None
            if threshold is not None and sum(e[2] for e in entries) > threshold:
                self.parts = _partition_entries(entries, threshold)
            else:
                self.parts = [entries]
            self.entries = entries
            self.multi = len(self.parts) > 1
            _step('discover_unrealpak')
            self.unrealpak = unrealpak.discover_unrealpak(engine_root=engine_root)
            self.token = '{0}.{1}.{2}'.format(result['mod'], os.getpid(), int(time.time() * 1000))
            self._start_part()
        except Exception:
            self._cleanup()
            raise

    def _start_part(self):
        self.index += 1
        idx = self.index + 1
        mod = self.result['mod']
        part = self.parts[self.index]
        stem = '{0}_part{1}'.format(mod, idx) if self.multi else mod
        suffix = '_' + str(idx)
        self._filelist = os.path.join(self.dest_dir, '.filelist_{0}{1}.txt'.format(self.token, suffix))
        self._temp_pak = os.path.join(self.dest_dir, '.{0}{1}.pak.tmp'.format(self.token, suffix))
        self._temp_listing = os.path.join(self.dest_dir, '.{0}{1}.txt.tmp'.format(self.token, suffix))
        self._article = os.path.join(self.dest_dir, stem + '.pak')
        self._listing_path = os.path.join(self.dest_dir, stem + '.txt')
        self._create_log = self._temp_pak + '.create.log'
        self._part = part
        self.temps.extend([self._filelist, self._temp_pak, self._temp_listing,
                           self._create_log, self._temp_pak + '.list.log'])
        _step('write_filelist')
        write_entries(part, self._filelist)
        _step('run_create')
        self._log_handle = open(self._create_log, 'wb')
        self.process = subprocess.Popen(
            unrealpak.create_command(self.unrealpak, self._temp_pak, self._filelist, self.compress),
            stdout=self._log_handle, stderr=subprocess.STDOUT,
            creationflags=engine.subprocess_flags())

    def _close_log(self):
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None

    def _cleanup(self):
        self._close_log()
        for path in self.temps:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        if self.staged_registry:
            try:
                if os.path.exists(self.staged_registry):
                    os.remove(self.staged_registry)
            except OSError:
                pass
            self.staged_registry = None

    def cancel(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
        self._cleanup()

    def poll(self):
        """None while running; the pak path(s) once published; RuntimeError on failure."""
        if self._done:
            return self.path
        try:
            if self.process.poll() is None:
                return None
            self._close_log()
            unrealpak.check_create(self.process.returncode, self._temp_pak, self._create_log)
            _step('run_list')
            if self.run_list:
                unrealpak.run_list(self.unrealpak, self._temp_pak, log_path=self._temp_listing)
            else:
                write_listing(self._part, self._temp_listing)
            _step('publish_pak')
            provenance._replace_file(self._temp_pak, self._article)
            provenance._replace_file(self._temp_listing, self._listing_path)
            print('pak part: {0} ({1} files, {2} bytes)'.format(
                self._article, len(self._part), sum(e[2] for e in self._part)))
            self.published.append(self._article)
            if self.index + 1 < len(self.parts):
                self._start_part()
                return None
        except Exception:
            self._cleanup()
            self._done = True
            raise
        self._cleanup()
        self._done = True
        print('pak: {0} ({1} asset(s), {2} part(s))'.format(
            ', '.join(self.published), len(self.entries), len(self.published)))
        _step('done')
        _dump_steps(self.result['mod'])
        self.path = self.published[0] if len(self.published) == 1 else self.published
        return self.path


def cook_and_pak(mod, dest_dir, uproject_path=None, ue4_root=None, max_chunk=250, max_pak_size=None, run_list=False):
    """Cook the mod folder, then pak it into dest_dir. Returns (result, pak path(s) or None)."""
    from deploy import cook
    result = cook.cook_mod(mod, uproject_path, ue4_root, max_chunk=max_chunk)
    return result, (pak_mod(result, dest_dir, ue4_root, max_pak_size=max_pak_size, run_list=run_list) if result['success'] else None)
