# -*- coding: utf-8 -*-
"""deploy - portable cook/pak + sync-tooling for the Chivalry 2 SDK.

Modules:
    engine.py      project + engine resolution (EngineAssociation, no hardcoded paths)
    unrealpak.py   UnrealPak discovery + create/list validation
    cook.py        cook commandlet runner (chunking, log judgment)
    pak.py         UnrealPak filelist (incl. _Content mount) + publish
    provenance.py  CookModManifest write/validate
    deploy.py      CLI + editor entry point (sibling file)

Kept Python 2.7-safe (editor) and CLI-run on any Python.
"""
from __future__ import print_function
