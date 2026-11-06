# -*- coding: utf-8 -*-
"""Tests for deploy.pak (filelist incl. _Content mount + validated publish)."""
from __future__ import print_function

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTENT_PYTHON = os.path.abspath(os.path.join(_HERE, '..', 'Content', 'Python'))
if _CONTENT_PYTHON not in sys.path:
    sys.path.insert(0, _CONTENT_PYTHON)

from deploy import pak  # noqa: E402
from deploy import unrealpak  # noqa: E402


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-pak-')
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _path(self, *parts):
        return os.path.join(self._dir, *parts)

    def _write(self, rel, text=''):
        path = self._path(*rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(text)
        return path


class WriteFilelistTests(TempCase):
    def setUp(self):
        super(WriteFilelistTests, self).setUp()
        # cooked_dir is the MOD-FOLDER level: <out>/TBL/Content/Mods/AgMods/MyMod
        self.cooked = self._path('cooked', 'TBL', 'Content', 'Mods', 'AgMods', 'MyMod')
        self.mod_dir = '/Game/Mods/AgMods/MyMod'

    def test_normal_mount(self):
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/A.uasset')
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/Sub/B.umap')
        fl = self._path('filelist.txt')
        count = pak.write_filelist(self.cooked, self.mod_dir, fl)
        with open(fl) as handle:
            lines = handle.read().splitlines()
        self.assertEqual(count, 2)
        self.assertIn('"../../../TBL/Content/Mods/AgMods/MyMod/A.uasset"', lines[0])

    def test_content_replacement_mount(self):
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/_Content/Mesh/Weapon.uasset')
        fl = self._path('filelist.txt')
        pak.write_filelist(self.cooked, self.mod_dir, fl)
        with open(fl) as handle:
            line = handle.read().strip()
        # the source (first quoted token) legitimately lives under _Content; the
        # TARGET must mount at TBL/Content root, not inside the mod folder.
        source, target = line.split('" "')
        target = target.rstrip('"')
        self.assertIn('_Content', source)
        self.assertEqual(target, '../../../TBL/Content/Mesh/Weapon.uasset')
        self.assertNotIn('Mods/AgMods', target)

    def test_mixed(self):
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/Normal.uasset')
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/_Content/Game/Override.uasset')
        fl = self._path('filelist.txt')
        pak.write_filelist(self.cooked, self.mod_dir, fl)
        with open(fl) as handle:
            lines = handle.read().splitlines()
        self.assertIn('"../../../TBL/Content/Mods/AgMods/MyMod/Normal.uasset"', lines[0])
        self.assertIn('"../../../TBL/Content/Game/Override.uasset"', lines[1])


class PakModTests(TempCase):
    def setUp(self):
        super(PakModTests, self).setUp()
        self.dest = self._path('paks')
        os.makedirs(self.dest)
        self.cooked = self._path('cooked')
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/A.uasset')
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/_Content/B.uasset')
        self.engine_root = self._path('engine')
        os.makedirs(os.path.join(self.engine_root, 'Engine', 'Binaries', 'Win64'))
        self.addCleanup(self._restore)
        self._orig = (unrealpak.discover_unrealpak, unrealpak.run_create, unrealpak.run_list)

    def _restore(self):
        unrealpak.discover_unrealpak, unrealpak.run_create, unrealpak.run_list = self._orig

    def test_publish_ok(self):
        unrealpak.discover_unrealpak = lambda *a, **k: self._path('engine', 'UnrealPak.exe')

        def fake_create(unrealpak_run, temp_pak, filelist, compress=True, log_path=None, _call=None):
            os.makedirs(os.path.dirname(temp_pak), exist_ok=True)
            with open(temp_pak, 'w') as handle:
                handle.write('pak')

        def fake_list(unrealpak_run, temp_pak, log_path=None, _call=None):
            if log_path:
                with open(log_path, 'w') as handle:
                    handle.write('A\nB\n')
            return 'A\nB\n'

        unrealpak.run_create = fake_create
        unrealpak.run_list = fake_list

        result = {'success': True, 'mod': 'MyMod', 'cooked_dir': self.cooked}
        path = pak.pak_mod(result, self.dest, ue4_root=self.engine_root)
        self.assertEqual(path, os.path.join(self.dest, 'MyMod.pak'))
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'MyMod.txt')))
        # temp files cleaned
        leftovers = [n for n in os.listdir(self.dest) if n.startswith('.')]
        self.assertEqual(leftovers, [])

    def test_failed_cook_aborts(self):
        with self.assertRaises(RuntimeError):
            pak.pak_mod({'success': False, 'mod': 'MyMod'}, self.dest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
