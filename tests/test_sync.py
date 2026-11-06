# -*- coding: utf-8 -*-
"""Tests for deploy.sync (the replace-not-merge / _Local-preserving update)."""
from __future__ import print_function

import logging
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTENT_PYTHON = os.path.abspath(os.path.join(_HERE, '..', 'Content', 'Python'))
if _CONTENT_PYTHON not in sys.path:
    sys.path.insert(0, _CONTENT_PYTHON)

_HERE_ROOT = os.path.abspath(os.path.join(_HERE, '..'))  # plugin root in repo
CONTENT_PYTHON = os.path.abspath(os.path.join(_HERE_ROOT, 'Content', 'Python'))
if CONTENT_PYTHON not in sys.path:
    sys.path.insert(0, CONTENT_PYTHON)

from deploy import sync  # noqa: E402


def _case_main():
    import unittest
    return unittest


unittest = _case_main()


class SyncFromDirTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-sync-')
        self.addCleanup(shutil.rmtree, self._dir, True)
        # source plugin (a newer/upstream checkout)
        self.src = os.path.join(self._dir, 'source')
        self._write('source/Content/Python/deploy/__init__.py', 'new deploy')
        self._write('source/Content/Scripts/ModHandling/ModPackerWidget.uasset', 'NEWWIDGET')
        # destination plugin with a USER customisation in _Local
        self.dest = os.path.join(self._dir, 'dest')
        self._write('dest/Content/Python/deploy/__init__.py', 'old deploy')
        self._write('dest/Content/Scripts/ModHandling/ModPackerWidget.uasset', 'OLDWIDGET')
        self._write('dest/Content/Scripts/ModHandling/NewModWidget.uasset', 'OLDNEW')
        self._write('dest/Content/Scripts/_Local/MyModPackerWidget.uasset', 'MYLOCAL')

    def _write(self, rel, text):
        path = os.path.join(self._dir, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(text)

    def _read(self, rel):
        with open(os.path.join(self._dir, *rel.split('/'))) as handle:
            return handle.read()

    def test_updates_stock_but_preserves_local(self):
        sync.sync_from_dir(self.src, self.dest, log=logging.warning)
        # stock widgets replaced
        self.assertEqual(self._read('dest/Content/Scripts/ModHandling/ModPackerWidget.uasset'), 'NEWWIDGET')
        # a file present in dest but NOT in source (old widget) is removed (replace, not merge)
        self.assertFalse(os.path.exists(os.path.join(self.dest, 'Content', 'Scripts', 'ModHandling',
                                                     'NewModWidget.uasset')))
        # scripts replaced
        self.assertEqual(self._read('dest/Content/Python/deploy/__init__.py'), 'new deploy')
        # the user's _Local customisation SURVIVES untouched
        self.assertTrue(os.path.exists(os.path.join(self.dest, 'Content', 'Scripts', '_Local',
                                                    'MyModPackerWidget.uasset')))
        self.assertEqual(self._read('dest/Content/Scripts/_Local/MyModPackerWidget.uasset'), 'MYLOCAL')

    def test_creates_managed_dirs_when_absent(self):
        self.dest = os.path.join(self._dir, 'fresh')
        self._write('fresh/Content/Scripts/_Local/.keep', '')
        # no ModHandling yet
        sync.sync_from_dir(self.src, self.dest, log=logging.warning)
        self.assertEqual(self._read('fresh/Content/Scripts/ModHandling/ModPackerWidget.uasset'), 'NEWWIDGET')
        self.assertTrue(os.path.exists(os.path.join(self.dest, 'Content', 'Scripts', '_Local', '.keep')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
