import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from native_contract import build_version, check_sdk


class NativeContractTests(unittest.TestCase):
    def test_build_sdk_does_not_raise_deployment_minimum(self):
        output = 'Load command 1\n cmd LC_BUILD_VERSION\n cmdsize 32\n minos 26.0\n sdk 27.0\nLoad command 2\n'
        self.assertEqual(build_version(output), {'minos': '26.0', 'sdk': '27.0'})
        with self.assertRaises(ValueError):
            build_version(output.replace('minos 26.0', 'minos 27.0'))

    def test_missing_or_unsupported_build_metadata_refused(self):
        for output in ('', 'cmd LC_BUILD_VERSION\n minos 26.0\n',
                       'cmd LC_BUILD_VERSION\n minos 26.0\n sdk 25.0\n'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                build_version(output)

    def test_sdk_prerequisite(self):
        for version in ('26.0', '26.1.1', '27.0'):
            check_sdk(version)
        for version in ('25.4', '', 'unknown'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                check_sdk(version)

    @unittest.skipUnless(os.environ.get('REPRISE_TEST_FM_BINARY'), 'Optional built FoundationExplain availability probe')
    def test_real_availability_probe_without_inference_or_stdin(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'FoundationExplain'
            shutil.copy2(os.environ['REPRISE_TEST_FM_BINARY'], binary)
            result = subprocess.run([str(binary), '--status'], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=20, check=True)
            response = json.loads(result.stdout)
            self.assertIs(type(response['available']), bool)
            self.assertEqual(response['operation'], 'availability')
            self.assertEqual(response['provider'], 'apple-foundation-models')
            self.assertIsNone(response['answer'])
            if not response['available']:
                self.assertIn(response['reason_code'], ('device_not_eligible', 'apple_intelligence_not_enabled',
                                                       'model_not_ready', 'model_unavailable', 'os_too_old'))
