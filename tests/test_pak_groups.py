# -*- coding: utf-8 -*-
"""Tests for the marker-plan pak builder (pak.build_pak_groups / plan_parts)."""
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

D = '../../../TBL/Content/'
R, RA, RB, EXT = '/Game/Mods/R/ModMarker', '/Game/Mods/R/A/ModMarker', '/Game/Mods/R/B/ModMarker', '/Game/Mods/Ext/ModMarker'
PLAN = {
    'dirs': {R: '/Game/Mods/R', RA: '/Game/Mods/R/A', RB: '/Game/Mods/R/B', EXT: '/Game/Mods/Ext'},
    'excluded': {R: ['Old/']},
    'paks': [{'stem': 'Coll', 'root': R, 'separate': False, 'markers': [R, RA, EXT]},
             {'stem': 'Beta', 'root': RB, 'separate': True, 'markers': [RB]}],
}


class GroupCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='deploy-groups-')
        self.addCleanup(shutil.rmtree, self._dir, True)
        self.content = os.path.join(self._dir, 'out', 'TBL', 'Content')
        for rel in ('Mods/R/Root.uasset', 'Mods/R/Root.uexp', 'Mods/R/A/AData.uasset', 'Mods/R/B/BData.uasset',
                    'Mods/R/Old/Skip.uasset', 'Mods/R/A/_Content/Replace/Thing.uasset', 'Mods/R/_Content/Top/Lone.uasset',
                    'Mods/R/Dup.uasset', 'Mods/R/_Content/Mods/R/Dup.uasset', 'Mods/Ext/ExtData.uasset', 'Mods/Ext/Data_bak.uasset'):
            path = os.path.join(self.content, *rel.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as handle:
                handle.write(rel)
        self.cooked = os.path.join(self.content, 'Mods', 'R')

    def groups(self, plan=PLAN, registry=None):
        return dict(pak.build_pak_groups(self.cooked, '/Game/Mods/R', plan, registry))

    def targets(self, entries):
        return sorted(t.replace(D, '') for _s, t, _z in entries)


class GroupTests(GroupCase):
    def test_merged_pak_has_nested_content_bundled_folder_and_content_replacements(self):
        got = self.targets(self.groups()['Coll'])
        self.assertEqual(got, ['Mods/Ext/Data_bak.uasset', 'Mods/Ext/ExtData.uasset', 'Mods/R/A/AData.uasset', 'Mods/R/Dup.uasset', 'Mods/R/Root.uasset',
                               'Mods/R/Root.uexp', 'Replace/Thing.uasset', 'Top/Lone.uasset'])

    def test_own_pak_marker_is_left_out_of_the_parent_and_gets_its_own_pak(self):
        groups = self.groups()
        self.assertEqual(self.targets(groups['Beta']), ['Mods/R/B/BData.uasset'])
        self.assertFalse([t for t in self.targets(groups['Coll']) if '/B/' in t])

    def test_excluded_paths_drop_stale_cooked_files_even_if_the_cook_left_them(self):
        self.assertNotIn('Mods/R/Old/Skip.uasset', self.targets(self.groups()['Coll']))
        plan = dict(PLAN, excluded={R: ['Old/'], EXT: ['*_bak']})
        got = self.targets(self.groups(plan)['Coll'])
        self.assertNotIn('Mods/Ext/Data_bak.uasset', got)
        self.assertIn('Mods/Ext/ExtData.uasset', got)
        self.assertIn('Mods/Ext/Data_bak.uasset', self.targets(self.groups(dict(PLAN, excluded={}))['Coll']))

    def test_content_replacement_wins_a_target_collision(self):
        entries = dict((t.replace(D, ''), s) for s, t, _z in self.groups()['Coll'])
        self.assertTrue(entries['Mods/R/Dup.uasset'].replace(os.sep, '/').endswith('_Content/Mods/R/Dup.uasset'))

    def test_a_content_folder_in_a_plain_subfolder_mounts_at_the_game_root(self):
        """Mods/ModLoading/_Content-style folders (no marker of their own) are content replacements too."""
        path = os.path.join(self.content, 'Mods', 'R', 'Plain', '_Content', 'UI', 'Deep', 'Widget.uasset')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as handle:
            handle.write('x')
        got = self.targets(self.groups()['Coll'])
        self.assertIn('UI/Deep/Widget.uasset', got)
        self.assertFalse([t for t in got if 'Plain/_Content' in t])

    def test_registry_goes_into_the_first_pak_only(self):
        reg = os.path.join(self._dir, 'reg.bin')
        with open(reg, 'w') as handle:
            handle.write('x')
        groups = self.groups(registry=reg)
        self.assertIn('Mods/R/ModAssetRegistry.bin', self.targets(groups['Coll']))
        self.assertNotIn('Mods/R/ModAssetRegistry.bin', self.targets(groups['Beta']))

    def test_pak_order_follows_the_plan(self):
        self.assertEqual([stem for stem, _e in pak.build_pak_groups(self.cooked, '/Game/Mods/R', PLAN)], ['Coll', 'Beta'])


class PlanPartsTests(GroupCase):
    def test_without_a_plan_it_is_the_single_legacy_pak(self):
        parts, stems, _all = pak.plan_parts({'mod': 'R', 'cooked_dir': self.cooked}, '/Game/Mods/R')
        self.assertEqual((len(parts), stems), (1, ['R']))

    def test_with_a_plan_every_planned_pak_is_produced(self):
        parts, stems, entries = pak.plan_parts({'mod': 'R', 'cooked_dir': self.cooked, 'pak_plan': PLAN}, '/Game/Mods/R')
        self.assertEqual(stems, ['Coll', 'Beta'])
        self.assertEqual(len(entries), sum(len(p) for p in parts))

    def test_size_splitting_applies_per_pak(self):
        for rel, size in (('Mods/R/Big1.uasset', 600), ('Mods/R/Big2.uasset', 600)):
            with open(os.path.join(self.content, *rel.split('/')), 'w') as handle:
                handle.write('x' * size)
        parts, stems, _all = pak.plan_parts({'mod': 'R', 'cooked_dir': self.cooked, 'pak_plan': PLAN}, '/Game/Mods/R', '1000b')
        self.assertTrue(any(s.startswith('Coll_part') for s in stems))
        self.assertIn('Beta', stems)  # small pak is not split

    def test_a_pak_with_no_files_fails_loudly(self):
        plan = dict(PLAN, paks=PLAN['paks'] + [{'stem': 'Empty', 'root': EXT, 'separate': True, 'markers': [EXT]}])
        plan = dict(plan, dirs=dict(PLAN['dirs'], **{EXT: '/Game/Mods/NoSuchFolder'}))
        with self.assertRaises(RuntimeError):
            pak.plan_parts({'mod': 'R', 'cooked_dir': self.cooked, 'pak_plan': plan}, '/Game/Mods/R')


if __name__ == '__main__':
    unittest.main()
