# -*- coding: utf-8 -*-
r"""deploy.unrealpak - UnrealPak discovery + create/list validation.

Discovery is portable ("what the project/machine uses", no hardcoded site list).
The Plus engine (a source build) ships NO UnrealPak.exe, so we fall through:

  1. UNREALPAK env var / deploy.local.json 'unrealpak'
  2. the resolved engine's own UnrealPak.exe
  3. every engine root registered in the 'Builds' registry (each may have one)
  4. Launcher builds under %ProgramData%\Epic\UnrealEngine\<id> (via Builds.txt)
  5. common Epic Games Launcher install roots (Program Files\Epic Games\UE_*)

create/list are validated (zero exit + non-empty usable output) before the
caller publishes, mirroring cook_mod.pak_mod. The subprocess call is injectable
so tests never spawn a real process.

Python 2.7-safe; works in the editor and on the CLI.
"""
from __future__ import print_function

import os
import subprocess

from deploy import engine

UNREALPAK_REL = ('Engine', 'Binaries', 'Win64', 'UnrealPak.exe')


# -- binary path helpers -------------------------------------------------------

def _unrealpak_in(engine_root):
    """Absolute UnrealPak.exe path for an engine root (whether or not it exists)."""
    if not engine_root:
        return None
    return os.path.join(os.path.abspath(engine_root), *UNREALPAK_REL)


# -- candidate sources ---------------------------------------------------------

def _registry_engine_roots(registry=None):
    """Every engine root under the Unreal 'Builds' registry key (HKLM then HKCU)."""
    reg = registry if registry is not None else engine._win_registry()
    if reg is None:
        return []
    roots = []
    for attr in ('HKEY_LOCAL_MACHINE', 'HKEY_CURRENT_USER'):
        hive = getattr(reg, attr, None)
        if hive is None:
            continue
        try:
            key = reg.OpenKey(hive, engine._BUILDS_KEY)
        except Exception:
            continue
        try:
            idx = 0
            while True:
                try:
                    _name, value, _typ = reg.EnumValue(key, idx)
                except Exception:
                    break
                if isinstance(value, str) and os.path.isdir(value):
                    roots.append(value)
                idx += 1
        finally:
            try:
                reg.CloseKey(key)
            except Exception:
                pass
    return roots


def _programdata_roots():
    r"""Engine roots from %ProgramData%\Epic\UnrealEngine\<id>\Builds.txt files."""
    base = os.environ.get('ProgramData') or os.environ.get('PROGRAMDATA') or r'C:\ProgramData'
    root_dir = os.path.join(base, 'Epic', 'UnrealEngine')
    if not os.path.isdir(root_dir):
        return []
    try:
        ids = os.listdir(root_dir)
    except OSError:
        return []
    roots = []
    for identifier in ids:
        builds = os.path.join(root_dir, identifier, 'Builds.txt')
        if not os.path.isfile(builds):
            continue
        try:
            with open(builds) as handle:
                for raw in handle:
                    line = raw.strip()
                    if line and os.path.isdir(line):
                        roots.append(line)
        except IOError:
            continue
    return roots


def _common_launcher_roots():
    r"""Engine roots under 'Program Files\Epic Games\UE_*' (common Launcher installs)."""
    roots = []
    for var in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432'):
        pf = os.environ.get(var)
        if not pf:
            continue
        epic = os.path.join(pf, 'Epic Games')
        if not os.path.isdir(epic):
            continue
        try:
            names = os.listdir(epic)
        except OSError:
            continue
        for name in names:
            if name.upper().startswith('UE_'):
                candidate = os.path.join(epic, name)
                if os.path.isdir(candidate):
                    roots.append(candidate)
    return roots


# -- discovery -----------------------------------------------------------------

def discover_unrealpak(engine_root=None, uproject=None, _config=None, _registry=None, _env=None):
    """Absolute UnrealPak.exe, from the most specific to the most general source."""
    env = os.environ if _env is None else _env
    candidates = []

    def add(path):
        if path and path not in candidates:
            candidates.append(path)

    add(env.get('UNREALPAK'))
    cfg = engine._load_config(_config)
    add(cfg.get('unrealpak'))

    if engine_root is None:
        try:
            engine_root = engine.find_engine(uproject=uproject, _config=_config,
                                             _registry=_registry, _env=env)
        except RuntimeError:
            engine_root = None
    add(_unrealpak_in(engine_root))

    for root in _registry_engine_roots(_registry):
        add(_unrealpak_in(root))
    for root in _programdata_roots():
        add(_unrealpak_in(root))
    for root in _common_launcher_roots():
        add(_unrealpak_in(root))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        'UnrealPak.exe not found: set UNREALPAK, build.json/deploy.local.json "unrealpak", or install a '
        'Launcher 4.25 engine. The Plus (source) engine has no UnrealPak.exe.')


# -- create / list validation --------------------------------------------------

def _call_default(command, log_path):
    with open(log_path, 'wb') as log:
        return subprocess.call(command, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=engine.subprocess_flags())


def create_command(unrealpak, temp_pak, filelist, compress=True):
    """UnrealPak -create command line (shared by blocking run_create and async PakJob)."""
    command = [unrealpak, temp_pak, '-create=' + filelist]
    if compress:
        command.append('-compress')
    return command


def check_create(code, temp_pak, log):
    """Validate a finished UnrealPak create: exit code 0 and a non-empty pak."""
    if code != 0:
        raise RuntimeError('UnrealPak create failed with exit code {0}; see {1}'.format(code, log))
    if not os.path.isfile(temp_pak) or os.path.getsize(temp_pak) == 0:
        raise RuntimeError('UnrealPak create produced no usable pak: {0}'.format(temp_pak))


def run_create(unrealpak, temp_pak, filelist, compress=True, log_path=None, _call=None):
    """Run UnrealPak -create=filelist [-compress]; validate exit code + non-empty pak."""
    command = create_command(unrealpak, temp_pak, filelist, compress)
    log = log_path or temp_pak + '.create.log'
    code = (_call or _call_default)(command, log)
    check_create(code, temp_pak, log)


def run_list(unrealpak, temp_pak, log_path=None, _call=None):
    """Run UnrealPak -List; validate exit code + non-empty listing. Returns the listing text."""
    command = [unrealpak, temp_pak, '-List']
    log = log_path or temp_pak + '.list.log'
    code = (_call or _call_default)(command, log)
    if code != 0:
        raise RuntimeError('UnrealPak list failed with exit code {0}; see {1}'.format(code, log))
    try:
        with open(log, 'rb') as handle:
            text = handle.read().decode('utf-8', 'replace')
    except IOError:
        text = ''
    if not text.strip():
        raise RuntimeError('UnrealPak list produced no asset listing: {0}'.format(log))
    return text
