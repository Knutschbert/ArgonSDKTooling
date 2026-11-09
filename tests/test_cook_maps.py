# -*- coding: utf-8 -*-
"""Tests for the map marker's role in the cook (deploy.maps + cook.select/plan/clean logic)."""
from __future__ import print_function

import os
import shutil
import sys
import tempfile
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTENT_PYTHON = os.path.abspath(os.path.join(_HERE, '..', 'Content', 'Python'))
if _CONTENT_PYTHON not in sys.path:
    sys.path.insert(0, _CONTENT_PYTHON)

from deploy import cook, engine, maps, provenance  # noqa: E402

M = '/Game/Mods/AgMods/Mod/'


class TempCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-cookmaps-')
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _path(self, *parts):
        return os.path.join(self._dir, *parts)

    def _touch(self, rel):
        path = self._path(*rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w').close()


class SelectPackagesTests(unittest.TestCase):
    ENTRIES = [(M + 'Marker', False), (M + 'Main', True), (M + 'Main_BuiltData', False),
               (M + 'Old', True), (M + 'Old_BuiltData', False), (M + 'Sub/Sky', True),
               (M + 'Actor', False)]

    def test_no_plan_keeps_everything(self):
        packages, skipped = cook.select_packages(self.ENTRIES, None)
        self.assertEqual(len(packages), 7)
        self.assertEqual(skipped, [])

    def test_only_required_maps_survive_and_builtdata_follows_its_map(self):
        packages, skipped = cook.select_packages(self.ENTRIES, [M + 'Main', M + 'Sub/Sky'])
        self.assertEqual(packages, [M + 'Actor', M + 'Main', M + 'Main_BuiltData', M + 'Marker', M + 'Sub/Sky'])
        self.assertEqual(skipped, [M + 'Old', M + 'Old_BuiltData'])

    def test_non_map_assets_are_never_skipped(self):
        packages, _skipped = cook.select_packages(self.ENTRIES, [])
        self.assertEqual(packages, [M + 'Actor', M + 'Marker'])

    def test_builtdata_name_alone_is_not_enough_to_skip(self):
        # a *_BuiltData asset whose map is not in this folder is just an asset
        packages, skipped = cook.select_packages([(M + 'Lone_BuiltData', False)], [])
        self.assertEqual((packages, skipped), ([M + 'Lone_BuiltData'], []))


class PlanPackagesTests(TempCase):
    def _project(self):
        for rel in ('Mod/Marker.uasset', 'Mod/Main.umap', 'Mod/Main_BuiltData.uasset', 'Mod/Old.umap',
                    'Mod/Old_BuiltData.uasset', 'Other/Layer.umap'):
            self._touch('Content/Mods/AgMods/' + rel)
        return self._path('TBL.uproject')

    def test_without_plan_lists_the_folder(self):
        sel = cook.plan_packages(self._project(), '/Game/Mods/AgMods/Mod', None)
        self.assertEqual(len(sel['packages']), 5)
        self.assertEqual((sel['skipped'], sel['external'], sel['missing']), ([], [], []))

    def test_plan_skips_unlisted_maps_and_only_reports_external_layers(self):
        plan = {'maps': [M + 'Main', '/Game/Mods/AgMods/Other/Layer', '/Game/Mods/AgMods/Mod/Gone',
                         '/Game/Mods/AgMods/Elsewhere/Nope', '/Engine/Maps/Foo']}
        sel = cook.plan_packages(self._project(), '/Game/Mods/AgMods/Mod', plan)
        self.assertEqual(sel['skipped'], [M + 'Old', M + 'Old_BuiltData'])
        self.assertEqual(sel['external'], ['/Game/Mods/AgMods/Other/Layer'])
        self.assertEqual(sorted(sel['missing']), ['/Game/Mods/AgMods/Elsewhere/Nope', '/Game/Mods/AgMods/Mod/Gone'])
        self.assertNotIn('/Game/Mods/AgMods/Other/Layer', sel['packages'])  # reported, not cooked explicitly
        self.assertIn(M + 'Marker', sel['packages'])
        self.assertNotIn(M + 'Old', sel['packages'])
        self.assertNotIn('/Engine/Maps/Foo', sel['packages'])


class CleanCookTests(unittest.TestCase):
    def test_nothing_to_clean_without_output(self):
        self.assertFalse(cook.needs_clean_cook(['/a'], [], True, has_output=False))

    def test_shrinking_cook_list_rebuilds(self):
        self.assertTrue(cook.needs_clean_cook(['/a', '/b'], ['/a'], True, True))
        self.assertTrue(cook.needs_clean_cook(['/a', '/b'], ['/a'], False, True))  # deleted asset too

    def test_growing_or_equal_list_stays_incremental(self):
        self.assertFalse(cook.needs_clean_cook(['/a'], ['/a', '/b'], True, True))
        self.assertFalse(cook.needs_clean_cook(['/a'], ['/a'], True, True))

    def test_unknown_previous_cook_rebuilds_only_with_a_plan(self):
        self.assertTrue(cook.needs_clean_cook(None, ['/a'], True, True))
        self.assertFalse(cook.needs_clean_cook(None, ['/a'], False, True))  # old behavior kept


class CommandTests(TempCase):
    def _cmd(self, **kw):
        root = self._path('engine')
        os.makedirs(os.path.join(root, 'Engine', 'Binaries', 'Win64'), exist_ok=True)
        return cook.cook_command(root, self._path('TBL.uproject'), ['/Game/A'], self._path('out'),
                                 self._path('x.log'), **kw)

    def test_iterate_by_default(self):
        self.assertIn('-iterate', self._cmd())

    def test_clean_cook_drops_iterate_only(self):
        with_it, without = self._cmd(), self._cmd(iterate=False)
        self.assertNotIn('-iterate', without)
        self.assertEqual([a for a in with_it if a != '-iterate'], without)

    def test_only_the_first_chunk_may_rebuild_from_scratch(self):
        packages = ['/Game/Mods/AgMods/M/P{0}'.format(i) for i in range(5)]
        job = cook.CookJob('M', self._path('u.uproject'), self._path('e'), packages, self._path('out'),
                           self._path('l.log'), [], max_chunk=2, _start=False, iterate=False)
        self.assertEqual([job.chunk_iterates(i) for i in range(3)], [False, True, True])
        normal = cook.CookJob('M', self._path('u.uproject'), self._path('e'), packages, self._path('out'),
                              self._path('l.log'), [], max_chunk=2, _start=False)
        self.assertTrue(all(normal.chunk_iterates(i) for i in range(3)))


class ManifestTests(TempCase):
    def test_extra_fields_are_recorded_without_overriding_core_ones(self):
        path = self._path('m', 'CookModManifest.json')
        provenance.write_manifest(path, 'Mod', [], 'cooked', extra={'cooked_packages': ['/a'], 'mod': 'evil'})
        data = provenance.read_manifest(path)
        self.assertEqual(data['cooked_packages'], ['/a'])
        self.assertEqual(data['mod'], 'Mod')
        self.assertTrue(data['complete'])


class MarkerPlanTests(unittest.TestCase):
    def setUp(self):
        self._saved = (engine.in_editor, sys.modules.get('mapmarker'), os.environ.get(maps.IGNORE_ENV))
        self.addCleanup(self._restore)
        engine.in_editor = lambda: True
        os.environ.pop(maps.IGNORE_ENV, None)

    def _restore(self):
        engine.in_editor = self._saved[0]
        if self._saved[1] is None:
            sys.modules.pop('mapmarker', None)
        else:
            sys.modules['mapmarker'] = self._saved[1]
        if self._saved[2] is None:
            os.environ.pop(maps.IGNORE_ENV, None)
        else:
            os.environ[maps.IGNORE_ENV] = self._saved[2]

    def _fake(self, **attrs):
        module = types.ModuleType('mapmarker')
        module.__dict__.update(attrs)
        sys.modules['mapmarker'] = module

    def test_plan_comes_from_the_sdk_module(self):
        self._fake(cook_plan=lambda folder: {'maps': [folder + '/A'], 'markers': ['m']})
        self.assertEqual(maps.marker_plan('/Game/X')['maps'], ['/Game/X/A'])

    def test_no_marker_means_no_plan(self):
        self._fake(cook_plan=lambda folder: None)
        self.assertIsNone(maps.marker_plan('/Game/X'))

    def test_outside_the_editor_there_is_no_plan(self):
        self._fake(cook_plan=lambda folder: {'maps': []})
        engine.in_editor = lambda: False
        self.assertIsNone(maps.marker_plan('/Game/X'))

    def test_sdk_without_the_module_cooks_as_before(self):
        sys.modules['mapmarker'] = None  # makes `import mapmarker` raise ImportError
        self.assertIsNone(maps.marker_plan('/Game/X'))

    def test_env_switch_ignores_the_marker(self):
        self._fake(cook_plan=lambda folder: {'maps': []})
        os.environ[maps.IGNORE_ENV] = '1'
        self.assertIsNone(maps.marker_plan('/Game/X'))

    def test_unreadable_marker_fails_loudly_instead_of_cooking_everything(self):
        def boom(folder):
            raise ValueError('bad property')
        self._fake(cook_plan=boom)
        with self.assertRaises(RuntimeError) as ctx:
            maps.marker_plan('/Game/X')
        self.assertIn('bad property', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
