import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]


class BrowserUITests(unittest.TestCase):
    def test_delayed_selection_responses_and_new_durable_intent_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); state = root/'state'; state.mkdir(mode=0o700)
            service = root/'service.py'
            service.write_text(r'''import sys,json,time
from pathlib import Path
state=Path(sys.argv[2])
for line in sys.stdin:
 x=json.loads(line);op=x['op']
 with (state/'ops').open('a') as f:f.write(op+'\n')
 if op=='close':
  print(json.dumps({'id':x['id'],'status':'closed'}),flush=True);break
 time.sleep(.2)
 if op=='job_list':
  row={'project_id':x['project_id'],'operation_id':'cccccccc-cccc-4ccc-8ccc-cccccccccccc','job_id':'dddddddd-dddd-4ddd-8ddd-dddddddddddd','objective':'Résumé existant','status':'preparation','revision':0,'created_at':1,'updated_at':1,'kind':'mission','work_association':'bound','work_id':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','selectable':True}
  out={'page':{'project_id':x['project_id'],'work_id':x['work_id'],'offset':x['offset'],'limit':x['limit'],'next_offset':None,'operations':[row]}}
 elif op=='job_recover':
  out={'job':{'project_id':x['project_id'],'operation_id':x['operation_id'],'status':'preparation','revision':0,'proposal':{'objective':'Réponse retardée','acceptance':['Texte'],'constraints':[],'questions':[]},'authority':None,'request_key':None}}
 else:raise AssertionError(op)
 print(json.dumps({'id':x['id'],'status':'finished',**out}),flush=True)
''')
            swift = root/'Probe.swift'
            swift.write_text(r'''import Foundation
@main struct Probe {
 @MainActor static func main() async throws {
  let a=CommandLine.arguments,state=URL(fileURLWithPath:a[2])
  let defaults=UserDefaults(suiteName:"browser.races."+UUID().uuidString)!
  defaults.set(1,forKey:StartupPreferences.versionKey)
  let p=ApplePilotUI(python:URL(fileURLWithPath:"/usr/bin/python3"),service:URL(fileURLWithPath:a[1]),state:state,requestTimeout:2,preferences:defaults)
  let project="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",work="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",other="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
  p.enabled=true;p.projectID=project;p.workID=work
  p.browseOperations();p.workID=other;p.workID=work
  try await Task.sleep(nanoseconds:400_000_000)
  precondition(p.operationRows.isEmpty,"A→B→A must reject old list")
  p.browseOperations()
  for _ in 0..<100{if !p.operationBrowserBusy{break};try await Task.sleep(nanoseconds:20_000_000)}
  precondition(p.operationRows.count==1,p.operationBrowserStatus)
  p.selectedExistingOperation="cccccccc-cccc-4ccc-8ccc-cccccccccccc";p.openExistingOperation()
  p.workID=other;p.workID=work
  try await Task.sleep(nanoseconds:400_000_000)
  precondition(p.preparationJob.isEmpty,"old recovery cannot replace current context")
  p.browseOperations()
  for _ in 0..<100{if !p.operationBrowserBusy{break};try await Task.sleep(nanoseconds:20_000_000)}
  p.selectedExistingOperation="cccccccc-cccc-4ccc-8ccc-cccccccccccc"
  let journal=PreparationIntentJournal(state:state)
  let marker=PreparationIntent(project:project,operation:"ffffffff-ffff-4fff-8fff-ffffffffffff",kind:"prepare",revision:0,fingerprint:String(repeating:"a",count:64))
  try journal.write(marker)
  p.openExistingOperation()
  precondition(p.preparationUncertain,"new durable intent must prevent browser switch")
  precondition(try journal.read(project:project)==marker)
  precondition(p.preparationOperationID==marker.operation)
  p.close()
 }
}
''')
            # Swift precondition's autoclosure cannot throw.
            swift.write_text(swift.read_text().replace('precondition(try journal.read(project:project)==marker)', 'let stored=try journal.read(project:project);precondition(stored==marker)'))
            # Same-file probe accesses private journal types without changing product visibility.
            swift.write_text((ROOT/'swift/ApplePilotUI.swift').read_text() + '\n' + swift.read_text())
            binary = root/'probe'
            subprocess.run(['swiftc','-parse-as-library','-module-cache-path',str(root/'cache'),str(swift),'-o',str(binary)],check=True)
            subprocess.run([str(binary),str(service),str(state)],check=True,timeout=10)
            ops=(state/'ops').read_text().splitlines()
            self.assertEqual(ops.count('job_list'),3)
            self.assertEqual(ops.count('job_recover'),1)
            self.assertTrue(set(ops)<={'job_list','job_recover','close'})
