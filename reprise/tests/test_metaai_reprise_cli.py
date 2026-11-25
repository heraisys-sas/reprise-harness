"""Tests du pont GUI metaai-integration (inspect/plan/apply/verify).

Aucun test ne touche /Applications ni ~/Applications : les constantes du module
sont redirigées vers des répertoires temporaires et les étapes de build sont
substituées. Compatible 3.9 et 3.13.
"""
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import metaai_reprise_build as build  # noqa: E402


class MetaaiCliTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.src = base / 'src' / 'Meta AI.app'
        self.dst = base / 'home' / 'Applications' / 'ReMetaAI.app'
        runtime = base / 'home' / '.reprise'
        (self.src / 'Contents/MacOS').mkdir(parents=True)
        (self.src / 'Contents/Info.plist').write_text(
            '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">'
            '<dict><key>CFBundleShortVersionString</key><string>14.0</string></dict></plist>')
        (self.src / 'Contents/MacOS/Meta AI').write_bytes(b'MACHO-ORIGINAL')
        patcher = mock.patch.multiple(
            build, SRC_APP=self.src, DST_APP=self.dst, RUNTIME=runtime,
            STATE_FILE=runtime / 'metaai-reprise-state.json',
            SPARKLE_META=runtime / 'Sparkle-real.meta.json',
            SPARKLE_REAL=runtime / 'Sparkle-real', BUILD_DIR=base / 'build')
        patcher.start()
        self.addCleanup(patcher.stop)
        # Par défaut : outils présents, rien ne tourne, socket absente.
        for name, value in (('missing_tools', lambda: []),
                            ('original_running', lambda: False),
                            ('copy_running', lambda: False),
                            ('socket_ping', lambda timeout=2.0: None),
                            ('find_identity', lambda explicit: 'IDEADBE')):
            patcher = mock.patch.object(build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_copy(self, patched=True):
        (self.dst / 'Contents/Frameworks/Sparkle.framework/Versions/B').mkdir(parents=True)
        (self.dst / 'Contents/MacOS').mkdir(parents=True)
        sparkle = self.dst / 'Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle'
        binary = self.dst / 'Contents/MacOS/Meta AI'
        plist = self.dst / 'Contents/Info.plist'
        if patched:
            sparkle.write_bytes(b'MACHO .reprise/Sparkle-real')
            binary.write_bytes(b'MACHO .reprise/sec-shim.dylib')
            plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">'
                '<dict><key>CFBundleShortVersionString</key><string>14.0</string>'
                '<key>CFBundleExecutable</key><string>Meta AI</string>'
                '<key>CFBundleIdentifier</key><string>' + build.DST_BUNDLE_ID + '</string>'
                '</dict></plist>')
        else:
            sparkle.write_bytes(b'MACHO-ORIGINAL')
            binary.write_bytes(b'MACHO-ORIGINAL')
            # Copie périmée (déploiement pré-identité) : ni exécutable ni identité.
            plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">'
                '<dict><key>CFBundleShortVersionString</key><string>13.0</string>'
                '</dict></plist>')

    def stub_signature(self, ok=True):
        patcher = mock.patch.object(build, 'patched_state', mock.Mock(return_value={
            'present': True, 'sparkle_patched': True, 'binary_patched': True,
            'plist_executable': True, 'plist_identity': True,
            'signature_ok': ok, 'version': '14.0'}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed_state(self, last_plan_id=None):
        build.save_state({'original_tree_digest': build.tree_digest(self.src),
                          'original_version': '14.0',
                          'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                          'last_plan_id': last_plan_id})

    # --- primitives ---

    def test_tree_digest_stable_et_sensible(self):
        first = build.tree_digest(self.src)
        self.assertEqual(first, build.tree_digest(self.src))
        (self.src / 'Contents/Resources').mkdir(exist_ok=True)
        (self.src / 'Contents/Resources/x.bin').write_bytes(b'payload')
        self.assertNotEqual(first, build.tree_digest(self.src))

    def test_read_version(self):
        self.assertEqual(build.read_version(self.src), '14.0')
        self.assertIsNone(build.read_version(self.dst))

    def test_socket_ping_absente_none(self):
        self.assertIsNone(build.socket_ping())

    # --- inspect ---

    def test_inspect_copie_absente(self):
        out = build.inspect_payload()
        self.assertTrue(out['ok'])
        self.assertEqual(out['status_label'], 'Copie absente')
        self.assertFalse(out['copy']['present'])
        self.assertEqual(out['original']['version'], '14.0')

    def test_inspect_originale_introuvable(self):
        self.src.rename(self.src.with_name('moved.app'))
        out = build.inspect_payload()
        self.assertFalse(out['ok'])
        self.assertEqual(out['status_label'], 'Originale introuvable')

    def test_inspect_copie_a_jour(self):
        self.make_copy()
        self.stub_signature()
        self.seed_state()
        out = build.inspect_payload()
        self.assertTrue(out['ok'])
        self.assertEqual(out['status_label'], 'Copie à jour')
        self.assertTrue(out['copy']['up_to_date'])

    def test_inspect_redéploiement_disponible(self):
        self.make_copy()
        self.stub_signature()
        build.save_state({'original_tree_digest': '0' * 64, 'original_version': '13.5',
                          'deployed_at': 'x', 'last_plan_id': None})
        out = build.inspect_payload()
        self.assertEqual(out['status_label'], 'Redéploiement disponible')
        self.assertFalse(out['copy']['up_to_date'])

    # --- plan ---

    def test_plan_noms_et_cible(self):
        out = build.plan_payload()
        plan = out['plan']
        self.assertEqual(plan['names'], ['metaai-reprise'])
        self.assertEqual(plan['target'], str(self.dst))
        self.assertEqual(plan['action'], 'redeploy')
        self.assertTrue(plan['feasible'])
        self.assertTrue(plan['steps'])

    def test_plan_bloque_si_originale_active(self):
        patcher = mock.patch.object(build, 'original_running', lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        out = build.plan_payload()
        self.assertFalse(out['plan']['feasible'])
        self.assertIn('quittez-la', out['message'])

    def test_plan_inclut_refresh_si_perime(self):
        self.make_copy()
        self.stub_signature()
        build.save_state({'original_tree_digest': '0' * 64, 'original_version': '13.5',
                          'deployed_at': 'x', 'last_plan_id': None})
        out = build.plan_payload()
        steps = [s['step'] for s in out['plan']['steps']]
        self.assertIn('refresh-copy', steps)

    # --- apply ---

    def plan(self):
        return build.plan_payload()['plan']

    def test_apply_refuse_originale_active(self):
        called = []
        for name in ('refresh_app', 'prepare_sparkle_real', 'compile_dylibs',
                     'swap_sparkle', 'patch_security_path', 'sign'):
            patcher = mock.patch.object(build, name, lambda *a, _n=name, **k: called.append(_n) or {})
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(build, 'original_running', lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        out = build.apply_payload(self.plan())
        self.assertFalse(out['ok'])
        self.assertEqual(out['status'], 'refused')
        self.assertEqual(called, [])
        self.assertFalse(build.STATE_FILE.exists())

    def test_apply_refuse_plan_perime(self):
        plan = self.plan()
        plan['original']['tree_digest'] = 'f' * 64
        out = build.apply_payload(plan)
        self.assertFalse(out['ok'])
        self.assertIn('périmé', out['message'])

    def test_apply_refuse_plan_etranger(self):
        out = build.apply_payload({'names': ['claude-reprise'], 'action': 'redeploy'})
        self.assertFalse(out['ok'])
        out = build.apply_payload({'names': ['metaai-reprise'], 'action': 'install', 'target': str(self.dst)})
        self.assertFalse(out['ok'])

    def test_refresh_app_rend_la_copie_inscriptible(self):
        # Meta AI distribue ses binaires en r-xr-xr-x ; sans réinscription,
        # codesign --deep échoue (Permission denied sur _CodeSignature/CodeResources).
        (self.src / 'Contents/_CodeSignature').mkdir()
        (self.src / 'Contents/_CodeSignature/CodeResources').write_bytes(b'<plist/>')
        (self.src / 'Contents/_CodeSignature/CodeResources').chmod(0o444)
        build.refresh_app()
        resources = self.dst / 'Contents/_CodeSignature/CodeResources'
        self.assertEqual(resources.stat().st_mode & 0o200, 0o200)

    def test_apply_heureux(self):
        called = []
        steps = {'refresh_app': lambda: {'step': 'refresh-copy'},
                 'prepare_sparkle_real': lambda: {'step': 'sparkle-real'},
                 'compile_dylibs': lambda: {'step': 'compile'},
                 'swap_sparkle': lambda: {'step': 'swap-sparkle'},
                 'patch_security_path': lambda: {'step': 'patch-security'},
                 'patch_info_plist': lambda: {'step': 'patch-info-plist'}}
        for name, fn in steps.items():
            patcher = mock.patch.object(build, name, lambda _f=fn, _n=name: called.append(_n) or _f())
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(build, 'sign', lambda identity: called.append('sign') or {'step': 'sign'})
        patcher.start()
        self.addCleanup(patcher.stop)
        plan = self.plan()
        out = build.apply_payload(plan)
        self.assertTrue(out['ok'])
        self.assertEqual(called, ['refresh_app', 'prepare_sparkle_real', 'compile_dylibs',
                                  'swap_sparkle', 'patch_security_path', 'patch_info_plist', 'sign'])
        state = build.load_state()
        self.assertEqual(state['last_plan_id'], plan['id'])
        self.assertEqual(state['original_version'], '14.0')
        self.assertEqual(state['original_tree_digest'], build.tree_digest(self.src))

    # --- verify ---

    def test_verify_conforme_apres_apply(self):
        self.make_copy()
        self.stub_signature()
        plan = self.plan()
        self.seed_state(last_plan_id=plan['id'])
        out = build.verify_payload(plan)
        self.assertTrue(out['ok'])
        self.assertTrue(all(out['checks'].values()))

    def test_verify_non_conforme(self):
        self.make_copy()
        self.stub_signature()
        plan = self.plan()
        self.seed_state(last_plan_id='autre-id')
        out = build.verify_payload(plan)
        self.assertFalse(out['ok'])
        self.assertFalse(out['checks']['plan_id_matches'])

    def test_verify_signature_cassee(self):
        self.make_copy()
        self.stub_signature(ok=False)
        plan = self.plan()
        self.seed_state(last_plan_id=plan['id'])
        out = build.verify_payload(plan)
        self.assertFalse(out['ok'])

    # --- CLI ---

    def cli(self, argv, stdin_text=None):
        buffer = io.StringIO()
        original_stdin = sys.stdin
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        try:
            with redirect_stdout(buffer):
                code = build.metaai_cli(None, argv)
        finally:
            sys.stdin = original_stdin
        return code, buffer.getvalue()

    def test_cli_inspect_json_et_exit0(self):
        self.make_copy()
        self.stub_signature()
        self.seed_state()
        code, out = self.cli(['metaai-integration-inspect'])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload['status_label'], 'Copie à jour')

    def test_cli_plan_json_et_exit0(self):
        code, out = self.cli(['metaai-integration-plan'])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload['plan']['names'], ['metaai-reprise'])

    def test_cli_apply_refuse_exit1(self):
        plan = self.plan()
        plan['target'] = '/ailleurs/Meta AI.app'
        code, out = self.cli(['metaai-integration-apply'], json.dumps({'plan': plan}))
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload['status'], 'refused')

    def test_cli_verify_stdin_exit0(self):
        self.make_copy()
        self.stub_signature()
        plan = self.plan()
        self.seed_state(last_plan_id=plan['id'])
        code, out = self.cli(['metaai-integration-verify'], json.dumps({'plan': plan}))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)['ok'])


if __name__ == '__main__':
    unittest.main()
