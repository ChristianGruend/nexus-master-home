"""Offline contract tests: no live LLM and no real Bridge writes."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nexus.common.ollama_client import OllamaClient
from nexus.integrations.hermes_brain import HermesBrain

REQ=[{'path':'workspace/projects/hermes/contract-test.txt','content':'exact ü\n'}]
TASK='Create the specified file, read it and compare exact content.'

def answer(name=None, args=None):
    calls=[] if name is None else [{'function':{'name':name,'arguments':args}}]
    return {'success':True,'model':'offline-test','reply':'done' if not calls else '',
            'message':{'role':'assistant','content':'done' if not calls else '', 'tool_calls':calls},'tool_calls':calls}

class Model:
    def __init__(self, responses): self.responses=list(responses); self.requests=[]
    def chat(self, messages, **kwargs):
        self.requests.append(copy.deepcopy({'messages':messages,**kwargs}))
        return self.responses.pop(0)

class Bridge:
    def __init__(self, root=None): self.calls=[]; self.records={}; self.content=REQ[0]['content']; self.root=root
    def __call__(self, args):
        self.calls.append(copy.deepcopy(args))
        if args['operation']=='status': return copy.deepcopy(self.records[args['task_id']])
        if args.get('task')=='create_file' and self.root:
            target=self.root/args['params']['path']; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(args['params']['content'].encode('utf-8'))
        tid='NX-20261009-'+str(len(self.records)+1).zfill(4)
        params=copy.deepcopy(args['params'])
        payload=({'created':True,'path':params['path'],'bytes':len(params['content'].encode('utf-8'))}
                 if args['task']=='create_file' else
                 {'path':params['path'],'content':self.content,'size':len(self.content.encode('utf-8')),
                  'truncated':False,'encoding':'utf-8'})
        r={'schema':'nexus.hermes-result.v1','success':True,'status':'succeeded',
           'backend_status':'succeeded','error_code':None,'task':args['task'],'params':params,
           'task_id':tid,'correlation_id':args['correlation_id'],
           'executed_by':'nexus_executor','requested_by':'hermes','transport':'REST','http_status':200,
           'execution_instance':{'host':'offline-test','pid':1,
                                 'instance_id':'00000000-0000-0000-0000-000000000001',
                                 'executor':'offline.'+args['task']},
           'task_readback_verified':True,'execution_instance_verified':True,'result':payload,
           'verification':'succeeded','request_sent':True,'retry_safe':False}
        self.records[tid]=r
        return copy.deepcopy(r)

class StructuredBrainTests(unittest.TestCase):
    def setUp(self):
        temporary_root=Path(__file__).resolve().parents[1]/'workspace/tmp/hermes_brain_tests'
        temporary_root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=temporary_root)
        self.base=Path(self.temp.name)/'brain.json'
        self.master=SimpleNamespace(project_fs=SimpleNamespace(root=self.temp.name))
        self.bridge=Bridge(Path(self.temp.name))
        self.patcher=patch('nexus.integrations.hermes_client.invoke',self.bridge)
        self.patcher.start()
    def tearDown(self): self.patcher.stop(); self.temp.cleanup()
    def brain(self,responses):
        b=HermesBrain(self.master,Model(responses)); b._state_path=lambda:self.base
        return b
    def sequence(self): return [answer('create_file',REQ[0]),answer('read_file',{'path':REQ[0]['path']}),answer()]
    def execute(self,b,**kwargs): return b.run(TASK,requirements=REQ,**kwargs)
    def state(self): return json.loads(self.base.read_text(encoding='utf-8'))
    def journal(self):
        fingerprint=hashlib.sha256(json.dumps([TASK,REQ],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return self.base.parent/('hermes-order-'+fingerprint+'.json')
    def write_journal(self,state): self.journal().write_text(json.dumps(state),encoding='utf-8')
    def open_completed_batch(self):
        first=self.execute(self.brain(self.sequence()))
        self.assertEqual(first['status'],'PASS')
        state=json.loads(self.journal().read_text(encoding='utf-8'))
        state.update(status='ACTIVE',batch_open=True,pending=None)
        self.write_journal(state)
        return state
    def assert_blocked_without_execution(self,before_calls):
        brain=self.brain([])
        result=self.execute(brain)
        self.assertFalse(result['success'])
        self.assertNotEqual(result['status'],'PASS')
        self.assertFalse(brain.llm.requests)
        self.assertFalse(any(call.get('operation')=='submit' for call in self.bridge.calls[before_calls:]))
        return result
    def test_complete_contract_and_real_tool_feedback(self):
        b=self.brain(self.sequence()); r=self.execute(b)
        self.assertEqual(r['status'],'PASS'); self.assertEqual(len(r['task_ids']),2)
        self.assertTrue(r['verification'][0]['equal'])
        self.assertEqual(r['verification'][0]['content'],REQ[0]['content'])
        request=b.llm.requests[1]
        self.assertEqual({t['function']['name'] for t in request['tools']},{'create_file','read_file'})
        self.assertEqual(request['messages'][-1]['role'],'tool')
        self.assertEqual(json.loads(request['messages'][-1]['content'])['task_id'],r['task_ids'][0])
    def test_text_only_not_pass(self):
        r=self.execute(self.brain([answer()])); self.assertFalse(r['success']); self.assertFalse(self.bridge.calls)
    def test_write_only_not_pass(self):
        r=self.execute(self.brain([answer('create_file',REQ[0]),answer()])); self.assertFalse(r['success'])
    def test_wrong_content_not_pass(self):
        self.bridge.content='wrong'; r=self.execute(self.brain(self.sequence())); self.assertFalse(r['success'])
    def test_every_requirement_must_be_verified(self):
        r=self.brain(self.sequence()).run(TASK,requirements=REQ+[{'path':'workspace/projects/hermes/second.txt','content':'second'}])
        self.assertFalse(r['success'])
    def test_missing_contract_denies_before_model(self):
        b=self.brain([]); r=b.run(TASK); self.assertEqual(r['error'],'EXPLICIT_FILE_REQUIREMENTS_REQUIRED'); self.assertFalse(b.llm.requests)
    def test_path_escape_denied(self):
        r=self.brain([]).run(TASK,requirements=[{'path':'../outside.txt','content':'x'}]); self.assertFalse(r['success']); self.assertFalse(self.bridge.calls)
    def test_arbitrary_shell_denied(self):
        r=self.execute(self.brain([answer('powershell',{'command':'whoami'})])); self.assertEqual(r['error'],'TOOL_OUTSIDE_AUTHORIZED_CONTRACT'); self.assertFalse(self.bridge.calls)
    def test_wrong_write_content_denied(self):
        r=self.execute(self.brain([answer('create_file',{'path':REQ[0]['path'],'content':'wrong'})])); self.assertFalse(r['success']); self.assertFalse(self.bridge.calls)
    def test_read_before_create_denied(self):
        r=self.execute(self.brain([answer('read_file',{'path':REQ[0]['path']})])); self.assertEqual(r['error'],'READ_BEFORE_CREATE')
    def test_model_error_retains_receipts_and_resume_does_not_write_twice(self):
        r=self.execute(self.brain([answer('create_file',REQ[0]),{'success':False,'error':'timeout'}]))
        self.assertEqual(r['status'],'PARTIAL'); self.assertEqual(len(self.state()['trace']),1)
        b=self.brain([answer('create_file',REQ[0]),answer('read_file',{'path':REQ[0]['path']}),answer()])
        r=self.execute(b); self.assertEqual(r['status'],'PASS')
        self.assertEqual(sum(x.get('task')=='create_file' for x in self.bridge.calls),1)
    def test_completed_order_reuses_verified_receipts_without_model_or_writes(self):
        first=self.execute(self.brain(self.sequence())); b=self.brain([]); second=self.execute(b)
        self.assertEqual(first['run_id'],second['run_id']); self.assertEqual(second['status'],'PASS'); self.assertFalse(b.llm.requests)
        self.assertEqual(sum(x.get('task')=='create_file' for x in self.bridge.calls),1)
    def test_uncertain_submit_never_repeated(self):
        with patch('nexus.integrations.hermes_client.invoke',return_value={'success':False,'error_code':'TRANSPORT_UNAVAILABLE'}):
            r=self.execute(self.brain([answer('create_file',REQ[0])]))
        self.assertFalse(r['success']); self.assertIsNotNone(self.state()['pending'])
        b=self.brain([]); r=self.execute(b)
        self.assertEqual(r['error'],'UNCERTAIN_SUBMIT_REQUIRES_RECONCILIATION'); self.assertFalse(b.llm.requests); self.assertFalse(self.bridge.calls)
    def test_interruption_after_submit_does_not_resubmit(self):
        with patch('nexus.integrations.hermes_client.invoke',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.execute(self.brain([answer('create_file',REQ[0])]))
        self.assertIsNotNone(self.state()['pending'])
        r=self.execute(self.brain([])); self.assertEqual(r['error'],'UNCERTAIN_SUBMIT_REQUIRES_RECONCILIATION')
    def test_persistence_failure_prevents_write(self):
        with patch('nexus.integrations.hermes_brain.os.replace',side_effect=OSError):
            r=self.execute(self.brain(self.sequence()))
        self.assertFalse(r['success']); self.assertFalse(self.bridge.calls)
    def test_corrupt_journal_fails_closed(self):
        self.execute(self.brain([answer()]))
        next(self.base.parent.glob('hermes-order-*.json')).write_text('{')
        r=self.execute(self.brain([])); self.assertFalse(r['success']); self.assertFalse(self.bridge.calls)
    def test_bridge_receipt_recheck_failure_blocks_resume(self):
        self.execute(self.brain([answer('create_file',REQ[0]),{'success':False,'error':'timeout'}]))
        for r in self.bridge.records.values(): r['success']=False
        r=self.execute(self.brain([])); self.assertEqual(r['error'],'RECEIPT_RECHECK_FAILED')
    def test_bridge_does_not_fake_success_without_readback(self):
        with patch('nexus.integrations.hermes_client.invoke',return_value={'success':True}):
            r=self.execute(self.brain([answer('create_file',REQ[0])]))
        self.assertFalse(r['success']); self.assertIsNotNone(self.state()['pending'])
    def test_duplicate_calls_reuse_original_result(self):
        r=self.execute(self.brain([answer('create_file',REQ[0]),answer('create_file',REQ[0]),answer('read_file',{'path':REQ[0]['path']}),answer()]))
        self.assertEqual(r['status'],'PASS'); self.assertEqual(sum(x.get('task')=='create_file' for x in self.bridge.calls),1)
    def test_parallel_calls_are_sequential_and_results_returned(self):
        a=answer('create_file',REQ[0]); a['tool_calls'].extend(answer('read_file',{'path':REQ[0]['path']})['tool_calls'])
        b=self.brain([a,answer()]); r=self.execute(b); self.assertEqual(r['status'],'PASS')
        self.assertEqual([m['role'] for m in b.llm.requests[1]['messages'][-3:]],['assistant','tool','tool'])

    def test_incomplete_batch_does_not_send_again(self):
        self.execute(self.brain([answer()]))
        journal=next(self.base.parent.glob('hermes-order-*.json'))
        state=json.loads(journal.read_text(encoding='utf-8')); state['batch_open']=True
        journal.write_text(json.dumps(state),encoding='utf-8')
        b=self.brain([]); r=self.execute(b)
        self.assertEqual(r['error'],'INCOMPLETE_TOOL_BATCH_REQUIRES_RECONCILIATION'); self.assertFalse(b.llm.requests)
    def test_concurrent_order_is_blocked_by_os_lock(self):
        import os
        with open(self.journal().with_suffix('.lock'),'a+b') as lock:
            lock.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:
                brain=self.brain([])
                r=self.execute(brain)
                self.assertEqual(r['error'],'ORDER_ALREADY_RUNNING')
                self.assertFalse(brain.llm.requests)
                self.assertFalse(self.bridge.calls)
            finally:
                lock.seek(0)
                if os.name=='nt': msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(lock,fcntl.LOCK_UN)


    def test_changed_completed_artifact_is_not_rewritten(self):
        self.execute(self.brain(self.sequence()))
        target=Path(self.temp.name)/REQ[0]['path']
        target.write_text('changed externally')
        before=len(self.bridge.calls)
        result=self.execute(self.brain([]))
        self.assertEqual(result['error'],'COMPLETED_ARTIFACT_CHANGED_NO_REWRITE')
        self.assertEqual(target.read_text(),'changed externally')
        self.assertFalse(any(c.get('operation')=='submit' for c in self.bridge.calls[before:]))

    def test_open_batch_refreshes_receipts_before_completed_result(self):
        state=self.open_completed_batch()
        self.bridge.records[state['trace'][0]['result']['task_id']]['success']=False
        before=len(self.bridge.calls)
        self.assert_blocked_without_execution(before)
        self.assertTrue(any(call.get('operation')=='status' for call in self.bridge.calls[before:]))

    def test_open_batch_checks_current_artifact_without_rewriting(self):
        self.open_completed_batch()
        target=Path(self.temp.name)/REQ[0]['path']
        target.write_bytes(b'changed after interruption')
        before=len(self.bridge.calls)
        self.assert_blocked_without_execution(before)
        self.assertEqual(target.read_bytes(),b'changed after interruption')

    def test_open_batch_missing_artifact_is_not_recreated(self):
        self.open_completed_batch()
        target=Path(self.temp.name)/REQ[0]['path']
        retained=target.with_name('retained-original.txt')
        target.rename(retained)
        before=len(self.bridge.calls)
        self.assert_blocked_without_execution(before)
        self.assertFalse(target.exists())
        self.assertEqual(retained.read_bytes(),REQ[0]['content'].encode('utf-8'))

    def test_open_batch_rejects_cached_receipt_bound_to_other_action_or_params(self):
        original=self.open_completed_batch()
        for mismatch in ('task','params','task_id','correlation_id'):
            with self.subTest(mismatch=mismatch):
                state=copy.deepcopy(original)
                cached=state['trace'][0]['result']
                if mismatch=='task': cached['task']='read_file'
                elif mismatch=='params': cached['params']={'path':'workspace/projects/hermes/other.txt','content':'other'}
                elif mismatch=='task_id': cached['task_id']='NX-20261009-9999'
                else: cached['correlation_id']='00000000-0000-0000-0000-000000000099'
                self.write_journal(state)
                self.assert_blocked_without_execution(len(self.bridge.calls))

    def test_completed_resume_rejects_changed_receipt_identity(self):
        first=self.execute(self.brain(self.sequence()))
        original=json.loads(self.journal().read_text(encoding='utf-8'))
        original_records=copy.deepcopy(self.bridge.records)
        for mismatch in ('task','params','task_id','correlation_id','execution_instance'):
            with self.subTest(mismatch=mismatch):
                self.write_journal(copy.deepcopy(original))
                self.bridge.records=copy.deepcopy(original_records)
                fresh=self.bridge.records[first['task_ids'][0]]
                if mismatch=='task': fresh['task']='read_file'
                elif mismatch=='params': fresh['params']={'path':'workspace/projects/hermes/other.txt','content':'other'}
                elif mismatch=='task_id': fresh['task_id']='NX-20261009-9999'
                elif mismatch=='correlation_id': fresh['correlation_id']='00000000-0000-0000-0000-000000000099'
                else: fresh['execution_instance']['instance_id']='00000000-0000-0000-0000-000000000002'
                self.assert_blocked_without_execution(len(self.bridge.calls))

    def test_open_batch_rejects_duplicate_task_ids(self):
        state=self.open_completed_batch()
        state['trace'][1]['result']['task_id']=state['trace'][0]['result']['task_id']
        self.write_journal(state)
        self.assert_blocked_without_execution(len(self.bridge.calls))

    def test_open_batch_rejects_unrelated_extra_tool_receipt(self):
        state=self.open_completed_batch()
        extra=copy.deepcopy(state['trace'][0])
        extra.update(name='ping',args={})
        extra['result'].update(task='ping',params={},task_id='NX-20261009-9999')
        state['trace'].append(extra)
        self.write_journal(state)
        self.assert_blocked_without_execution(len(self.bridge.calls))

    def test_transient_journal_replace_permission_error_is_retried(self):
        import os
        real_replace=os.replace
        failures=[]
        def replace(source,destination):
            if Path(destination)==self.journal() and not failures:
                failures.append(True)
                raise PermissionError('synthetic temporary reader lock')
            return real_replace(source,destination)
        with patch('nexus.integrations.hermes_brain.os.replace',side_effect=replace), \
                patch('nexus.common.atomic.time.sleep') as sleep:
            result=self.execute(self.brain(self.sequence()))
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(sleep.call_count,1)
        self.assertEqual(sum(call.get('task')=='create_file' for call in self.bridge.calls),1)

    def test_persistent_journal_replace_error_is_bounded_and_preserves_receipts(self):
        from nexus.common.atomic import REPLACE_ATTEMPTS
        self.execute(self.brain(self.sequence()))
        before_bytes=self.journal().read_bytes()
        before_calls=len(self.bridge.calls)
        with patch('nexus.integrations.hermes_brain.os.replace',side_effect=PermissionError('synthetic permanent reader lock')) as replace, \
                patch('nexus.common.atomic.time.sleep') as sleep:
            result=self.execute(self.brain([]))
        self.assertFalse(result['success'])
        self.assertEqual(replace.call_count,REPLACE_ATTEMPTS)
        self.assertEqual(sleep.call_count,REPLACE_ATTEMPTS-1)
        self.assertEqual(self.journal().read_bytes(),before_bytes)
        self.assertEqual(len(self.bridge.calls),before_calls)

    def test_malformed_journal_shapes_fail_closed_without_overwriting(self):
        self.execute(self.brain(self.sequence()))
        original=json.loads(self.journal().read_text(encoding='utf-8'))
        for shape in ('top_list','trace_null','trace_dict','trace_string',
                      'entry_null','entry_string','result_null','result_string'):
            with self.subTest(shape=shape):
                state=copy.deepcopy(original)
                if shape=='top_list': state=[]
                elif shape=='trace_null': state['trace']=None
                elif shape=='trace_dict': state['trace']={}
                elif shape=='trace_string': state['trace']='invalid trace'
                elif shape=='entry_null': state['trace'][0]=None
                elif shape=='entry_string': state['trace'][0]='invalid entry'
                elif shape=='result_null': state['trace'][0]['result']=None
                else: state['trace'][0]['result']='invalid result'
                self.write_journal(state)
                before=self.journal().read_bytes()
                self.assert_blocked_without_execution(len(self.bridge.calls))
                self.assertEqual(self.journal().read_bytes(),before,
                                 'Invalid journal must remain available for diagnosis')

    def test_falsey_pending_marker_does_not_authorize_new_write(self):
        self.execute(self.brain([answer()]))
        state=json.loads(self.journal().read_text(encoding='utf-8'))
        self.assertEqual(state['trace'],[])
        state['pending']={}
        self.write_journal(state)
        before=len(self.bridge.calls)
        brain=self.brain(self.sequence())
        result=self.execute(brain)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'],'UNCERTAIN_SUBMIT_REQUIRES_RECONCILIATION')
        self.assertFalse(brain.llm.requests)
        self.assertFalse(any(call.get('operation')=='submit' for call in self.bridge.calls[before:]))
        self.assertEqual(json.loads(self.journal().read_text(encoding='utf-8'))['pending'],{})

class OllamaWireTests(unittest.TestCase):
    def test_tools_sent_and_structured_message_preserved(self):
        c=OllamaClient(); tool={'type':'function','function':{'name':'read_file'}}
        message=answer('read_file',{'path':'x'})['message']
        with patch.object(c,'_req',return_value=(True,{'message':message,'done':True})) as req:
            r=c.chat([{'role':'user','content':'read'}],model='qwen2.5:7b',tools=[tool])
        self.assertEqual(req.call_args.args[2]['tools'],[tool]); self.assertEqual(r['message'],message); self.assertEqual(r['tool_calls'],message['tool_calls'])
    def test_ordinary_chat_without_tools_still_works(self):
        c=OllamaClient()
        with patch.object(c,'_req',return_value=(True,{'message':{'role':'assistant','content':'hello'},'done':True})) as req:
            r=c.chat([],model='qwen2.5:7b')
        self.assertNotIn('tools',req.call_args.args[2]); self.assertEqual(r['reply'],'hello')

if __name__=='__main__': unittest.main()
