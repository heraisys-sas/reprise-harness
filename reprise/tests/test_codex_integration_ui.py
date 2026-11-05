"""Codex integration UI state machine against a simulated setup channel."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.uname().sysname == 'Darwin', 'requires macOS Swift')
class CodexIntegrationUISmokeTests(unittest.TestCase):
    def test_workspace_write_inspection_does_not_regrant_previous_opt_in(self):
        source = (ROOT / 'swift' / 'Integrations.swift').read_text()
        inspect = source.split('    func inspectIntegration() {', 1)[1].split('    func previewIntegration', 1)[0]
        self.assertIn('integrationCodexWorkspaceWriteInstalled = result["codex_workspace_write"] as? Bool ?? false', inspect)
        self.assertIn('integrationCodexWorkspaceWrite = false', inspect)
        self.assertIn('if integrationDelegate && integrationCodexWorkspaceWrite { args.append("--codex-workspace-write") }', source)

    def test_selection_consumes_plan_and_retains_verification_after_lost_reply(self):
        source = (ROOT / 'swift' / 'Integrations.swift').read_text()
        methods = source.split('    var codexIntegrationEntries: [String] {', 1)[1].split('    func inspectClaudeIntegration()', 1)[0]
        swift = '''import Foundation
final class App {
 var stateReady = true, setupBusy = false
 var codexIntegrationExpert = false, codexIntegrationHarness = false
 var codexIntegrationPlan: [String: Any]? = nil
 var codexIntegrationVerificationPlan: [String: Any]? = nil
 var codexIntegrationResult: [String: Any] = [:]
 var codexIntegrationOperations: [[String: Any]] = []
 var codexIntegrationOperationID = ""
 var codexIntegrationAcknowledged = false
 var codexIntegrationGeneration: UInt64 = 0
 var calls: [String] = []
 var callback: (([String: Any]) -> Void)?
 func setupCommand(_ args: [String], input: [String: Any]? = nil, completion: @escaping ([String: Any]) -> Void) {
   calls.append(args.joined(separator: " ")); callback = completion
 }
 var codexIntegrationEntries: [String] {''' + methods + '''
}
@main struct Main {
 static func main() {
  let app = App()
  app.previewCodexIntegration(); precondition(app.calls.isEmpty)
  app.codexIntegrationExpert = true
  app.previewCodexIntegration(); precondition(app.calls.last == "codex-integration-plan --entry reprise_codex")
  let stale = app.callback!; app.codexIntegrationHarness = true; app.invalidateCodexIntegrationPlan()
  stale(["plan": ["names": ["reprise_codex"], "target": "/release/old"]]); precondition(app.codexIntegrationPlan == nil)
  app.previewCodexIntegration()
  precondition(app.calls.last == "codex-integration-plan --entry reprise_codex --entry reprise-harness")
  app.callback!(["plan": ["names": ["reprise_codex", "reprise-harness"], "target": "/release/new"]])
  app.codexIntegrationAcknowledged = true
  app.applyCodexIntegration(); precondition(app.calls.last == "codex-integration-apply")
  let count = app.calls.count
  app.applyCodexIntegration(); precondition(app.calls.count == count)
  precondition(app.codexIntegrationPlan == nil && app.codexIntegrationVerificationPlan != nil)
  app.callback!(["status": "failed", "timed_out": true])
  app.verifyCodexIntegration(); precondition(app.calls.last == "codex-integration-verify")
  precondition(app.codexIntegrationResult["timed_out"] as? Bool == true)
  let reopened = App()
  reopened.inspectCodexIntegration()
  reopened.callback!(["operations": [["id": "operation-id", "status": "indeterminate"]]])
  reopened.codexIntegrationOperationID = "operation-id"
  reopened.verifyCodexIntegrationOperation()
  precondition(reopened.calls.last == "codex-integration-verify-operation --id operation-id")
  reopened.callback!(["operation_status": "indeterminate", "configuration_written": true, "client_loaded": "unknown"])
  precondition(reopened.codexIntegrationResult["operation_status"] as? String == "indeterminate")
  precondition(!reopened.calls.contains("codex-integration-apply"))
 }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            swift_file = Path(tmp) / 'test.swift'
            binary = Path(tmp) / 'test'
            swift_file.write_text(swift)
            subprocess.run(['swiftc', '-module-cache-path', str(Path(tmp) / 'cache'), '-parse-as-library', str(swift_file), '-o', str(binary)], check=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)

    def test_ui_discloses_client_status_and_target(self):
        source = (ROOT / 'swift' / 'Integrations.swift').read_text()
        self.assertIn('Chargement client inconnu', source)
        self.assertIn('Release cible', source)
        self.assertIn('Identité de la release', source)
