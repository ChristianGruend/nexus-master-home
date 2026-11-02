"""Offline regression tests for the actual engine route, evidence and memory boundaries."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from nexus.master import evidence as ev
from nexus.chat import engine as eng
from nexus.memory import store as mem
from nexus.memory.self_improve import SelfImproveLoop
from nexus.integrations.hermes_brain import HermesBrain

class EvidenceTests(unittest.TestCase):
    def test_fake_ids_and_unproven_actions_are_blocked(self):
        for reply in ['Task-IDs: 123456, 789012, 345678, 901234','Task_ID: NX-1234567890','PASS: Datei geschrieben','Niemandem von der Existenz von NEXUS erzählen']:
            with self.subTest(reply=reply):
                r=ev.guard_output({'success':True,'intent':'chat','reply':reply})
                self.assertFalse(r['success']); self.assertEqual(r['data']['task_ids'],[])
                self.assertNotIn('123456',r['reply']); self.assertNotIn('Niemandem',r['reply'])
    def test_plain_conversation_is_not_execution(self):
        r=ev.guard_output({'success':True,'intent':'chat','reply':'Guten Morgen.'})
        self.assertEqual(r['data']['execution_status'],'PLANNED')
        self.assertEqual(r['data']['task_ids'],[])
    def test_read_does_not_prove_creation(self):
        req={'path':'workspace/projects/hermes/a.md','content':'a'}
        r={'status':'PASS','task_ids':['NX-20261009-1000'],'requirements':[req],
           'verification':[{'create_task_id':'NX-20261009-1000','read_task_id':'NX-20261009-1000','equal':True}]}
        with patch.object(ev,'persisted_task',return_value={'id':'NX-20261009-1000','task':'read_file','status':'succeeded','params':{'path':req['path']},'result':{'content':'a','truncated':False}}):
            out=ev._response(r)
        self.assertFalse(out['success'])
    def test_missing_task_record_blocks_pass(self):
        with patch.object(ev,'persisted_task',return_value={}):
            out=ev._response({'status':'PASS','task_ids':['NX-20261009-1000']})
        self.assertFalse(out['success'])
    def test_every_capability_has_separate_evidence_levels(self):
        with tempfile.TemporaryDirectory() as tmp, patch('nexus.integrations.hermes_client.invoke',return_value={'success':False}):
            snapshot=ev.capability_snapshot(Path(tmp))
        self.assertEqual(len(snapshot['capabilities']),14)
        for row in snapshot['capabilities']:
            self.assertTrue({'registered','reachable','tested','ready','provenance'}<=set(row)); self.assertFalse(row['ready'])
        self.assertIn('UNKNOWN',ev.capability_text(snapshot))
    def test_report_does_not_include_unsubstantiated_directives(self):
        with patch('nexus.integrations.hermes_client.invoke',return_value={'success':False}): text=ev.development_report()
        self.assertNotIn('Niemandem',text); self.assertNotIn('123456',text)
        self.assertIn('Modellannahmen: nicht',text)
    def test_productive_self_modification_is_blocked(self):
        s=object.__new__(SelfImproveLoop)
        self.assertEqual(s._apply_safe([{'id':'X'}]),['activation_skip:LOCAL_REVIEW_AND_APPROVAL_REQUIRED'])

class RouteTests(unittest.TestCase):
    def setUp(self): self.master=SimpleNamespace(); self.session={}
    def test_normal_german_report_routes_to_existing_brain(self):
        with patch.object(ev,'development_report',return_value='known facts'), patch('nexus.integrations.hermes_brain.HermesBrain.run',return_value={'status':'BLOCKED','task_ids':[]}) as run:
            ev.route_work(self.master,'Erstelle die neue Markdown-Datei bericht.md mit einem sachlichen Entwicklungsbericht. Lies sie zurück.',[],self.session)
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.kwargs['requirements'],[{'path':'workspace/projects/hermes/bericht.md','content':'known facts'}])
        self.assertIn('hermes_order',self.session)
    def test_explicit_content_is_exact(self):
        with patch('nexus.integrations.hermes_brain.HermesBrain.run',return_value={'status':'BLOCKED','task_ids':[]}) as run:
            ev.route_work(self.master,'Erstelle die Datei test.txt mit Inhalt: "exakt ü" und lies sie zurück.',[],self.session)
        self.assertEqual(run.call_args.kwargs['requirements'][0]['content'],'exakt ü')
    def test_unauthorized_path_never_runs(self):
        with patch('nexus.integrations.hermes_brain.HermesBrain.run') as run:
            out=ev.route_work(self.master,'Erstelle die Datei C:/Windows/secret.txt mit Inhalt: "x"',[],self.session)
        self.assertFalse(out['success']); run.assert_not_called()
    def test_missing_content_never_inferred(self):
        with patch('nexus.integrations.hermes_brain.HermesBrain.run') as run:
            out=ev.route_work(self.master,'Erstelle die Datei test.txt.',[],self.session)
        self.assertEqual(out['error'],'EXACT_CONTENT_REQUIRED'); run.assert_not_called()
    def test_resume_reuses_exact_requirements(self):
        self.session['hermes_order']={'task':'original','requirements':[{'path':'workspace/projects/hermes/a.txt','content':'stable'}]}
        with patch('nexus.integrations.hermes_brain.HermesBrain.run',return_value={'status':'BLOCKED','task_ids':[]}) as run:
            ev.route_work(self.master,'weiter',[],self.session)
        self.assertEqual(run.call_args.args[0],'original'); self.assertEqual(run.call_args.kwargs['requirements'][0]['content'],'stable')
    def test_prose_about_writing_does_not_execute(self):
        with patch('nexus.integrations.hermes_brain.HermesBrain.run') as run:
            out=ev.route_work(self.master,'Erkläre wie ich eine Datei test.txt schreibe.',[],self.session)
        run.assert_not_called()

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.history=Path(self.temp.name)/'conversations.json'
        self.patchers=[patch.object(eng,'HISTORY_FILE',self.history),patch.object(eng,'ACTIVE_FILE',Path(self.temp.name)/'active.json'),patch.object(eng,'ensure_dirs'),patch.object(eng.ai_layer,'apply_to_client'),patch.object(eng.ai_layer,'resolve_chat_model',return_value='test')]
        for p in self.patchers:p.start()
        self.master=SimpleNamespace(llm=SimpleNamespace(),projects=SimpleNamespace(set_active=lambda x:None))
        with patch.object(eng,'MasterController',return_value=self.master): self.engine=eng.NexusEngine()
    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.temp.cleanup()
    def test_real_engine_stream_path_uses_route_and_persists_evidence(self):
        value={'intent':'hermes_work','success':False,'reply':'BLOCKED','data':{'execution_status':'BLOCKED','task_ids':[]}}
        with patch('nexus.master.evidence.route_work',return_value=value) as route:
            events=list(self.engine.chat_stream('Erstelle eine Datei'))
        self.assertEqual(route.call_count,1); self.assertEqual(events[-1]['type'],'done')
        persisted=json.loads(self.history.read_text(encoding='utf-8'))
        last=persisted['sessions'][self.engine.session_id]['messages'][-1]
        self.assertEqual(last['data']['execution_status'],'BLOCKED')
    def test_unverified_stream_tokens_are_not_exposed(self):
        self.master.handle_stream=lambda *a:iter([{'type':'token','text':'Task-ID: 123456'},{'type':'done','intent':'chat','success':True,'reply':'Task-ID: 123456'}])
        with patch('nexus.master.evidence.route_work',return_value=None): events=list(self.engine.chat_stream('Hallo'))
        self.assertEqual(len(events),1); self.assertNotIn('123456',events[0]['reply']); self.assertFalse(events[0]['success'])
    def test_corrupt_history_is_not_silently_overwritten(self):
        self.history.write_text('{')
        with self.assertRaisesRegex(RuntimeError,'CHAT_HISTORY_UNREADABLE'): self.engine._load_sessions()
        self.assertEqual(self.history.read_text(),'{')

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.patchers=[patch.object(mem,'STORE',self.root/'memories.json'),patch.object(mem,'ROOT',self.root),patch.object(mem,'ensure_dirs')]
        for p in self.patchers:p.start()
        self.store=mem.MemoryStore()
    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.temp.cleanup()
    def test_restart_recall_and_correction_revision(self):
        self.store.merk('alte Aussage')
        other=mem.MemoryStore(); item=other._data[0]
        other.correct(item['id'],'korrigierte Aussage')
        third=mem.MemoryStore(); self.assertEqual(third._data[0]['text'],'korrigierte Aussage')
        self.assertEqual(third._data[0]['revisions'][0]['text'],'alte Aussage')
        self.assertIn('NICHT unabhängig',third.context_snippets()); self.assertNotIn('Bekannte Fakten',third.context_snippets())
    def test_corrupt_memory_is_preserved(self):
        mem.STORE.write_text('{'); self.store.merk('should not overwrite')
        self.assertEqual(mem.STORE.read_text(),'{')
    def test_prior_execution_and_error_are_searchable(self):
        d=self.root/'workspace/state'; d.mkdir(parents=True)
        (d/'hermes-order-test.json').write_text(json.dumps({'contract':'hermes.structured.v1','run_id':'BRAIN-test','task':'Datei Bericht','status':'PARTIAL','error':'timeout','trace':[]}))
        items=self.store._experiences('timeout',10)
        self.assertEqual(items[0]['provenance'],'execution_journal'); self.assertEqual(items[0]['recorded_status'],'PARTIAL')

class RetryTests(unittest.TestCase):
    def test_429_retries_model_only(self):
        class LLM:
            calls=0
            def chat(self,*a,**kw):
                self.calls+=1
                return {'success':False,'error':'HTTP 429'} if self.calls<3 else {'success':True,'reply':'ok'}
        llm=LLM(); b=HermesBrain(SimpleNamespace(),llm)
        with patch('nexus.integrations.hermes_brain.time.sleep'),patch('nexus.integrations.hermes_client.invoke') as invoke:
            r=b._chat([])
        self.assertTrue(r['success']); self.assertEqual(llm.calls,3); invoke.assert_not_called()
    def test_non_retryable_model_error_is_not_repeated(self):
        llm=SimpleNamespace(chat=lambda *a,**k:{'success':False,'error':'HTTP 401'})
        b=HermesBrain(SimpleNamespace(),llm)
        with patch('nexus.integrations.hermes_brain.time.sleep') as sleep: r=b._chat([])
        self.assertFalse(r['success']); sleep.assert_not_called()

if __name__=='__main__': unittest.main()


class DurableBridgeTests(unittest.TestCase):
    def test_persistence_failure_prevents_executor(self):
        from nexus.bridge.storage import TaskStorage
        from nexus.bridge.tasks import TaskManager
        from nexus.bridge.models import TaskCreateRequest
        with tempfile.TemporaryDirectory() as tmp:
            storage=TaskStorage(Path(tmp)); manager=TaskManager(storage)
            executed=[]
            manager.register('ping',lambda *args: executed.append(True) or {})
            with patch.object(storage,'_persist',side_effect=OSError('disk full')):
                with self.assertRaises(OSError): manager.create_and_run(TaskCreateRequest(task='ping'))
            self.assertFalse(executed)
    def test_corrupt_task_record_blocks_id_reuse(self):
        from nexus.bridge.storage import TaskStorage
        with tempfile.TemporaryDirectory() as tmp:
            storage=TaskStorage(Path(tmp)); path=Path(tmp)/'NX-20261009-0001.json'; path.write_text('{')
            with self.assertRaisesRegex(RuntimeError,'TASK_RECORD_UNREADABLE'): storage.get('NX-20261009-0001')
            self.assertEqual(path.read_text(),'{')
    def test_registered_task_survives_new_storage_instance(self):
        from nexus.bridge.storage import TaskStorage
        from nexus.bridge.models import TaskRecord
        with tempfile.TemporaryDirectory() as tmp:
            record=TaskRecord(id='NX-20261009-0001',task='ping',status='running',permission='READ_ONLY',created_at='now',updated_at='now')
            storage=TaskStorage(Path(tmp)); storage.put(record)
            record.status='succeeded'
            self.assertEqual(storage.get(record.id).status,'running')
            self.assertEqual(TaskStorage(Path(tmp)).get(record.id).status,'running')

class AdditionalRouteTests(unittest.TestCase):
    def test_legacy_resume_does_not_repeat_uncorrelated_write(self):
        with patch('nexus.integrations.hermes_brain.HermesBrain.run') as run:
            out=ev.route_work(SimpleNamespace(),'weiter',[],{})
        self.assertEqual(out['error'],'NO_PERSISTED_HERMES_ORDER'); run.assert_not_called()
    def test_explicit_content_beats_report_filename(self):
        with patch('nexus.integrations.hermes_brain.HermesBrain.run',return_value={'status':'BLOCKED','task_ids':[]}) as run:
            ev.route_work(SimpleNamespace(),'Erstelle die Datei arbeitsplan.md mit Inhalt: "exakt"',[],{})
        self.assertEqual(run.call_args.kwargs['requirements'][0]['content'],'exakt')
    def test_skill_registry_does_not_assert_live_readiness(self):
        from nexus.skills.registry import list_skills
        result=list_skills()
        for skill in result.get('skills',result.get('data',{}).get('skills',[])):
            self.assertIsNone(skill['ready']); self.assertIsNone(skill['tested'])
