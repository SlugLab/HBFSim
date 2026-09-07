"""TEST_ONLY joined fixture; never imports CUDA or acquires a real capture."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import hf_route_horizon_inputs as bridge
from budget_fast_tier import budget_fast_tier
from evaluation_inventory import load_hf_snapshot, adapt_hf_inventory
from routing_metrics import analyze_routes
from test_evaluation_inventory import hf_fixture
from verify_hf_metadata import canonical


def capture_chain(buffers):
    """Update only hash links so semantic corruptions reach their intended gate."""
    docs={k:json.loads(v) for k,v in buffers.items() if k not in
          ('raw_routes','routing_trace','capture_review')}
    b=docs['binding'];event=docs['sidecar'];worker=docs['worker'];wrapper=docs['wrapper']
    event['bindings']=dict(arm='capture',run_id=b['run_id'],
        input_binding_sha256=bridge.sha(buffers['binding']),protocol_sha256=bridge.sha(buffers['protocol']),
        raw_return_sha256=bridge.sha(buffers['raw_return']),raw_routes_sha256=bridge.sha(buffers['raw_routes']))
    buffers['sidecar']=canonical(event)
    worker['route_cuda_events']=dict(enabled=True,status=event['status'],artifact='route-device-events.json',
                                   sha256=bridge.sha(buffers['sidecar']),counts=event['counts'])
    buffers['worker']=canonical(worker)
    wrapper.update(input_binding_sha256=bridge.sha(buffers['binding']),worker_status_sha256=bridge.sha(buffers['worker']),
                   source_manifest_sha256=bridge.sha(buffers['project_manifest']))
    buffers['wrapper']=canonical(wrapper)
    arm=docs['triplet']['arms'][1];arm['worker_status']=worker
    arm['files']={key:dict(sha256=bridge.sha(buffers[name])) for key,name in
                  (('input-binding','binding'),('worker-status','worker'),('owned-worker-status','wrapper'))}
    docs['triplet']['input_bindings']['project_source_manifest_sha256']=bridge.sha(buffers['project_manifest'])
    buffers['triplet']=canonical(docs['triplet'])
    docs['consistency']['arms']=[dict(arm='capture',artifacts={key:bridge.sha(buffers[name]) for key,name in
        (('raw-routes.npy','raw_routes'),('routing.jsonl','routing_trace'),('input-binding.json','binding'),
         ('raw-return.json','raw_return'),('protocol.json','protocol'))})]
    buffers['consistency']=canonical(docs['consistency'])


def joined_fixture(base):
    _,bundle=hf_fixture(base)
    hf=load_hf_snapshot(bundle);inv=adapt_hf_inventory(hf,16384);ib=canonical(inv);model=inv['model_binding']
    shape=inv['kv_shape']
    kv=64*shape['layers']*shape['heads_kv']*(shape['key_length']+shape['value_length'])*2
    budget=budget_fast_tier(inv,fast_bytes=inv['resident_non_offloaded_bytes']+kv+98304,
        active_sequences=1,context_tokens=64,kv_element_bytes=2,workspace_bytes=0,safety_bytes=0,
        hf_snapshot=hf,inventory_file_bytes=ib)
    layers=inv['layers'];top_k=inv['k'];assert (layers,inv['E'],top_k)==(2,2,1)
    identity=dict(pid=101,ppid=99,pgrp=101,session=101,start_time=55,boot_id='TEST_ONLY')
    runtime=canonical(dict(source_extension='MOE_TUNING_V1',cuda_event_extension='ROUTE_EVENTS_V1',
        artifacts={**{'TEST_ONLY/'+str(i):'0'*64 for i in range(133)},
                   'site-packages/torch/cuda/__init__.py':'0'*64,'site-packages/torch/cuda/streams.py':'0'*64}))
    project=canonical(dict(git_commit='a'*40,test_only=True));tuning=canonical(dict(test_only=True))
    protocol=dict(layers=2,experts=2,top_k=1,input_len=32,output_len=8,
                  prompt_token_ids=list(range(32)),route_shape=[39,2,1],vocab_size=128)
    buffers=dict(runtime_manifest=runtime,project_manifest=project,tuning_manifest=tuning,
                 protocol=canonical(protocol),capture_review=b'TEST_ONLY fixture review, no capture certification\n')
    binding=dict(arm='capture',run_id='TEST_ONLY-horizon',gpu_uuid='GPU-TEST_ONLY',test_only=True,
        provenance='MOCK',scientific_validation_passed=False,
        protocol_sha256=bridge.sha(buffers['protocol']),runtime_source_manifest_sha256=bridge.sha(runtime),
        selected_tuning_manifest_sha256=bridge.sha(tuning),git_commit='a'*40,
        metadata_receipt_sha256=model['receipt_sha256'],metadata_complete_sha256=model['complete_sha256'],
        metadata_identity_sha256=model['metadata_identity_sha256'],observation_identity_sha256=model['observation_identity_sha256'],
        donor_sha256=model['legacy_inventory_sha256'],model_fingerprint=model['historical_model_fingerprint'])
    buffers['binding']=canonical(binding)
    array=[[[int((t+l)%2)] for l in range(layers)] for t in range(39)]
    header=repr(dict(descr='<i4',fortran_order=False,shape=(39,layers,top_k))).encode('ascii')
    header+=b' '*((64-(10+len(header)+1)%64)%64)+b'\n'
    payload=b''.join(e.to_bytes(4,'little',signed=True) for token in array for ids in token for e in ids)
    buffers['raw_routes']=b'\x93NUMPY\x01\x00'+len(header).to_bytes(2,'little')+header+payload
    buffers['raw_return']=canonical(dict(prompt_token_ids=list(range(32)),output_token_ids=[1]*8,
        finished=True,finish_reason='length',route_shape=[39,2,1],route_dtype='int32',
        provenance='MOCK',scientific_validation_passed=False))
    events=[]
    for token in range(39):
        for layer in range(layers):
            events.append(dict(run_id=binding['run_id'],route_token_index=token,layer_id=layer,
                token_step=token if token<32 else token-32,phase='prefill' if token<32 else 'decode',
                topk_expert_ids=array[token][layer],model_fingerprint=binding['model_fingerprint']))
    buffers['routing_trace']=b''.join(canonical(row)+b'\n' for row in events)
    slots=[11+t*3 for t in range(39)];captures=[];saves=[];joins=[];intervals=[]
    for i in range(8*layers):
        batch,layer=divmod(i,layers)
        captures.append(dict(event_index=i,batch_index=batch,layer_id=layer,batch_rows=32 if batch==0 else 1,
                             relative_ms=float(i*20/1000000),relative_ns=i*20))
    for batch in range(8):
        first,last=(0,32) if batch==0 else (batch+31,batch+32)
        saves.append(dict(batch_index=batch,indices=slots[first:last],routes=array[first:last],
                          event_indices=list(range(batch*layers,(batch+1)*layers))))
    for token,slot in enumerate(slots):
        batch,row=(0,token) if token<32 else (token-31,0)
        joins.append(dict(route_token_index=token,slot=slot,batch_index=batch,batch_row=row,
                          event_indices=list(range(batch*layers,(batch+1)*layers))))
    for i in range(7*layers):
        step,layer=divmod(i,layers);last=i==7*layers-1;ns=20
        successor=None if last else dict(step=(i+1)//layers,layer_id=(i+1)%layers,event_index=layers+i+1)
        intervals.append(dict(step=step,layer_id=layer,event_index=layers+i,next_node=successor,
            elapsed_ms=None if last else float(ns/1000000),elapsed_ns=None if last else ns,
            availability='MISSING' if last else 'OBSERVED',reason='NO_SUCCESSOR_ROUTE' if last else None))
    event=dict(status='UNVALIDATED_ROUTE_INTERVAL_CAPTURE',timing_semantics=bridge.SEMANTICS,
        test_only=True,provenance='MOCK',scientific_validation_passed=False,preinitialization_complete=True,
        primary_error=None,time_origin='FIRST_MEASURED_CAPTURE_EVENT',
        nanosecond_encoding='round(milliseconds * 1000000); no nanosecond resolution claim',
        restoration=[dict(method=m,status='RESTORED') for m in ('capture','save','reader')],
        stream=dict(device_index=0,cuda_stream=7),worker_identity=identity,gpu_uuid='GPU-TEST_ONLY',
        captures=captures,saves=saves,readers=[dict(indices=slots,routes=array)],token_join=joins,
        decode_intervals=intervals,counts=dict(batch_events=16,save_batches=8,reader_calls=1,saved_slots=39,
                                             decode_nodes=14,observed_intervals=13,missing_terminal=1))
    buffers['sidecar']=canonical(event)
    worker=dict(status='ARM_RETURNED_UNVALIDATED',arm='capture',test_only=True,primary_error=None,
                provenance='MOCK',scientific_validation_passed=False,
                cleanup_errors=[],compatibility_restored=True,worker_identity=identity)
    buffers['worker']=canonical(worker)
    buffers['wrapper']=canonical(dict(status=worker['status'],arm='capture',source_manifest_sha256=bridge.sha(project),
        provenance='MOCK',scientific_validation_passed=False))
    triplet=dict(status='PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED',test_only=True,run_id=binding['run_id'],
        provenance='MOCK',scientific_validation_passed=False,
        gpu_uuid=binding['gpu_uuid'],input_bindings=dict(runtime_manifest_sha256=bridge.sha(runtime),
            tuning_manifest_sha256=bridge.sha(tuning),project_source_manifest_sha256=bridge.sha(project)),
        arms=[dict(arm=name,status=worker['status'],exit_code=0,exit_code_observed=True,owned_exit_observed=True,
                   owned_remaining=[],uncertain_session=False,process_identity=identity)
              for name in ('native','capture','repeat')])
    buffers['triplet']=canonical(triplet)
    buffers['consistency']=canonical(dict(status='ROUTE_CONSISTENCY_ONLY',checks={'fixture_only':True},
        effective_test_only=True,scientific_validation_passed=False,metadata={k:model[v] for k,v in
        (('receipt_sha256','receipt_sha256'),('complete_sha256','complete_sha256'),('donor_sha256','legacy_inventory_sha256'),
         ('metadata_identity_sha256','metadata_identity_sha256'),('observation_identity_sha256','observation_identity_sha256'))}))
    capture_chain(buffers)
    analysis=analyze_routes({'TEST_ONLY-member':events},experts=2,top_k=1,layers=2,seed=0)
    real=canonical(dict(series='real',provenance='MOCK',**analysis['real']))
    shuffled=canonical(dict(series='shuffled',provenance='MOCK',**analysis['shuffled']))
    manifest=dict(schema_version=1,provenance='MOCK',input_source_kind='SYNTHETIC_CONTROL',
        concurrency_kind='TRACE_COMPOSED',composition_seed=0,inventory_sha256=model['legacy_inventory_sha256'],
        layers=2,E=2,k=1,member_traces=['TEST_ONLY-member'],
        input_members=[dict(member_id='TEST_ONLY-member',sha256=bridge.sha(buffers['routing_trace']))],
        outputs={series:dict(sha256=bridge.sha(raw)) for series,raw in (('real',real),('shuffled',shuffled))})
    index=dict(schema_version=1,kind='HF_ROUTE_HORIZON_INPUTS_V1',member_id='TEST_ONLY-member',artifacts={})
    for name,raw in buffers.items():
        path=base/(name+'.input');path.write_bytes(raw)
        index['artifacts'][name]=dict(path=str(path),sha256=bridge.sha(raw))
    snapshots=dict(inventory=ib,budget=canonical(budget),routes=real,routing_manifest=canonical(manifest),
                   route_horizon=canonical(index))
    return snapshots,index,buffers,hf,bundle,shuffled


class RouteHorizonInputTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='.test-hf-horizon-',dir=bridge.ROOT)
        self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.fixture=joined_fixture(self.base)

    def test_joined_input_oracle_and_exact_frozen_acquisition(self):
        snapshots,index,buffers,hf,_,shuffled=self.fixture
        acquired_index,acquired=bridge.acquire(snapshots['route_horizon'])
        self.assertEqual((acquired_index,acquired),(index,buffers))
        for series,raw in (('real',snapshots['routes']),('shuffled',shuffled)):
            current=dict(snapshots,routes=raw)
            data,nodes,objects,resident,provenance=bridge.prepare_route_horizon(current,index,buffers,hf,'cold')
            self.assertEqual(provenance,'MOCK');self.assertEqual(resident,())
            self.assertEqual(len(nodes),14);self.assertEqual([r['route_gap_ns'] for r in nodes],[20]*13+[None])
            self.assertEqual([(r['step'],r['layer']) for r in nodes],[(s,l) for s in range(7) for l in range(2)])
            self.assertEqual(len(objects),4);self.assertEqual(objects[1,1],dict(bytes=49152,transfer_bytes=49152,logical_address=147456))
            self.assertEqual(data['budget']['page_aligned_effective_bytes'],98304)
            rows=json.loads(raw)['routes']
            self.assertEqual([n['members']['TEST_ONLY-member'] for n in nodes],[r['topk_expert_ids'] for r in rows])
            original=json.loads(buffers['sidecar'])['readers'][0]['routes']
            self.assertTrue(all(r['topk_expert_ids']==original[32+r['source_token_step']][r['layer_id']] for r in rows))
            self.assertEqual(data['route_horizon']['series'],series)

    def test_acquire_uses_each_artifact_role_byte_limit(self):
        snapshots,index,_,_,_,_=self.fixture
        large=b'{}\n'*(((20<<20)//3)+1)
        path=self.base/'large-routing.jsonl';path.write_bytes(large)
        routing=copy.deepcopy(index)
        routing['artifacts']['routing_trace']=dict(path=str(path),sha256=bridge.sha(large))
        _,acquired=bridge.acquire(canonical(routing))
        self.assertEqual(acquired['routing_trace'],large)
        runtime=copy.deepcopy(index)
        runtime['artifacts']['runtime_manifest']=dict(path=str(path),sha256=bridge.sha(large))
        with self.assertRaisesRegex(ValueError,'artifact file type/size'):
            bridge.acquire(canonical(runtime))

    def test_wrong_identity_gap_slot_or_synthetic_claim_rejects_before_service(self):
        snapshots,index,buffers,hf,_,_=self.fixture
        cases=[('missing middle',lambda e:e['decode_intervals'][2].update(elapsed_ms=None,elapsed_ns=None), 'interval successor/encoding'),
               ('filled terminal',lambda e:e['decode_intervals'][-1].update(elapsed_ms=0.0,elapsed_ns=0),'terminal successor'),
               ('wrong successor',lambda e:e['decode_intervals'][0]['next_node'].update(event_index=7),'successor'),
               ('bad slot',lambda e:e['token_join'][32].update(slot=999),'token slot'),
               ('negative ms',lambda e:e['decode_intervals'][0].update(elapsed_ms=-0.01),'milliseconds')]
        for name,change,pattern in cases:
            with self.subTest(name=name):
                bad=dict(buffers);event=json.loads(bad['sidecar']);change(event)
                bad['sidecar']=canonical(event);capture_chain(bad)
                with self.assertRaisesRegex(ValueError,pattern):
                    bridge.prepare_route_horizon(snapshots,index,bad,hf,'cold')
        wrong=dict(snapshots);doc=json.loads(wrong['routing_manifest']);doc['provenance']='PROJECTED'
        wrong['routing_manifest']=canonical(doc)
        with self.assertRaisesRegex(ValueError,'routing manifest'):
            bridge.prepare_route_horizon(wrong,index,buffers,hf,'cold')
        bad_head=dict(buffers);binding=json.loads(bad_head['binding']);binding['git_commit']='b'*39
        bad_head['binding']=canonical(binding);capture_chain(bad_head)
        with self.assertRaisesRegex(ValueError,'source HEAD declaration'):
            bridge.prepare_route_horizon(snapshots,index,bad_head,hf,'cold')
        bad_index=copy.deepcopy(index);bad_index['artifacts']['sidecar']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'artifact bytes/hash'):
            bridge.acquire(canonical(bad_index))


if __name__=='__main__':unittest.main()
