# -*- coding: utf-8 -*-
"""deploy.engine - portable project + engine resolution for the Chivalry 2 SDK.

No hardcoded paths. Resolves the project from the script's own location and the
engine from the .uproject's EngineAssociation, the same way Unreal does:

  project: explicit arg -> open editor project -> nearest *.uproject upward from
           this package -> deploy.local.json 'uproject_path'
  engine : explicit arg -> open editor engine -> EngineAssociation resolution ->
           deploy.local.json + DEPLOY_ENGINE env

EngineAssociation resolution (mirrors Unreal's FDesktopPlatform):
  * plain Launcher name (SDK1: '4.25'):
        HKLM/HKCU SOFTWARE\\EpicGames\\Unreal Engine\\Builds  (value = engine root)
        then %ProgramData%\\Epic\\UnrealEngine\\<name>\\Builds.txt
  * source-build GUID (SDK2: '{66C5D560-...}'):
        %APPDATA%\\UnrealEngine\\Install.ini  [InstalledBuilds]  <GUID>=<engine root>
        then the registry Builds key

find_engine() returns the ENGINE ROOT (the directory that CONTAINS Engine/);
engine_dir()/engine_cmd_path() derive the actual Engine folder and the
UE4Editor-Cmd.exe. This matches Unreal semantics (a 4.25 install 'H:/epic/UE_4.25'
has root H:/epic/UE_4.25, engine H:/epic/UE_4.25/Engine; the Plus source engine
'P:/Modding/plus/UnrealEngine' has engine .../UnrealEngine/Engine).

Everything is Python 2.7-safe (also runs in the UE editor's Python). The registry
and config lookups are injectable so they can be stubbed in tests.
"""
from __future__ import print_function

import json
import os
import sys


# -- helpers ------------------------------------------------------------------

def in_editor():
    """True inside the editor's interpreter (the built-in `unreal` has no __file__)."""
    module = sys.modules.get('unreal')
    return module is not None and not hasattr(module, '__file__')


def _module_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _find_uproject(start):
    """Walk up from `start` and return the first *.uproject found (or None)."""
    d = os.path.abspath(start)
    while True:
        try:
            names = os.listdir(d)
        except OSError:  # non-existent / unreadable dir: keep ascending
            names = []
        ups = sorted(n for n in names if n.lower().endswith('.uproject'))
        if ups:
            return os.path.join(d, ups[0])
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


# -- registry access (injectable) ----------------------------------------------

# Set by tests (or discovered lazily) to the winreg/_winreg module or a stub.
_REGISTRY = None


def _win_registry():
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    for modname in ('winreg', '_winreg'):
        try:
            mod = __import__(modname)
        except ImportError:
            continue
        _REGISTRY = mod
        return _REGISTRY
    return None


_BUILDS_KEY = r'SOFTWARE\EpicGames\Unreal Engine\Builds'


def _registry_engine(association, registry=None):
    """Engine root from the Unreal 'Builds' registry values (HKLM then HKCU)."""
    reg = registry or _win_registry()
    hives = []
    for attr in ('HKEY_LOCAL_MACHINE', 'HKEY_CURRENT_USER'):
        h = getattr(reg, attr, None)
        if h is not None:
            hives.append(h)
    for hive in hives:
        try:
            key = reg.OpenKey(hive, _BUILDS_KEY)
        except Exception:
            continue
        try:
            try:
                value, _ = reg.QueryValueEx(key, association)
            except Exception:
                continue
            value = str(value)
            if value and os.path.isdir(value):
                return value
        finally:
            try:
                reg.CloseKey(key)
            except Exception:
                pass
    return None


def _install_ini_engine(guid):
    """Engine root for a source-build GUID from %APPDATA%\\UnrealEngine\\Install.ini."""
    base = os.environ.get('APPDATA')
    if not base:
        return None
    ini = os.path.join(base, 'UnrealEngine', 'Install.ini')
    if not os.path.isfile(ini):
        return None
    try:
        with open(ini) as handle:
            section = ''
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith(';') or line.startswith('#'):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    section = line[1:-1].strip().lower()
                    continue
                if section != 'installedbuilds':
                    continue
                key, _, value = line.partition('=')
                if key.strip() == guid:
                    value = value.strip()
                    if value and os.path.isdir(value):
                        return value
    except IOError:
        return None
    return None


def _programdata_engine(name):
    """Engine root for a Launcher name from %ProgramData%\\Epic\\UnrealEngine\\<name>\\Builds.txt."""
    base = os.environ.get('ProgramData') or os.environ.get('PROGRAMDATA') or r'C:\ProgramData'
    builds = os.path.join(base, 'Epic', 'UnrealEngine', name, 'Builds.txt')
    if not os.path.isfile(builds):
        return None
    try:
        with open(builds) as handle:
            for raw in handle:
                line = raw.strip()
                if line and os.path.isdir(line):
                    return line
    except IOError:
        return None
    return None


# -- per-user config -----------------------------------------------------------

def _config_candidates():
    cwd = os.getcwd()
    module_dir = _module_dir()
    # plugin root = Content/Python/deploy -> up 3
    plugin_root = os.path.abspath(os.path.join(module_dir, '..', '..', '..'))
    seen, out = set(), []
    for cand in (os.path.join(cwd, 'deploy.local.json'),
                 os.path.join(plugin_root, 'deploy.local.json'),
                 os.path.join(module_dir, 'deploy.local.json')):
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def _load_config(_config=None):
    """dict from deploy.local.json. `_config` (used by tests) wins if a dict."""
    if isinstance(_config, dict):
        return _config
    for path in _config_candidates():
        if os.path.isfile(path):
            try:
                with open(path) as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
            except (IOError, ValueError):
                continue
    return {}


# -- engine root normalization -------------------------------------------------

def engine_dir(engine_root):
    """The Engine/ folder for an engine root (tolerates receiving the Engine dir itself)."""
    if os.path.isdir(os.path.join(engine_root, 'Engine')):
        return os.path.join(engine_root, 'Engine')
    if os.path.basename(engine_root).lower() == 'engine':
        return engine_root
    return os.path.join(engine_root, 'Engine')


def engine_cmd_path(engine_root, target='Win64', exe='UE4Editor-Cmd.exe'):
    """Absolute path to UE4Editor-Cmd.exe (or another engine binary) for a root."""
    return os.path.join(engine_dir(engine_root), 'Binaries', target, exe)


def subprocess_flags():
    """Windows CREATE_NO_WINDOW (hide the subprocess console window); 0 elsewhere.

    Pass to Popen/call as `creationflags`. Guards so non-Windows (and Python 2)
    never reference a constant that does not exist there.
    """
    if os.name != 'nt':
        return 0
    try:
        import subprocess
        return getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    except Exception:  # pragma: no cover
        return 0x08000000


def _normalize_root(value):
    """Return the engine root for a config/env value (which may be a root or Engine dir)."""
    if not value:
        return None
    value = os.path.abspath(value)
    if os.path.isdir(os.path.join(value, 'Engine')):
        return value
    if os.path.basename(value).lower() == 'engine':
        return os.path.dirname(value)
    return value if os.path.isdir(value) else None


# -- project -------------------------------------------------------------------

def find_project(uproject_path=None, _config=None):
    """The .uproject: explicit arg, open editor, nearest upward, or config."""
    if uproject_path:
        return os.path.abspath(uproject_path)
    if in_editor():  # pragma: no cover - editor only
        import unreal
        path = unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path())
        if path:
            return os.path.abspath(path)
    found = _find_uproject(_module_dir())
    if found:
        return found
    cfg = _load_config(_config)
    if cfg.get('uproject_path'):
        return os.path.abspath(cfg['uproject_path'])
    raise RuntimeError('Project not found: pass uproject_path, run in the editor, or set uproject_path in deploy.local.json')


def engine_association(uproject):
    """The EngineAssociation string declared by the .uproject (or None)."""
    try:
        with open(uproject) as handle:
            data = json.load(handle)
    except (IOError, ValueError):
        return None
    return data.get('EngineAssociation')


def find_engine(engine_root=None, uproject=None, _config=None, _registry=None, _env=None):
    """The ENGINE ROOT for the project: arg, editor, EngineAssociation, config, env."""
    env = os.environ if _env is None else _env
    if engine_root:
        return _normalize_root(engine_root)
    if in_editor():  # pragma: no cover - editor only
        import unreal
        eng = unreal.Paths.engine_dir()
        if eng:
            return _normalize_root(eng)
    up = uproject or find_project(_config=_config)
    assoc = engine_association(up)
    candidates = []
    if assoc:
        assoc = str(assoc).strip()
        if assoc.startswith('{'):
            candidates.append(_install_ini_engine(assoc))
            candidates.append(_registry_engine(assoc, _registry))
        else:
            candidates.append(_programdata_engine(assoc))
            candidates.append(_registry_engine(assoc, _registry))
    cfg = _load_config(_config)
    for key in ('engine_root', 'ue4_root', 'engine'):
        candidates.append(_normalize_root(cfg.get(key)))
    candidates.append(_normalize_root(env.get('DEPLOY_ENGINE')))
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return _normalize_root(cand)
    raise RuntimeError(
        'Engine not found for {0} (association {1}): pass engine_root, run in the editor, or set '
        'engine_root in deploy.local.json / DEPLOY_ENGINE'.format(up, assoc if assoc else 'none'))
