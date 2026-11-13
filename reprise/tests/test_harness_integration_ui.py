"""Harness integration plan state machine against synthetic asynchronous replies."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'swift' / 'Integrations.swift'


def _swift_member(source, signature):
    start = source.index('    ' + signature)
    opened = source.index('{', start)
    depth = 0
    for cursor in range(opened, len(source)):
        if source[cursor] == '{':
            depth += 1
        elif source[cursor] == '}':
            depth -= 1
            if depth == 0:
                return source[start:cursor + 1]
    raise AssertionError('Unterminated Swift member: ' + signature)


class HarnessIntegrationUITests(unittest.TestCase):
    def test_selection_invalidation_and_recovery_wiring(self):
        source = SOURCE.read_text()
        for selection in ('integrationHome', 'integrationProject', 'integrationProfile',
                          'integrationAsi1Effort', 'integrationOutputResume',
                          'integrationOutputResumeProject', 'integrationCodexWorkspaceWrite',
                          'integrationDelegate', 'integrationAgentRole', 'selectedProjectID'):
            self.assertIn('.onChange(of: app.' + selection + ')', source)
        self.assertIn('!app.integrationAcknowledged || !app.harnessPlanReady', source)
        self.assertIn('path("Projet du plan"', source)
        self.assertIn('path("Dossier de configuration"', source)
        self.assertIn('Plan durable retrouvé après interruption', source)

    @unittest.skipUnless(os.uname().sysname == 'Darwin', 'requires macOS Swift')
    def test_actual_swift_callbacks_reject_stale_plan_and_reapprove_recovery(self):
        source = SOURCE.read_text()
        members = [
            'var harnessPlanContext:', 'func harnessPlanMatchesDestination(',
            'func harnessPlanMatchesOptions(', 'var harnessPlanReady:',
            'func invalidateIntegrationPlan(', 'func inspectIntegration(',
            'func previewIntegration(', 'func applyIntegration(', 'func verifyIntegration('
        ]
        methods = '\n'.join(_swift_member(source, signature) for signature in members)
        swift = r'''import Foundation
final class App {
    var stateReady = true, setupBusy = false
    var selectedProjectID = "project-a"
    var integrationHome = "/tmp/reprise-harness-config"
    var integrationProfile = "standard"
    var integrationProject = "/tmp/reprise-harness-project"
    var integrationDelegate = false, integrationCodexWorkspaceWrite = false
    var integrationOutputResume = false, integrationAsi1Effort = false
    var integrationCodexWorkspaceWriteInstalled = false
    var integrationOutputResumeProject = ""
    var integrationAgentRole = "standard"
    var integrationPlan: [String: Any]? = nil
    var integrationPlanGeneration: UInt64 = 0
    var integrationPlanContext: [String]? = nil
    var integrationPlanRecovered = false
    var integrationAcknowledged = false
    var integrationResult: [String: Any]? = nil
    var integrationVerification: [String: Any]? = nil
    var integrationProfiles: [String] = []
    var integrationMessage = ""
    var calls: [[String]] = []
    var callback: (([String: Any]) -> Void)?
    var lastInput: [String: Any]? = nil
    func setupCommand(_ args: [String], input: [String: Any]? = nil,
                      completion: @escaping ([String: Any]) -> Void) {
        calls.append(args)
        callback = completion
        lastInput = input
    }
''' + methods + r'''
}
@main struct Test {
 static func main() {
    let app = App()
    func plan(_ project: String = "/tmp/reprise-harness-project", _ profile: String = "standard") -> [String: Any] {
        ["action": "install", "home": "/tmp/reprise-harness-config", "profile": profile,
         "project": project, "delegate": false, "fingerprint": String(repeating:"a", count:64)]
    }
    let correct = plan()

    // A -> B -> A must not revive an earlier preview.
    app.previewIntegration(action: "install")
    let old = app.callback!
    app.selectedProjectID = "project-b"
    app.invalidateIntegrationPlan()
    app.selectedProjectID = "project-a"
    app.invalidateIntegrationPlan()
    old(["plan": correct])
    precondition(app.integrationPlan == nil, "ABA callback restored a stale plan")

    // A fresh preview belongs to the current project and options.
    app.previewIntegration(action: "install")
    app.callback!(["plan": correct])
    precondition(app.harnessPlanReady && app.integrationPlan != nil)
    precondition(!app.integrationAcknowledged)
    app.integrationAcknowledged = true
    app.integrationAsi1Effort = true
    precondition(!app.harnessPlanReady, "changed options still allow old plan")
    app.invalidateIntegrationPlan()
    precondition(app.integrationPlan == nil && !app.integrationAcknowledged)
    app.integrationAsi1Effort = false

    // A wrong destination must never be admitted from a preview.
    app.previewIntegration(action: "install")
    app.callback!(["plan": plan("/tmp/other")])
    precondition(app.integrationPlan == nil)
    app.previewIntegration(action: "install")
    app.callback!(["plan": correct])
    app.integrationAcknowledged = true
    let before = app.calls.count
    app.applyIntegration()
    precondition(app.calls.count == before + 1)
    precondition(app.calls.last!.first == "harness-integration-apply")
    precondition(app.integrationPlan == nil && !app.integrationAcknowledged)
    precondition(app.lastInput?["plan"] is [String: Any])
    app.applyIntegration()
    precondition(app.calls.count == before + 1, "duplicate apply was dispatched")

    // Uncertain apply result: only a matching durable pending plan can reappear.
    app.callback!(["status": "partial", "pending_plan": correct])
    precondition(app.harnessPlanReady && app.integrationPlanRecovered)
    precondition(!app.integrationAcknowledged, "pending plan reused old approval")
    app.integrationAcknowledged = true
    app.applyIntegration()
    let staleApply = app.callback!
    app.integrationProject = "/tmp/other"
    app.invalidateIntegrationPlan()
    staleApply(["status": "partial", "pending_plan": correct])
    precondition(app.integrationPlan == nil, "stale apply revived a plan")
    app.integrationProject = "/tmp/reprise-harness-project"

    // Inspection is a deliberate recovery, never a permission-grant or auto-apply.
    app.inspectIntegration()
    app.callback!(["status": "inspected", "pending_plan": correct, "profiles": ["standard"]])
    precondition(app.harnessPlanReady && app.integrationPlanRecovered)
    precondition(!app.integrationAcknowledged)
    app.invalidateIntegrationPlan()
    app.inspectIntegration()
    app.callback!(["status": "inspected", "pending_plan": plan("/tmp/other")])
    precondition(app.integrationPlan == nil, "foreign durable plan was displayed")

    // Changed profile while inspection is running drops the old response.
    app.inspectIntegration()
    let staleInspect = app.callback!
    app.integrationProfile = "another"
    app.invalidateIntegrationPlan()
    staleInspect(["status": "inspected", "pending_plan": correct])
    precondition(app.integrationPlan == nil)
    app.integrationProfile = "standard"

    // A verification result is likewise bound to the selected context.
    app.verifyIntegration()
    let staleVerify = app.callback!
    app.integrationProject = "/tmp/changed"
    app.invalidateIntegrationPlan()
    staleVerify(["status": "verified"])
    precondition(app.integrationVerification == nil)
 }
}
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'HarnessPlanTest.swift'
            binary = Path(folder) / 'HarnessPlanTest'
            path.write_text(swift)
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path',
                            str(Path(folder) / 'cache'), str(path), '-o', str(binary)],
                           check=True, timeout=60)
            subprocess.run([str(binary)], check=True, timeout=10)
