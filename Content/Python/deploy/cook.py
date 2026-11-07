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

from deploy import engine

PROJECT_NAME = 'TBL'
MOD_ROOT_DEFAULT = '/Game/Mods/AgMods/'
MAX_PACKAGES_PER_RUN = 250

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


def collect_packages(uproject, mod_dir):
    """Package names of every .uasset/.umap below the mod folder (disk scan)."""
    folder = os.path.join(_content_dir(uproject), mod_dir[len('/Game/'):].replace('/', os.sep))
    if not os.path.isdir(folder):
        raise RuntimeError('Mod folder not found: {0}'.format(folder))
    packages = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            base, ext = os.path.splitext(name)
            if ext.lower() in ('.uasset', '.umap'):
                relative = os.path.relpath(os.path.join(root, base), _content_dir(uproject)).replace(os.sep, '/')
                packages.append('/Game/' + relative)
    return sorted(packages)


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

def cook_command(engine_root, uproject, packages, out_dir, log_path):
    """The UE4Editor-Cmd.exe -run=Cook -cooksinglepackage command list."""
    return [engine.engine_cmd_path(engine_root), uproject, '-run=Cook', '-TargetPlatform=WindowsNoEditor',
            '-cooksinglepackage', '-Map=' + '+'.join(packages), '-unversioned', '-skipeditorcontent',
            '-iterate', '-OutputDir=' + out_dir, '-fileopenlog', '-stdout', '-CrashForUAT',
            '-unattended', '-NoLogTimes', '-UTF8Output', '-abslog=' + log_path]


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
                 max_chunk=MAX_PACKAGES_PER_RUN, mod_dir=None, _start=True):
        self.mod, self.uproject, self.engine_root = mod, uproject, engine_root
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
                cook_command(self.engine_root, self.uproject, self.chunks[self.index], self.out_dir, log_path),
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
                       'errors': self.errors, 'chunks': len(self.chunks)}
        if ok:
            _step('manifest')
            try:
                from deploy import provenance
                provenance.write_manifest(self.manifest_path, self.mod, self.source_files, self.cooked_dir)
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

def start_cook(mod, uproject_path=None, ue4_root=None, out_dir=None, max_chunk=MAX_PACKAGES_PER_RUN):
    """Start cooking one mod folder in a separate UE4Editor-Cmd process. Returns a CookJob."""
    if max_chunk is None:
        max_chunk = MAX_PACKAGES_PER_RUN
    del _STEPS[:]
    _step('find_project')
    uproject = engine.find_project(uproject_path)
    _step('find_engine')
    engine_root = engine.find_engine(ue4_root, uproject=uproject)
    mod_dir = mod_package_dir(mod)
    _step('collect_packages')
    packages = collect_packages(uproject, mod_dir)
    _step('source_manifest')
    source_files = source_manifest(uproject, mod_dir)
    if not packages:
        raise RuntimeError('No packages below {0}'.format(mod_dir))
    name = mod_dir.rstrip('/').split('/')[-1]
    saved = os.path.join(os.path.dirname(uproject), 'Saved')
    out_dir = os.path.abspath(out_dir or os.path.join(saved, 'CookedMod', name))
    old_manifest = os.path.join(out_dir, 'CookModManifest.json')
    if os.path.isfile(old_manifest):
        os.remove(old_manifest)
    log_path = os.path.join(saved, 'Logs', 'CookMod_{0}.log'.format(name))
    if not os.path.isdir(os.path.dirname(log_path)):
        os.makedirs(os.path.dirname(log_path))
    _step('spawn')
    return CookJob(name, uproject, engine_root, packages, out_dir, log_path, source_files,
                   max_chunk=max_chunk, mod_dir=mod_dir)


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
