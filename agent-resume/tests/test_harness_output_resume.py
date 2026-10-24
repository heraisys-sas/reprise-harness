"""Run the CommonJS plugin against the public native event/agent interface."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which('node') or '/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node'
PLUGIN = Path(__file__).resolve().parents[1] / 'harness-output-resume.cjs'


@unittest.skipUnless(Path(NODE).is_file(), 'Node runtime unavailable')
class HarnessOutputResumeTests(unittest.TestCase):
    def test_native_lifecycle_and_durable_claims(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const plugin = require(process.argv[1]);
const root = fs.realpathSync(process.argv[2]);
const db = new DatabaseSync(path.join(root, 'tasks.sqlite'));
db.exec("CREATE TABLE scheduler_meta(key TEXT PRIMARY KEY,value TEXT); INSERT INTO scheduler_meta VALUES('auto_enabled','true')");
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup(name, extra={}) {
  const handlers = new Map();
  const session = {id:name,header:{cwd:root,origin:'user'}, events:[], snapshotEvents(){return this.events.slice();}};
  const agent = {session,status:'idle', inbox:{hasPending:false,nextStep:[],nextTurn:[]}, sent:[], followup(message){this.sent.push(message);}};
  const ctx = {agents:{get(id){return id===session.id?agent:undefined;},roots(){return [agent];}},sessions:{},workspaceRegistry:{archivedSessionIds:[]},on(name,fn){handlers.set(name,fn);},effect(fn){this.cleanup=fn();}};
  plugin.apply(ctx,{enabled:true,stateDirectory:path.join(root,'harness-output-resume'),projectRoot:root,...extra});
  const emit = (kind='max-tokens') => {
    const event = {type:'turn/end',seq:session.events.length,data:{reason:{kind}}};
    session.events.push(event); handlers.get('session/event')?.(session,event); return event;
  };
  return {ctx,agent,session,handlers,emit};
}
(async()=>{
  let s=setup('success');const e=s.emit();await tick();assert.equal(s.agent.sent.length,1);
  assert.equal(s.agent.sent[0].role,'user');assert.equal(s.agent.sent[0].source.kind,'user');
  s.handlers.get('session/event')(s.session,e);await tick();assert.equal(s.agent.sent.length,1);
  let restart=setup('success');restart.session.events=[e];restart.handlers.get('session/event')(restart.session,e);await tick();assert.equal(restart.agent.sent.length,0);
  const cold=setup('cold');cold.session.events=[e];await tick();assert.equal(cold.agent.sent.length,0);
  s=setup('active');s.agent.status='running';s.emit();await tick();assert.equal(s.agent.sent.length,0);s.agent.status='idle';s.handlers.get('agent/status')({agent:s.agent,status:'idle'});await tick();assert.equal(s.agent.sent.length,1);
  s=setup('manual');s.emit();const user={type:'user/message',seq:1,data:{source:{kind:'user'}}};s.session.events.push(user);s.handlers.get('session/event')(s.session,user);await tick();assert.equal(s.agent.sent.length,0);
  for(const kind of ['stop','aborted','quota','auth','completed']) {s=setup(kind);s.emit(kind);await tick();assert.equal(s.agent.sent.length,0);}
  for(const mode of ['inbox','subagent','archive','ask']) {
    s=setup(mode);if(mode==='inbox')s.agent.inbox.hasPending=true;
    if(mode==='subagent')s.session.header.origin='subagent';
    if(mode==='archive')s.ctx.workspaceRegistry.archivedSessionIds.push(mode);
    if(mode==='ask')s.session.events.push({type:'tool/call',seq:0,data:{callId:'pending',name:'ask_user'}});
    s.emit();await tick();assert.equal(s.agent.sent.length,0);
  }
  s=setup('streak');for(let i=0;i<3;i++){s.emit();await tick();}assert.equal(s.agent.sent.length,2);
  s=setup('ambiguous');s.agent.followup=()=>{throw Error('private content');};s.emit();await tick();
  const receipts=fs.readdirSync(path.join(root,'harness-output-resume')).map(n=>JSON.parse(fs.readFileSync(path.join(root,'harness-output-resume',n))));assert(receipts.some(r=>r.status==='uncertain'));assert(!JSON.stringify(receipts).includes('private content'));
  db.exec("UPDATE scheduler_meta SET value='false'");s=setup('paused');s.emit();await tick();assert.equal(s.agent.sent.length,0);db.exec("UPDATE scheduler_meta SET value='true'");
  s=setup('claimfail');fs.rmSync(path.join(root,'harness-output-resume'),{recursive:true});fs.writeFileSync(path.join(root,'harness-output-resume'),'blocked');s.emit();await tick();assert.equal(s.agent.sent.length,0);
  db.close();
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([NODE, '-e', script, str(PLUGIN), directory], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
