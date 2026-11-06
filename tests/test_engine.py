# -*- coding: utf-8 -*-
"""Tests for deploy.engine (portable project/engine resolution).

Runs on any Python (stdlib only), no editor required. A stub registry + config
and temp dirs stand in for Windows-specific lookups.
"""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import unittest

# Make the deploy package importable regardless of CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTENT_PYTHON = os.path.abspath(os.path.join(_HERE, '..', 'Content', 'Python'))
if _CONTENT_PYTHON not in sys.path:
    sys.path.insert(0, _CONTENT_PYTHON)

from deploy import engine  # noqa: E402


class StubRegistry(object):
    """Minimal fake winreg for _registry_engine: maps association name -> path."""
    HKEY_LOCAL_MACHINE = 'HKLM'
    HKEY_CURRENT_USER = 'HKCU'

    def __init__(self, values=None):
        self.values = values or {}
        self.opened = []

    def OpenKey(self, hive, key):
        self.opened.append((hive, key))
        return (hive, key)

    def QueryValueEx(self, key, name):
        if name in self.values:
            return (self.values[name], 1)
        raise KeyError(name)

    def CloseKey(self, key):
        pass


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-engine-')
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _path(self, *parts):
        return os.path.join(self._dir, *parts)

    def _write(self, rel, text):
        path = self._path(*rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(text)
        return path


class FindProjectTests(TempCase):
    def test_explicit_path_is_absolutized(self):
        up = self._path('SDK', 'TBL.uproject')
        self._write('SDK/TBL.uproject', '{}')
        self.assertEqual(engine.find_project(up), os.path.abspath(up))

    def test_find_uproject_walks_up(self):
        up = self._path('SDK', 'TBL.uproject')
        self._write('SDK/TBL.uproject', '{"FileVersion":3}')
        leaf = self._path('SDK', 'Plugins', 'ArgonSDKTooling', 'Content', 'Python', 'deploy')
        self._write('SDK/Plugins/ArgonSDKTooling/Content/Python/deploy/__init__.py', '')
        found = engine._find_uproject(leaf)
        self.assertEqual(found, up)

    def test_find_project_from_config(self):
        cfg = {'uproject_path': self._path('cfg', 'TBL.uproject')}
        self._write('cfg/TBL.uproject', '{}')
        self.assertEqual(engine.find_project(_config=cfg), os.path.abspath(cfg['uproject_path']))

    def test_missing_project_raises(self):
        # No uproject reachable, no config: in a temp CWD nothing upward has one.
        old = os.getcwd()
        os.chdir(self._dir)
        try:
            with self.assertRaises(RuntimeError):
                engine.find_project(_config={})
        finally:
            os.chdir(old)


class EngineResolutionTests(TempCase):
    def _mkroot(self, name):
        # an engine root with an Engine/ dir beneath it
        root = self._path(name)
        os.makedirs(os.path.join(root, 'Engine', 'Binaries', 'Win64'))
        return root

    def test_explicit_root_passthrough(self):
        root = self._mkroot('plus')
        got = engine.find_engine(engine_root=root)
        self.assertEqual(got, root)

    def test_engine_dir_input_normalizes_to_root(self):
        root = self._mkroot('plus')
        eng = os.path.join(root, 'Engine')
        self.assertEqual(engine.find_engine(engine_root=eng), root)

    def test_launcher_association_via_registry(self):
        root = self._mkroot('ue425')
        up = self._write('SDK/TBL.uproject', json.dumps({'EngineAssociation': '4.25'}))
        reg = StubRegistry({'4.25': root})
        self.assertEqual(engine.find_engine(uproject=up, _registry=reg), root)

    def test_source_guid_association_via_registry(self):
        root = self._mkroot('plus')
        up = self._write('SDK/TBL.uproject',
                          json.dumps({'EngineAssociation': '{66C5D560-4730-FC80-FDCE-03AF405F06AC}'}))
        # Unreal stores the source-build GUID "with braces" as the registry value name.
        reg = StubRegistry({'{66C5D560-4730-FC80-FDCE-03AF405F06AC}': root})
        self.assertEqual(engine.find_engine(uproject=up, _registry=reg), root)

    def test_source_guid_via_install_ini(self):
        root = self._mkroot('plus')
        up = self._write('SDK/TBL.uproject',
                          json.dumps({'EngineAssociation': '{GUID01-0000}'}))
        ini = self._path('APPDATA', 'UnrealEngine', 'Install.ini')
        self._write('APPDATA/UnrealEngine/Install.ini',
                     '[InstalledBuilds]\n{GUID01-0000}=%s\n' % root)
        old = os.environ.get('APPDATA')
        os.environ['APPDATA'] = os.path.dirname(os.path.dirname(ini))
        try:
            self.assertEqual(engine.find_engine(uproject=up, _registry=StubRegistry()), root)
        finally:
            if old is None:
                os.environ.pop('APPDATA', None)
            else:
                os.environ['APPDATA'] = old

    def test_config_fallback(self):
        root = self._mkroot('plus')
        up = self._write('SDK/TBL.uproject', json.dumps({'EngineAssociation': '4.25'}))
        # no registry hit, but config provides the root
        got = engine.find_engine(uproject=up, _config={'engine_root': root},
                                 _registry=StubRegistry())
        self.assertEqual(got, root)

    def test_env_fallback(self):
        root = self._mkroot('plus')
        up = self._write('SDK/TBL.uproject', json.dumps({'EngineAssociation': '4.25'}))
        got = engine.find_engine(uproject=up, _config={}, _registry=StubRegistry(),
                                 _env={'DEPLOY_ENGINE': root})
        self.assertEqual(got, root)

    def test_missing_engine_raises(self):
        up = self._write('SDK/TBL.uproject', json.dumps({'EngineAssociation': '4.25'}))
        with self.assertRaises(RuntimeError):
            engine.find_engine(uproject=up, _config={}, _registry=StubRegistry(), _env={})


class DirectoryHelpersTests(TempCase):
    def test_engine_dir_from_root(self):
        root = self._path('root')
        os.makedirs(os.path.join(root, 'Engine'))
        self.assertEqual(engine.engine_dir(root), os.path.join(root, 'Engine'))

    def test_engine_dir_from_engine_dir(self):
        root = self._path('root')
        eng = os.path.join(root, 'Engine')
        os.makedirs(eng)
        self.assertEqual(engine.engine_dir(eng), eng)

    def test_engine_cmd_path(self):
        root = self._path('root')
        os.makedirs(os.path.join(root, 'Engine', 'Binaries', 'Win64'))
        expected = os.path.join(root, 'Engine', 'Binaries', 'Win64', 'UE4Editor-Cmd.exe')
        self.assertEqual(engine.engine_cmd_path(root), expected)

    def test_engine_association_parse(self):
        up = self._write('SDK/TBL.uproject', json.dumps({'EngineAssociation': '4.25'}))
        self.assertEqual(engine.engine_association(up), '4.25')
        up2 = self._write('SDK/no.json', '{}')
        self.assertIsNone(engine.engine_association(up2))


if __name__ == '__main__':
    unittest.main(verbosity=2)
