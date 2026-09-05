"""CPU subprocess controls for the optional no-site ownership bootstrap."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import run_matrix


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        base=run_matrix.ROOT/'results/gold/hf-routing-runner'
        base.mkdir(parents=True,exist_ok=True)
        temp=tempfile.TemporaryDirectory(prefix='.bootstrap-test-',dir=base)
        self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.hooks=self.base/'hooks';self.hooks.mkdir()
        # Only the ordinary bootstrap sees this path. The target uses -I -S.
        (self.hooks/'sitecustomize.py').write_text(
            'import os\nfrom pathlib import Path\n'
            'Path(os.environ["HBFSIM_TEST_HOOK"]).write_text(str(os.getpid()))\n')
        self.target=self.base/'target.py'
        self.target.write_text('import json,os,sys\nfrom pathlib import Path\n'
            'Path(sys.argv[1]).write_text(json.dumps(dict(pid=os.getpid(),no_site=sys.flags.no_site,isolated=sys.flags.isolated)))\n')
        self.calls=[];self.children=[];self.failure=None

    def execute(self,name,**options):
        attempt=self.base/name;attempt.mkdir();self.attempt=attempt
        hook=attempt/'hook';payload=attempt/'payload.json'
        env=dict(os.environ,PYTHONPATH=str(self.hooks),HBFSIM_TEST_HOOK=str(hook))
        test=self
        class Guard:
            child=None;started=False
            def check(self,stage):
                test.calls.append(stage)
                if stage=='start-producer':
                    test.assertFalse(payload.exists())
                    test.assertIsNotNone(self.child)
                    if test.failure=='guard':raise RuntimeError('TEST_ONLY guard refused')
        guard=Guard();self.guard=guard
        def status(child,phase):
            self.children.append(dict(child))
            self.assertEqual(phase,'producer')
            if self.failure=='status':raise RuntimeError('TEST_ONLY identity persistence refused')
        code=run_matrix.run_child([sys.executable,'-I','-S','-B',str(self.target),str(payload)],
            env,attempt,'producer',guard,status,{'signal':None},5,.01,**options)
        return code,hook,payload

    def test_default_startup_compatibility_and_no_site_child_keep_owned_acknowledgement(self):
        for name,options in [('default',{}),('explicit-false',{'bootstrap_no_site':False}),
                             ('no-site',{'bootstrap_no_site':True})]:
            with self.subTest(name=name):
                self.calls=[];self.children=[]
                code,hook,payload=self.execute(name,**options)
                self.assertEqual(code,0);actual=json.loads(payload.read_text())
                self.assertEqual((actual['no_site'],actual['isolated']),(1,1))
                self.assertTrue(self.children)
                self.assertEqual(actual['pid'],self.children[0]['pid'])
                self.assertTrue(all(c['pid']==actual['pid'] and c['start_time']>0 and c['boot_id'] for c in self.children))
                self.assertEqual(hook.exists(),name!='no-site')
                if hook.exists():self.assertEqual(int(hook.read_text()),actual['pid'])
                self.assertIn('start-producer',self.calls);self.assertIn('end-producer',self.calls)
                self.assertTrue(self.guard.started);self.assertIsNone(self.guard.child)

    def test_failed_guard_or_identity_persistence_never_executes_target(self):
        for failure in ('guard','status'):
            with self.subTest(failure=failure):
                self.failure=failure;self.children=[]
                with self.assertRaisesRegex(RuntimeError,'TEST_ONLY'):
                    self.execute(failure,bootstrap_no_site=True)
                self.assertFalse((self.attempt/'payload.json').exists())
                self.assertFalse((self.attempt/'hook').exists())
                self.assertTrue(self.children);self.assertIsNone(self.guard.child)
                self.assertFalse(self.guard.started)
                self.assertFalse(run_matrix.owned_processes(self.children[0]))

    def test_non_boolean_option_rejects_before_guard_or_log_creation(self):
        for index,value in enumerate((1,0,None,'true')):
            self.calls=[];self.children=[]
            with self.assertRaises(ValueError):self.execute('bad-'+str(index),bootstrap_no_site=value)
            self.assertEqual(self.calls,[]);self.assertEqual(self.children,[])
            self.assertEqual(list(self.attempt.iterdir()),[])


if __name__=='__main__':unittest.main()
