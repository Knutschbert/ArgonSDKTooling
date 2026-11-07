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
import threading

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


def cook_many(mods, dest_dir=None, uproject_path=None, ue4_root=None, max_chunk=_cook.MAX_PACKAGES_PER_RUN,
              non_blocking=False):
    """Cook each mod in the list; if dest_dir is given, pak each after a successful cook.

    Accepts a single mod string or a list/tuple.

    Blocking (default): each cook runs to completion in turn; returns the per-mod
    result dicts.

    non_blocking=True: returns immediately and drives the SAME poll() state machine
    the single-mod widget uses (deploy.poll() reports 'mod'/'index'/'total' while
    working and lands on state='done' with 'success'). Returns None; read deploy.poll().
    Only valid in the editor (outside it, non_blocking is ignored and it blocks).
    """
    if not isinstance(mods, (list, tuple)):
        mods = [mods]
    if non_blocking:
        return start_many(mods, dest_dir=dest_dir, uproject_path=uproject_path,
                          engine_dir=ue4_root, do_cook=True, do_pak=bool(dest_dir),
                          max_chunk=max_chunk)
    results = []
    for mod in mods:
        result = _cook.cook_mod(mod, uproject_path, ue4_root, max_chunk=max_chunk)
        if result['success'] and dest_dir:
            _pak.pak_mod(result, dest_dir, ue4_root)
        results.append(result)
    return results


def pak_many(mods, dest_dir, uproject_path=None, ue4_root=None, max_pak_size=None, non_blocking=False):
    """Pak already-cooked output for each mod (provenance-validated).

    Accepts a single mod string or a list/tuple.

    Blocking (default): paks each in turn; returns a list of (mod, pak path).

    non_blocking=True: returns immediately and drives the SAME poll() state machine
    used by the single-mod widget (deploy.poll(): 'mod'/'index'/'total', then
    state='done' with 'success'). Returns None; read deploy.poll(). Only non-blocking
    in the editor (outside it, non_blocking is ignored and it blocks).
    """
    if not isinstance(mods, (list, tuple)):
        mods = [mods]
    if non_blocking:
        return start_many(mods, dest_dir=dest_dir, uproject_path=uproject_path,
                          engine_dir=ue4_root, do_cook=False, do_pak=True,
                          max_pak_size=max_pak_size)
    out = []
    for mod in mods:
        out.append((mod, _pak_cooked(mod, None, dest_dir, uproject_path, ue4_root, max_pak_size)))
    return out


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

# Overall operation state for widget polling: state in
# idle / cooking / paking / done, plus success/result/error when done, plus the
# current mod and index/total so a multi-mod run can display progress.
_OP = {'state': 'idle', 'mod': None, 'success': None, 'result': None, 'error': None,
       'index': 0, 'total': 0}


def poll():
    """Overall status for the widget tick: a dict with 'state' in
    idle/cooking/paking/done, the current 'mod', 'index'/'total', and when done
    'success'/'result'/'error'."""
    return dict(_OP)


def _set(state, mod=None, success=None, result=None, error=None, index=None, total=None):
    _OP.update(state=state, mod=mod, success=success, result=result, error=error)
    if index is not None:
        _OP['index'] = index
    if total is not None:
        _OP['total'] = total


# Current run: the mods queue (+ how to process each) for multi-mod runs.
_QUEUE = []
_CTX = {'mods': [], 'dest': None, 'upr': None, 'eng': None,
        'do_cook': False, 'do_pak': False, 'all_ok': True, 'total': 0,
        'max_chunk': None, 'max_pak_size': None}


def _stop_tick_callback():
    if _RUNNING or _PAK_RUNNING or _STATE.tick_handle is None or not _engine.in_editor():
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


def _start_next_mod():
    """Begin the next queued mod (cook, or pak if do_cook is off) and reflect in poll()."""
    mod = _QUEUE[0]
    index = _CTX['total'] - len(_QUEUE)
    if _CTX['do_cook']:
        _set('cooking', mod, index=index, total=_CTX['total'])
        job = _cook.start_cook(mod, _CTX['upr'], _CTX['eng'], max_chunk=_CTX['max_chunk'])
        _RUNNING.append((job, _CTX['do_pak']))
    else:
        _set('paking', mod, index=index, total=_CTX['total'])
        start_pak(mod, _CTX['dest'], _CTX['upr'], _CTX['eng'], max_pak_size=_CTX['max_pak_size'])


def _finish_op(success, error=None, result=None):
    """Record a completed mod, pop it, and let the next tick continue the queue."""
    if not success:
        _CTX['all_ok'] = False
    if _QUEUE:
        _QUEUE.pop(0)
    _OP['result'] = result if success else None
    _OP['error'] = error


def _tick(_delta=0.0):
    # -- a cook is in flight --
    if _RUNNING:
        job, pak_wanted = _RUNNING[0]
        try:
            result = job.poll()
        except Exception as error:  # noqa: BLE001
            job.cancel()
            _RUNNING.pop(0)
            _finish_op(success=False, error='cook polling failed: {0}'.format(error))
            return
        if result is None:
            return
        _RUNNING.pop(0)
        _log('cook {0}: {1}'.format(result.get('mod'), 'OK' if result.get('success') else 'FAILED'),
             not result.get('success'))
        if not result.get('success'):
            for line in (result.get('errors') or [])[:5]:
                _log('  ' + line[:200], True)
            _finish_op(success=False, error='; '.join(result.get('errors') or []))
            return
        if pak_wanted:
            _start_async_pak(result['mod'], _CTX['dest'], _CTX['eng'], _CTX['upr'])
            return
        _finish_op(success=True, result=result.get('cooked_dir'))
        return
    # -- a pak result just became available (or a pak is still running) --
    done = pak_poll()
    if done is not None and _OP.get('state') == 'paking':
        if done[0] == 'ok':
            _finish_op(success=True, result=done[1])
        else:
            _finish_op(success=False, error=done[2])
        return
    # a pak is STILL in flight: do NOT start the next mod while one is being paked.
    if _PAK_RUNNING:
        return
    # -- nothing running: start the next mod, or finish the whole run --
    if _QUEUE:
        _start_next_mod()
        return
    _set('done', None, success=_CTX['all_ok'], index=_CTX['total'], total=_CTX['total'])
    _stop_tick_callback()


def _start_async_pak(mod, dest_dir, engine_dir, uproject_path=None):
    """Begin the (non-blocking) pak stage for one mod and reflect it in poll()."""
    _set('paking', mod, index=_CTX['total'] - len(_QUEUE), total=_CTX['total'])
    _log('pak started: {0} -> {1}'.format(mod, dest_dir))
    start_pak(mod, dest_dir, uproject_path, engine_dir, max_pak_size=_CTX['max_pak_size'])


def _truthy(value):
    return value is True or str(value).strip().lower() in ('true', '1', 'yes')


def _log(message, error=False):
    if _engine.in_editor():
        import unreal
        (unreal.log_error if error else unreal.log)('[deploy] ' + message)
    else:
        print(message)


def start_many(mods, dest_dir=None, uproject_path=None, engine_dir=None, do_cook=True, do_pak=False,
               max_chunk=None, max_pak_size=None):
    """Cook/pak a LIST of mods, fully NON-BLOCKING in the editor.

    Drives the same poll() state machine as single runs: poll() reports the
    current 'mod' and 'index'/'total' while working, and lands on
    state='done' (with 'success') when all mods are processed. A single mod
    string is treated as a one-element list.

    In the editor this returns immediately and work proceeds on the Slate tick
    (paids run on a worker thread). Outside the editor it blocks.

    max_chunk / max_pak_size thread through to the underlying cook/pak when set
    (None uses each module's default).
    """
    if not isinstance(mods, (list, tuple)):
        mods = [mods]
    mods = [str(m) for m in mods]
    if not mods:
        _set('done', None, success=True, index=0, total=0)
        return
    if _RUNNING or _PAK_RUNNING:
        _log('deploy run already in progress; ignoring start_many', True)
        return
    if do_pak and not dest_dir:
        _log('No destination folder for the pak (DestDir is empty).', True)
        _set('done', None, success=False, error='no destination folder for the pak', index=0, total=len(mods))
        return
    _CTX.update(mods=mods, dest=dest_dir, upr=uproject_path, eng=engine_dir,
                do_cook=do_cook, do_pak=do_pak, all_ok=True, total=len(mods),
                max_chunk=max_chunk, max_pak_size=max_pak_size)
    _QUEUE[:] = list(mods)
    _set('idle', None, index=0, total=_CTX['total'])
    if _engine.in_editor():
        _start_tick_callback()
        return
    _blocking_many()


def _blocking_many():
    """Non-editor fallback: process the queued mods sequentially, blocking."""
    dest = _CTX['dest']
    total = _CTX['total']
    for idx, mod in enumerate(_CTX['mods']):
        if _CTX['do_cook']:
            _set('cooking', mod, index=idx, total=total)
            result = _cook.cook_mod(mod, _CTX['upr'], _CTX['eng'], max_chunk=_CTX['max_chunk'])
            if not result['success']:
                _CTX['all_ok'] = False
                _OP['error'] = '; '.join(result.get('errors') or [])
                continue
            if _CTX['do_pak']:
                _set('paking', mod, index=idx, total=total)
                try:
                    _OP['result'] = _pak.pak_mod(result, dest, _CTX['eng'], max_pak_size=_CTX['max_pak_size'])
                except Exception as error:  # noqa: BLE001
                    _CTX['all_ok'] = False
                    _OP['error'] = str(error)
        else:  # pak-only
            _set('paking', mod, index=idx, total=total)
            try:
                _OP['result'] = _pak_cooked(mod, None, dest, _CTX['upr'], _CTX['eng'], _CTX['max_pak_size'])
            except Exception as error:  # noqa: BLE001
                _CTX['all_ok'] = False
                _OP['error'] = str(error)
    _set('done', None, success=_CTX['all_ok'], index=total, total=total)


def run_from_widget(mod_name, mod_dir, uproject_path, engine_dir, dest_dir, do_cook, do_pak):
    """Widget entry for ONE mod: cook, pak, or cook then pak, non-blocking.

    Just a convenience around start_many([mod], ...); poll() reports the mod.
    """
    do_cook, do_pak = _truthy(do_cook), _truthy(do_pak)
    mod = mod_dir or mod_name
    if not (do_cook or do_pak):
        _log('Nothing to do: hold Shift to cook, Ctrl to pak, both to cook then pak.')
        _set('idle')
        return
    start_many([mod], dest_dir=dest_dir if do_pak else None,
               uproject_path=uproject_path, engine_dir=engine_dir,
               do_cook=do_cook, do_pak=do_pak)


def _pak_cooked(mod, _deprecated, dest_dir, uproject_path, engine_dir, max_pak_size=None, log=_log):
    """Pak already-cooked output, validating provenance (pak-only mode)."""
    from . import provenance
    uproject = _engine.find_project(uproject_path)
    mod_dir = _cook.mod_package_dir(mod)
    name = mod_dir.rstrip('/').split('/')[-1]
    cooked_root = os.path.join(os.path.dirname(uproject), 'Saved', 'CookedMod', name)
    cooked = os.path.join(cooked_root, 'TBL', 'Content', mod_dir[len('/Game/'):].replace('/', os.sep))
    manifest = os.path.join(cooked_root, 'CookModManifest.json')
    provenance.validate_cooked_output(cooked, manifest, name, _cook.source_manifest(uproject, mod_dir))
    log('cook provenance validated: {0}'.format(manifest))
    return _pak.pak_mod({'success': True, 'mod': name, 'mod_dir': mod_dir, 'cooked_dir': cooked},
                        dest_dir, engine_dir, max_pak_size=max_pak_size)


# -- async pak (non-blocking, poll from a widget tick) --------------------------

_PAK_RUNNING = []
_PAK_RESULT = []


def start_pak(mod, dest_dir, uproject_path=None, ue4_root=None, max_pak_size=None):
    """Pak already-cooked output in the background (non-blocking).

    Poll with pak_poll() from a widget Event Tick. Returns immediately. The actual
    UnrealPak work runs on a worker thread (pure file/subprocess - no engine/UI
    calls), so it is safe; logging is routed to print, not unreal.log.
    """
    if _PAK_RUNNING:
        raise RuntimeError('a pak is already running; wait for pak_poll() before starting another')
    _PAK_RUNNING.append(True)
    del _PAK_RESULT[:]

    def _work():
        try:
            path = _pak_cooked(mod, None, dest_dir, uproject_path, ue4_root, max_pak_size, log=print)
            _PAK_RESULT.append(('ok', path, None))
        except Exception as error:  # noqa: BLE001
            _PAK_RESULT.append(('error', None, str(error)))
        finally:
            _PAK_RUNNING[:] = []

    thread = threading.Thread(target=_work)
    thread.daemon = True
    thread.start()
    return None


def pak_poll():
    """None while a pak is running; else (status, pak_path, error) - consumed ONCE."""
    if _PAK_RUNNING:
        return None
    if _PAK_RESULT:
        result = _PAK_RESULT[0]
        del _PAK_RESULT[:]  # consumed: next poll returns None until the next start_pak
        return result
    return None


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
