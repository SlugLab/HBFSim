"""CPU-only syscall-ledger controls; no storage access."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import storage_arrivals

from storage_arrivals import ROOT, convert, convert_records


class StorageArrivalTests(unittest.TestCase):
    def fixture(self):
        records=[]
        for rid,requested,actual in [(0,0,90),(1,10,80)]:
            records.extend([dict(event='issue',request_id=rid,offset=rid*512,bytes=512,
                                 operation='read',requested_issue_ns=requested,scheduled_ns=20+requested),
                            dict(event='completion',request_id=rid,offset=rid*512,bytes=512,requested_bytes=512,
                                 operation='read',requested_issue_ns=requested,actual_submit_ns=actual,
                                 completion_ns=actual+100,observed_completion_ns=actual+120,
                                 admission_lag_ns=actual-requested,latency_ns=100)])
        summary=dict(issued=2,completed=2,completed_bytes=1024,outstanding=0,provenance='TEST_ONLY')
        for record in records:
            record['provenance']='TEST_ONLY'
        return records,summary

    def test_actual_arrival_order_and_offset_identity_are_preserved(self):
        records,summary=self.fixture()
        arrivals,mapping=convert_records(records,summary,request_count=2)
        self.assertEqual([r['issue_ns'] for r in arrivals],[80,90])
        self.assertEqual([r['logical_address'] for r in arrivals],[512,0])
        self.assertEqual([m['collector_request_id'] for m in mapping],[1,0])
        self.assertEqual([m['requested_issue_ns'] for m in mapping],[10,0])
        self.assertEqual([r['request_id'] for r in arrivals],[1,2])

    def test_closed_loop_actual_arrivals_are_not_recreated_by_model_completions(self):
        records,summary=self.fixture()
        for record in records:
            record['requested_issue_ns']=None
            if record['event']=='completion':
                record['admission_lag_ns']=None
        arrivals,mapping=convert_records(records,summary,request_count=2,arrival_family='closed_loop_qd')
        self.assertEqual([r['issue_ns'] for r in arrivals],[80,90])
        self.assertTrue(all(m['requested_issue_ns'] is None for m in mapping))

    def test_arrival_mode_must_match_requested_timestamp_contract(self):
        records,summary=self.fixture()
        for family in ('UNKNOWN_POLICY','closed_loop_qd'):
            with self.subTest(family=family),self.assertRaises(ValueError):
                convert_records(records,summary,request_count=2,arrival_family=family)

    def test_string_and_integer_source_ids_preserve_issue_order_for_ties(self):
        records,summary=self.fixture()
        for record in records:
            if record['request_id']==0:record['request_id']='io-0'
            if record['event']=='completion':
                record.update(actual_submit_ns=90,completion_ns=190,observed_completion_ns=210,
                              admission_lag_ns=90-record['requested_issue_ns'])
        arrivals,mapping=convert_records(records,summary,request_count=2)
        self.assertEqual([r['request_id'] for r in arrivals],[1,2])
        self.assertEqual([r['collector_request_id'] for r in mapping],['io-0',1])

    def test_missing_duplicate_abort_and_short_reads_reject(self):
        records,summary=self.fixture()
        invalid=[records[:-1],records+[records[-1]],records+[dict(event='abort')]]
        changed=copy.deepcopy(records);changed[-1]['bytes']=1;invalid.append(changed)
        for case in invalid:
            with self.subTest(case=case),self.assertRaises(ValueError):
                convert_records(case,summary,request_count=2)

    def test_summary_and_clock_conservation_reject(self):
        records,summary=self.fixture()
        summary['completed_bytes']=1
        with self.assertRaises(ValueError):
            convert_records(records,summary,request_count=2)
        records,summary=self.fixture()
        records[-1]['actual_submit_ns']=0
        with self.assertRaises(ValueError):
            convert_records(records,summary,request_count=2)

    def test_declared_test_events_cannot_be_promoted_by_summary(self):
        records,summary=self.fixture()
        summary['provenance']='MEASURED'
        with self.assertRaisesRegex(ValueError,'provenance'):
            convert_records(records,summary,request_count=2)

    def test_frozen_collection_conversion_preserves_control_attribution(self):
        with tempfile.TemporaryDirectory(prefix='.storage-arrivals-test-',dir=ROOT) as directory:
            base=Path(directory)
            source=base/'collection';source.mkdir()
            records,summary=self.fixture()
            for record in records:
                record['requested_issue_ns']=None
                if record['event']=='completion':record['admission_lag_ns']=None
            payloads={'raw.requests.jsonl':''.join(json.dumps(r)+'\n' for r in records).encode(),
                      'raw.summary.json':json.dumps(summary).encode(),'frozen.input':b'TEST_ONLY'}
            for name,payload in payloads.items():
                (source/name).write_bytes(payload)
            manifest=dict(schema_version=1,test_only=True,complete=True,state='TEST_ONLY_DONE',
                          plan=dict(kind='closed_loop_qd'),request_count=2,
                          artifact_hashes={n:hashlib.sha256(p).hexdigest() for n,p in payloads.items()})
            encoded=json.dumps(manifest).encode()
            (source/'manifest.json').write_bytes(encoded)
            (source/'status.json').write_text(json.dumps(dict(state='TEST_ONLY_DONE',
                                manifest_sha256=hashlib.sha256(encoded).hexdigest())))
            result=convert(source,base/'output')
            self.assertEqual(result['validation'],'VALIDATED_ARRIVALS')
            self.assertEqual(result['provenance'],'MOCK')
            self.assertEqual(result['required_replay_arrival_mode'],'fixed_arrival_trace')
            self.assertFalse(result['scientific_validation_passed'])
            self.assertEqual(result['bytes'],1024)
            self.assertTrue(all((base/'output'/r['path']).is_file() for r in result['inputs'].values()))
            with self.assertRaises(FileExistsError):
                convert(source,base/'output')
            (source/'raw.requests.jsonl').write_bytes(payloads['raw.requests.jsonl']+b'\n')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                convert(source,base/'corrupt')
            self.assertEqual(json.loads((base/'corrupt/manifest.json').read_text())['validation'],'FAILED')
            (source/'raw.requests.jsonl').write_bytes(payloads['raw.requests.jsonl'])
            freeze=storage_arrivals.freeze_file
            def mutate(source_path,target):
                result=freeze(source_path,target)
                if target.name=='raw.requests.jsonl':
                    rows=[json.loads(line) for line in target.read_text().splitlines()]
                    for row in rows:row['offset']+=1024
                    target.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                return result
            with patch.object(storage_arrivals,'freeze_file',mutate):
                with self.assertRaisesRegex(ValueError,'hash'):
                    convert(source,base/'mutated-copy')


if __name__=='__main__':
    unittest.main()
