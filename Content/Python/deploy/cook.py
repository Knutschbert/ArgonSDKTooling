# -*- coding: utf-8 -*-
"""deploy.cook - single-mod cook (-cooksinglepackage) runner with chunking.

Port of cook_mod.py's cook core, behavior-preserving:
- Collect every .uasset/.umap under the mod folder as /Game/... package names.
- Chunk packages into batches of <= MAX_PACKAGES_PER_RUN (Windows cmd line
  limit ~32k chars); each chunk is one UE4Editor-Cmd -run=Cook run.
- -cooksinglepackage switches off the project's MapsToCook / AlwaysCook rules
  and unsolicited packages; referenced /Game content is still cooked as deps,
  engine content is skipped.
- Success = log reached 'Packages Remain 0' with no 'Critical error'/'Assertion
  failed'; the commandlet's exit code is NOT used (1 after harmless logged
  errors).

Differences from cook_mod.py: engine/project come from deploy.engine; the max
chunk size is a parameter. Project/engine resolution, provenance and paking
live in their own modules.
"""
from __future__ import print_function

import os
import subprocess
import time

from deploy import engine, maps, provenance

PROJECT_NAME = 'TBL'
MOD_ROOT_DEFAULT = '/Game/Mods/AgMods/'
MAX_PACKAGES_PER_RUN = 250
BUILT_DATA_SUFFIX = '_BuiltData'  # <Map>_BuiltData.uasset sits beside <Map>.umap

# Step timing for one cook, collected on the orchestrating thread and printed
# once when the cook finishes (avoids per-line print/flush lag). Records BOTH
# wall time and calling-thread CPU time so GIL/descheduling stalls (wall high,
# cpu low) can be told apart from real work.
try:
    _STEPS
except NameError:  # pragma: no cover - module reload in the editor
    _STEPS = []

try:
    _TCPU = time.thread_time
except AttributeError:  # Python 2.7 safe fallback
    _TCPU = time.time


def _step(label):
    return _STEPS.append([label, time.time(), _TCPU()])


def _dump_steps(mod):
    """Print one consolidated timing line for the whole cook (labels in order)."""
    if not _STEPS:
        return
    w0, c0 = _STEPS[0][1], _STEPS[0][2]
    deltas, prev_w, prev_c = [], w0, c0
    for label, ts, tcpu in _STEPS:
        deltas.append((label, ts - prev_w, tcpu - prev_c))
        prev_w, prev_c = ts, tcpu
    total, tcpuf = prev_w - w0, prev_c - c0
    parts = ' ; '.join('{0}=w{1:.2f}c{2:.2f}'.format(k, w, c) for k, w, c in deltas)
    print('[deploy-step] cook {0}: total=w{1:.2f}s cpu={2:.2f}s {{{3}}}'.format(mod, total, tcpuf, parts))
    del _STEPS[:]


# -- paths ---------------------------------------------------------------------

def mod_package_dir(mod):
    """'MyMod' -> '/Game/Mods/AgMods/MyMod'; a /Game/... path is used as-is."""
    mod = mod.replace('\\', '/').rstrip('/')
    return mod if mod.startswith('/Game/') else MOD_ROOT_DEFAULT + mod


def _content_dir(uproject):
    return os.path.join(os.path.dirname(uproject), 'Content')


def scan_packages(uproject, mod_dir):
    """[(package, is_map)] for every .uasset/.umap below the mod folder (disk scan)."""
    folder = os.path.join(_content_dir(uproject), mod_dir[len('/Game/'):].replace('/', os.sep))
    if not os.path.isdir(folder):
        raise RuntimeError('Mod folder not found: {0}'.format(folder))
    entries = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            base, ext = os.path.splitext(name)
            if ext.lower() in ('.uasset', '.umap'):
                relative = os.path.relpath(os.path.join(root, base), _content_dir(uproject)).replace(os.sep, '/')
                entries.append(('/Game/' + relative, ext.lower() == '.umap'))
    return sorted(entries)


def collect_packages(uproject, mod_dir):
    """Package names of every .uasset/.umap below the mod folder (disk scan)."""
    return [package for package, _is_map in scan_packages(uproject, mod_dir)]


def select_packages(entries, required_maps=None):
    """Apply a map marker's plan to the folder's packages -> (packages to cook, skipped packages).

    entries: [(package, is_map)]. required_maps=None keeps everything. Otherwise every non-map package
    stays, a map stays only if it is required, and the <Map>_BuiltData asset of a skipped map is skipped
    with it (it would otherwise be cooked on its own, lightmaps and all).
    """
    if required_maps is None:
        return sorted(package for package, _is_map in entries), []
    required = set(required_maps)
    skipped_maps = set(package for package, is_map in entries if is_map and package not in required)
    skipped_built = set(package + BUILT_DATA_SUFFIX for package in skipped_maps)
    keep, skipped = [], []
    for package, _is_map in entries:
        (skipped if package in skipped_maps or package in skipped_built else keep).append(package)
    return sorted(keep), sorted(skipped)


def plan_packages(uproject, mod_dir, plan=None):
    """The cook list for a mod folder, optionally narrowed by a map plan (see deploy.maps).

    Returns {'packages': [...], 'skipped': [...], 'external': [...], 'missing': [...]}:
    - packages: what to cook - the folder's packages that survive the plan
    - skipped: folder packages left out (maps the marker does not need, and their _BuiltData)
    - external: required maps OUTSIDE the folder that exist (a layer that belongs to the base game or
      another mod). Reported only: they are not listed for the cook, because this mod's pak cannot contain
      them and the cooker already follows references to outside content on its own
    - missing: required maps that exist nowhere (a stale marker entry)
    """
    entries = scan_packages(uproject, mod_dir)
    if plan is None:
        return {'packages': sorted(package for package, _m in entries), 'skipped': [], 'external': [], 'missing': []}
    required = set(plan['maps'])
    packages, skipped = select_packages(entries, required)
    prefix = mod_dir.rstrip('/') + '/'
    in_folder = set(package for package, is_map in entries if is_map)
    external, missing = [], []
    for package in sorted(required):
        if package in in_folder or not package.startswith('/Game/'):
            continue
        path = os.path.join(_content_dir(uproject), package[len('/Game/'):].replace('/', os.sep) + '.umap')
        if not package.startswith(prefix) and os.path.isfile(path):
            external.append(package)
        else:
            missing.append(package)
    return {'packages': packages, 'skipped': skipped, 'external': external, 'missing': missing}


def needs_clean_cook(previous_packages, new_packages, marker_active, has_output):
    """True when the cooker output folder must be rebuilt from scratch.

    The cooker keeps its output folder between runs (-iterate), and the pak packs whatever is in it. So
    a package cooked earlier but not cooked now would be packed anyway. A non-iterative cook makes the
    engine wipe the folder. Rebuild when the cook list shrank, or - with a map plan - when the previous
    cook is unknown (no recorded package list) and there is output that could contain stale maps.
    """
    if not has_output:
        return False
    if previous_packages is None:
        return bool(marker_active)
    return bool(set(previous_packages) - set(new_packages))


def source_manifest(uproject, mod_dir):
    """Source .uasset/.umap + size + mtime used to prove a cook is current."""
    folder = os.path.join(_content_dir(uproject), mod_dir[len('/Game/'):].replace('/', os.sep))
    if not os.path.isdir(folder):
        raise RuntimeError('Mod folder not found: {0}'.format(folder))
    entries = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() not in ('.uasset', '.umap'):
                continue
            path = os.path.join(root, name)
            entries.append({'path': os.path.relpath(path, _content_dir(uproject)).replace(os.sep, '/'),
                            'size': os.path.getsize(path), 'mtime': os.path.getmtime(path)})
    return sorted(entries, key=lambda entry: entry['path'])


# -- commandlet ----------------------------------------------------------------

def cook_command(engine_root, uproject, packages, out_dir, log_path, iterate=True):
    """The UE4Editor-Cmd.exe -run=Cook -cooksinglepackage command list.

    iterate=False omits -iterate, which makes the engine clear the output folder first.
    """
    command = [engine.engine_cmd_path(engine_root), uproject, '-run=Cook', '-TargetPlatform=WindowsNoEditor',
               '-cooksinglepackage', '-Map=' + '+'.join(packages), '-unversioned', '-skipeditorcontent']
    if iterate:
        command.append('-iterate')
    command += ['-OutputDir=' + out_dir, '-fileopenlog', '-stdout', '-CrashForUAT',
                '-unattended', '-NoLogTimes', '-UTF8Output', '-abslog=' + log_path]
    return command


def _read(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read().decode('utf-8', 'replace')
    except IOError:
        return ''


def judge(log_path):
    """(success, first error lines) from one cook run's log."""
    text = _read(log_path)
    errors = [line.strip() for line in text.splitlines() if 'Error:' in line][:8]
    ok = ('Packages Remain 0' in text and 'Critical error' not in text
          and 'Assertion failed' not in text)
    return ok, errors


# -- CookJob -------------------------------------------------------------------

class CookJob(object):
    """A running cook (one or more commandlet runs, chunked). poll() never blocks."""

    def __init__(self, mod, uproject, engine_root, packages, out_dir, log_path, source_files,
                 max_chunk=MAX_PACKAGES_PER_RUN, mod_dir=None, _start=True,
                 iterate=True, manifest_extra=None, plan_info=None):
        self.mod, self.uproject, self.engine_root = mod, uproject, engine_root
        # iterate=False cleans the output folder, which only the FIRST chunk may do: a later chunk
        # would wipe the earlier chunks' output (all chunks share one OutputDir).
        self.iterate = iterate
        self.manifest_extra = manifest_extra
        self.plan_info = plan_info
        self.packages = packages
        self.out_dir, self.log_path = out_dir, log_path
        self.source_files = source_files
        # The mod's REAL /Game/... root (not assumed AgMods). Locates where the
        # cook writes output; a bare name falls back to the AgMods default.
        self.mod_dir = mod_dir or mod_package_dir(self.mod)
        self.cooked_dir = os.path.join(
            out_dir, PROJECT_NAME, 'Content', self.mod_dir[len('/Game/'):].replace('/', os.sep))
        self.manifest_path = os.path.join(out_dir, 'CookModManifest.json')
        self.chunks = [packages[i:i + max_chunk] for i in range(0, len(packages), max_chunk)]
        self.max_chunk = max_chunk
        self.index, self.process, self.began, self.result = -1, None, time.time(), None
        self._devnull = None
        self.errors = []
        if _start:
            self._next()

    def chunk_iterates(self, index):
        """Whether chunk `index` runs with -iterate (only chunk 0 may rebuild from scratch)."""
        return self.iterate or index > 0

    def _next(self):
        self.index += 1
        if self.index >= len(self.chunks):
            return False
        log_path = self.log_path if len(self.chunks) == 1 else self.log_path.replace('.log', '_{0}.log'.format(self.index + 1))
        self._chunk_log = log_path
        self._close_devnull()
        self._devnull = open(os.devnull, 'w')
        self._chunk_start = time.time()
        try:
            self.process = subprocess.Popen(
                cook_command(self.engine_root, self.uproject, self.chunks[self.index], self.out_dir, log_path,
                             iterate=self.chunk_iterates(self.index)),
                stdout=self._devnull, stderr=subprocess.STDOUT,
                creationflags=engine.subprocess_flags())
        except Exception as error:
            self._close_devnull()
            self.process = None
            self.result = {'success': False, 'mod': self.mod, 'mod_dir': self.mod_dir, 'packages': len(self.packages),
                           'cooked_dir': self.cooked_dir, 'log': log_path,
                           'seconds': round(time.time() - self.began, 1),
                           'errors': ['could not start cook process: {0}'.format(error)],
                           'chunks': len(self.chunks)}
            return False
        _step('cook_chunk_{0}_spawn'.format(self.index + 1))
        return True

    def _close_devnull(self):
        if self._devnull is not None:
            try:
                self._devnull.close()
            except Exception:
                pass
            self._devnull = None

    def cancel(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
        self._close_devnull()

    def poll(self):
        if self.result is not None:
            return self.result
        if self.process is None:
            return self.result
        if self.process.poll() is None:
            return None
        self._close_devnull()
        _step('cook_chunk_{0}_judge'.format(self.index))
        ok, errors = judge(self._chunk_log)
        self.errors += errors
        if ok and self._next():
            return None
        self.result = {'success': ok, 'mod': self.mod, 'mod_dir': self.mod_dir, 'packages': len(self.packages),
                       'cooked_dir': self.cooked_dir, 'log': self._chunk_log,
                       'seconds': round(time.time() - self.began, 1),
                       'errors': self.errors, 'chunks': len(self.chunks), 'map_plan': self.plan_info}
        if ok:
            _step('manifest')
            try:
                from deploy import provenance
                provenance.write_manifest(self.manifest_path, self.mod, self.source_files, self.cooked_dir,
                                          extra=self.manifest_extra)
                self.result['manifest'] = self.manifest_path
            except Exception as error:
                self.result['success'] = False
                self.result['errors'].append('could not write cook provenance: {0}'.format(error))
        _dump_steps(self.mod)
        return self.result

    def wait(self, poll_seconds=0.5):
        while self.poll() is None:
            time.sleep(poll_seconds)
        return self.result


# -- orchestration -------------------------------------------------------------

def _log_plan(mod_dir, plan, selection, clean):
    """Say what the map marker changed about this cook (visible in the Output Log / console)."""
    if plan is not None:
        names = lambda packages: ', '.join(p.split('/')[-1] for p in packages)  # noqa: E731
        print('[deploy] map marker {0}: cooking {1} map(s) ({2} shipped, {3} extra, {4} layer(s)); '
              'skipping {5} package(s)'.format(', '.join(m.split('/')[-1] for m in plan['markers']),
                                               len(plan['maps']) - len(selection['missing']) - len(selection['external']),
                                               len(plan['shipped']), len(plan['extra']), len(plan['layers']),
                                               len(selection['skipped'])))
        if selection['skipped']:
            shown = selection['skipped'][:15]
            more = ' ... +{0} more'.format(len(selection['skipped']) - len(shown)) if len(selection['skipped']) > len(shown) else ''
            print('[deploy]   skipped: {0}{1}'.format(names(shown), more))
        if selection['external']:
            print('[deploy]   layers outside {0} (provided by the base game or another mod, not part of this pak): {1}'.format(
                mod_dir, names(selection['external'])))
        if selection['missing']:
            print('[deploy] WARNING: the marker lists map(s) that do not exist: {0}'.format(names(selection['missing'])))
        if not plan['maps']:
            print('[deploy] WARNING: the map marker lists no maps, so no map in {0} will be cooked'.format(mod_dir))
    if clean:
        print('[deploy] cooking {0} from scratch: the cook list shrank or the previous cook is unknown, '
              'so the engine clears the old output (no stale maps in the pak)'.format(mod_dir))


def start_cook(mod, uproject_path=None, ue4_root=None, out_dir=None, max_chunk=MAX_PACKAGES_PER_RUN,
               use_marker=True):
    """Start cooking one mod folder in a separate UE4Editor-Cmd process. Returns a CookJob.

    use_marker=True (default) lets a map marker in the folder decide which maps are cooked (editor only,
    see deploy.maps); DEPLOY_IGNORE_MAP_MARKER=1 turns it off for a run.
    """
    if max_chunk is None:
        max_chunk = MAX_PACKAGES_PER_RUN
    del _STEPS[:]
    _step('find_project')
    uproject = engine.find_project(uproject_path)
    _step('find_engine')
    engine_root = engine.find_engine(ue4_root, uproject=uproject)
    mod_dir = mod_package_dir(mod)
    _step('map_plan')
    plan = maps.marker_plan(mod_dir) if use_marker else None
    _step('collect_packages')
    selection = plan_packages(uproject, mod_dir, plan)
    packages = selection['packages']
    _step('source_manifest')
    source_files = source_manifest(uproject, mod_dir)
    if not packages:
        raise RuntimeError('No packages below {0}'.format(mod_dir))
    name = mod_dir.rstrip('/').split('/')[-1]
    saved = os.path.join(os.path.dirname(uproject), 'Saved')
    out_dir = os.path.abspath(out_dir or os.path.join(saved, 'CookedMod', name))
    old_manifest = os.path.join(out_dir, 'CookModManifest.json')
    previous = provenance.read_manifest(old_manifest) if os.path.isfile(old_manifest) else None
    if os.path.isfile(old_manifest):
        os.remove(old_manifest)
    clean = needs_clean_cook((previous or {}).get('cooked_packages'), packages, plan is not None,
                             os.path.isdir(os.path.join(out_dir, PROJECT_NAME)))
    _log_plan(mod_dir, plan, selection, clean)
    log_path = os.path.join(saved, 'Logs', 'CookMod_{0}.log'.format(name))
    if not os.path.isdir(os.path.dirname(log_path)):
        os.makedirs(os.path.dirname(log_path))
    plan_info = None
    if plan is not None:
        plan_info = {'markers': plan['markers'], 'maps_cooked': len(plan['maps']) - len(selection['missing']) - len(selection['external']),
                     'packages_skipped': len(selection['skipped']), 'missing': selection['missing'],
                     'external': selection['external'], 'clean': clean}
    _step('spawn')
    return CookJob(name, uproject, engine_root, packages, out_dir, log_path, source_files,
                   max_chunk=max_chunk, mod_dir=mod_dir, iterate=not clean, plan_info=plan_info,
                   manifest_extra={'cooked_packages': packages, 'skipped_packages': selection['skipped'],
                                   'map_markers': plan['markers'] if plan is not None else []})


def cook_mod(mod, uproject_path=None, ue4_root=None, out_dir=None, max_chunk=MAX_PACKAGES_PER_RUN):
    """Cook one mod folder and wait. Returns the result dict."""
    result = start_cook(mod, uproject_path, ue4_root, out_dir, max_chunk=max_chunk).wait()
    print('cook {0}: {1} ({2} packages, {3} s) -> {4}'.format(
        result['mod'], 'OK' if result['success'] else 'FAILED',
        result['packages'], result['seconds'], result['cooked_dir']))
    if not result['success']:
        print('  see', result['log'])
        for line in result['errors'][:5]:
            print('  ', line[:200])
    return result
