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

try:
    import unreal
except ImportError:  # CLI / unit-test environment
    unreal = None

from deploy import engine, maps, markers, provenance

PROJECT_NAME = 'TBL'
MOD_ROOT_DEFAULT = '/Game/Mods/AgMods/'
MAX_PACKAGES_PER_RUN = 250
BUILT_DATA_SUFFIX = '_BuiltData'  # <Map>_BuiltData.uasset sits beside <Map>.umap
NATIVIZATION_SECTION = 'BlueprintNativizationSettings'

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


def discover_nativization_exclusions(mod_dir, bundled=(), unreal_module=None):
    """Return UserDefinedStruct/UserDefinedEnum paths from all cooked folders.

    The editor Asset Registry is the authoritative source for the asset class.
    Never infer these from filenames: user-defined types may have arbitrary
    names.  This helper deliberately fails closed when called outside an
    editor, because silently missing an exclusion produces a successful but
    unusable cook.
    """
    api = unreal_module or unreal
    if api is None:
        raise RuntimeError('cannot discover Blueprint nativization exclusions outside the Unreal editor')
    exclusions = []
    folders = [mod_dir] + [folder for folder in bundled if folder and folder != mod_dir]
    for folder in folders:
        try:
            try:
                assets = api.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False)
            except TypeError:
                # UE4.25 has the older three-argument form.
                assets = api.EditorAssetLibrary.list_assets(folder, recursive=True)
        except Exception as error:
            raise RuntimeError('Asset Registry discovery failed for {0}: {1}'.format(folder, error))

        for asset_path in assets:
            try:
                data = api.EditorAssetLibrary.find_asset_data(asset_path)
                class_name = str(data.asset_class)
            except Exception:
                # A stale registry entry is not a reason to guess. Continue so
                # valid registry results remain useful; final PAK validation
                # catches missing type assets.
                continue
            if class_name in ('UserDefinedStruct', 'UserDefinedEnum'):
                package = str(asset_path).split('.', 1)[0].replace('\\', '/')
                if package not in exclusions:
                    exclusions.append(package)
    return sorted(exclusions)


def format_nativization_config(excluded_assets):
    """Return the config fragment for one cook's nativization exclusions."""
    lines = ['[{0}]'.format(NATIVIZATION_SECTION)]
    lines.extend('+ExcludedAssets={0}'.format(path) for path in sorted(set(excluded_assets)))
    return '\n'.join(lines) + '\n'


def write_nativization_config(path, excluded_assets):
    """Write a minimal per-cook editor config overlay."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, 'w') as handle:
        handle.write(format_nativization_config(excluded_assets))
    return path


def install_nativization_overlay(uproject, excluded_assets):
    """Temporarily append exclusions to the project's DefaultEditor.ini.

    Returns a restore token. The original file is restored byte-for-byte after
    the last cook chunk, including the absent-file case. This is intentionally
    project-local: UE4's commandlet config loader reads the same project config
    hierarchy as the editor, whereas a bare -EditorIni override is not a
    reliable merge mechanism in this UE4.25 branch.
    """
    if not excluded_assets:
        return None
    # The deploy state machine already serializes runs, but this guard also
    # protects callers that invoke start_cook directly from two threads.
    lock_path = os.path.join(os.path.dirname(uproject), 'Saved', '.deploy-nativization.lock')
    if os.path.exists(lock_path):
        raise RuntimeError('another cook already owns the Blueprint nativization config overlay')
    config_dir = os.path.join(os.path.dirname(uproject), 'Config')
    path = os.path.join(config_dir, 'DefaultEditor.ini')
    existed = os.path.isfile(path)
    original = None
    if existed:
        with open(path, 'rb') as handle:
            original = handle.read()
    if not os.path.isdir(config_dir):
        os.makedirs(config_dir)
    if not os.path.isdir(os.path.dirname(lock_path)):
        os.makedirs(os.path.dirname(lock_path))
    with open(lock_path, 'w') as handle:
        handle.write('deploy nativization overlay\n')
    try:
        with open(path, 'ab') as handle:
            if original and not original.endswith(b'\n'):
                handle.write(b'\n')
            handle.write(format_nativization_config(excluded_assets).encode('utf-8'))
    except Exception:
        try:
            os.remove(lock_path)
        except OSError:
            pass
        raise
    return {'path': path, 'existed': existed, 'original': original, 'lock_path': lock_path}


def restore_nativization_overlay(token):
    """Restore a token returned by install_nativization_overlay."""
    if not token:
        return
    path = token['path']
    if token['existed']:
        with open(path, 'wb') as handle:
            handle.write(token['original'])
    elif os.path.isfile(path):
        os.remove(path)
    lock_path = token.get('lock_path')
    if lock_path and os.path.isfile(lock_path):
        os.remove(lock_path)


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


def plan_packages(uproject, mod_dir, plan=None, pak_plan=None):
    """The cook list for a mod folder, optionally narrowed by a map plan (see deploy.maps).

    Returns {'packages': [...], 'skipped': [...], 'external': [...], 'missing': [...]}:
    - packages: what to cook - the folder's packages that survive the plan
    - skipped: folder packages left out (maps the marker does not need, and their _BuiltData)
    - external: required maps OUTSIDE the folder that exist (a layer that belongs to the base game or
      another mod). Reported only: they are not listed for the cook, because this mod's pak cannot contain
      them and the cooker already follows references to outside content on its own
    - missing: required maps that exist nowhere (a stale marker entry)
    - bundled: marker folders OUTSIDE `mod_dir` that a mod marker bundles into this mod's pak; their packages are
      cooked too (their map markers and ExcludedPaths apply to them)
    `pak_plan` (deploy.markers.preflight) adds the mod markers' ExcludedPaths and the bundled folders.
    """
    result = _plan_map_packages(uproject, mod_dir, plan)
    result['bundled'] = []
    if pak_plan is None:
        return result
    excludes = markers.folder_excludes(pak_plan)
    result['packages'], dropped = markers.apply_excludes(result['packages'], excludes)
    result['skipped'] = sorted(set(result['skipped']) | set(dropped))
    result['packages'], dropped = markers.apply_actor_excludes(result['packages'], markers.actor_excludes(pak_plan))
    result['skipped'] = sorted(set(result['skipped']) | set(dropped))
    for folder in markers.bundled_folders(pak_plan, mod_dir):
        try:
            entries = scan_packages(uproject, folder)
        except RuntimeError:
            result['missing'].append(folder)
            continue
        folder_maps = maps.marker_plan(folder)
        kept, skipped = select_packages(entries, set(folder_maps['maps']) if folder_maps is not None else None)
        kept, dropped = markers.apply_excludes(kept, excludes)
        kept, actor_dropped = markers.apply_actor_excludes(kept, markers.actor_excludes(pak_plan))
        result['packages'] = sorted(set(result['packages']) | set(kept))
        result['skipped'] = sorted(set(result['skipped']) | set(skipped) | set(dropped) | set(actor_dropped))
        result['bundled'].append(folder)
    return result


def _plan_map_packages(uproject, mod_dir, plan):
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


def sources_for(uproject, mod_dir, bundled=()):
    """source_manifest of the mod folder plus every bundled marker folder (one sorted list)."""
    entries = list(source_manifest(uproject, mod_dir))
    for folder in bundled or ():
        entries += source_manifest(uproject, folder)
    return sorted(entries, key=lambda entry: entry['path'])


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

def cook_command(engine_root, uproject, packages, out_dir, log_path, iterate=True,
                 nativization_config=None):
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
                 iterate=True, manifest_extra=None, plan_info=None,
                 nativization_config=None, nativization_exclusions=None):
        self.mod, self.uproject, self.engine_root = mod, uproject, engine_root
        # iterate=False cleans the output folder, which only the FIRST chunk may do: a later chunk
        # would wipe the earlier chunks' output (all chunks share one OutputDir).
        self.iterate = iterate
        self.manifest_extra = manifest_extra
        self.plan_info = plan_info
        self.nativization_config = nativization_config
        self.nativization_exclusions = list(nativization_exclusions or [])
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
                             iterate=self.chunk_iterates(self.index),
                             nativization_config=self.nativization_config),
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
        self.cleanup_nativization_config()

    def cleanup_nativization_config(self):
        """Restore the project config after all chunks have stopped."""
        if self.nativization_config:
            try:
                restore_nativization_overlay(self.nativization_config)
            finally:
                self.nativization_config = None

    def poll(self):
        if self.result is not None:
            self.cleanup_nativization_config()
            return self.result
        if self.process is None:
            self.cleanup_nativization_config()
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
        self.cleanup_nativization_config()
        _dump_steps(self.mod)
        return self.result

    def wait(self, poll_seconds=0.5):
        while self.poll() is None:
            time.sleep(poll_seconds)
        return self.result


# -- orchestration -------------------------------------------------------------

def _log_plan(mod_dir, plan, selection, clean, pak_plan=None):
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
    if pak_plan is not None:
        print('[deploy] mod markers: {0} marker(s) -> {1}'.format(
            len(pak_plan['dirs']), '; '.join('{0}.pak{1} [{2}]'.format(
                pak['stem'], ' (own pak)' if pak['separate'] else '', len(pak['markers'])) for pak in pak_plan['paks'])))
        if selection.get('bundled'):
            print('[deploy]   bundled from other directories: {0}'.format(', '.join(f.split('/')[-1] for f in selection['bundled'])))
        for warning in pak_plan.get('warnings') or ():
            print('[deploy] WARNING: ' + warning)
    if clean:
        print('[deploy] cooking {0} from scratch: the cook list shrank or the previous cook is unknown, '
              'so the engine clears the old output (no stale maps in the pak)'.format(mod_dir))


def start_cook(mod, uproject_path=None, ue4_root=None, out_dir=None, max_chunk=MAX_PACKAGES_PER_RUN,
               use_marker=True, nativization_exclusions=None, nativization_config=None):
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
    _step('mod_markers')
    pak_plan = markers.preflight(mod_dir) if use_marker else None
    _step('collect_packages')
    selection = plan_packages(uproject, mod_dir, plan, pak_plan)
    packages = selection['packages']
    _step('source_manifest')
    source_files = sources_for(uproject, mod_dir, selection['bundled'])
    if nativization_exclusions is None:
        _step('discover_nativization_exclusions')
        try:
            nativization_exclusions = discover_nativization_exclusions(mod_dir, selection['bundled'])
        except RuntimeError as error:
            raise RuntimeError('{0}; run this cook from the editor or pass --exclude-asset for each user-defined type'.format(error))
    nativization_exclusions = sorted(set(nativization_exclusions))
    if nativization_exclusions:
        print('[deploy] excluding {0} user-defined type asset(s) from Blueprint nativization: {1}'.format(
            len(nativization_exclusions), ', '.join(nativization_exclusions)))
    if nativization_config is None and nativization_exclusions:
        nativization_config = install_nativization_overlay(uproject, nativization_exclusions)
    if not packages:
        raise RuntimeError('No packages below {0}'.format(mod_dir))
    name = mod_dir.rstrip('/').split('/')[-1]
    saved = os.path.join(os.path.dirname(uproject), 'Saved')
    out_dir = os.path.abspath(out_dir or os.path.join(saved, 'CookedMod', name))
    old_manifest = os.path.join(out_dir, 'CookModManifest.json')
    previous = provenance.read_manifest(old_manifest) if os.path.isfile(old_manifest) else None
    if os.path.isfile(old_manifest):
        os.remove(old_manifest)
    clean = needs_clean_cook((previous or {}).get('cooked_packages'), packages, plan is not None or pak_plan is not None,
                             os.path.isdir(os.path.join(out_dir, PROJECT_NAME)))
    _log_plan(mod_dir, plan, selection, clean, pak_plan)
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
                   nativization_config=nativization_config,
                   nativization_exclusions=nativization_exclusions,
                   manifest_extra={'cooked_packages': packages, 'skipped_packages': selection['skipped'],
                                   'nativization_exclusions': nativization_exclusions,
                                   'map_markers': plan['markers'] if plan is not None else [],
                                   'bundled_folders': selection['bundled'],
                                   'pak_plan': markers.manifest_plan(pak_plan) if pak_plan is not None else None})


def cook_mod(mod, uproject_path=None, ue4_root=None, out_dir=None, max_chunk=MAX_PACKAGES_PER_RUN,
             nativization_exclusions=None, nativization_config=None):
    """Cook one mod folder and wait. Returns the result dict."""
    result = start_cook(mod, uproject_path, ue4_root, out_dir, max_chunk=max_chunk,
                        nativization_exclusions=nativization_exclusions,
                        nativization_config=nativization_config).wait()
    print('cook {0}: {1} ({2} packages, {3} s) -> {4}'.format(
        result['mod'], 'OK' if result['success'] else 'FAILED',
        result['packages'], result['seconds'], result['cooked_dir']))
    if not result['success']:
        print('  see', result['log'])
        for line in result['errors'][:5]:
            print('  ', line[:200])
    return result
