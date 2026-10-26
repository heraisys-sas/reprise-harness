import copy
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import shutil
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import proxy_service as service
import harness_integration as integration
import package_helpers


class Launchd:
    def __init__(self):
        self.current = None
        self.commands = []
        self.fail_bootstrap = False

    def __call__(self, args):
        self.commands.append(args)
        if args[0] == 'print':
            if self.current is None:
                return subprocess.CompletedProcess(args, 113, '', 'not found')
            out = 'state = running\npid = 123\narguments = {\n' + '\n'.join(self.current['ProgramArguments']) + '\n}\nenvironment = {\n'
            out += '\n'.join(k + ' => ' + v for k, v in self.current['EnvironmentVariables'].items()) + '\n}\n'
            return subprocess.CompletedProcess(args, 0, out, '')
        if args[0] == 'bootout':
            self.current = None
        if args[0] == 'bootstrap':
            if self.fail_bootstrap:
                return subprocess.CompletedProcess(args, 1, '', 'failure')
            self.current = plistlib.loads(Path(args[2]).read_bytes())
        return subprocess.CompletedProcess(args, 0, '', '')


class ProxyServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='reprise-proxy-service-test-')
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name).resolve()
        self.root = package_helpers.package(SOURCE, self.home / 'Reprise.app')
        runtime = self.root.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        (runtime / 'bin/python3').write_text('#!/bin/sh\nexit 99\n')
        (runtime / 'bin/python3').chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': integration.tree_files(runtime)}))
        payload = json.loads((self.root / 'payload-manifest.json').read_text())
        payload['runtime'] = {'fixture': True}
        (self.root / 'payload-manifest.json').write_text(json.dumps(payload))
        self.launch = Launchd()
        self.idle = lambda _: None
        self.port = lambda: None
        self.ready = lambda runner, desired: None

    def plan(self, **options):
        return service.plan(self.root, self.home, runner=self.launch, **options)

    def apply(self, value):
        return service.apply(value, runner=self.launch, idle_check=self.idle, port_check=self.port, ready_check=self.ready)

    def test_install_owns_runtime_and_start_stop_without_original_dependencies(self):
        value = self.plan(activate=True)
        result = self.apply(value)
        self.assertTrue(result['managed'] and result['loaded_matches'] and result['integrity'])
        release = Path(value['release'])
        self.assertTrue(release.is_relative_to(self.home / 'Library/Application Support/Reprise'))
        shutil.rmtree(self.home / 'Reprise.app')
        self.assertTrue(service.integrity(release))
        self.assertEqual(result['arguments'][1:4], ['-E', '-s', '-B'])
        self.assertTrue(result['arguments'][-1].endswith('asi1_planner_proxy.py'))
        self.apply(self.plan(action='stop'))
        self.assertIsNone(self.launch.current)
        self.apply(self.plan(action='start'))
        self.assertIsNotNone(self.launch.current)

    def test_install_without_activation_disables_login_start(self):
        result = self.apply(self.plan())
        self.assertFalse(result['loaded'])
        self.assertTrue(any(x[0] == 'disable' for x in self.launch.commands))

    def test_start_loaded_does_not_interrupt_service(self):
        self.apply(self.plan(activate=True))
        self.launch.commands.clear()
        self.apply(self.plan(action='start'))
        self.assertTrue(all(x[0] == 'print' for x in self.launch.commands))

    def test_install_same_release_does_not_interrupt_service(self):
        self.apply(self.plan(activate=True))
        self.launch.commands.clear()
        self.apply(self.plan(activate=True))
        self.assertTrue(all(x[0] == 'print' for x in self.launch.commands))

    def test_unknown_launchd_state_refuses_plan(self):
        with self.assertRaisesRegex(ValueError, 'launchd inconnu'):
            service.plan(self.root, self.home, runner=lambda args: subprocess.CompletedProcess(args, 1, '', 'denied'))

    def test_runtime_drift_after_plan_refused(self):
        value = self.plan()
        (self.root.parent / 'runtime/bin/python3').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Runtime embarqué'):
            self.apply(value)

    def test_loaded_plist_drift_refused(self):
        self.apply(self.plan(activate=True))
        self.launch.current['EnvironmentVariables']['ASI1_PLANNER_UPSTREAM'] = 'http://wrong'
        with self.assertRaisesRegex(ValueError, 'chargés différents'):
            self.plan(action='stop')

    def test_unmanaged_proxy_requires_adoption_and_exact_assets(self):
        _, plist, _ = service.paths(self.home)
        plist.parent.mkdir(parents=True)
        node = self.home / 'node'
        node.write_text('fixture node')
        legacy_script = self.home / 'asi1-planner-proxy.js'
        legacy_script.write_bytes((SOURCE.parent / 'proxy/asi1-planner-proxy.js').read_bytes())
        legacy = {'Label': service.LABEL,
                  'ProgramArguments': [str(node), str(legacy_script)],
                  'EnvironmentVariables': {'HOME': str(self.home)}}
        plist.write_bytes(plistlib.dumps(legacy))
        self.launch.current = copy.deepcopy(legacy)
        with self.assertRaisesRegex(ValueError, 'adoption'):
            self.plan()
        result = self.apply(self.plan(adopt=True, activate=True))
        self.assertTrue(result['managed'])
        self.assertNotEqual(result['arguments'], legacy['ProgramArguments'])
        self.assertEqual(sum(x[0] == 'bootout' for x in self.launch.commands), 1)

    def test_active_connections_refuse_before_modifications(self):
        self.apply(self.plan(activate=True))
        before = service.paths(self.home)[0].joinpath('managed.json').read_bytes()
        self.idle = lambda _: (_ for _ in ()).throw(ValueError('active'))
        with self.assertRaisesRegex(ValueError, 'active'):
            self.apply(self.plan(action='stop'))
        self.assertIsNotNone(self.launch.current)
        self.assertEqual(before, service.paths(self.home)[0].joinpath('managed.json').read_bytes())

    def test_port_taken_by_other_launcher_refuses_activation(self):
        self.port = lambda: (_ for _ in ()).throw(ValueError('port occupé'))
        with self.assertRaisesRegex(ValueError, 'occupé'):
            self.apply(self.plan(activate=True))
        self.assertFalse(any(x[0] == 'bootstrap' for x in self.launch.commands))
        self.assertFalse((service.paths(self.home)[0] / 'managed.json').exists())

    def test_bootstrap_failure_keeps_pending_and_refuses_replay(self):
        value = self.plan(activate=True)
        self.launch.fail_bootstrap = True
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.apply(value)
        with self.assertRaisesRegex(ValueError, 'réconciliation'):
            self.apply(value)
        self.assertTrue(service.inspect(self.home, self.launch)['pending'])

    def test_crashing_job_keeps_pending(self):
        self.ready = lambda *_: (_ for _ in ()).throw(ValueError('listener non confirmé'))
        with self.assertRaisesRegex(ValueError, 'listener'):
            self.apply(self.plan(activate=True))
        self.assertTrue(service.inspect(self.home, self.launch)['pending'])

    def test_stopped_plist_changed_during_staging_preserved(self):
        value = self.plan()
        original_stage = service.stage_release
        _, plist, _ = service.paths(self.home)
        def stage(*args):
            original_stage(*args)
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text('operator change')
        with patch.object(service, 'stage_release', side_effect=stage):
            with self.assertRaisesRegex(ValueError, 'aucun écrasement'):
                self.apply(value)
        self.assertEqual(plist.read_text(), 'operator change')

    def test_unknown_state_after_stop_keeps_pending(self):
        self.apply(self.plan(activate=True))
        value = self.plan(action='stop')
        normal_runner = self.launch
        def runner(args):
            if args[0] == 'print' and self.launch.current is None:
                return subprocess.CompletedProcess(args, 1, '', 'denied')
            return normal_runner(args)
        with self.assertRaisesRegex(ValueError, 'Arrêt non confirmé'):
            service.apply(value, runner=runner, idle_check=self.idle, port_check=self.port, ready_check=self.ready)
        self.assertTrue(service.inspect(self.home, self.launch)['pending'])

    def test_plan_tampering_refused(self):
        value = self.plan()
        value['release'] = '/somewhere'
        with self.assertRaisesRegex(ValueError, 'altéré'):
            self.apply(value)

    def test_bundle_without_python_proxy_refused(self):
        (self.root / 'asi1_planner_proxy.py').unlink()
        with self.assertRaisesRegex(ValueError, 'Payload helpers modifié'):
            self.plan()

    def test_managed_release_tamper_refused(self):
        value = self.plan()
        self.apply(value)
        (Path(value['release']) / 'Contents/Resources/runtime/bin/python3').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'intègre'):
            self.plan(action='start')

    def test_no_secrets_in_service_environment(self):
        value = self.plan(activate=True)
        self.apply(value)
        self.assertEqual(set(self.launch.current['EnvironmentVariables']),
                         {'HOME', 'ASI1_PLANNER_UPSTREAM', 'ASI1_PLANNER_LOG_PATH'})

    def test_idle_refuses_established_or_unknown_native_connections(self):
        observed = self.launch(['print', 'unused'])
        observed.returncode = 0
        observed.stdout = 'pid = 123\n'
        with patch.object(service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'TST=LISTEN\nTST=ESTABLISHED\n', '')):
            with self.assertRaises(ValueError):
                service.idle(observed)
        observed.stdout = ''
        with self.assertRaises(ValueError):
            service.idle(observed)


if __name__ == '__main__':
    unittest.main()
