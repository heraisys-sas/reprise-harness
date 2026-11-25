import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import orchestrator_memory
import coordinator_jobs
SPEC = importlib.util.spec_from_file_location('release_build_review', Path(__file__).parents[1] / 'release_build.py')
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


class ReleaseBuildTests(unittest.TestCase):
    def test_inputs_include_swift_sources_required_by_native_install(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            swift = root / 'swift'
            swift.mkdir()
            (swift / 'Native.swift').write_text('swift source')
            (swift / 'ignored.txt').write_text('not a compiler input')
            self.assertIn('swift/Native.swift', b.inputs(root))
            self.assertNotIn('swift/ignored.txt', b.inputs(root))

    def test_docs_inputs_resolve_from_repository_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            repository = Path(td)
            source = repository / 'reprise'
            source.mkdir()
            doc = repository / 'docs/contracts/example.md'
            doc.parent.mkdir(parents=True)
            doc.write_text('contract')
            self.assertEqual(b.input_path(source, 'docs/contracts/example.md'), doc)
            helper = source / 'release_build.py'
            helper.write_text('source')
            self.assertEqual(b.input_path(source, 'release_build.py'), helper)

    def test_inputs_only_include_root_sources_and_vendored_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'Native.swift').write_text('source')
            for name in ('vendor/yaml', 'state', 'tests', 'Reprise.app/Contents'):
                (root / name).mkdir(parents=True)
            for name in b.YAML_FILES:
                (root / 'vendor/yaml' / name).write_text(name)
            (root / 'vendor/yaml/.env').write_text('private')
            (root / 'vendor/yaml/token.pem').write_text('private')
            (root / 'vendor/yaml/__pycache__').mkdir()
            (root / 'vendor/yaml/__pycache__/cache.py').write_text('cache')
            (root / 'state/token.json').write_text('private')
            (root / 'tests/test_example.py').write_text('test')
            (root / 'Reprise.app/Contents/private.txt').write_text('private')
            (root / 'link.py').symlink_to(root / 'Native.swift')
            (root / 'vendor/yaml/link.py').symlink_to(root / 'Native.swift')
            inputs = b.inputs(root)
            self.assertEqual(set(inputs), {'Native.swift'} | {f'vendor/yaml/{name}' for name in b.YAML_FILES})
            (root / 'state/token.json').write_text('changed')
            (root / 'tests/test_example.py').write_text('changed')
            (root / 'Reprise.app/Contents/private.txt').write_text('changed')
            self.assertEqual(inputs, b.inputs(root))
            (root / 'vendor/yaml/__init__.py').write_text('changed')
            self.assertNotEqual(b.identity(inputs), b.identity(b.inputs(root)))

    def test_incomplete_or_symlinked_yaml_file_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            yaml = root / 'vendor/yaml'
            yaml.mkdir(parents=True)
            for name in b.YAML_FILES:
                (yaml / name).write_text(name)
            (yaml / 'LICENSE').unlink()
            with self.assertRaisesRegex(ValueError, 'LICENSE'):
                b.inputs(root)
            (yaml / 'LICENSE').symlink_to(yaml / '__init__.py')
            with self.assertRaisesRegex(ValueError, 'LICENSE'):
                b.inputs(root)

    def test_symlinked_vendor_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'external/yaml').mkdir(parents=True)
            (root / 'external/yaml/file.py').write_text('not vendored')
            (root / 'vendor').symlink_to(root / 'external', target_is_directory=True)
            self.assertEqual(b.inputs(root), {})

    def fixture(self, root):
        (root / 'Native.swift').write_text('source')
        (root / 'state').mkdir()
        (root / 'state/tasks.sqlite').write_text('private state')
        app = root / 'builds/current/Reprise.app'
        (app / 'Contents/Resources').mkdir(parents=True)
        (app / 'Contents/runtime').write_text('payload')
        evidence = {'format': 1, 'inputs': b.inputs(root), 'bundle': b.bundle_files(app)}
        (app / 'Contents/Resources/build-evidence.json').write_text(json.dumps(evidence))
        return app

    def test_sources_change_refuses_stale_build_but_state_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);app = self.fixture(root)
            b.check(app, root)
            (root / 'state/tasks.sqlite').write_text('new state')
            b.check(app, root)
            (root / 'Native.swift').write_text('changed source')
            with self.assertRaisesRegex(ValueError, 'périmé'):
                b.check(app, root)

    def test_tampered_bundle_and_escaping_symlink_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);app = self.fixture(root)
            (app / 'Contents/runtime').write_text('altered')
            with self.assertRaisesRegex(ValueError, 'modifié'):
                b.check(app, root)
            (app / 'escape').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'Lien sortant'):
                b.check(app, root)

    def test_open_uses_no_development_args_and_no_state_override(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);app = self.fixture(root)
            with patch.dict(os.environ, {'AGENT_RESUME_STATE': '/wrong/clone/state'}), patch.object(b.subprocess, 'run') as run:
                b.launch(app, root)
            argv = run.call_args.args[0]
            self.assertEqual(argv, ['/usr/bin/open', '-n', '-a', str(app.resolve())])
            self.assertNotIn('AGENT_RESUME_STATE', run.call_args.kwargs['env'])

    def test_invalid_build_never_launches(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);app=self.fixture(root)
            (root/'Native.swift').write_text('changed')
            with patch.object(b.subprocess,'run') as run, self.assertRaises(ValueError):
                b.launch(app,root)
            run.assert_not_called()


class RelaunchScriptTests(unittest.TestCase):
    def test_default_relaunch_does_not_touch_daemons_or_development_mode(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            python = folder / 'python3'
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$CALLS"\n')
            python.chmod(0o700)
            log = folder / 'calls'
            env = dict(os.environ, PATH=str(folder) + ':/usr/bin:/bin', CALLS=str(log))
            result = subprocess.run(['/bin/sh', str(Path(__file__).parents[1] / 'restart-chain'), '--no-build'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('release_build.py open', log.read_text())
            self.assertNotIn('daemon', log.read_text())
            self.assertNotIn('--args', log.read_text())


class DiffInventoriesTests(unittest.TestCase):
    def test_stable(self):
        before = {'a': {'sha256': 'x'}, 'b/c': {'sha256': 'y'}}
        self.assertEqual(b._diff_inventories(before, dict(before)), [])

    def test_added_pyc(self):
        before = {'a': {'sha256': 'x'}}
        after = {'a': {'sha256': 'x'}, 'm/__pycache__/m.pyc': {'sha256': 'z'}}
        self.assertEqual(b._diff_inventories(before, after), ['m/__pycache__/m.pyc'])

    def test_modified(self):
        self.assertEqual(b._diff_inventories({'a': {'sha256': 'x'}}, {'a': {'sha256': 'changed'}}), ['a'])

    def test_removed(self):
        self.assertEqual(b._diff_inventories({'a': {'sha256': 'x'}, 'c': {'sha256': 'y'}}, {'a': {'sha256': 'x'}}), ['c'])


class MemoryProbeFlagsTests(unittest.TestCase):
    def test_orchestrator_memory_probe_is_isolated_no_bytecode(self):
        source = Path(orchestrator_memory.__file__).read_text()
        self.assertIn("[sys.executable, '-I', '-B', '-c', _REGISTRY_PROBE]", source)

    def test_coordinator_jobs_probe_is_isolated_no_bytecode(self):
        source = Path(coordinator_jobs.__file__).read_text()
        self.assertIn("[sys.executable, '-I', '-B', '-c', orchestrator_memory._REGISTRY_PROBE]", source)


class VerifyNoDriftTests(unittest.TestCase):
    def drift_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        app = Path(temporary.name).resolve() / 'Reprise.app'
        (app / 'Contents/Resources/runtime/bin').mkdir(parents=True)
        python = app / 'Contents/Resources/runtime/bin/python3'
        python.write_text('#!/bin/sh\necho \'{"status": "ok"}\'\n')
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        return app

    def test_accepts_stable_bundle(self):
        # metadata() exige un runtime réel épinglé : fixture avec metadata simulée,
        # le test couvre la comparaison d'inventaire avant/après probe.
        with patch.object(b.package_runtime, 'metadata', lambda app: {'fixture': True}):
            b.verify_no_drift(self.drift_fixture())

    def test_refuses_file_created_during_probe(self):
        app = self.drift_fixture()
        python = app / 'Contents/Resources/runtime/bin/python3'
        python.write_text('#!/bin/sh\necho x > "$0.pyc"\necho \'{"status": "ok"}\'\n')
        with patch.object(b.package_runtime, 'metadata', lambda app: {'fixture': True}):
            with self.assertRaisesRegex(ValueError, 'Dérive du bundle'):
                b.verify_no_drift(app)

    def test_probe_uses_bare_interpreter_without_protective_env(self):
        source = Path(b.__file__).read_text()
        self.assertIn("'-I', '-B', '-c', _DRIFT_PROBE", source)
        self.assertIn('assert sys.dont_write_bytecode', b._DRIFT_PROBE)
        probe_source = source.split('def _drift_probe')[1].split('def verify_no_drift')[0]
        self.assertNotIn('PYTHONDONTWRITEBYTECODE', probe_source)
