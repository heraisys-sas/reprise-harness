import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.uname().sysname == 'Darwin', 'requires macOS Swift')
class ApplePilotUISmokeTests(unittest.TestCase):
    def test_local_start_journal_recovers_exact_run_without_resubmission(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); fake=root/'service.py'
            fake.write_text("""import json,sys,hashlib,os
from pathlib import Path
state=Path(sys.argv[sys.argv.index('--state')+1]); saved=state/'job.json'
for line in sys.stdin:
 x=json.loads(line); op=x['op']
 with (state/'ops').open('a') as f:f.write(op+'\\n')
 if op=='job_prepare':
  row={'status':'preparation','project_id':x['project_id'],'operation_id':x['operation_id'],'revision':0,
       'proposal':x['proposal'],'proposal_sha256':hashlib.sha256(json.dumps(x['proposal'],sort_keys=True,separators=(',',':')).encode()).hexdigest()}
  saved.write_text(json.dumps(row)); out={'job':row}
 elif op=='job_consent_context':
  out={'context':{'project_id':x['project_id'],'work_id':x['work_id'],'project_root':'/test','execution_verification':'not_performed'},'context_sha256':'a'*64}
 elif op=='job_authorize_local':
  row=json.loads(saved.read_text());row.update(status='consented',revision=1,request_key='local-key',authorization_kind='operator_consent_v1',
    operator_authorization={'authorization_id':x['authorization_id'],'backend':'foundation-models','context_sha256':'a'*64,'execution_authorized':False,'host_context':{'work_id':x['work_id']}})
  saved.write_text(json.dumps(row));out={'job':row}
 elif op=='job_local_plan':
  row=json.loads(saved.read_text());manifest={'project_id':x['project_id'],'work_id':row['operator_authorization']['host_context']['work_id'],
      'request_key':'local-key','backend':'foundation-models','context_sha256':'a'*64}
  (state/'plan.json').write_text(json.dumps(manifest));out={'local_plan':{'manifest':manifest,'manifest_sha256':'b'*64}}
 elif op=='job_local_start':
  row=json.loads(saved.read_text());row.update(status='local_running',revision=2,local_execution={'run_id':x['run_id'],
      'manifest_sha256':'b'*64,'manifest':json.loads((state/'plan.json').read_text()),'stage':'running'})
  saved.write_text(json.dumps(row));os._exit(0)
 elif op=='job_recover':
  row=json.loads(saved.read_text())
  if not (state/'wrong').exists():(state/'wrong').touch();row['local_execution']['run_id']='foreign'
  else:row.update(status='local_recorded',revision=3);row['local_execution'].update(stage='recorded',receipt={'status':'finished','response':'local ui result'})
  out={'job':row}
 elif op=='close':print(json.dumps({'id':x['id'],'status':'closed'}),flush=True);continue
 else:raise AssertionError(op)
 print(json.dumps({'id':x['id'],'status':'finished',**out}),flush=True)
""")
            swift=root/'LocalUI.swift'
            swift.write_text('''import Foundation
@main struct LocalUI {
 @MainActor static func main() async {
  let a=CommandLine.arguments,d=UserDefaults(suiteName:"test.local.ui."+UUID().uuidString)!
  func make()->ApplePilotUI { ApplePilotUI(python:URL(fileURLWithPath:a[1]),service:URL(fileURLWithPath:a[2]),state:URL(fileURLWithPath:a[3]),requestTimeout:2,preferences:d) }
  func wait(_ p:ApplePilotUI) async {for _ in 0..<150{if !p.preparationBusy{break};try? await Task.sleep(nanoseconds:20_000_000)}}
  let p=make();p.enabled=true;p.projectID="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";p.workID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
  p.newPreparation();let operation=p.preparationOperationID;p.proposalObjective="objective";p.proposalAcceptance="proof"
  p.savePreparation();await wait(p);p.loadConsentContext();await wait(p)
  p.consentBackend="foundation-models";p.authorizePreparation();await wait(p)
  precondition(!p.preparationUncertain && p.preparationJob["status"] as? String == "consented")
  p.planLocalExecution();await wait(p);precondition(p.canStartLocal)
  p.startLocalExecution();await wait(p);precondition(p.preparationUncertain)
  let q=make();q.enabled=true;precondition(q.preparationUncertain && q.preparationOperationID==operation)
  q.loadPreparation();await wait(q);precondition(q.preparationUncertain,"foreign run must not clear journal")
  q.loadPreparation();await wait(q);precondition(!q.preparationUncertain)
  precondition(q.preparationJob["status"] as? String == "local_recorded")
  precondition((q.preparationJob["local_execution"] as? [String:Any])?["receipt"] is [String:Any])
  precondition(!q.canStartLocal);q.startLocalExecution();q.close()
 }
}
''')
            binary=root/'local-ui'
            subprocess.run(['swiftc','-parse-as-library','-module-cache-path',str(root/'cache'),str(ROOT/'swift'/'ApplePilotUI.swift'),str(swift),'-o',str(binary)],check=True)
            subprocess.run([str(binary),'/usr/bin/python3',str(fake),str(root)],check=True,timeout=12)
            self.assertEqual((root/'ops').read_text().splitlines().count('job_local_start'),1)

    def test_native_local_helper_contract_and_self_deadline_without_inference(self):
        # Compile only the real timer/contract prefix, before any model API call.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            prefix=(ROOT/'swift'/'FoundationExplain.swift').read_text().split('// Explains recent Reprise activity')[0]
            source=root/'Deadline.swift'; source.write_text(prefix+'\nThread.sleep(forTimeInterval: 10)\n')
            binary=root/'deadline'
            subprocess.run(['swiftc','-module-cache-path',str(root/'cache'),str(source),'-o',str(binary)],check=True)
            contract=subprocess.run([str(binary),'--supervision-contract'],capture_output=True,text=True,check=True)
            import json
            self.assertEqual(json.loads(contract.stdout)['contract'],'foundation-explain-v1-self-deadline')
            expired=subprocess.run([str(binary),'--deadline-seconds','1'],capture_output=True,text=True,timeout=3)
            self.assertEqual(expired.returncode,124)
            self.assertEqual(expired.stdout, '')  # expiry never blocks behind an output pipe/lock

    def test_terminal_receipt_process_boundaries_and_sync_failures(self):
        # Exercise the production journal directly, with a fresh executable
        # process for every write/recovery boundary and no UserDefaults.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix = (ROOT / 'swift' / 'ApplePilotUI.swift').read_text().split('/// Isolated UI controller.')[0]
            swift = root / 'Journal.swift'
            swift.write_text(prefix + r'''
@main struct JournalTest {
 static func main() throws {
  let a = CommandLine.arguments, state = URL(fileURLWithPath: a[1])
  let project = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
  let intent = PreparationIntent(project: project, operation: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      kind: "prepare", revision: 0, fingerprint: String(repeating: "a", count: 64),
      attempt: "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
  let journal = PreparationIntentJournal(state: state)
  if a[2] == "write" { try journal.write(intent); return }
  if a[2] == "terminal" {
   var count = 0
   let failing = PreparationIntentJournal(state: state, syncFD: { fd in
    count += 1
    if count == Int(a[3])! { return -1 }
    return Darwin.fsync(fd)
   })
   do { try failing.recordTerminal(intent, effect: "not_admitted", status: "invalid_proposal") }
   catch { Darwin._exit(0) }
   // Exit immediately at the boundary before unlink, or after unlink/fsync failure.
   if a[3] == "4" { precondition(!failing.clear(intent)) }
   if a[3] == "5" {
    let unlinkFailed = PreparationIntentJournal(state: state, unlinkMarker: { _, _ in -1 })
    precondition(!unlinkFailed.clear(intent))
   }
   Darwin._exit(0)
  }
  if a[2] == "uncertain" {
   precondition(try journal.read(project: project) == intent)
   precondition(try journal.terminal(project: project) == nil)
   return
  }
  if a[2] == "resolve" {
   precondition(try journal.terminal(project: project) == intent)
   precondition(journal.clear(intent))
   precondition(try journal.read(project: project) == nil)
   return
  }
  if a[2] == "oldwriter" {
   FileManager.default.createFile(atPath: a[1] + "/old-ready", contents: Data())
   for _ in 0..<500 {
    if FileManager.default.fileExists(atPath: a[1] + "/next-written") { break }
    usleep(10_000)
   }
   precondition(FileManager.default.fileExists(atPath: a[1] + "/next-written"))
   do { try journal.recordTerminal(intent, effect: "not_admitted", status: "invalid_proposal"); preconditionFailure() }
   catch {}
   precondition(!journal.clear(intent))
   FileManager.default.createFile(atPath: a[1] + "/old-done", contents: Data())
   return
  }
  if a[2] == "retry" || a[2] == "newwriter" {
   if a[2] == "newwriter" {
    for _ in 0..<500 {
     if FileManager.default.fileExists(atPath: a[1] + "/old-ready") { break }
     usleep(10_000)
    }
    precondition(FileManager.default.fileExists(atPath: a[1] + "/old-ready"))
   }
   let next = PreparationIntent(project: project, operation: intent.operation, kind: intent.kind,
       revision: intent.revision, fingerprint: intent.fingerprint, attempt: UUID().uuidString.lowercased())
   try journal.write(next)
   if a[2] == "newwriter" {
    FileManager.default.createFile(atPath: a[1] + "/next-written", contents: Data())
    for _ in 0..<500 {
     if FileManager.default.fileExists(atPath: a[1] + "/old-done") { break }
     usleep(10_000)
    }
    precondition(FileManager.default.fileExists(atPath: a[1] + "/old-done"))
   }
   precondition(try journal.terminal(project: project) == nil)
   do { try journal.recordTerminal(intent, effect: "not_admitted", status: "invalid_proposal"); preconditionFailure() }
   catch {}
   precondition(!journal.clear(intent))
   precondition(try journal.read(project: project) == next)
   return
  }
  if a[2] == "error" {
   do { _ = try journal.terminal(project: project); preconditionFailure() } catch {}
   precondition(try journal.read(project: project) == intent)
  }
 }
}
'''.replace('precondition(try ', 'precondition(try! '))
            binary = root / 'journal'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(swift), '-o', str(binary)], check=True)
            for boundary in (0, 1, 2, 3, 4, 5):
                state = root / str(boundary)
                state.mkdir(mode=0o700)
                def run(mode, *args):
                    subprocess.run([str(binary), str(state), mode, *map(str, args)], check=True, timeout=5)
                run('write')
                run('terminal', boundary)
                if boundary in (1, 2):
                    run('uncertain')
                else:
                    run('resolve')
                    run('resolve')  # idempotent across another fresh process
                    run('retry')  # stale response cannot resolve a new admission
            state = root / 'concurrent'
            state.mkdir(mode=0o700)
            run('write')
            run('terminal', 0)
            run('resolve')
            old = subprocess.Popen([str(binary), str(state), 'oldwriter'])
            try:
                run('newwriter')
                self.assertEqual(old.wait(timeout=5), 0)
            finally:
                if old.poll() is None:
                    old.kill()
                    old.wait()
            state = root / 'errors'
            state.mkdir(mode=0o700)
            run('write')
            receipt = state / 'apple-pilot-intents.noindex' / 'terminal-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.json'
            receipt.write_text('{}')
            receipt.chmod(0o600)
            run('error')
            receipt.unlink()
            receipt.symlink_to(root / 'missing')
            run('error')

    def test_identical_revise_is_local_noop_before_journal_or_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import json, pathlib, sys
state = pathlib.Path(sys.argv[sys.argv.index('--state')+1])
for line in sys.stdin:
 x = json.loads(line)
 with (state / 'ops').open('a') as f: f.write(x['op']+'\\n')
 if x['op'] == 'job_prepare':
  if x['proposal']['objective'] == 'rejet':
   print(json.dumps({'id':x['id'], 'status':'invalid_proposal', 'effect':'not_admitted'}), flush=True); continue
  job = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
         'request_key':None, 'authority':None, 'revision':0, 'proposal':x['proposal']}
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':job}), flush=True)
 elif x['op'] == 'job_recover': print(json.dumps({'id':x['id'], 'status':'finished', 'job':job}), flush=True)
 elif x['op'] == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'IdenticalRevise.swift'
            swift.write_text('''import Foundation
@main struct IdenticalRevise {
 @MainActor static func main() async {
  let d = UserDefaults(suiteName: "test.preparation.identical." + UUID().uuidString)!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  let args = CommandLine.arguments
  var failClear = false
  var syncCount = 0
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), preferences: d, journalSync: { fd in
   if failClear { syncCount += 1; if syncCount == 8 { failClear = false; return -1 } }
   return Darwin.fsync(fd)
  })
  p.enabled = true; p.projectID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"; p.newPreparation()
  p.proposalObjective = "rejet"; p.proposalAcceptance = "preuve"; p.savePreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(!p.preparationUncertain && p.preparationStatus.contains("refusée"))
  failClear = true
  p.proposalObjective = "objectif"; p.proposalAcceptance = "preuve"; p.savePreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain)
  p.loadPreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationJob["revision"] as? Int == 0 && !p.preparationUncertain)
  p.revisePreparation()
  precondition(!p.preparationBusy && p.preparationStatus.contains("identique"))
  precondition(!FileManager.default.fileExists(atPath: args[3] + "/apple-pilot-intents.noindex/intent-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.json"))
  p.close()
 }
}
''')
            binary = root / 'identical-revise'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True, timeout=8)
            self.assertEqual((root / 'ops').read_text().splitlines(), ['job_prepare', 'job_prepare', 'close'])

    def test_intent_journal_survives_process_exit_before_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seen = root / 'prepare-seen'
            fake = root / 'service.py'
            fake.write_text("""import json, pathlib, sys, time
seen = pathlib.Path(sys.argv[sys.argv.index('--state')+1]) / 'prepare-seen'
for line in sys.stdin:
 x = json.loads(line)
 if x['op'] == 'job_prepare':
  seen.write_text('yes')
  time.sleep(5)
 elif x['op'] == 'job_recover': print(json.dumps({'id':x['id'], 'status':'not_found'}), flush=True)
 elif x['op'] == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'ProcessCrash.swift'
            swift.write_text('''import Foundation
import Darwin
@main struct ProcessCrash {
 @MainActor static func main() async {
  let args = CommandLine.arguments, project = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
  let d = UserDefaults(suiteName: "test.preparation.crash." + args[4])!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), requestTimeout: 10, preferences: d)
  p.enabled = true; p.projectID = project
  if args[5] == "write" {
   p.newPreparation(); p.proposalObjective = "objectif"; p.proposalAcceptance = "preuve"; p.savePreparation()
   for _ in 0..<250 {
    if FileManager.default.fileExists(atPath: args[3] + "/prepare-seen") { Darwin._exit(0) }
    try? await Task.sleep(nanoseconds: 20_000_000)
   }
   preconditionFailure("service did not receive preparation")
  }
  let operation = p.preparationOperationID
  precondition(!operation.isEmpty && p.preparationUncertain)
  p.newPreparation(); precondition(p.preparationOperationID == operation)
  p.loadPreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain && p.preparationOperationID == operation)
  p.newPreparation(); precondition(p.preparationOperationID == operation)
  p.close()
 }
}
''')
            binary = root / 'process-crash'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            suite = root.name
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root), suite, 'write'], check=True, timeout=8)
            self.assertTrue(seen.exists())
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root), suite, 'read'], check=True, timeout=8)

    def test_fsync_failure_refuses_emit_and_not_found_stays_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import json, pathlib, sys
state = pathlib.Path(sys.argv[sys.argv.index('--state')+1])
for line in sys.stdin:
 x = json.loads(line)
 with (state / 'ops').open('a') as f: f.write(x['op']+'\\n')
 if x['op'] == 'job_recover': print(json.dumps({'id':x['id'], 'status':'not_found'}), flush=True)
 elif x['op'] == 'job_prepare':
  job = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
         'request_key':None, 'authority':None, 'revision':0, 'proposal':x['proposal']}
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':job}), flush=True)
 elif x['op'] == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'FsyncRetry.swift'
            swift.write_text('''import Foundation
import Darwin
@main struct FsyncRetry {
 @MainActor static func main() async {
  let args = CommandLine.arguments, project = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
  let d = UserDefaults(suiteName: "test.preparation.fsync." + UUID().uuidString)!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  var failOnce = true
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), requestTimeout: 5, preferences: d, journalSync: { fd in
   if failOnce { failOnce = false; return -1 }; return Darwin.fsync(fd)
  })
  p.enabled = true; p.projectID = project; p.newPreparation(); let operation = p.preparationOperationID
  p.proposalObjective = "objectif"; p.proposalAcceptance = "preuve"; p.savePreparation()
  precondition(p.preparationUncertain && p.preparationJournalUnavailable && !p.preparationBusy)
  precondition(!FileManager.default.fileExists(atPath: args[3] + "/ops"))
  p.loadPreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain && p.preparationOperationID == operation)
  p.savePreparation()
  precondition(p.preparationBusy == false && p.preparationOperationID == operation)
  p.close()
 }
}
''')
            binary = root / 'fsync-retry'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True, timeout=8)
            self.assertEqual((root / 'ops').read_text().splitlines(), ['job_recover', 'close'])

    def test_consent_journal_recovers_exact_authorization_after_lost_reply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import hashlib, json, os, sys
from pathlib import Path
state=Path(sys.argv[sys.argv.index('--state')+1]); stored=state/'consent.json'
for line in sys.stdin:
 x=json.loads(line); op=x['op']
 with (state/'ops').open('a') as log: log.write(op+'\\n')
 if op=='job_prepare':
  sha=hashlib.sha256(json.dumps(x['proposal'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
  row={'status':'preparation','project_id':x['project_id'],'operation_id':x['operation_id'],
       'request_key':None,'authority':None,'revision':0,'proposal':x['proposal'],'proposal_sha256':sha}
  stored.write_text(json.dumps(row)); result={'job':row}
 elif op=='job_consent_context':
  result={'context':{'project_id':x['project_id'],'work_id':x['work_id'],'project_root':'/test',
                    'execution_verification':'not_performed'},'context_sha256':'a'*64}
 elif op=='job_authorize':
  row=json.loads(stored.read_text()); row.update(status='consented',revision=1,
   authorization_kind='operator_consent_v1',operator_authorization={
    'authorization_id':x['authorization_id'],'context_sha256':x['expected_context_sha256'],
    'execution_authorized':False,'host_context':{'work_id':x['work_id']}})
  stored.write_text(json.dumps(row)); os._exit(0)
 elif op=='job_recover':
  row=json.loads(stored.read_text())
  if not (state/'wrong-seen').exists():
   (state/'wrong-seen').touch(); row['operator_authorization']['authorization_id']='wrong'
  result={'job':row}
 elif op=='close':
  print(json.dumps({'id':x['id'],'status':'closed'}),flush=True); continue
 else: raise AssertionError(op)
 print(json.dumps({'id':x['id'],'status':'finished',**result}),flush=True)
""")
            swift = root / 'Consent.swift'
            swift.write_text('''import Foundation
@main struct ConsentRecovery {
 @MainActor static func main() async {
  let a=CommandLine.arguments
  let d=UserDefaults(suiteName: "test.consent." + UUID().uuidString)!
  func make() -> ApplePilotUI { ApplePilotUI(python:URL(fileURLWithPath:a[1]),service:URL(fileURLWithPath:a[2]),state:URL(fileURLWithPath:a[3]),requestTimeout:2,preferences:d) }
  func wait(_ p:ApplePilotUI) async { for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds:20_000_000) } }
  let p=make(); p.enabled=true
  p.projectID="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"; p.workID="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
  p.newPreparation(); let operation=p.preparationOperationID
  p.proposalObjective="objective"; p.proposalAcceptance="proof"; p.savePreparation(); await wait(p)
  precondition(!p.preparationUncertain && !p.canConsent)
  p.loadConsentContext(); await wait(p); precondition(p.canConsent)
  p.proposalQuestions="question"; precondition(!p.canConsent); p.proposalQuestions=""
  let work=p.workID; p.workID="cccccccc-cccc-4ccc-8ccc-cccccccccccc"; precondition(!p.canConsent)
  p.workID=work; p.loadConsentContext(); await wait(p); precondition(p.canConsent)
  p.authorizePreparation(); await wait(p)
  precondition(p.preparationUncertain)
  let q=make(); q.enabled=true
  precondition(q.preparationUncertain && q.preparationOperationID==operation)
  q.loadPreparation(); await wait(q)
  precondition(q.preparationUncertain, "foreign authorization must not resolve journal")
  q.newPreparation(); precondition(q.preparationOperationID==operation)
  q.loadPreparation(); await wait(q)
  precondition(!q.preparationUncertain && q.preparationJob["status"] as? String == "consented")
  precondition(!q.canConsent && !q.preparationCanEdit)
  precondition(q.preparationStatus.contains("exécution pas encore disponible"))
  q.close()
 }
}
''')
            binary = root / 'consent-recovery'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root/'cache'),
                            str(ROOT/'swift'/'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True, timeout=10)
            ops=(root/'ops').read_text().splitlines()
            self.assertEqual(ops.count('job_authorize'), 1)
            self.assertEqual(ops.count('job_recover'), 2)
            self.assertTrue(set(ops) <= {'job_prepare','job_consent_context','job_authorize','job_recover','close'})

    def test_inflight_marker_survives_restart_without_ack_until_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import json, sys, time
for line in sys.stdin:
 x = json.loads(line)
 if x['op'] == 'job_prepare':
  time.sleep(.5)
  job = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
         'request_key':None, 'authority':None, 'revision':0, 'proposal':x['proposal']}
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':job}), flush=True)
 elif x['op'] == 'job_recover':
  job = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
         'request_key':None, 'authority':None, 'revision':0,
         'proposal':{'objective':'objectif', 'acceptance':['preuve'], 'constraints':[], 'questions':[]}}
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':job}), flush=True)
 elif x['op'] == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'InflightRecovery.swift'
            swift.write_text('''import Foundation
@main struct InflightRecovery {
 @MainActor static func main() async {
  let project = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
  let d = UserDefaults(suiteName: "test.preparation.inflight." + UUID().uuidString)!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  let args = CommandLine.arguments
  func make() -> ApplePilotUI { ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), requestTimeout: 5, preferences: d) }
  let p = make(); p.enabled = true; p.projectID = project; p.newPreparation(); let operation = p.preparationOperationID
  p.proposalObjective = "objectif"; p.proposalAcceptance = "preuve"; p.savePreparation()
  let restarted = make()
  precondition(restarted.preparationUncertain && restarted.preparationOperationID == operation)
  restarted.enabled = true; restarted.newPreparation()
  precondition(restarted.preparationOperationID == operation)
  restarted.loadPreparation()
  for _ in 0..<100 { if !restarted.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(!restarted.preparationUncertain && restarted.preparationOperationID == operation,
               "recovery status=\\(restarted.preparationStatus), busy=\\(restarted.preparationBusy), op=\\(restarted.preparationOperationID), expected=\\(operation)")
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  p.close(); restarted.close()
 }
}
''')
            binary = root / 'inflight-recovery'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True)

    def test_late_preparation_response_stays_bound_to_its_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import json, sys, time
for line in sys.stdin:
 x = json.loads(line)
 if x['op'] == 'job_prepare':
  time.sleep(.2)
  job = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
         'request_key':None, 'authority':None, 'revision':0, 'proposal':x['proposal']}
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':job}), flush=True)
 elif x['op'] == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'ProjectBinding.swift'
            swift.write_text('''import Foundation
@main struct ProjectBinding {
 @MainActor static func main() async {
  let a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
  let d = UserDefaults(suiteName: "test.preparation.binding." + UUID().uuidString)!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  let args = CommandLine.arguments
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), preferences: d)
  p.enabled = true; p.projectID = a; p.newPreparation(); let operation = p.preparationOperationID
  p.proposalObjective = "A"; p.proposalAcceptance = "preuve"; p.savePreparation()
  p.projectID = b
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationJob.isEmpty && p.preparationOperationID.isEmpty)
  p.projectID = a
  precondition(p.preparationOperationID == operation && p.preparationJob.isEmpty)
  p.close()
 }
}
''')
            binary = root / 'project-binding'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True)

    def test_uncertain_preparation_requires_recovery_across_ui_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import json, pathlib, sys, threading, time
state = pathlib.Path(sys.argv[sys.argv.index('--state')+1]); record = state / 'delayed-job.json'
for line in sys.stdin:
 x = json.loads(line)
 if x['op'] == 'job_prepare':
  job = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
         'request_key':None, 'authority':None, 'revision':0, 'proposal':x['proposal']}
  threading.Thread(target=lambda: (time.sleep(.7), record.write_text(json.dumps(job))), daemon=False).start()
  print(json.dumps({'id':x['id'], 'status':'write_uncertain', 'effect':'indeterminate'}), flush=True)
 elif x['op'] == 'job_recover':
  if record.exists(): print(json.dumps({'id':x['id'], 'status':'finished', 'job':json.loads(record.read_text())}), flush=True)
  else: print(json.dumps({'id':x['id'], 'status':'not_found'}), flush=True)
 elif x['op'] == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'PreparationRecovery.swift'
            swift.write_text('''import Foundation
@main struct PreparationRecovery {
 @MainActor static func main() async {
  let project = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
  let d = UserDefaults(suiteName: "test.preparation.recovery." + UUID().uuidString)!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  let args = CommandLine.arguments
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), preferences: d)
  p.enabled = true; p.projectID = project; p.newPreparation(); let operation = p.preparationOperationID
  p.proposalObjective = "objectif"; p.proposalAcceptance = "preuve"; p.savePreparation()
  for _ in 0..<100 { if p.preparationUncertain { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain)
  p.close()
  let q = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), preferences: d)
  precondition(q.preparationOperationID == operation && q.preparationUncertain)
  q.enabled = true; q.loadPreparation()
  for _ in 0..<100 { if !q.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(q.preparationUncertain && q.preparationOperationID == operation)
  q.newPreparation(); precondition(q.preparationOperationID == operation)
  for _ in 0..<100 { if FileManager.default.fileExists(atPath: args[3] + "/delayed-job.json") { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  q.loadPreparation()
  for _ in 0..<100 { if !q.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(!q.preparationUncertain && q.preparationOperationID == operation)
  q.newPreparation(); precondition(q.preparationOperationID != operation)
  q.close()
 }
}
''')
            binary = root / 'preparation-recovery'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True)

    def test_delayed_revise_recovery_requires_exact_revision_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import json, pathlib, sys, threading, time
state = pathlib.Path(sys.argv[sys.argv.index('--state')+1]); committed = state / 'committed-revision.json'
current = None; revised = None; recoveries = 0
for line in sys.stdin:
 x = json.loads(line); op = x['op']
 if op == 'job_prepare':
  current = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
             'request_key':None, 'authority':None, 'revision':0, 'proposal':x['proposal']}
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':current}), flush=True)
 elif op == 'job_revise':
  revised = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
             'request_key':None, 'authority':None, 'revision':1, 'proposal':x['proposal']}
  threading.Thread(target=lambda: (time.sleep(.5), committed.write_text(json.dumps(revised)), None), daemon=True).start()
  print(json.dumps({'id':x['id'], 'status':'write_uncertain', 'effect':'indeterminate'}), flush=True)
 elif op == 'job_recover':
  recoveries += 1
  if recoveries == 1:
   print(json.dumps({'id':x['id'], 'status':'invalid_proposal', 'effect':'not_admitted'}), flush=True); continue
  elif recoveries == 2: result = current
  elif recoveries == 3:
   result = dict(current, revision=1, proposal={'objective':'concurrent autre', 'acceptance':['preuve'], 'constraints':[], 'questions':[]})
  else:
   deadline = time.monotonic() + 2
   while not committed.exists() and time.monotonic() < deadline: time.sleep(.01)
   result = json.loads(committed.read_text())
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':result}), flush=True)
 elif op == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'ReviseRecovery.swift'
            swift.write_text('''import Foundation
@main struct ReviseRecovery {
 @MainActor static func main() async {
  let project = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
  let d = UserDefaults(suiteName: "test.preparation.revise-recovery." + UUID().uuidString)!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  let args = CommandLine.arguments
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), preferences: d)
  p.enabled = true; p.projectID = project; p.newPreparation(); let operation = p.preparationOperationID
  p.proposalObjective = "objectif"; p.proposalAcceptance = "preuve"; p.savePreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(!p.preparationUncertain && p.preparationJob["revision"] as? Int == 0)
  p.proposalObjective = "objectif révisé"; p.revisePreparation()
  for _ in 0..<100 { if p.preparationUncertain { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain && p.preparationOperationID == operation)
  p.loadPreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain && p.preparationJob["revision"] as? Int == 0 && p.proposalObjective == "objectif révisé")
  p.loadPreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain && p.preparationJob["revision"] as? Int == 0 && p.proposalObjective == "objectif révisé")
  p.loadPreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.preparationUncertain && p.preparationJob["revision"] as? Int == 1 && p.proposalObjective == "objectif révisé")
  p.loadPreparation()
  for _ in 0..<100 { if !p.preparationBusy { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(!p.preparationUncertain && p.preparationOperationID == operation)
  precondition(p.preparationJob["revision"] as? Int == 1 && p.proposalObjective == "objectif révisé")
  p.close()
 }
}
''')
            binary = root / 'revise-recovery'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True, timeout=10)

    def test_legacy_intent_is_recoverable_but_never_cleared_by_unproven_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
            operation = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
            journal = root / 'apple-pilot-intents.noindex'
            journal.mkdir(mode=0o700)
            (journal / f'intent-{project}.json').write_text(f'{project}\n{operation}\n')
            fake = root / 'service.py'
            fake.write_text("""import json, sys
for line in sys.stdin:
 x = json.loads(line)
 if x['op'] == 'job_recover':
  job = {'status':'preparation', 'project_id':x['project_id'], 'operation_id':x['operation_id'],
         'request_key':None, 'authority':None, 'revision':0,
         'proposal':{'objective':'objectif', 'acceptance':['preuve'], 'constraints':[], 'questions':[]}}
  print(json.dumps({'id':x['id'], 'status':'finished', 'job':job}), flush=True)
 elif x['op'] == 'close': print(json.dumps({'id':x['id'], 'status':'closed'}), flush=True)
""")
            swift = root / 'LegacyIntent.swift'
            swift.write_text(f'''import Foundation
@main struct LegacyIntent {{
 @MainActor static func main() async {{
  let d = UserDefaults(suiteName: "test.preparation.legacy." + UUID().uuidString)!
  StartupPreferences.migrate(d); d.set(false, forKey: StartupPreferences.pilotEnabledKey)
  let args = CommandLine.arguments
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), preferences: d)
  p.enabled = true; p.projectID = "{project}"
  precondition(p.preparationOperationID == "{operation}" && p.preparationUncertain)
  p.loadPreparation()
  for _ in 0..<100 {{ if !p.preparationBusy {{ break }}; try? await Task.sleep(nanoseconds: 20_000_000) }}
  precondition(p.preparationUncertain && p.preparationOperationID == "{operation}")
  precondition(FileManager.default.fileExists(atPath: args[3] + "/apple-pilot-intents.noindex/intent-{project}.json"))
  p.close()
 }}
}}
''')
            binary = root / 'legacy-intent'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True, timeout=8)

    def test_startup_migration_and_saved_context_without_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            swift = root / 'Startup.swift'
            swift.write_text('''import Foundation
@main struct StartupTest {
  @MainActor static func main() {
    let suite = "test.reprise.startup." + UUID().uuidString
    let d = UserDefaults(suiteName: suite)!
    defer { d.removePersistentDomain(forName: suite) }
    d.set(false, forKey: "DesktopLinkedStartup")
    StartupPreferences.migrate(d)
    precondition(d.bool(forKey: "DesktopLinkedStartup") && d.bool(forKey: "SharedCodexStartup"))
    d.set(false, forKey: "DesktopLinkedStartup")
    d.set(false, forKey: StartupPreferences.pilotEnabledKey)
    StartupPreferences.migrate(d)
    precondition(!d.bool(forKey: "DesktopLinkedStartup") && !d.bool(forKey: StartupPreferences.pilotEnabledKey))
    let file = URL(fileURLWithPath: CommandLine.arguments[1])
    let p = ApplePilotUI(python: file, service: file, state: file, preferences: d)
    precondition(!p.enabled && p.contextNeedsConfiguration && !p.hostRunning)
    p.enabled = true
    p.projectID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    p.workID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    p.checkpointID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    p.skill = "project-summary"
    precondition(!p.canOpen)
    p.updateKnownProjects([p.projectID])
    precondition(p.canOpen)
    let q = ApplePilotUI(python: file, service: file, state: file, preferences: d)
    precondition(q.enabled && q.projectID == p.projectID && q.workID == p.workID && q.checkpointID == p.checkpointID && q.skill == p.skill)
    precondition(q.contextNeedsConfiguration && !q.hostRunning && q.prompt.isEmpty)
    q.updateKnownProjects([])
    precondition(!q.canOpen)
    q.updateKnownProjects([q.projectID])
    precondition(q.canOpen)
    q.checkpointID = "partial-uuid"
    let incomplete = ApplePilotUI(python: file, service: file, state: file, preferences: d)
    incomplete.updateKnownProjects([incomplete.projectID])
    precondition(incomplete.checkpointID == "partial-uuid" && !incomplete.canOpen)
    precondition(incomplete.contextConfigurationMessage == "Corrigez ou effacez l’identifiant du checkpoint.")
    incomplete.openRestoredSessionIfReady()
    precondition(!incomplete.hostRunning)
    incomplete.checkpointID = ""
    let cleared = ApplePilotUI(python: file, service: file, state: file, preferences: d)
    cleared.updateKnownProjects([cleared.projectID])
    precondition(cleared.checkpointID.isEmpty && cleared.canOpen)
    q.workID = "invalid"
    precondition(d.string(forKey: "ApplePilotWork") == nil)
    let invalid = ApplePilotUI(python: file, service: file, state: file, preferences: d)
    precondition(invalid.workID.isEmpty && !invalid.canOpen)
    d.set("invalid", forKey: "ApplePilotWork")
    d.set("unknown", forKey: "ApplePilotSkill")
    let r = ApplePilotUI(python: file, service: file, state: file, preferences: d)
    r.updateKnownProjects([r.projectID])
    precondition(r.workID.isEmpty && r.skill == "checkpoint-review" && !r.canOpen && !r.hostRunning)
    let projectA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", projectB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    q.enabled = true; q.projectID = projectA; q.newPreparation()
    let operationA = q.preparationOperationID
    q.proposalObjective = "secret A"
    q.projectID = projectB
    precondition(q.preparationOperationID.isEmpty && q.proposalObjective.isEmpty)
    q.newPreparation(); let operationB = q.preparationOperationID
    precondition(!operationB.isEmpty && operationB != operationA)
    q.projectID = projectA
    precondition(q.preparationOperationID == operationA && q.proposalObjective.isEmpty)
    q.projectID = projectB
    precondition(q.preparationOperationID == operationB && q.proposalObjective.isEmpty)
    precondition(!FileManager.default.fileExists(atPath: CommandLine.arguments[1] + "/apple-pilot-intents.noindex"))
  }
}
''')
            binary = root / 'startup-test'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), str(root)], check=True)

    def test_restored_context_starts_once_without_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / 'service.py'
            fake.write_text("""import json, pathlib, sys
p = pathlib.Path(sys.argv[sys.argv.index('--state')+1])
for line in sys.stdin:
 x = json.loads(line)
 with (p / 'ops').open('a') as f: f.write(x['op']+'\\n')
 print(json.dumps({'id':x['id'], 'status':'opened' if x['op']=='open' else 'closed'}), flush=True)
""")
            swift = root / 'StartupOpen.swift'
            swift.write_text('''import Foundation
@main struct StartupOpen {
 @MainActor static func main() async {
  let d = UserDefaults(suiteName: "test.open." + UUID().uuidString)!
  StartupPreferences.migrate(d)
  let a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
  d.set(a, forKey: "ApplePilotProject"); d.set(b, forKey: "ApplePilotWork")
  let args = CommandLine.arguments
  let p = ApplePilotUI(python: URL(fileURLWithPath: args[1]), service: URL(fileURLWithPath: args[2]), state: URL(fileURLWithPath: args[3]), preferences: d)
  p.updateKnownProjects([]); p.openRestoredSessionIfReady()
  precondition(!p.hostRunning)
  p.updateKnownProjects([a]); p.enabled = false; p.openRestoredSessionIfReady()
  precondition(!p.hostRunning)
  p.enabled = true; p.openRestoredSessionIfReady()
  for _ in 0..<100 { if p.sessionOpen { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  precondition(p.sessionOpen, p.status)
  p.close()
  for _ in 0..<100 { if !p.hostRunning { break }; try? await Task.sleep(nanoseconds: 20_000_000) }
  p.openRestoredSessionIfReady()
  precondition(!p.hostRunning && !p.sessionOpen)
 }
}
''')
            binary = root / 'startup-open'
            subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(root / 'cache'),
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)], check=True)
            subprocess.run([str(binary), '/usr/bin/python3', str(fake), str(root)], check=True)
            self.assertEqual((root / 'ops').read_text().splitlines(), ['open', 'close'])

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
                            str(ROOT / 'swift' / 'ApplePilotUI.swift'), str(swift), '-o', str(binary)],
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
