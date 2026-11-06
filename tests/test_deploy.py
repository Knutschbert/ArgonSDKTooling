# -*- coding: utf-8 -*-
"""Tests for deploy.cook_many / deploy.pak_many (multi-mod helpers)."""
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

import deploy  # noqa: E402


def _fake_cook_result(mod):
    return {'success': True, 'mod': mod, 'mod_dir': '/Game/Mods/AgMods/' + mod,
            'cooked_dir': os.path.join('x', mod), 'errors': [], 'chunks': 1}


class TempCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self._restore)
        self._saved = (deploy._cook.cook_mod, deploy._pak.pak_mod, deploy._pak_cooked)

    def _restore(self):
        deploy._cook.cook_mod, deploy._pak.pak_mod, deploy._pak_cooked = self._saved

    def _mock(self, cook=None, pak=None, pak_cooked=None):
        if cook is not None:
            deploy._cook.cook_mod = cook
        if pak is not None:
            deploy._pak.pak_mod = pak
        if pak_cooked is not None:
            deploy._pak_cooked = pak_cooked


class CookManyTests(TempCase):
    def test_iterates_and_returns_results(self):
        seen = []

        def cook(mod, up=None, ue=None, max_chunk=250):
            seen.append(mod)
            return _fake_cook_result(mod)

        self._mock(cook=cook)
        results = deploy.cook_many(['A', 'B', 'C'])
        self.assertEqual(seen, ['A', 'B', 'C'])
        self.assertEqual([r['mod'] for r in results], ['A', 'B', 'C'])

    def test_single_string_wrapped(self):
        seen = []

        def cook(mod, up=None, ue=None, max_chunk=250):
            seen.append(mod)
            return _fake_cook_result(mod)

        self._mock(cook=cook)
        deploy.cook_many('Only')
        self.assertEqual(seen, ['Only'])

    def test_paks_each_when_dest_given(self):
        paked = []

        def cook(mod, up=None, ue=None, max_chunk=250):
            return _fake_cook_result(mod)

        def pak(result, dest, ue=None, **kw):
            paked.append(result['mod'])

        self._mock(cook=cook, pak=pak)
        deploy.cook_many(['A', 'B'], dest_dir='out')
        self.assertEqual(paked, ['A', 'B'])


class PakManyTests(TempCase):
    def test_iterates_provenance_pak(self):
        paked = []

        def pak_cooked(mod, _dep, dest, up, ue, max_pak_size=None):
            paked.append(mod)
            return os.path.join(dest, mod + '.pak')

        self._mock(pak_cooked=pak_cooked)
        out = deploy.pak_many(['A', 'B'], 'dest')
        self.assertEqual(paked, ['A', 'B'])
        self.assertEqual([m for m, _p in out], ['A', 'B'])
        self.assertEqual(out[0][1], os.path.join('dest', 'A.pak'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
