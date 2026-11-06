# -*- coding: utf-8 -*-
"""Tests for deploy.provenance (manifest write/read + validation)."""
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

from deploy import provenance  # noqa: E402


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-prov-')
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _path(self, *parts):
        return os.path.join(self._dir, *parts)

    def _write(self, rel, text=''):
        path = self._path(*rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(text)
        return path


class ManifestTests(TempCase):
    def test_write_read_roundtrip(self):
        path = self._path('out', 'CookModManifest.json')
        sources = [{'path': 'Mods/AgMods/M/A.uasset', 'size': 1, 'mtime': 2}]
        provenance.write_manifest(path, 'M', sources, self._path('cooked'))
        manifest = provenance.read_manifest(path)
        self.assertTrue(manifest['complete'])
        self.assertEqual(manifest['mod'], 'M')
        self.assertEqual(manifest['source_files'], sources)

    def test_write_is_atomic_publish(self):
        path = self._path('out', 'CookModManifest.json')
        provenance.write_manifest(path, 'M', [], self._path('cooked'))
        self.assertTrue(os.path.isfile(path))
        # no leftover temp
        leftovers = [n for n in os.listdir(self._path('out')) if '.tmp.' in n]
        self.assertEqual(leftovers, [])


class ValidateTests(TempCase):
    def _setup_valid(self):
        cooked = self._path('cooked')
        self._write('cooked/TBL/Content/Mods/AgMods/M/A.uasset', 'x')
        sources = [{'path': 'Mods/AgMods/M/A.uasset', 'size': 1, 'mtime': 1}]
        manifest = self._path('CookModManifest.json')
        provenance.write_manifest(manifest, 'M', sources, cooked)
        return cooked, manifest, sources

    def test_valid_passes(self):
        cooked, manifest, sources = self._setup_valid()
        got = provenance.validate_cooked_output(cooked, manifest, 'M', sources)
        self.assertEqual(got['mod'], 'M')

    def test_missing_manifest(self):
        cooked, _m, sources = self._setup_valid()
        with self.assertRaises(RuntimeError):
            provenance.validate_cooked_output(cooked, self._path('nope.json'), 'M', sources)

    def test_wrong_mod(self):
        cooked, manifest, sources = self._setup_valid()
        with self.assertRaises(RuntimeError):
            provenance.validate_cooked_output(cooked, manifest, 'Other', sources)

    def test_stale_sources(self):
        cooked, manifest, sources = self._setup_valid()
        stale = list(sources)
        stale[0] = dict(sources[0])
        stale[0]['size'] = 999
        with self.assertRaises(RuntimeError):
            provenance.validate_cooked_output(cooked, manifest, 'M', stale)

    def test_missing_cooked_dir(self):
        _cooked, manifest, sources = self._setup_valid()
        with self.assertRaises(RuntimeError):
            provenance.validate_cooked_output(self._path('gone'), manifest, 'M', sources)


if __name__ == '__main__':
    unittest.main(verbosity=2)
