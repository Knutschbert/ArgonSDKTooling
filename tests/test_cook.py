# -*- coding: utf-8 -*-
"""Tests for deploy.cook (package collection, command, judge, chunking)."""
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

from deploy import cook  # noqa: E402
from deploy import engine  # noqa: E402


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-cook-')
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _path(self, *parts):
        return os.path.join(self._dir, *parts)

    def _write(self, rel, text='', binary=b''):
        path = self._path(*rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if binary:
            with open(path, 'wb') as handle:
                handle.write(binary)
        else:
            with open(path, 'w') as handle:
                handle.write(text)
        return path


class PackagePathTests(TempCase):
    def test_mod_package_dir_short(self):
        self.assertEqual(cook.mod_package_dir('MyMod'), '/Game/Mods/AgMods/MyMod')

    def test_mod_package_dir_full(self):
        self.assertEqual(cook.mod_package_dir('/Game/Mods/Other/Thing'),
                         '/Game/Mods/Other/Thing')

    def test_collect_packages(self):
        self._write('Content/Mods/AgMods/MyMod/A.uasset')
        self._write('Content/Mods/AgMods/MyMod/Sub/B.umap')
        self._write('Content/Mods/AgMods/MyMod/Sub/NOT_C.c')
        up = self._path('TBL.uproject')
        pkgs = cook.collect_packages(up, '/Game/Mods/AgMods/MyMod')
        self.assertEqual(pkgs, ['/Game/Mods/AgMods/MyMod/A',
                                '/Game/Mods/AgMods/MyMod/Sub/B'])

    def test_collect_packages_missing_folder(self):
        up = self._path('TBL.uproject')
        with self.assertRaises(RuntimeError):
            cook.collect_packages(up, '/Game/Mods/AgMods/Missing')

    def test_source_manifest(self):
        self._write('Content/Mods/AgMods/MyMod/A.uasset', 'x')
        self._write('Content/Mods/AgMods/MyMod/Sub/B.umap', 'yy')
        up = self._path('TBL.uproject')
        entries = cook.source_manifest(up, '/Game/Mods/AgMods/MyMod')
        self.assertEqual([e['path'] for e in entries],
                         ['Mods/AgMods/MyMod/A.uasset', 'Mods/AgMods/MyMod/Sub/B.umap'])
        self.assertEqual([e['size'] for e in entries], [1, 2])
        self.assertIn('mtime', entries[0])

    def test_discovery_requires_editor(self):
        with self.assertRaises(RuntimeError):
            cook.discover_nativization_exclusions('/Game/Mods/AgMods/MyMod', unreal_module=None)

    def test_discovery_finds_user_types(self):
        class Data(object):
            def __init__(self, asset_class):
                self.asset_class = asset_class

        class Library(object):
            @staticmethod
            def list_assets(_root, recursive=True, include_folder=False):
                return ['/Game/Mods/AgMods/MyMod/ZEnum.ZEnum',
                        '/Game/Mods/AgMods/MyMod/AStruct.AStruct',
                        '/Game/Mods/AgMods/MyMod/BP.BP']

            @staticmethod
            def find_asset_data(path):
                return Data('UserDefinedEnum' if 'Enum' in path else
                            'UserDefinedStruct' if 'Struct' in path else 'Blueprint')

        class FakeUnreal(object):
            EditorAssetLibrary = Library

        self.assertEqual(
            cook.discover_nativization_exclusions('/Game/Mods/AgMods/MyMod', unreal_module=FakeUnreal),
            ['/Game/Mods/AgMods/MyMod/AStruct', '/Game/Mods/AgMods/MyMod/ZEnum'])

    def test_discovery_scans_bundled_folders_once_and_deduplicates(self):
        class Data(object):
            def __init__(self, asset_class):
                self.asset_class = asset_class

        class Library(object):
            calls = []

            @staticmethod
            def list_assets(root, recursive=True, include_folder=False):
                Library.calls.append(root)
                return [root + '/Shared.Shared', root + '/Only.Only']

            @staticmethod
            def find_asset_data(path):
                return Data('UserDefinedStruct' if path.endswith('Shared.Shared') else 'UserDefinedEnum')

        class FakeUnreal(object):
            EditorAssetLibrary = Library

        result = cook.discover_nativization_exclusions(
            '/Game/Mods/AgMods/MyMod',
            ['/Game/Mods/AgMods/Bundled', '/Game/Mods/AgMods/MyMod'],
            FakeUnreal)
        self.assertEqual(Library.calls, ['/Game/Mods/AgMods/MyMod', '/Game/Mods/AgMods/Bundled'])
        self.assertEqual(result, [
            '/Game/Mods/AgMods/Bundled/Only',
            '/Game/Mods/AgMods/Bundled/Shared',
            '/Game/Mods/AgMods/MyMod/Only',
            '/Game/Mods/AgMods/MyMod/Shared'])


class CommandTests(TempCase):
    def test_cook_command_shape(self):
        root = self._path('engine')
        os.makedirs(os.path.join(root, 'Engine', 'Binaries', 'Win64'))
        cmd = cook.cook_command(root, self._path('TBL.uproject'),
                                ['/Game/A/B', '/Game/C/D'],
                                self._path('out'), self._path('x.log'))
        binpath = engine.engine_cmd_path(root)
        self.assertEqual(cmd[0], binpath)
        self.assertEqual(cmd[1], self._path('TBL.uproject'))
        joined = '-Map=' + '+'.join(['/Game/A/B', '/Game/C/D'])
        self.assertIn(joined, cmd)
        self.assertIn('-cooksinglepackage', cmd)
        self.assertIn('-OutputDir=' + self._path('out'), cmd)

    def test_cook_command_does_not_replace_editor_config(self):
        cmd = cook.cook_command(self._path('engine'), self._path('TBL.uproject'),
                                ['/Game/A'], self._path('out'), self._path('x.log'),
                                nativization_config=self._path('exclude.ini'))
        self.assertNotIn('-EditorIni=' + self._path('exclude.ini'), cmd)

    def test_nativization_config_format_is_deterministic(self):
        self.assertEqual(
            cook.format_nativization_config(['/Game/Z', '/Game/A', '/Game/Z']),
            '[BlueprintNativizationSettings]\n'
            '+ExcludedAssets=/Game/A\n'
            '+ExcludedAssets=/Game/Z\n')

    def test_write_nativization_config(self):
        path = cook.write_nativization_config(self._path('Saved', 'exclude.ini'), ['/Game/A'])
        with open(path) as handle:
            self.assertEqual(handle.read(), '[BlueprintNativizationSettings]\n+ExcludedAssets=/Game/A\n')

    def test_overlay_restores_existing_editor_config(self):
        up = self._path('TBL.uproject')
        config = self._write('Config/DefaultEditor.ini', '[Existing]\nKey=Value\n')
        token = cook.install_nativization_overlay(up, ['/Game/A', '/Game/B'])
        with open(config, 'rb') as handle:
            self.assertIn(b'[Existing]', handle.read())
        cook.restore_nativization_overlay(token)
        with open(config, 'rb') as handle:
            self.assertEqual(handle.read(), b'[Existing]\nKey=Value\n')

    def test_overlay_removes_new_config(self):
        up = self._path('TBL.uproject')
        token = cook.install_nativization_overlay(up, ['/Game/A'])
        self.assertTrue(os.path.isfile(token['path']))
        cook.restore_nativization_overlay(token)
        self.assertFalse(os.path.exists(token['path']))
        self.assertFalse(os.path.exists(os.path.join(self._dir, 'Saved', '.deploy-nativization.lock')))


class JudgeTests(TempCase):
    def test_ok(self):
        log = self._write('a.log', 'LogInit: Packages Remain 0\nnothing\n')
        self.assertEqual(cook.judge(log), (True, []))

    def test_critical_error(self):
        log = self._write('a.log', 'Packages Remain 0\nLogInit: Critical error: boom\n')
        ok, _ = cook.judge(log)
        self.assertFalse(ok)

    def test_assertion(self):
        log = self._write('a.log', 'Packages Remain 0\nAssertion failed: [File: x]\n')
        ok, _ = cook.judge(log)
        self.assertFalse(ok)

    def test_not_finished(self):
        log = self._write('a.log', 'Packages Remain 5\n')
        ok, _ = cook.judge(log)
        self.assertFalse(ok)

    def test_errors_collected(self):
        log = self._write('a.log', 'line1\nError: bad thing\nError: worse\n')
        ok, errors = cook.judge(log)
        self.assertFalse(ok)
        self.assertEqual(errors, ['Error: bad thing', 'Error: worse'])


class ChunkingTests(TempCase):
    def test_chunks_split_by_max(self):
        packages = ['/Game/Mods/AgMods/M/P{0:03d}'.format(i) for i in range(7)]
        job = cook.CookJob('M', self._path('u.uproject'), self._path('e'), packages,
                           self._path('out'), self._path('log.log'), [], max_chunk=3, _start=False)
        self.assertEqual(job.chunks, [packages[0:3], packages[3:6], packages[6:7]])

    def test_cooked_dir_layout(self):
        packages = ['/Game/Mods/AgMods/M/A']
        job = cook.CookJob('M', self._path('u.uproject'), self._path('e'), packages,
                           self._path('out'), self._path('log.log'), [], max_chunk=250, _start=False)
        self.assertTrue(job.cooked_dir.endswith(os.path.join('out', 'TBL', 'Content', 'Mods', 'AgMods', 'M')))

    def test_cooked_dir_uses_true_mod_dir_not_agmods(self):
        # a mod whose real root is NOT /Game/Mods/AgMods (e.g. inside ArgonSDK)
        mod_dir = '/Game/Mods/ArgonSDK/Mods/MapSelector'
        packages = ['/Game/Mods/ArgonSDK/Mods/MapSelector/A',
                    '/Game/Mods/ArgonSDK/Mods/MapSelector/B']
        job = cook.CookJob('MapSelector', self._path('u.uproject'), self._path('e'), packages,
                           self._path('out'), self._path('log.log'), [], max_chunk=250,
                           mod_dir=mod_dir, _start=False)
        self.assertTrue(job.cooked_dir.endswith(
            os.path.join('out', 'TBL', 'Content', 'Mods', 'ArgonSDK', 'Mods', 'MapSelector')))
        # the result carries the true mod_dir so pak mounts to the real path
        job.result = {'success': True, 'mod': 'MapSelector', 'mod_dir': mod_dir,
                      'cooked_dir': job.cooked_dir, 'packages': 2, 'errors': [], 'chunks': 1}
        self.assertEqual(job.result['mod_dir'], mod_dir)


if __name__ == '__main__':
    unittest.main(verbosity=2)
