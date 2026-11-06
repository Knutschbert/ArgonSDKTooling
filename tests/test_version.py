# -*- coding: utf-8 -*-
"""Tests for deploy.version (mod.json reading; the editor stamp is gated)."""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTENT_PYTHON = os.path.abspath(os.path.join(_HERE, '..', 'Content', 'Python'))
if _CONTENT_PYTHON not in sys.path:
    sys.path.insert(0, _CONTENT_PYTHON)

from deploy import version  # noqa: E402


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-version-')
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _path(self, *parts):
        return os.path.join(self._dir, *parts)

    def _write(self, rel, text):
        path = self._path(*rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(text)
        return path

    def _uproject(self, mod='MyMod'):
        return self._write('TBL.uproject', '{}')


class ModJsonTests(TempCase):
    def test_find_in_mod_folder(self):
        self._uproject('MyMod')
        self._write('Content/Mods/AgMods/MyMod/mod.json', '{"version":"2.1.0"}')
        path = version.find_mod_json(self._path('TBL.uproject'),
                                     '/Game/Mods/AgMods/MyMod')
        self.assertEqual(path, self._path('Content/Mods/AgMods/MyMod/mod.json'))

    def test_find_in_ancestor_content(self):
        up = self._write('TBL.uproject', '{}')
        self._write('Content/mod.json', '{"version":"1.0.0"}')
        path = version.find_mod_json(up, '/Game/Mods/AgMods/MyMod')
        self.assertEqual(path, self._path('Content/mod.json'))

    def test_missing_returns_none(self):
        up = self._write('TBL.uproject', '{}')
        self.assertIsNone(version.find_mod_json(up, '/Game/Mods/AgMods/MyMod'))

    def test_read_and_version(self):
        self._write('Content/Mods/AgMods/MyMod/mod.json', json.dumps({"version": "3.0.1", "name": "X"}))
        data = version.read_mod_json(self._path('TBL.uproject'), '/Game/Mods/AgMods/MyMod')
        self.assertEqual(data['version'], '3.0.1')
        self.assertEqual(version.mod_version(self._path('TBL.uproject'), '/Game/Mods/AgMods/MyMod'),
                         '3.0.1')

    def test_no_dotjson_version_field(self):
        self._write('Content/Mods/AgMods/MyMod/mod.json', json.dumps({"name": "X"}))
        self.assertIsNone(version.mod_version(self._path('TBL.uproject'), '/Game/Mods/AgMods/MyMod'))

    def test_invalid_json(self):
        self._write('Content/Mods/AgMods/MyMod/mod.json', 'not-json{')
        self.assertIsNone(version.read_mod_json(self._path('TBL.uproject'), '/Game/Mods/AgMods/MyMod'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
