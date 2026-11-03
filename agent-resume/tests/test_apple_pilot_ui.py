import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.uname().sysname == 'Darwin', 'requires macOS Swift')
class ApplePilotUISmokeTests(unittest.TestCase):
    def test_process_lifecycle_with_fake_service_and_no_implicit_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'fake.py'
            fake.write_text('''import json, pathlib, sys
p = pathlib.Path(sys.argv[sys.argv.index('--state') + 1]); (p / 'started').write_text('yes')
if (p / 'eof').exists():
    import os
    os.close(1)
    sys.stdin.read()
    sys.exit(0)
last_turn = None
for line in sys.stdin:
    x = json.loads(line)
    with (p / 'requests').open('a') as f: f.write(x['op'] + (':' + x['request']['op'] if x['op'] == 'memory_call' else '') + '\\n')
    if (p / 'silent').exists() and x['op'] != 'open': continue
    if (p / 'model-uncertain').exists() and x['op'] == 'turn':
        print(json.dumps({'id':x['id'], 'status':'cleanup_unconfirmed'}), flush=True)
        continue
    if (p / 'reversed').exists() and x['op'] == 'turn':
        last_turn = x['id']
        continue
    if (p / 'reversed').exists() and x['op'] == 'cancel':
        import time
        print(json.dumps({'id':last_turn, 'status':'cancelled'}), flush=True)
        time.sleep(.2)
        print(json.dumps({'id':x['id'], 'status':'cancelling'}), flush=True)
        continue
    if x['op'] == 'memory_bind': r = {'status':'bound'}
    elif x['op'] == 'memory_call':
        sub = x['request']['op']
        if sub in ('create_fact', 'create_checkpoint'):
            if (p / 'memory-timeout').exists(): continue
            if (p / 'indeterminate').exists(): r = {'status':'memory_unavailable', 'effect':'indeterminate'}
            else: r = {'status':'finished', 'effect':'recorded', 'result': {'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}}
        elif sub == 'list_facts':
            r = {'status':'finished', 'items':[{'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'revision':1}], 'next_offset':None}
        elif sub == 'list_checkpoints':
            r = {'status':'finished', 'items':[{'id':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'version':1}], 'next_offset':None}
        elif sub == 'get_fact': r = {'status':'finished', 'item':{'text':'asserted'}}
        elif sub == 'operation_status': r = {'status':'finished', 'state':'not_recorded' if (p / 'indeterminate').exists() or ((p / 'memory-timeout').exists() and not (p / 'recorded').exists()) else 'recorded', 'intent':'present'}
        elif sub == 'list_intents': r = {'status':'finished', 'items':[], 'next_offset':None}
        else: r = {'status':'not_found'}
    elif x['op'] == 'open': r = {'status':'opened'}
    elif x['op'] == 'turn': r = {'status':'finished','response':'fake response'}
    elif x['op'] == 'close': r = {'status':'closed'}
    else: r = {'status':'idle'}
    print(json.dumps({'id':x['id'], **r}), flush=True)
''')
            swift = root / 'Smoke.swift'
            swift.write_text('''import Foundation
@main struct Smoke {
  @MainActor static func main() async {
    let args = CommandLine.arguments
    let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), requestTimeout: args.count > 4 && (args[4] == "silent" || args[4] == "memory-timeout") ? 0.3 : 90)
    precondition(!FileManager.default.fileExists(atPath: args[3] + "/started"))
    p.enabled = true
    p.factText = "a"; p.factText = "ab"
    p.sourceKind = "a"; p.sourceKind = "ab"
    precondition(!p.draftEditable && p.factText.isEmpty && p.sourceKind.isEmpty && p.draftProjectID.isEmpty)
    p.projectID = UUID().uuidString.lowercased()
    p.nextAction = "a"; p.nextAction = "ab"
    p.selectedFactIDs.insert("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    precondition(!p.checkpointEditable && p.nextAction.isEmpty && p.selectedFactIDs.isEmpty && p.checkpointDraftWorkID.isEmpty)
    p.factText = "a"; p.factText = "ab"
    precondition(p.factText == "ab" && p.draftProjectID == p.projectID)
    p.discardMemoryDraft()
    p.workID = UUID().uuidString.lowercased()
    p.nextAction = "a"; p.nextAction = "ab"
    precondition(p.checkpointEditable && p.nextAction == "ab" && p.checkpointDraftWorkID == p.workID)
    p.discardMemoryDraft()
    p.open()
    if args.count > 4 && args[4] == "eof" {
      for _ in 0..<100 { if p.cleanupUnconfirmed { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.cleanupUnconfirmed && !p.sessionOpen && !p.busy, p.status)
      return
    }
    for _ in 0..<100 { if p.sessionOpen { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.sessionOpen && p.hostRunning, p.status)
    if args.count > 4 && args[4] == "work-switch" {
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      p.bindMemory()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      p.sourceKind = "document"; p.sourceReference = "source"; p.sourceProducer = "operator"; p.sourceObservedAt = "2026-11-02"
      p.factText = "fact for project"; p.nextAction = "checkpoint for A"
      p.selectedFactIDs.insert("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
      let oldWork = p.workID
      precondition(p.checkpointDraftWorkID == oldWork && p.checkpointDraftMatchesWork)
      p.workID = UUID().uuidString.lowercased()
      p.clearCheckpoints()
      precondition(!p.checkpointDraftMatchesWork && !p.draftEditable && p.nextAction == "checkpoint for A" && !p.selectedFactIDs.isEmpty)
      p.nextAction = "injected from B"; p.factText = "wrong work"; p.sourceReference = "wrong source"
      p.selectedFactIDs.insert("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
      precondition(p.nextAction == "checkpoint for A" && p.factText == "fact for project" && p.sourceReference == "source" && p.selectedFactIDs.count == 1)
      p.createCheckpoint()
      p.createFact()
      precondition(p.memoryOperationID.isEmpty && p.nextAction == "checkpoint for A")
      p.workID = oldWork
      precondition(p.checkpointDraftMatchesWork && p.draftEditable)
      p.createFact()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.memoryOperationState.hasPrefix("Enregistrée"), p.memoryOperationState)
      p.discardMemoryDraft()
      precondition(p.checkpointDraftWorkID.isEmpty && p.nextAction.isEmpty && p.selectedFactIDs.isEmpty)
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      return
    }
    if args.count > 4 && args[4] == "project-switch" {
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      let oldProject = p.projectID
      p.bindMemory()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      p.sourceKind = "document"; p.sourceReference = "source"; p.sourceProducer = "operator"; p.sourceObservedAt = "2026-11-02"
      p.factText = "sensitive draft"; p.nextAction = "continue"
      p.selectedFactIDs.insert("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
      precondition(p.draftProjectID == oldProject && p.draftMatchesProject)
      p.projectID = UUID().uuidString.lowercased()
      p.clearCheckpoints()
      precondition(!p.draftMatchesProject && !p.draftEditable && p.factText == "sensitive draft" && !p.selectedFactIDs.isEmpty)
      p.factText = "injected from B"; p.nextAction = "wrong project"; p.sourceProducer = "wrong actor"
      p.selectedFactIDs.insert("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
      precondition(p.factText == "sensitive draft" && p.nextAction == "continue" && p.sourceProducer == "operator" && p.selectedFactIDs.count == 1)
      p.bindMemory()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      p.createFact()
      p.createCheckpoint()
      precondition(p.memoryOperationID.isEmpty && p.factText == "sensitive draft" && !p.selectedFactIDs.isEmpty)
      p.discardMemoryDraft()
      precondition(p.factText.isEmpty && p.selectedFactIDs.isEmpty && p.draftProjectID.isEmpty)
      p.factText = "new draft"
      precondition(p.draftProjectID == p.projectID)
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      return
    }
    if args.count > 4 && args[4] == "model-uncertain" {
      p.prompt = "hello"
      p.send()
      for _ in 0..<100 { if p.cleanupUnconfirmed && !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.cleanupUnconfirmed && !p.hostRunning && p.memoryReady, p.status)
      p.bindMemory()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.memoryProject == p.projectID && p.memoryReady, p.memoryStatus)
      p.memoryInspectOperationID = UUID().uuidString.lowercased()
      p.inspectOperation()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.memoryOperationState.contains("recorded"), p.memoryOperationState)
      p.sourceKind = "document"; p.sourceReference = "source"; p.sourceProducer = "operator"; p.sourceObservedAt = "2026-11-02"
      p.factText = "not allowed"
      p.createFact()
      precondition(p.memoryOperationID.isEmpty && !p.memoryBusy)
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      return
    }
    if args.count > 4 && args[4] == "silent" {
      p.prompt = "hello"
      p.send()
      p.cancel()
      p.cancel()
      precondition(p.cancelling)
      for _ in 0..<100 { if p.cleanupUnconfirmed { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.cleanupUnconfirmed && !p.busy && !p.sessionOpen, p.status)
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      return
    }
    if args.count > 4 && args[4] == "reversed" {
      p.prompt = "hello"
      p.send()
      p.cancel()
      for _ in 0..<100 { if !p.busy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      try? await Task.sleep(nanoseconds: 400_000_000)
      precondition(!p.busy && !p.cancelling && p.status == "Tour annulé.", p.status)
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(!p.cleanupUnconfirmed, p.status)
      return
    }
    p.bindMemory()
    for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.memoryProject == p.projectID, p.memoryStatus)
    p.sourceKind = "document"; p.sourceReference = "source"; p.sourceProducer = "operator"; p.sourceObservedAt = "2026-11-02"
    p.factText = "test fact"
    if args.count > 4 && args[4] == "memory-timeout" {
      p.createFact()
      for _ in 0..<200 { if p.memoryUncertain && !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      let operation = p.memoryOperationID
      precondition(p.memoryUncertain && !p.hostRunning && p.memoryReady, p.status)
      p.bindMemory()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.memoryProject == p.projectID, p.memoryStatus)
      p.inspectOperation()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.memoryUncertain && p.memoryOperationState.contains("not_recorded"), p.memoryOperationState)
      p.createFact()
      precondition(p.memoryOperationID == operation && !p.memoryBusy)
      FileManager.default.createFile(atPath: args[3] + "/recorded", contents: Data())
      p.inspectOperation()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(!p.memoryUncertain && p.memoryOperationID == operation, p.memoryOperationState)
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      return
    }
    if args.count > 4 && args[4] == "indeterminate" {
      p.createFact()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      let operation = p.memoryOperationID
      precondition(p.memoryUncertain && !operation.isEmpty, p.memoryOperationState)
      p.inspectOperation()
      for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      precondition(p.memoryUncertain && p.memoryOperationID == operation, p.memoryOperationState)
      p.createFact()
      precondition(p.memoryOperationID == operation && !p.memoryBusy)
      p.close()
      for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
      return
    }
    p.createFact()
    for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.memoryOperationState.hasPrefix("Enregistrée"), p.memoryOperationState)
    p.loadFacts()
    for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.facts.count == 1)
    p.selectedFactIDs.insert("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    p.nextAction = "next"
    p.createCheckpoint()
    for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.memoryOperationState.hasPrefix("Enregistrée"), p.memoryOperationState)
    p.loadMemoryCheckpoints()
    for _ in 0..<100 { if !p.memoryBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.checkpoints.count == 1)
    p.prompt = "hello"
    p.send()
    for _ in 0..<100 { if !p.busy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.messages.last?.text == "fake response")
    p.close()
    for _ in 0..<100 { if p.canSelect { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
    precondition(p.canSelect && !p.cleanupUnconfirmed && p.messages.isEmpty)
  }
}
''')
            binary = root / 'smoke'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)],
                           check=True, capture_output=True, text=True, timeout=60)
            run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)],
                           capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue((root / 'started').exists())
            eof_root = root / 'unexpected-eof'
            eof_root.mkdir()
            (eof_root / 'eof').write_text('yes')
            eof_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(eof_root), 'eof'],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(eof_run.returncode, 0, eof_run.stderr)
            silent_root = root / 'silent'
            silent_root.mkdir()
            (silent_root / 'silent').write_text('yes')
            silent_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(silent_root), 'silent'],
                                        capture_output=True, text=True, timeout=10)
            self.assertEqual(silent_run.returncode, 0, silent_run.stderr)
            self.assertEqual((silent_root / 'requests').read_text().splitlines().count('cancel'), 1)
            reversed_root = root / 'reversed'
            reversed_root.mkdir()
            (reversed_root / 'reversed').write_text('yes')
            reversed_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(reversed_root), 'reversed'],
                                          capture_output=True, text=True, timeout=10)
            self.assertEqual(reversed_run.returncode, 0, reversed_run.stderr)
            uncertain_root = root / 'indeterminate'
            uncertain_root.mkdir()
            (uncertain_root / 'indeterminate').write_text('yes')
            uncertain_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(uncertain_root), 'indeterminate'],
                                           capture_output=True, text=True, timeout=10)
            self.assertEqual(uncertain_run.returncode, 0, uncertain_run.stderr)
            timed_root = root / 'memory-timeout'
            timed_root.mkdir()
            (timed_root / 'memory-timeout').write_text('yes')
            timed_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(timed_root), 'memory-timeout'],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(timed_run.returncode, 0, timed_run.stderr)
            model_root = root / 'model-uncertain'
            model_root.mkdir()
            (model_root / 'model-uncertain').write_text('yes')
            model_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(model_root), 'model-uncertain'],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(model_run.returncode, 0, model_run.stderr)
            switch_root = root / 'project-switch'
            switch_root.mkdir()
            switch_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(switch_root), 'project-switch'],
                                        capture_output=True, text=True, timeout=10)
            self.assertEqual(switch_run.returncode, 0, switch_run.stderr)
            requests = (switch_root / 'requests').read_text().splitlines()
            self.assertNotIn('memory_call:create_fact', requests)
            self.assertNotIn('memory_call:create_checkpoint', requests)
            work_root = root / 'work-switch'
            work_root.mkdir()
            work_run = subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(work_root), 'work-switch'],
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(work_run.returncode, 0, work_run.stderr)
            work_requests = (work_root / 'requests').read_text().splitlines()
            self.assertIn('memory_call:create_fact', work_requests)
            self.assertNotIn('memory_call:create_checkpoint', work_requests)


if __name__ == '__main__':
    unittest.main()
