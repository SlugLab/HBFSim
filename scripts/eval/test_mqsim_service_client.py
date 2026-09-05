"""Native CPU MQSim client integration; no physical I/O."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from mqsim_service import MqsimService

ROOT=Path(__file__).resolve().parents[2]
BINARY=Path(os.environ.get('HBFSIM_MQSIM_SERVICE_BINARY',ROOT/'build-eval-implementation/hbf_mqsim_service'))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.mqsim-client-test-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        profile=json.loads((ROOT/'configs/profiles/nominal.json').read_text())
        profile.update(capacity_bytes=16<<30,hbm_cache_bytes=64<<20,queue_depth=2,time_scale=1)
        self.profile=self.directory/'profile.json'
        self.profile.write_text(json.dumps(profile))

    def test_live_native_clock_and_finish_receipt(self):
        with MqsimService(BINARY, self.profile, self.directory/'attempt', timeout=20) as service:
            service.submit(dict(request_id=1,issue_ns=0,logical_address=0,bytes=16384,operation='read'))
            self.assertIsNone(service.until(1000))
            self.assertEqual(service.now,1000)
            completion=service.until(100000)
            self.assertEqual(completion['request_id'],1)
            receipt=service.finish()
            self.assertEqual(receipt['completed_bytes'],16384)
            self.assertEqual(len(service.observations),3)
        self.assertEqual(service.process.returncode,0)
        self.assertTrue((self.directory/'attempt/service-transcript.jsonl').is_file())

    def test_invalid_request_retains_failure_transcript_and_owns_cleanup(self):
        with self.assertRaises(ValueError):
            with MqsimService(BINARY, self.profile, self.directory/'failure', timeout=20) as service:
                service.submit(dict(request_id=1,issue_ns=0,logical_address=0,bytes=1,operation='read'))
        self.assertIsNotNone(service.process.returncode)
        self.assertNotEqual(service.process.returncode,0)
        self.assertTrue((self.directory/'failure/service-stderr.log').read_text())

    def test_empty_fully_resident_service_can_finish(self):
        with MqsimService(BINARY,self.profile,self.directory/'empty',timeout=20) as service:
            self.assertIsNone(service.until(2500))
            self.assertEqual(service.finish()['issued'],0)


if __name__=='__main__':
    unittest.main()
