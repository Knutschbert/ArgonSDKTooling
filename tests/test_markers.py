# -*- coding: utf-8 -*-
"""Tests for deploy.markers (ExcludedPaths matching, exclusion split, plan helpers)."""
from __future__ import print_function

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTENT_PYTHON = os.path.abspath(os.path.join(_HERE, '..', 'Content', 'Python'))
if _CONTENT_PYTHON not in sys.path:
    sys.path.insert(0, _CONTENT_PYTHON)

from deploy import engine, markers  # noqa: E402

VECTORS = [
    ('Old/Thing', 'Old/', True), ('Older/Thing', 'Old', True), ('Old', 'Old/', False), ('Newer/Old', 'Old', False),
    ('Maps/Town_bak', '*_bak', True), ('Maps/Town_bak/Deep/X', '*_bak', True), ('Maps/Town', '*_bak', False),
    ('X/Backup/A', '*/Backup', True), ('Backup/A', '*/Backup', False),
    ('Maps/Town_old', 'Maps/*_old', True), ('maps/town_OLD', 'MAPS/*_old', True), ('Other/Town_old', 'Maps/*_old', False),
    ('a1', 'a?', True), ('a12', 'a?', False), ('Maps/Town.v2', '*.v2', True), ('Maps/TownXv2', '*.v2', False),
    ('Foo', '', False), ('Foo', '   ', False), ('Foo/Bar', '/Foo/', True), ('Foo/Bar', 'Foo\\', True),
]


class PatternTests(unittest.TestCase):
    def test_shared_vectors(self):
        for rel, pattern, expected in VECTORS:
            self.assertEqual(markers.pattern_matches(rel, pattern), expected, '{0!r} vs {1!r}'.format(rel, pattern))

    def test_any_of_several_patterns(self):
        self.assertTrue(markers.excluded('Maps/Town_bak', ['Nope/', '*_bak']))
        self.assertFalse(markers.excluded('Maps/Town', ['Nope/', '*_bak']))
        self.assertFalse(markers.excluded('Maps/Town', []))
        self.assertFalse(markers.excluded('Maps/Town', None))


class ApplyExcludesTests(unittest.TestCase):
    PKGS = ['/Game/M/Mod/Keep', '/Game/M/Mod/Old/A', '/Game/M/Mod/Maps/Town_bak', '/Game/M/Other/Old/A']

    def test_patterns_apply_only_inside_their_marker_folder(self):
        kept, skipped = markers.apply_excludes(self.PKGS, {'/Game/M/Mod': ['Old/', '*_bak']})
        self.assertEqual(kept, ['/Game/M/Mod/Keep', '/Game/M/Other/Old/A'])
        self.assertEqual(skipped, ['/Game/M/Mod/Old/A', '/Game/M/Mod/Maps/Town_bak'])

    def test_nested_marker_folders_each_use_their_own_patterns(self):
        kept, skipped = markers.apply_excludes(
            ['/Game/R/A/X', '/Game/R/A/Sub/X', '/Game/R/B/X'],
            {'/Game/R': ['B/'], '/Game/R/A': ['Sub/']})
        self.assertEqual(kept, ['/Game/R/A/X'])
        self.assertEqual(skipped, ['/Game/R/A/Sub/X', '/Game/R/B/X'])

    def test_folder_name_prefix_is_not_a_parent(self):
        kept, _skipped = markers.apply_excludes(['/Game/M/Mod2/Old/A'], {'/Game/M/Mod': ['Old/']})
        self.assertEqual(kept, ['/Game/M/Mod2/Old/A'])

    def test_no_patterns_keeps_everything(self):
        kept, skipped = markers.apply_excludes(self.PKGS, {'/Game/M/Mod': []})
        self.assertEqual((kept, skipped), (self.PKGS, []))


class PlanHelperTests(unittest.TestCase):
    PLAN = {'dirs': {'/Game/R/ModMarker': '/Game/R', '/Game/R/A/ModMarker': '/Game/R/A', '/Game/X/Ext/ModMarker': '/Game/X/Ext'},
            'excluded': {'/Game/R/ModMarker': ['B/'], '/Game/R/A/ModMarker': [], '/Game/X/Ext/ModMarker': ['*_bak']}}

    def test_folder_excludes_skips_empty_lists(self):
        self.assertEqual(markers.folder_excludes(self.PLAN), {'/Game/R': ['B/'], '/Game/X/Ext': ['*_bak']})

    def test_bundled_folders_are_the_ones_outside_the_mod_dir(self):
        self.assertEqual(markers.bundled_folders(self.PLAN, '/Game/R'), ['/Game/X/Ext'])
        self.assertEqual(markers.bundled_folders(self.PLAN, '/Game/R/'), ['/Game/X/Ext'])


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self._saved = (engine.in_editor, sys.modules.get('modmarker'), os.environ.get(markers.IGNORE_ENV))
        self.addCleanup(self._restore)
        engine.in_editor = lambda: True
        os.environ.pop(markers.IGNORE_ENV, None)

    def _restore(self):
        engine.in_editor = self._saved[0]
        if self._saved[1] is None:
            sys.modules.pop('modmarker', None)
        else:
            sys.modules['modmarker'] = self._saved[1]
        if self._saved[2] is None:
            os.environ.pop(markers.IGNORE_ENV, None)
        else:
            os.environ[markers.IGNORE_ENV] = self._saved[2]

    def _fake(self, **attrs):
        import types
        module = types.ModuleType('modmarker')
        module.__dict__.update(attrs)
        sys.modules['modmarker'] = module
        return module

    def test_stamps_then_returns_the_plan(self):
        calls = []
        self._fake(find_markers=lambda f: ['/Game/X/ModMarker'], stamp_versions=lambda f: calls.append(('stamp', f)),
                   plan=lambda f: calls.append(('plan', f)) or {'paks': []})
        self.assertEqual(markers.preflight('/Game/X'), {'paks': []})
        self.assertEqual(calls, [('stamp', '/Game/X'), ('plan', '/Game/X')])

    def test_no_markers_no_plan_and_nothing_stamped(self):
        calls = []
        self._fake(find_markers=lambda f: [], stamp_versions=lambda f: calls.append(f), plan=lambda f: {'paks': []})
        self.assertIsNone(markers.preflight('/Game/X'))
        self.assertEqual(calls, [])

    def test_outside_the_editor_missing_module_and_env_switch(self):
        self._fake(find_markers=lambda f: ['m'], stamp_versions=lambda f: None, plan=lambda f: {'paks': []})
        engine.in_editor = lambda: False
        self.assertIsNone(markers.preflight('/Game/X'))
        engine.in_editor = lambda: True
        os.environ[markers.IGNORE_ENV] = '1'
        self.assertIsNone(markers.preflight('/Game/X'))
        os.environ.pop(markers.IGNORE_ENV)
        sys.modules['modmarker'] = None  # import raises ImportError
        self.assertIsNone(markers.preflight('/Game/X'))

    def test_unreadable_markers_fail_loudly(self):
        def boom(f):
            raise ValueError('bad')
        self._fake(find_markers=lambda f: ['m'], stamp_versions=boom, plan=lambda f: {})
        with self.assertRaises(RuntimeError):
            markers.preflight('/Game/X')


if __name__ == '__main__':
    unittest.main()
