# -*- coding: utf-8 -*-
"""deploy - portable cook/pak + sync-tooling for the Chivalry 2 SDK.

This is the package; `import deploy` gives you the whole API (see below). Run
the CLI with `python deploy.py <verb> ...` (the sibling file is a thin shim onto
deploy._main).

    # in the editor (Content/Python on the path -> import deploy)
    import deploy
    deploy.cook_mod("MyMod")                   # blocks -> result dict
    deploy.cook_and_pak("MyMod", "C:/MyPaks")
    deploy.run_from_widget(name, mod_dir, upr, eng, dest, do_cook, do_pak)  # widget entry

The engine/project are resolved portably (deploy.engine), UnrealPak discovered
portably (deploy.unrealpak) - no hardcoded paths.

Submodules: engine, unrealpak, cook, pak, provenance, sync, version.
Kept Python 2.7-safe (editor) and CLI-run on any Python.
"""
from __future__ import print_function

import os
import sys

from . import engine as _engine  # noqa: E402
from . import cook as _cook      # noqa: E402
from . import pak as _pak        # noqa: E402


# -- public blocking helpers ---------------------------------------------------

def cook_mod(mod, uproject_path=None, ue4_root=None, out_dir=None, max_chunk=_cook.MAX_PACKAGES_PER_RUN):
    """Cook one mod folder and wait. Returns the result dict (deploy.cook_mod)."""
    return _cook.cook_mod(mod, uproject_path, ue4_root, out_dir, max_chunk=max_chunk)


def cook_and_pak(mod, dest_dir, uproject_path=None, ue4_root=None, max_chunk=_cook.MAX_PACKAGES_PER_RUN):
    """Cook one mod, then pak it into dest_dir. Returns (result, pak path or None)."""
    return _pak.cook_and_pak(mod, dest_dir, uproject_path, ue4_root, max_chunk=max_chunk)


# -- editor entry (async via Slate tick, faithful port of cook_mod) ------------

try:
    _STATE
except NameError:  # pragma: no cover - module reload in the editor
    class _State(object):
        def __init__(self):
            self.running = []
            self.tick_handle = None
    _STATE = _State()

_RUNNING = _STATE.running  # (job, pak_args or None)


def _stop_tick_callback():
    if _RUNNING or _STATE.tick_handle is None or not _engine.in_editor():
        return
    try:
        import unreal
        unreal.unregister_slate_post_tick_callback(_STATE.tick_handle)
    except Exception as error:
        print('deploy: could not unregister cook tick: {0}'.format(error))
    _STATE.tick_handle = None


def _start_tick_callback():
    if _STATE.tick_handle is not None:
        return
    import unreal
    _STATE.tick_handle = unreal.register_slate_post_tick_callback(_tick)


def _tick(_delta=0.0):
    for entry in list(_RUNNING):
        job, pak_args = entry
        try:
            result = job.poll()
        except Exception as error:
            job.cancel()
            result = {'success': False, 'mod': job.mod,
                      'errors': ['cook polling failed: {0}'.format(error)]}
        if result is None:
            continue
        try:
            _RUNNING.remove(entry)
        except ValueError:
            continue
        try:
            _log('cook {0}: {1}'.format(result.get('mod'), 'OK' if result.get('success') else 'FAILED'),
                 not result.get('success'))
            if not result.get('success'):
                _log('  see {0}'.format(result.get('log', '')), True)
                for line in (result.get('errors') or [])[:5]:
                    _log('  ' + line[:200], True)
            elif pak_args:
                _pak.pak_mod(result, *pak_args)
        except Exception as error:
            _log('cook/pak {0} failed after completion: {1}'.format(result.get('mod') or job.mod, error), True)
        finally:
            _stop_tick_callback()


def _truthy(value):
    return value is True or str(value).strip().lower() in ('true', '1', 'yes')


def _log(message, error=False):
    if _engine.in_editor():
        import unreal
        (unreal.log_error if error else unreal.log)('[deploy] ' + message)
    else:
        print(message)


def run_from_widget(mod_name, mod_dir, uproject_path, engine_dir, dest_dir, do_cook, do_pak):
    """Widget entry: cook, pak, or cook then pak. Async in the editor, blocking outside."""
    do_cook, do_pak = _truthy(do_cook), _truthy(do_pak)
    mod = mod_dir or mod_name
    if not (do_cook or do_pak):
        _log('Nothing to do: hold Shift to cook, Ctrl to pak, both to cook then pak.')
        return
    if do_pak and not dest_dir:
        _log('No destination folder for the pak (DestDir is empty).', True)
        return
    if not do_cook:
        _pak_cooked(mod, None, dest_dir, uproject_path, engine_dir)
        return
    job = _cook.start_cook(mod, uproject_path, engine_dir)
    _log('cook {0} started: {1} packages (log {2})'.format(job.mod, len(job.packages), job.log_path))
    pak_args = (dest_dir, engine_dir) if do_pak else None
    if _engine.in_editor():
        entry = (job, pak_args)
        _RUNNING.append(entry)
        try:
            _start_tick_callback()
            return
        except AttributeError:
            _RUNNING.remove(entry)
        except Exception as error:
            _RUNNING.remove(entry)
            job.cancel()
            _log('could not start cook polling: {0}'.format(error), True)
            return
    result = job.wait()
    _log('cook {0}: {1}'.format(result['mod'], 'OK' if result['success'] else 'FAILED'), not result['success'])
    if result['success'] and pak_args:
        _pak.pak_mod(result, *pak_args)


def _pak_cooked(mod, _deprecated, dest_dir, uproject_path, engine_dir, max_pak_size=None):
    """Pak already-cooked output, validating provenance (pak-only mode)."""
    from . import provenance
    uproject = _engine.find_project(uproject_path)
    mod_dir = _cook.mod_package_dir(mod)
    name = mod_dir.rstrip('/').split('/')[-1]
    cooked_root = os.path.join(os.path.dirname(uproject), 'Saved', 'CookedMod', name)
    cooked = os.path.join(cooked_root, 'TBL', 'Content', mod_dir[len('/Game/'):].replace('/', os.sep))
    manifest = os.path.join(cooked_root, 'CookModManifest.json')
    provenance.validate_cooked_output(cooked, manifest, name, _cook.source_manifest(uproject, mod_dir))
    _log('cook provenance validated: {0}'.format(manifest))
    return _pak.pak_mod({'success': True, 'mod': name, 'cooked_dir': cooked}, dest_dir, engine_dir,
                        max_pak_size=max_pak_size)


# -- CLI -----------------------------------------------------------------------

def _main(argv=None):
    import argparse
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='deploy.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='verb')

    p = sub.add_parser('cook', help='cook one or more mod folders (optionally pak with --dest)')
    p.add_argument('mods', nargs='+', help='mod folder name below /Game/Mods/AgMods/ or a full /Game/... path')
    p.add_argument('--map', action='append', default=[], help='extra package path to force-cook (same mod)')
    p.add_argument('--out', help='output dir (default <Saved>/CookedMod/<mod>)')
    p.add_argument('--dest', help='pak destination dir (cook then pak)')
    p.add_argument('--max-chunk', type=int, default=_cook.MAX_PACKAGES_PER_RUN)
    p.add_argument('--max-pak-size', help='split the pak if it exceeds this size, e.g. 200MB')
    p.add_argument('--dry-run', action='store_true', help='print commands without running')
    p.add_argument('--uproject'); p.add_argument('--engine')

    p = sub.add_parser('pak', help='pak already-cooked output (provenance-validated)')
    p.add_argument('mods', nargs='+')
    p.add_argument('--dest', required=True)
    p.add_argument('--max-pak-size', help='split the pak if it exceeds this size, e.g. 200MB')
    p.add_argument('--uproject'); p.add_argument('--engine')

    p = sub.add_parser('sync-tooling', help='update the plugin/widget pack, preserving _Local')
    p.add_argument('--src', help='source dir or git URL; default: DEPLOY_TOOLING_REPO/config or git update')

    args = parser.parse_args(argv)
    if args.verb == 'cook':
        return _cli_cook(args)
    if args.verb == 'pak':
        return _cli_pak(args)
    if args.verb == 'sync-tooling':
        from . import sync
        return sync.run(args)
    parser.print_help()
    return 2


def _cli_cook(args):
    if args.map and len(args.mods) != 1:
        print('deploy: --map is only valid with exactly one mod target', file=sys.stderr)
        return 2
    ok = True
    for mod in args.mods:
        if args.dry_run:
            ok = _dry_cook(mod, args) and ok
            continue
        result = _cook.cook_mod(mod, args.uproject, args.engine,
                                out_dir=args.out, max_chunk=args.max_chunk)
        if not result['success']:
            ok = False
            continue
        if args.dest:
            _pak.pak_mod(result, args.dest, args.engine, max_pak_size=args.max_pak_size)
    return 0 if ok else 1


def _dry_cook(mod, args):
    """Print (without running) the cook command for one target."""
    uproject = _engine.find_project(args.uproject)
    engine_root = _engine.find_engine(args.engine, uproject=uproject)
    mod_dir = _cook.mod_package_dir(mod)
    try:
        packages = _cook.collect_packages(uproject, mod_dir)
    except RuntimeError as error:
        print('deploy: {0}'.format(error), file=sys.stderr)
        return False
    if args.map:
        packages = packages + [m for m in args.map if m not in packages]
    name = mod_dir.rstrip('/').split('/')[-1]
    saved = os.path.join(os.path.dirname(uproject), 'Saved')
    out = os.path.abspath(args.out or os.path.join(saved, 'CookedMod', name))
    log_path = os.path.join(saved, 'Logs', 'CookMod_{0}.log'.format(name))
    chunks = [packages[i:i + args.max_chunk] for i in range(0, len(packages), args.max_chunk)]
    print('# dry-run: {0} packages, {1} chunk(s) -> {2}'.format(len(packages), len(chunks), out))
    for idx, chunk in enumerate(chunks):
        logp = log_path if len(chunks) == 1 else log_path.replace('.log', '_{0}.log'.format(idx + 1))
        print(' '.join(_cook.cook_command(engine_root, uproject, chunk, out, logp)))
    if args.dest:
        print('# would pak {0} into {1}/{0}.pak'.format(name, args.dest))
    return True


def _cli_pak(args):
    ok = True
    for mod in args.mods:
        try:
            _pak_cooked(mod, None, args.dest, args.uproject, args.engine, args.max_pak_size)
        except RuntimeError as error:
            print('deploy: {0}'.format(error), file=sys.stderr)
            ok = False
    return 0 if ok else 1
