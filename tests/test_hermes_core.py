"""Offline contracts; real I/O only inside TemporaryDirectory."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nexus.bridge.models import TaskCreateRequest
from nexus.bridge.storage import TaskStorage
from nexus.bridge.tasks import TaskManager
from nexus.integrations import hermes_client as client


class CoreTests(unittest.TestCase):
    def test_scoped_arguments(self):
        client.validate_task('create_file', {'path':client.ARTIFACT_ROOT+'/report.md','content':'hello'})
        client.validate_task('python_run_project_script', {'script':client.PROBE_SCRIPT})
        for task, params in [
            ('create_file',{'path':'nexus/bridge/tasks.py','content':'x'}),
            ('create_file',{'path':client.ARTIFACT_ROOT+'/code.py','content':'x'}),
            ('create_file',{'path':client.ARTIFACT_ROOT+'/../escape.md','content':'x'}),
            ('create_file',{'path':client.ARTIFACT_ROOT+'/file.md:stream','content':'x'}),
            ('create_file',{'path':client.ARTIFACT_ROOT+'/file.md','content':'x','overwrite':True}),
            ('write_file',{'path':client.ARTIFACT_ROOT+'/file.md','content':'x'}),
            ('python_run_project_script',{'script':client.PROBE_SCRIPT,'args':['x']}),
            ('python_run_project_script',{'script':'workspace/scripts/other.py'}),
            ('powershell_registered_action',{'action':'echo_host'}),
            ('read_file',{'path':'workspace/bridge/.token'}),
        ]:
            with self.subTest(task=task,params=params), self.assertRaises(client.IntegrationError):
                client.validate_task(task,params)

    def test_identity_and_failure_survive_persistence_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            manager=TaskManager(TaskStorage(Path(directory)))
            def fail(params, rec):
                return {'available':True,'returncode':17,'stdout':'output','stderr':'error'}
            manager.register('git_status', fail)
            first=manager.create_and_run(TaskCreateRequest(task='git_status'))
            restored=TaskStorage(Path(directory)).get(first.id)
            self.assertEqual(restored.status,'failed')
            self.assertEqual(restored.result['returncode'],17)
            self.assertEqual(restored.execution, first.execution)
            self.assertGreater(first.execution['pid'],0)
            self.assertTrue(first.execution['host'])
            self.assertTrue(first.execution['executor'].endswith('.fail'))
            another=TaskManager(TaskStorage(Path(directory)))
            another.register('git_status', fail)
            self.assertNotEqual(another.create_and_run(TaskCreateRequest(task='git_status')).id,first.id)

    def test_client_keeps_failure_and_streams(self):
        import uuid
        correlation=str(uuid.uuid4())
        record={'id':'NX-20261007-9999','task':'git_status','params':{},'source':'hermes-core:'+correlation,
                'client':'hermes-core','permission':'READ_ONLY','status':'failed','error_code':'TASK_FAILED',
                'result':{'available':True,'returncode':128,'stdout':'out','stderr':'err'},
                'execution':{'host':'test','pid':1,'instance_id':str(uuid.uuid4()),'executor':'test.git'}}
        instance=client.NexusClient()
        with patch.object(instance,'_request',side_effect=[(400,{'task':record}),(200,{'task':record})]):
            result=instance.submit('git_status',{},correlation)
        self.assertFalse(result['success'])
        self.assertEqual((result['exit_code'],result['stdout'],result['stderr']),(128,'out','err'))
        self.assertEqual(result['execution_instance'],record['execution'])
        self.assertTrue(result['task_readback_verified'])

    def test_no_retry_after_uncertain_post(self):
        with patch.object(client.NexusClient,'_request',side_effect=client.IntegrationError('TRANSPORT_UNAVAILABLE')) as request:
            result=client.invoke({'operation':'submit','task':'ping','params':{}})
        self.assertEqual(request.call_count,1)
        self.assertFalse(result['retry_safe'])
        self.assertEqual(result['status'],'verification_unknown')

    def test_readback_mismatch_never_succeeds(self):
        import uuid
        correlation=str(uuid.uuid4())
        record={'id':'NX-20261007-9999','task':'ping','params':{}}
        instance=client.NexusClient()
        with patch.object(instance,'_request',side_effect=[(200,{'task':record}),(200,{'task':{**record,'status':'failed'}})]), self.assertRaises(client.IntegrationError):
            instance.submit('ping',{},correlation)


if __name__=='__main__':
    unittest.main()
