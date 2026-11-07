# -*- coding: utf-8 -*-
"""deploy.sync - conflict-free update of the ArgonSDKTooling plugin/widget pack.

The guarantee (replace-not-merge): a tooling update OVERWRITES the stock widget
pack and scripts, and NEVER touches a user's Content/Scripts/_Local/ (the
git-ignored customization overlay). Versions are pinned/overwritten, never
merged, so users with local state can update safely.

Sources accepted by the `sync-tooling` verb:
  --src DIR           copy-replace from a checked-out tooling dir
  --src git-URL       clone once into a temp dir, then copy-replace
  (none)              treat this plugin root as a git repo and `git pull`/reset
                      to default (branch from config/env, else the remote HEAD)

Everything is Python 2.7-safe.
"""
from __future__ import print_function

import os
import shutil
import subprocess
import sys
import tempfile

from deploy import engine


def plugin_root():
    """<plugin>/ArgonSDKTooling (this file is <plugin>/Content/Python/deploy/sync.py)."""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))


def local_dir(plugin=None):
    return os.path.join(plugin or plugin_root(), 'Content', 'Scripts', '_Local')


# -- copy-replace core (the tested guarantee) -----------------------------------

def _under(path, root):
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    return path == root or path.startswith(root + os.sep)


def _replace_dir(src, dst, protected=()):
    """Make dst a fresh copy of src, deleting dst entries NOT under `protected`."""
    if not os.path.isdir(dst):
        os.makedirs(dst)
    for name in os.listdir(dst):
        target = os.path.join(dst, name)
        if any(_under(target, p) for p in protected):
            continue
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    for name in os.listdir(src):
        source = os.path.join(src, name)
        target = os.path.join(dst, name)
        if os.path.isdir(source) and not os.path.islink(source):
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def sync_from_dir(source_root, dest_plugin=None, log=print):
    """Copy-replace the managed script/widget folders from a checked-out source.

    Managed folders: <dest>/Content/Python and <dest>/Content/Scripts/ModHandling.
    <dest>/Content/Scripts/_Local/ is protected - it is never touched or deleted.
    Returns the dest plugin root.
    """
    dest = dest_plugin or plugin_root()
    _local = local_dir(dest)
    backup = None
    if os.path.isdir(_local) and os.listdir(_local):
        backup = tempfile.mkdtemp(prefix='deploy-local-bk-')
        shutil.copytree(_local, os.path.join(backup, '_Local'))
    try:
        for rel in ('Content/Python', 'Content/Scripts/ModHandling'):
            src = os.path.join(source_root, rel)
            dst = os.path.join(dest, rel)
            if os.path.isdir(src):
                _replace_dir(src, dst, protected=(_local,))
        log('tooling updated from {0} -> {1}'.format(source_root, dest))
        log('_Local preserved: {0}'.format(_local))
        return dest
    finally:
        if backup:
            shutil.rmtree(backup, True)


# -- orchestration ---------------------------------------------------------------

def _git_update(dest, log=print):
    """Update an installed git plugin to its default branch (pull/reset, not merge)."""
    branch = (os.environ.get('DEPLOY_TOOLING_BRANCH')
              or engine._load_config().get('tooling_branch') or 'main')
    log('git update {0} to {1} (reset, merge-free)'.format(dest, branch))
    for cmd, check in ((['git', '-C', dest, 'fetch', '--all', '--prune'], True),
                       (['git', '-C', dest, 'checkout', branch], True),
                       (['git', '-C', dest, 'reset', '--hard', 'origin/' + branch], True)):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=engine.subprocess_flags())
        out, _ = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError('sync-tooling failed: {0}\n{1}'.format(' '.join(cmd), out.decode('utf-8', 'replace')))
    log('tooling git update complete')
    # git reset --hard keeps ignored untracked files (incl. _Local); still protect it
    return dest


def run(args, match=None):
    """Entry for the sync-tooling verb. args.src may be a dir, a git URL, or None."""
    dest = plugin_root()
    src = getattr(args, 'src', None)
    if src is None:
        cfg = engine._load_config()
        src = (os.environ.get('DEPLOY_TOOLING_REPO')
               or cfg.get('tooling_repo'))
        # nothing looked up? a plain 'tooling_dir' in config
        src = src or cfg.get('tooling_dir')
    log = print
    if src and os.path.isdir(src):
        sync_from_dir(src, dest, log=log)
        return 0
    if src and src.startswith(('git@', 'http://', 'https://', 'ssh://')):
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.check_call(['git', 'clone', '--depth', '1', src, os.path.join(scratch, 'tooling')],
                                  creationflags=engine.subprocess_flags())
            sync_from_dir(os.path.join(scratch, 'tooling'), dest, log=log)
        return 0
    if os.path.isdir(os.path.join(dest, '.git')):
        _git_update(dest, log=log)
        return 0
    print('sync-tooling: no source. Pass --src DIR|URL, set tooling_repo/tooling_dir in deploy.local.json, '
          'or run from an installed git plugin.', file=sys.stderr)
    return 2
