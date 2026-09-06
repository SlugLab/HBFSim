"""Finite CPU controls for the instance adapter. No Torch imports or GPU calls."""
import os
from types import SimpleNamespace
import unittest

import numpy as np

from hf_route_cuda_events import RouteCudaEvents, SEMANTICS, STATUS


class FakeCuda:
    def __init__(self):
        self.device = SimpleNamespace(type='cuda', index=0)
        self.stream = SimpleNamespace(device=self.device, cuda_stream=0, synchronize=self.synchronize)
        self.clock = self.records = 0
        self.calls = []
        self.fail_record = None
        self.fail_sync = False

    def current_stream(self):
        return self.stream

    def Event(self, **kwargs):
        if kwargs != dict(enable_timing=True, blocking=False, interprocess=False):
            raise AssertionError('wrong Event options')
        owner = self

        class Event:
            device = None
            tick = None

            def record(self, stream):
                owner.records += 1
                if owner.records == owner.fail_record:
                    raise RuntimeError('event record failed')
                self.device, self.tick = stream.device, owner.clock
                owner.clock += 1000
                owner.calls.append('record')

            def synchronize(self):
                owner.calls.append('preinit-sync')
                owner.clock += 10000

            def elapsed_time(self, other):
                return float(other.tick-self.tick) / 1_000_000

        return Event()

    def synchronize(self):
        self.calls.append('generation-sync')
        if self.fail_sync:
            raise RuntimeError('stream sync failed')


class Topk:
    def __init__(self, data, device):
        self.data, self.device, self.shape = data, device, data.shape


class FakeCapturer:
    def __init__(self, cuda, layers=48, top_k=8):
        self.cuda = cuda
        self._device_buffer = np.zeros((64,layers,top_k),dtype=np.int32)
        self._host_buffer_view = np.zeros((512,layers,top_k),dtype=np.int32)
        self.original_calls = []

    def capture(self, layer_id, topk_ids):
        self.original_calls.append(('capture',layer_id))
        self._device_buffer[:topk_ids.shape[0],layer_id,:] = topk_ids.data
        self.cuda.clock += 4000

    def save_captured_experts(self, indices):
        self.original_calls.append(('save',len(indices)))
        if getattr(self,'fail_save',False):
            raise RuntimeError('original save failed')
        self._host_buffer_view[indices] = self._device_buffer[:len(indices)]
        self.cuda.clock += 9000


class FakeReader:
    def __init__(self, capturer):
        self.capturer = capturer
        self.calls = 0

    def get_routed_experts(self, indices):
        self.calls += 1
        return self.capturer._host_buffer_view[indices].copy()


def emit_batches(capturer, reader, cuda, routes, slots):
    for batch in range(8):
        begin, end = (0,32) if batch==0 else (batch+31,batch+32)
        for layer in range(routes.shape[1]):
            capturer.capture(layer, Topk(routes[begin:end,layer,:],cuda.device))
        capturer.save_captured_experts(indices=slots[begin:end])
    return reader.get_routed_experts(indices=slots)


class RouteCudaEventTests(unittest.TestCase):
    def fixture(self, constant=False):
        cuda = FakeCuda()
        cap, reader = FakeCapturer(cuda), None
        reader = FakeReader(cap)
        token, layer, expert = np.indices((39,48,8),dtype=np.int32)
        routes = ((0 if constant else token*3+layer*5)+expert)%128
        slots = ((np.arange(39,dtype=np.int32)*7+5)%512)
        control = dict(layers=48,top_k=8,experts=128,input_len=32,output_len=8)
        adapter = RouteCudaEvents(cap,reader,cuda=cuda,numpy=np,control=control,
            gpu_uuid='GPU-TEST',worker_identity=dict(pid=os.getpid(),start_time=17),test_only=True)
        return adapter,cuda,cap,reader,routes,slots

    def test_exact_noncontiguous_join_preinit_384_and_335_plus_missing(self):
        adapter,cuda,cap,reader,routes,slots = self.fixture()
        adapter.start()
        self.assertEqual(cuda.calls,['record']*384+['preinit-sync'])
        returned = emit_batches(cap,reader,cuda,routes,slots)
        np.testing.assert_array_equal(returned,routes)
        adapter.finish(returned)
        adapter.close()
        report = adapter.snapshot(bindings={'test':'fixture'},primary_error=None)
        self.assertEqual(report['status'],STATUS)
        self.assertEqual(report['timing_semantics'],SEMANTICS)
        self.assertEqual(report['counts'],dict(batch_events=384,save_batches=8,reader_calls=1,
            saved_slots=39,decode_nodes=336,observed_intervals=335,missing_terminal=1))
        self.assertEqual(report['provenance'],'MOCK')
        self.assertFalse(report['scientific_validation_passed'])
        self.assertEqual(report['captures'][0]['relative_ns'],0)
        for token,row in enumerate(report['token_join']):
            batch,index = (0,token) if token<32 else (token-31,0)
            self.assertEqual(row,dict(route_token_index=token,slot=int(slots[token]),
                batch_index=batch,batch_row=index,event_indices=list(range(batch*48,(batch+1)*48))))
        for index,node in enumerate(report['decode_intervals'][:-1]):
            self.assertEqual((node['step'],node['layer_id'],node['event_index']),
                             (index//48,index%48,index+48))
            self.assertEqual(node['next_node'],dict(step=(index+1)//48,layer_id=(index+1)%48,event_index=index+49))
            # Fake original capture costs 4us; event insertion costs 1us;
            # batch save adds 9us. Initialization never enters these intervals.
            self.assertEqual(node['elapsed_ns'],14000 if index%48==47 else 5000)
        self.assertEqual(report['decode_intervals'][-1],dict(step=6,layer_id=47,event_index=383,
            next_node=None,elapsed_ms=None,elapsed_ns=None,availability='MISSING',reason='NO_SUCCESSOR_ROUTE'))
        self.assertEqual(len(cap.original_calls),392)
        self.assertEqual(reader.calls,1)
        self.assertEqual(cuda.calls.count('generation-sync'),1)
        self.assertNotIn('capture',vars(cap));self.assertNotIn('save_captured_experts',vars(cap))
        self.assertNotIn('get_routed_experts',vars(reader))

    def test_incomplete_stream_record_sync_and_bounds_cannot_finish(self):
        for case in ('record','stream','layer','save','save-call','extra','sync','returned'):
            adapter,cuda,cap,reader,routes,slots = self.fixture()
            adapter.start()
            with self.subTest(case=case),self.assertRaises((ValueError,RuntimeError)):
                if case=='record':
                    cuda.fail_record=cuda.records+1
                    cap.capture(0,Topk(routes[:32,0],cuda.device))
                elif case=='stream':
                    cuda.stream=SimpleNamespace(device=cuda.device,cuda_stream=19)
                    cap.capture(0,Topk(routes[:32,0],cuda.device))
                elif case=='layer':
                    cap.capture(1,Topk(routes[:32,1],cuda.device))
                elif case=='save':
                    cap.save_captured_experts(slots[:32])
                elif case=='save-call':
                    for layer in range(48):
                        cap.capture(layer,Topk(routes[:32,layer],cuda.device))
                    cap.fail_save=True
                    cap.save_captured_experts(slots[:32])
                elif case=='extra':
                    emit_batches(cap,reader,cuda,routes,slots)
                    cap.capture(0,Topk(routes[-1:,0],cuda.device))
                else:
                    returned=emit_batches(cap,reader,cuda,routes,slots)
                    if case=='sync':cuda.fail_sync=True
                    else:returned[0,0,0]=127
                    adapter.finish(returned)
            adapter.close()
            self.assertEqual(adapter.snapshot(bindings={},primary_error={'test':case})['status'],'FAILED')

    def test_identical_routes_do_not_hide_bad_slot_order_and_restore_never_clobbers_foreign_binding(self):
        adapter,cuda,cap,reader,routes,slots = self.fixture(constant=True)
        adapter.start()
        # Capture all eight batches, then change reader order without changing
        # any route value. A value-only join would silently accept this case.
        for batch in range(8):
            begin,end=(0,32) if batch==0 else (batch+31,batch+32)
            for layer in range(48):
                cap.capture(layer,Topk(routes[begin:end,layer],cuda.device))
            cap.save_captured_experts(slots[begin:end])
        swapped=slots.copy();swapped[[0,1]]=swapped[[1,0]]
        returned=reader.get_routed_experts(swapped)
        np.testing.assert_array_equal(returned,routes)
        with self.assertRaisesRegex(ValueError,'token order'):
            adapter.finish(returned)
        foreign=lambda *args:None
        cap.capture=foreign
        with self.assertRaisesRegex(ValueError,'restoration incomplete'):
            adapter.close()
        self.assertIs(cap.capture,foreign)
        self.assertNotIn('save_captured_experts',vars(cap))
        self.assertNotIn('get_routed_experts',vars(reader))
        self.assertEqual([r['status'] for r in adapter.restoration],['RESTORED','RESTORED','FAILED'])


if __name__=='__main__':
    unittest.main()
