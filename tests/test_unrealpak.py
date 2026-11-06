# -*- coding: utf-8 -*-
"""Tests for deploy.unrealpak (discovery + create/list validation)."""
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

from deploy import unrealpak  # noqa: E402


class EnumRegistry(object):
    """Stub winreg with enumerable Builds values (name -> path)."""
    HKEY_LOCAL_MACHINE = 'HKLM'
    HKEY_CURRENT_USER = 'HKCU'

    def __init__(self, builds=None):
        self.builds = list((builds or {}).items())

    def OpenKey(self, hive, key):
        return (hive, key)

    def EnumValue(self, key, idx):
        if idx < len(self.builds):
            name, value = self.builds[idx]
            return (name, value, 1)
        raise IndexError(idx)

    def CloseKey(self, key):
        pass


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-unrealpak-')
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _path(self, *parts):
        return os.path.join(self._dir, *parts)

    def _mkexe(self, rel):
        path = self._path(*rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(b'fake exe')
        return path


class DiscoveryTests(TempCase):
    def test_direct_env(self):
        exe = self._mkexe('tools/UnrealPak.exe')
        self.assertEqual(unrealpak.discover_unrealpak(_env={'UNREALPAK': exe}), exe)

    def test_direct_config(self):
        exe = self._mkexe('tools/UnrealPak.exe')
        self.assertEqual(unrealpak.discover_unrealpak(_config={'unrealpak': exe}, _env={}), exe)

    def test_from_resolved_engine(self):
        exe = self._mkexe('plus/Engine/Binaries/Win64/UnrealPak.exe')
        root = self._path('plus')
        self.assertEqual(unrealpak.discover_unrealpak(engine_root=root, _env={}), exe)

    def test_from_registry_engine_roots(self):
        exe = self._mkexe('ue425/Engine/Binaries/Win64/UnrealPak.exe')
        roots = {'4.25': self._path('ue425')}
        self.assertEqual(unrealpak.discover_unrealpak(_registry=EnumRegistry(roots), _env={}), exe)

    def test_falls_through_resolved_engine_without_exe(self):
        # engine root exists but has no UnrealPak (the Plus case); no other source
        root = self._path('plus')
        os.makedirs(os.path.join(root, 'Engine', 'Binaries', 'Win64'))
        with self.assertRaises(RuntimeError):
            unrealpak.discover_unrealpak(engine_root=root, _config={}, _registry=EnumRegistry(), _env={})

    def test_falls_through_to_registry_when_engine_has_none(self):
        plus = self._path('plus')
        os.makedirs(os.path.join(plus, 'Engine', 'Binaries', 'Win64'))  # Plus: no exe
        exe = self._mkexe('ue425/Engine/Binaries/Win64/UnrealPak.exe')
        found = unrealpak.discover_unrealpak(engine_root=plus,
                                             _registry=EnumRegistry({'4.25': self._path('ue425')}),
                                             _env={})
        self.assertEqual(found, exe)


class CreateTests(TempCase):
    def setUp(self):
        super(CreateTests, self).setUp()
        self.upak = self._mkexe('tools/UnrealPak.exe')
        self.temp_pak = self._path('out', '.MyMod.pak.tmp')
        self.filelist = self._path('out', 'filelist.txt')
        self._write(self.filelist, '"a" "b"\n')

    def _write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(text)

    def test_create_ok(self):
        def fake_call(command, log_path):
            with open(log_path, 'w') as handle:
                handle.write('ok')
            with open(self.temp_pak, 'w') as handle:
                handle.write('pakdata')
            return 0
        unrealpak.run_create(self.upak, self.temp_pak, self.filelist, compress=True, _call=fake_call)

    def test_create_fail_exit(self):
        def fake_call(command, log_path):
            return 1
        with self.assertRaises(RuntimeError):
            unrealpak.run_create(self.upak, self.temp_pak, self.filelist, _call=fake_call)

    def test_create_no_output(self):
        def fake_call(command, log_path):
            return 0  # but writes nothing usable
        with self.assertRaises(RuntimeError):
            unrealpak.run_create(self.upak, self.temp_pak, self.filelist, _call=fake_call)

    def test_compress_flag_present(self):
        seen = []

        def fake_call(command, log_path):
            seen.append(command)
            with open(self.temp_pak, 'w') as handle:
                handle.write('x')
            return 0
        unrealpak.run_create(self.upak, self.temp_pak, self.filelist, compress=True, _call=fake_call)
        self.assertIn('-compress', seen[0])
        self.assertIn('-create=' + self.filelist, seen[0])


class ListTests(TempCase):
    def setUp(self):
        super(ListTests, self).setUp()
        os.makedirs(self._path('out'), exist_ok=True)
        self.upak = self._mkexe('tools/UnrealPak.exe')
        self.temp_pak = self._path('out', '.MyMod.pak.tmp')

    def test_list_ok(self):
        def fake_call(command, log_path):
            with open(log_path, 'w') as handle:
                handle.write('AssetA\nAssetB\n')
            return 0
        text = unrealpak.run_list(self.upak, self.temp_pak, _call=fake_call)
        self.assertEqual(text, 'AssetA\nAssetB\n')

    def test_list_fail_exit(self):
        def fake_call(command, log_path):
            return 1
        with self.assertRaises(RuntimeError):
            unrealpak.run_list(self.upak, self.temp_pak, _call=fake_call)

    def test_list_empty(self):
        def fake_call(command, log_path):
            with open(log_path, 'w') as handle:
                handle.write('')
            return 0
        with self.assertRaises(RuntimeError):
            unrealpak.run_list(self.upak, self.temp_pak, _call=fake_call)


if __name__ == '__main__':
    unittest.main(verbosity=2)
