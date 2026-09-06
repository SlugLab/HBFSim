"""Bounded in-process CUDA route intervals; importing this module is CPU only.

The owned loaded arm supplies already verified CUDA/numpy APIs. No environment,
class, singleton, runtime factory, or installed package is changed here. Times
include ordinary scheduling and capture overhead; they are not kernel timings.
"""
from __future__ import annotations

import inspect
import math
import os
import threading


SEMANTICS = 'ROUTE_TO_ROUTE_DEVICE_ELAPSED_INCLUDING_CAPTURE_AND_SCHEDULING'
STATUS = 'UNVALIDATED_ROUTE_INTERVAL_CAPTURE'
MAX_ARTIFACT_BYTES = 1 << 20


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _stream_key(stream):
    device = stream.device
    _require(device.type == 'cuda' and device.index == 0,
             'route events require the already verified visible CUDA device 0')
    handle = stream.cuda_stream
    _require(type(handle) is int and handle >= 0, 'invalid route-event stream handle')
    return dict(device_index=device.index, cuda_stream=handle)


def _ns(value):
    _require(type(value) is float and math.isfinite(value) and value >= 0,
             'invalid CUDA elapsed milliseconds')
    # The integer unit is an encoding choice, not a resolution claim.
    return int(round(value * 1_000_000))


class RouteCudaEvents:
    def __init__(self, capturer, reader, *, cuda, numpy, control, gpu_uuid,
                 worker_identity, test_only=False):
        _require(type(test_only) is bool, 'event test provenance must be boolean')
        self.layers, self.top_k, self.experts = (
            control[key] for key in ('layers', 'top_k', 'experts'))
        _require(all(type(v) is int and v > 0 for v in
                     (self.layers, self.top_k, self.experts))
                 and self.top_k <= self.experts and self.layers <= 48
                 and self.top_k <= 8 and self.experts <= 128,
                 'unsupported bounded route-event geometry')
        _require(test_only or (self.layers, self.top_k, self.experts) == (48, 8, 128),
                 'real route-event geometry differs from fixed HF control')
        _require(control['input_len'] == 32 and control['output_len'] == 8,
                 'route-event prompt/output lengths differ')
        _require(type(gpu_uuid) is str and gpu_uuid.startswith('GPU-'), 'missing GPU binding')
        _require(worker_identity['pid'] == os.getpid(), 'event worker is not the owned process')
        self.cuda, self.np = cuda, numpy
        self.capturer, self.reader = capturer, reader
        self.gpu_uuid, self.identity, self.test_only = gpu_uuid, dict(worker_identity), test_only
        self.thread = threading.get_ident()
        self.expected_events = 8 * self.layers
        self.events, self.captures, self.saves, self.reads = [], [], [], []
        self.pending, self.saved_slots = [], set()
        self.originals, self.installed = [], []
        self.started = self.initialized = self.finished = self.closed = False
        self.stage, self.restoration = 'created', []
        self.stream = self.stream_identity = None
        self.join, self.intervals = [], []

    def _owner(self):
        _require(os.getpid() == self.identity['pid'] and threading.get_ident() == self.thread,
                 'route-event process/thread changed')

    def _check(self):
        self._owner()
        _require(self.started and not self.closed, 'route-event adapter is not active')
        _require(_stream_key(self.cuda.current_stream()) == self.stream_identity,
                 'route-event current stream changed')
        for owner, name, wrapper, descriptor in self.installed:
            _require(vars(owner).get(name) is wrapper
                     and inspect.getattr_static(type(owner), name) is descriptor,
                     'route-event instance/class method binding changed: ' + name)

    def start(self):
        self._owner()
        _require(not self.started and not self.closed, 'route-event adapter already started/closed')
        self.stage = 'preinitialize'
        self.stream = self.cuda.current_stream()
        self.stream_identity = _stream_key(self.stream)
        for owner, name in ((self.capturer, 'capture'), (self.capturer, 'save_captured_experts'),
                            (self.reader, 'get_routed_experts')):
            method = getattr(owner, name)
            descriptor = inspect.getattr_static(type(owner), name)
            _require(inspect.ismethod(method) and method.__self__ is owner
                     and method.__func__ is descriptor, 'unexpected route method: ' + name)
            self.originals.append((owner, name, name in vars(owner), vars(owner).get(name), method, descriptor))
        self.events = [self.cuda.Event(enable_timing=True, blocking=False, interprocess=False)
                       for _ in range(self.expected_events)]
        for event in self.events:
            event.record(self.stream)
        self.events[-1].synchronize()
        _require(all(event.device == self.stream.device for event in self.events),
                 'initialized CUDA event device differs')
        self.initialized = True
        for original, callback in zip(self.originals, (self._capture, self._save, self._read)):
            owner, name, _, _, _, descriptor = original
            # A bound adapter method stored on the instance preserves the public
            # argument list; its saved original is called exactly once below.
            setattr(owner, name, callback)
            self.installed.append((owner, name, callback, descriptor))
        self.started = True
        self.stage = 'await-capture'
        self._check()

    def _capture(self, layer_id, topk_ids):
        self.stage = 'capture'
        self._check()
        batch = len(self.saves)
        expected_rows = 32 if batch == 0 else 1
        _require(batch < 8 and len(self.captures) < self.expected_events,
                 'route-event capture bound exceeded')
        _require(type(layer_id) is int and layer_id == len(self.pending) < self.layers,
                 'route-event layer missing, duplicate or out of order')
        _require(tuple(topk_ids.shape) == (expected_rows, self.top_k)
                 and topk_ids.device == self.stream.device,
                 'route-event capture shape/device differs')
        index = len(self.captures)
        self.events[index].record(self.stream)
        self.captures.append(dict(event_index=index, batch_index=batch,
                                  layer_id=layer_id, batch_rows=expected_rows))
        self.pending.append(index)
        return self.originals[0][4](layer_id, topk_ids)

    def _indices(self, indices, count):
        np = self.np
        _require(type(indices) is np.ndarray and indices.ndim == 1
                 and indices.dtype.kind in 'iu' and len(indices) == count,
                 'route-event indices shape/type/count differs')
        copied = indices.copy()
        values = [int(v) for v in copied]
        host = self.capturer._host_buffer_view
        _require(type(host) is np.ndarray and host.ndim == 3
                 and tuple(host.shape[1:]) == (self.layers, self.top_k),
                 'route-event host buffer geometry differs')
        _require(len(set(values)) == count and all(0 <= v < host.shape[0] for v in values),
                 'route-event slot duplicate/out of bounds')
        return copied, values

    def _routes(self, array, rows):
        np = self.np
        _require(type(array) is np.ndarray and array.shape == (rows, self.layers, self.top_k)
                 and array.dtype == np.dtype('int32'), 'route-event saved routes metadata differs')
        _require(not np.any(array < 0) and not np.any(array >= self.experts)
                 and not np.any(np.diff(np.sort(array, axis=-1), axis=-1) == 0),
                 'route-event saved expert IDs invalid')
        return array.copy()

    def _save(self, indices):
        self.stage = 'save'
        self._check()
        batch = len(self.saves)
        _require(batch < 8 and len(self.pending) == self.layers,
                 'route-event save missing capture batch or exceeds bound')
        rows = 32 if batch == 0 else 1
        copied, values = self._indices(indices, rows)
        _require(not self.saved_slots.intersection(values), 'route-event saved slot reused')
        result = self.originals[1][4](indices=indices)
        _require(self.np.array_equal(indices, copied), 'route-event save indices changed')
        routes = self._routes(self.capturer._host_buffer_view[copied], rows)
        self.saves.append(dict(batch_index=batch, indices=values,
                               event_indices=list(self.pending), routes=routes.tolist()))
        self.saved_slots.update(values)
        self.pending.clear()
        return result

    def _read(self, indices):
        self.stage = 'reader'
        self._check()
        _require(len(self.saves) == 8 and not self.pending and not self.reads,
                 'route-event reader before complete saves or repeated')
        copied, values = self._indices(indices, 39)
        _require(set(values) == self.saved_slots, 'route-event reader slot coverage differs')
        result = self.originals[2][4](indices=indices)
        _require(self.np.array_equal(indices, copied), 'route-event reader indices changed')
        routes = self._routes(result, 39)
        self.reads.append(dict(indices=values, routes=routes.tolist()))
        return result

    def finish(self, array):
        self.stage = 'synchronize-and-join'
        self._check()
        _require(self.initialized and len(self.captures) == self.expected_events
                 and len(self.saves) == 8 and len(self.reads) == 1 and not self.pending,
                 'route-event acquisition incomplete')
        self.stream.synchronize()
        self._check()
        returned = self._routes(array, 39)
        slot_rows = {slot: (saved, row) for saved in self.saves
                     for row, slot in enumerate(saved['indices'])}
        for token, slot in enumerate(self.reads[0]['indices']):
            saved, row = slot_rows[slot]
            # Fixed one-request execution must map prefill and successive decode
            # slots to their actual batches, even if expert IDs happen to match.
            _require((saved['batch_index'], row) == ((0, token) if token < 32 else (token-31, 0)),
                     'route-event reader token order differs from saved batches')
            _require(self.np.array_equal(returned[token], saved['routes'][row])
                     and self.np.array_equal(returned[token], self.reads[0]['routes'][token]),
                     'route-event saved/reader/returned route bytes disagree')
            self.join.append(dict(route_token_index=token, slot=slot,
                                  batch_index=saved['batch_index'], batch_row=row,
                                  event_indices=list(saved['event_indices'])))
        previous = 0.0
        for row, event in zip(self.captures, self.events):
            elapsed = self.events[0].elapsed_time(event)
            offset = _ns(elapsed)
            _require(elapsed >= previous, 'route-event relative time regressed')
            row.update(relative_ms=elapsed, relative_ns=offset)
            previous = elapsed
        decode = self.captures[self.layers:]
        for index, current in enumerate(decode):
            node = dict(step=index//self.layers, layer_id=current['layer_id'],
                        event_index=current['event_index'])
            if index+1 == len(decode):
                node.update(next_node=None, elapsed_ms=None, elapsed_ns=None,
                            availability='MISSING', reason='NO_SUCCESSOR_ROUTE')
            else:
                successor = decode[index+1]
                elapsed = self.events[current['event_index']].elapsed_time(self.events[successor['event_index']])
                node.update(next_node=dict(step=(index+1)//self.layers, layer_id=successor['layer_id'],
                                           event_index=successor['event_index']),
                            elapsed_ms=elapsed, elapsed_ns=_ns(elapsed), availability='OBSERVED', reason=None)
            self.intervals.append(node)
        self.finished = True
        self.stage = 'joined'

    def close(self):
        if self.closed:
            return
        self._owner()
        errors = []
        for owner, name, wrapper, descriptor in reversed(self.installed):
            original = next(row for row in self.originals if row[0] is owner and row[1] == name)
            try:
                _require(vars(owner).get(name) is wrapper
                         and inspect.getattr_static(type(owner), name) is descriptor,
                         'route-event restore binding changed: ' + name)
                if original[2]:
                    setattr(owner, name, original[3])
                else:
                    delattr(owner, name)
                _require((name in vars(owner)) == original[2]
                         and (not original[2] or vars(owner)[name] is original[3]),
                         'route-event instance restoration failed: ' + name)
                self.restoration.append(dict(method=name, status='RESTORED'))
            except BaseException as error:
                self.restoration.append(dict(method=name, status='FAILED',
                                             error_type=type(error).__name__, message=str(error)))
                errors.append(error)
        self.closed = True
        if errors:
            raise ValueError('route-event instance restoration incomplete') from errors[0]

    def snapshot(self, *, bindings, primary_error):
        complete = (self.finished and self.closed and primary_error is None
                    and len(self.restoration) == 3
                    and all(row['status'] == 'RESTORED' for row in self.restoration))
        return dict(schema_version=1, status=STATUS if complete else 'FAILED',
                    timing_semantics=SEMANTICS, scientific_validation_passed=False,
                    provenance='MOCK' if self.test_only else 'UNVALIDATED_ROUTING_CAPTURE',
                    test_only=self.test_only, gpu_uuid=self.gpu_uuid,
                    worker_identity=self.identity, stream=self.stream_identity,
                    time_origin='FIRST_MEASURED_CAPTURE_EVENT',
                    nanosecond_encoding='round(milliseconds * 1000000); no nanosecond resolution claim',
                    preinitialization_complete=self.initialized, stage=self.stage,
                    captures=self.captures, saves=self.saves, readers=self.reads,
                    token_join=self.join, decode_intervals=self.intervals,
                    counts=dict(batch_events=len(self.captures), save_batches=len(self.saves),
                                reader_calls=len(self.reads), saved_slots=len(self.saved_slots),
                                decode_nodes=len(self.intervals),
                                observed_intervals=sum(row['availability'] == 'OBSERVED' for row in self.intervals),
                                missing_terminal=sum(row['availability'] == 'MISSING' for row in self.intervals)),
                    restoration=self.restoration, bindings=bindings, primary_error=primary_error,
                    boundary='Route-to-route device elapsed includes capture, scheduling and adapter overhead; '
                             'terminal successor is missing; not compute-only, kernel-only, or serving speed.')
