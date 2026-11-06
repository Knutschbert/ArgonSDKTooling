# -*- coding: utf-8 -*-
"""Portable cook/pak tooling for the Chivalry 2 SDK.

Self-contained replacement for the old per-SDK `cook_mod.py`. Lives in this
plugin's `Content/Python` so it is on the editor's Python path, and is also a
standalone CLI.

    # in the editor (Python mode / Editor Utility Widget / Scripting BP)
    import deploy
    deploy.cook("MyMod")                      # blocks until done -> result dict
    deploy.cook_and_pak("MyMod", "C:/MyPaks")

    # on the command line (any Python; project/engine resolved, no hardcoded paths)
    python deploy.py cook MyMod
    python deploy.py cook MyMod MySecondMod --map /Game/Mods/AgMods/MyMod/Maps/MyMap
    python deploy.py pak MyMod --dest C:/MyPaks
    python deploy.py sync-tooling

Implementation lives in the `deploy/` package (see the sibling modules). This
file is the thin entry point and CLI.

Kept Python 2.7-safe so it also runs in the UE editor's interpreter.
"""
from __future__ import print_function

import sys

# <plugin>/Content/Python -> our package dir
sys.path.insert(0, __file__ and __file__[:__file__.rfind('/')] or '.')


def _main(argv=None):
    """One subcommand per verb: cook / pak / sync-tooling (and cook+pak via --dest on cook).

    TODO(vje.7): full CLI wiring. Stub for the skeleton commit - prints usage.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    verb = argv[0]
    print('deploy: verb=%r parsed=%r (implementation lands in vje.2-vje.8)' % (verb, argv[1:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
