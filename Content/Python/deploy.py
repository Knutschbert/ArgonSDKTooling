#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Command-line shim for the `deploy` package.

All logic (cook, pak, sync-tooling, run_from_widget) lives in the deploy package:

    python deploy.py <verb> ...            # CLI (this file -> deploy._main)
    import deploy                          # API: deploy.cook, deploy.run_from_widget, ...

Keeping both a `deploy.py` file and a `deploy/` package means `import deploy`
resolves to the PACKAGE (the directory wins over the same-named module), which
is where the public API lives; this file only bridges the CLI.
"""
from __future__ import print_function

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)


def _main(argv=None):
    from deploy import _main
    return _main(argv)


if __name__ == '__main__':
    raise SystemExit(_main())
