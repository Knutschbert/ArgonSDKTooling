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


class PakAsyncTests(TempCase):
    def test_nonblocking_then_poll_resolves(self):
        import time

        def slow_pak(mod, _d, dest, up, ue, max_pak_size=None, log=print):
            time.sleep(0.15)
            return os.path.join(dest, mod + '.pak')

        self._mock(pak_cooked=slow_pak)
        deploy.start_pak('A', 'out')
        # immediately after start it should be running (None)
        self.assertIsNone(deploy.pak_poll())
        deadline = time.time() + 5
        result = None
        while time.time() < deadline:
            result = deploy.pak_poll()
            if result is not None:
                break
            time.sleep(0.02)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'ok')
        self.assertEqual(result[1], os.path.join('out', 'A.pak'))

    def test_error_is_captured(self):
        import time

        def fail_pak(mod, _d, dest, up, ue, max_pak_size=None, log=print):
            raise RuntimeError('no manifest')

        self._mock(pak_cooked=fail_pak)
        deploy.start_pak('A', 'out')
        deadline = time.time() + 5
        result = None
        while time.time() < deadline:
            result = deploy.pak_poll()
            if result is not None:
                break
            time.sleep(0.02)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 'error')
        self.assertIsNone(result[1])
        self.assertIn('no manifest', result[2])

    def test_second_start_while_running_raises(self):
        import time

        def slow_pak(mod, _d, dest, up, ue, max_pak_size=None, log=print):
            time.sleep(0.3)
            return 'x.pak'

        self._mock(pak_cooked=slow_pak)
        deploy.start_pak('A', 'out')
        try:
            with self.assertRaises(RuntimeError):
                deploy.start_pak('B', 'out')
        finally:
            # let the worker finish so it doesn't leak into later tests
            deadline = time.time() + 5
            while time.time() < deadline and deploy.pak_poll() is None:
                time.sleep(0.02)


class OperationStateTests(TempCase):
    def test_poll_reflects_set(self):
        deploy._set('cooking', 'M', index=1, total=2)
        s = deploy.poll()
        self.assertEqual(s['state'], 'cooking')
        self.assertEqual(s['mod'], 'M')
        self.assertEqual(s['index'], 1)
        self.assertEqual(s['total'], 2)
        deploy._set('idle')
        self.assertEqual(deploy.poll()['state'], 'idle')

    def test_start_many_blocking_cook_reports_mods_and_done(self):
        cooked = []

        def fake_cook_mod(mod, up=None, ue=None, max_chunk=250):
            cooked.append(mod)
            return {'success': True, 'mod': mod, 'cooked_dir': 'cooked/' + mod,
                    'errors': [], 'chunks': 1}

        deploy._cook.cook_mod = fake_cook_mod
        deploy.start_many(['A', 'B'])  # cook-only; non-editor blocks
        s = deploy.poll()
        self.assertEqual(s['state'], 'done')
        self.assertTrue(s['success'])
        self.assertEqual(cooked, ['A', 'B'])
        self.assertEqual((s['index'], s['total']), (2, 2))

    def test_start_many_cook_and_pak(self):
        paks = []

        def fake_cook_mod(mod, up=None, ue=None, max_chunk=250):
            return {'success': True, 'mod': mod, 'cooked_dir': 'cooked/' + mod,
                    'errors': [], 'chunks': 1}

        def fake_pak_mod(result, dest, ue=None, max_pak_size=None):
            paks.append(result['mod'])
            return dest + '/' + result['mod'] + '.pak'

        deploy._cook.cook_mod = fake_cook_mod
        deploy._pak.pak_mod = fake_pak_mod
        deploy.start_many(['A', 'B'], dest_dir='out', do_cook=True, do_pak=True)
        s = deploy.poll()
        self.assertEqual(s['state'], 'done')
        self.assertEqual(paks, ['A', 'B'])
        self.assertTrue(s['success'])

    def test_start_many_pak_only(self):
        paks = []

        def fake_pak(mod, _d, dest, up, ue, max_pak_size=None, log=print):
            paks.append(mod)
            return dest + '/' + mod + '.pak'

        self._mock(pak_cooked=fake_pak)
        deploy.start_many(['A', 'B'], dest_dir='out', do_cook=False, do_pak=True)
        s = deploy.poll()
        self.assertEqual(s['state'], 'done')
        self.assertEqual(paks, ['A', 'B'])

    def test_start_many_empty(self):
        deploy.start_many([])
        self.assertEqual(deploy.poll()['state'], 'done')

    def test_tick_does_not_start_next_while_pak_running(self):
        started = []

        def fake_start(mod, up=None, ue=None):
            started.append(mod)
            return None

        saved = deploy._cook.start_cook
        deploy._cook.start_cook = fake_start
        deploy._PAK_RUNNING[:] = [True]
        deploy._QUEUE[:] = ['A']
        deploy._set('paking', 'A')
        try:
            deploy._tick()
        finally:
            deploy._PAK_RUNNING[:] = []
            deploy._QUEUE[:] = []
            deploy._cook.start_cook = saved
        self.assertEqual(started, [])  # must not start the next cook mid-pak
        self.assertEqual(deploy.poll()['state'], 'paking')  # still in the pak phase

    def test_tick_finishes_op_when_pak_result_ready(self):
        deploy._PAK_RUNNING[:] = []
        deploy._PAK_RESULT[:] = [('ok', 'out/A.pak', None)]
        deploy._QUEUE[:] = ['A']
        deploy._set('paking', 'A')
        try:
            deploy._tick()
        finally:
            deploy._PAK_RESULT[:] = []
            deploy._QUEUE[:] = []
        self.assertEqual(deploy._PAK_RESULT, [])  # result consumed
        self.assertEqual(deploy._QUEUE, [])       # mod finished and popped

if __name__ == '__main__':
    unittest.main(verbosity=2)
