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

    def test_non_agmods_mount_tracks_the_true_mod_dir(self):
        # a mod under /Game/Mods/ArgonSDK/Mods/MapSelector must mount there, not under AgMods
        cooked = self._path('cooked2', 'TBL', 'Content', 'Mods', 'ArgonSDK', 'Mods', 'MapSelector')
        self._write('cooked2/TBL/Content/Mods/ArgonSDK/Mods/MapSelector/A.uasset')
        fl = self._path('fl2.txt')
        mod_dir = '/Game/Mods/ArgonSDK/Mods/MapSelector'
        pak.write_filelist(cooked, mod_dir, fl)
        with open(fl) as handle:
            line = handle.read().strip()
        self.assertIn('"../../../TBL/Content/Mods/ArgonSDK/Mods/MapSelector/A.uasset"', line)
        self.assertNotIn('AgMods', line)

    def test_mixed(self):
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/Normal.uasset')
        self._write('cooked/TBL/Content/Mods/AgMods/MyMod/_Content/Game/Override.uasset')
        fl = self._path('filelist.txt')
        pak.write_filelist(self.cooked, self.mod_dir, fl)
        with open(fl) as handle:
            lines = handle.read().splitlines()
        joined = ' '.join(lines)
        self.assertIn('"../../../TBL/Content/Mods/AgMods/MyMod/Normal.uasset"', joined)
        self.assertIn('"../../../TBL/Content/Game/Override.uasset"', joined)


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


class ParseSizeTests(unittest.TestCase):
    def test_bare_bytes(self):
        self.assertEqual(pak.parse_size(52428800), 52428800)
        self.assertEqual(pak.parse_size('1000'), 1000)

    def test_units(self):
        self.assertEqual(pak.parse_size('200MB'), 200 * 1024 * 1024)
        self.assertEqual(pak.parse_size('1g'), 1024 ** 3)
        self.assertEqual(pak.parse_size('64Kb'), 64 * 1024)
        self.assertEqual(pak.parse_size('2 m'), 2 * 1024 ** 2)

    def test_invalid(self):
        for bad in ('abc', '', '-5MB', 'MB'):
            with self.assertRaises(ValueError):
                pak.parse_size(bad)


class PartitionTests(unittest.TestCase):
    @staticmethod
    def _e(target, size):
        return ('SRC', target, size)

    def test_packages_stay_together(self):
        entries = [self._e('/A.uasset', 100), self._e('/A.uexp', 100),  # package A = 200
                   self._e('/B.uasset', 50)]                            # package B = 50
        parts = pak._partition_entries(entries, 200)
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[0]), 2)  # A.uasset + A.uexp together
        self.assertEqual(len(parts[1]), 1)  # B in its own part

    def test_no_split_within_threshold(self):
        entries = [self._e('/A.uasset', 100), self._e('/B.uasset', 50)]
        parts = pak._partition_entries(entries, 1000)
        self.assertEqual(len(parts), 1)
        self.assertEqual(len(parts[0]), 2)

    def test_oversized_package_own_part(self):
        entries = [self._e('/Huge.uasset', 10 ** 9)]
        parts = pak._partition_entries(entries, 100)
        self.assertEqual(len(parts), 1)
        self.assertEqual(len(parts[0]), 1)


class PakSplitTests(TempCase):
    def setUp(self):
        super(PakSplitTests, self).setUp()
        self.dest = self._path('paks')
        self.engine_root = self._path('engine')
        os.makedirs(self.dest)
        os.makedirs(os.path.join(self.engine_root, 'Engine', 'Binaries', 'Win64'))
        self.cooked = self._path('cooked', 'TBL', 'Content', 'Mods', 'AgMods', 'MyMod')
        self.addCleanup(self._restore)
        self._orig = (unrealpak.discover_unrealpak, unrealpak.run_create, unrealpak.run_list)

    def _restore(self):
        unrealpak.discover_unrealpak, unrealpak.run_create, unrealpak.run_list = self._orig

    def _patch_pak(self):
        unrealpak.discover_unrealpak = lambda *a, **k: self._path('engine', 'UnrealPak.exe')

        def fake_create(_u, temp_pak, filelist, compress=True, log_path=None, _call=None):
            os.makedirs(os.path.dirname(temp_pak), exist_ok=True)
            with open(temp_pak, 'w') as handle:
                handle.write('pak')

        def fake_list(_u, temp_pak, log_path=None, _call=None):
            if log_path:
                with open(log_path, 'w') as handle:
                    handle.write('a\nb\n')
            return 'a\nb\n'

        unrealpak.run_create = fake_create
        unrealpak.run_list = fake_list

    def test_split_produces_named_parts(self):
        # package A = A.uasset + A.uexp (600KB), package B = B.uasset (600KB); threshold 500KB
        self._write_pkg('A.uasset', 300000)
        self._write_pkg('A.uexp', 300000)
        self._write_pkg('B.uasset', 600000)
        self._patch_pak()
        out = pak.pak_mod({'success': True, 'mod': 'MyMod', 'cooked_dir': self.cooked},
                          self.dest, ue4_root=self.engine_root, max_pak_size='500KB')
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 2)
        self.assertEqual(os.path.basename(out[0]), 'MyMod_part1.pak')
        self.assertEqual(os.path.basename(out[1]), 'MyMod_part2.pak')
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'MyMod_part1.txt')))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, 'MyMod_part2.txt')))
        leftovers = [n for n in os.listdir(self.dest) if n.startswith('.')]
        self.assertEqual(leftovers, [])

    def test_within_threshold_single_pak(self):
        self._write_pkg('A.uasset', 100)
        self._write_pkg('B.uasset', 100)
        self._patch_pak()
        out = pak.pak_mod({'success': True, 'mod': 'MyMod', 'cooked_dir': self.cooked},
                          self.dest, ue4_root=self.engine_root, max_pak_size='500MB')
        self.assertIsInstance(out, str)
        self.assertEqual(os.path.basename(out), 'MyMod.pak')

    def _write_pkg(self, name, size):
        path = os.path.join(self.cooked, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write('x' * size)


if __name__ == '__main__':
    unittest.main(verbosity=2)
