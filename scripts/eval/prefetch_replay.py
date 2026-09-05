#!/usr/bin/env python3
"""Causal layer-synchronous TRACE_COMPOSED projection, with one shared service.

The controller models whole-expert reservations and pins each batch's weights
through its supplied compute interval. It does not model GPU allocators or live
serving. `none` serializes each miss; `on_demand` issues all batch misses before
waiting; `one_layer_ahead` predicts from the latest already observed route of
each currently active member at the next layer. Unknown first-layer routes
produce no prediction. Even final speculative traffic is drained and reported.
"""
from __future__ import annotations

from collections import OrderedDict

POLICIES = ('none', 'on_demand', 'one_layer_ahead')
MAX_TIME = (1 << 63)-1


def nonnegative(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(name+' must be a nonnegative integer')
    return value


def validate_nodes(nodes, objects, capacity_bytes, initial_resident):
    if not nodes or not objects or nonnegative(capacity_bytes, 'capacity') == 0:
        raise ValueError('INFEASIBLE empty trace/object set/capacity')
    layers = max(key[0] for key in objects)+1
    for key, obj in objects.items():
        if (len(key)!=2 or any(type(k) is not int or k<0 for k in key)
                or type(obj['bytes']) is not int or obj['bytes']<=0
                or type(obj['transfer_bytes']) is not int or obj['transfer_bytes']<obj['bytes']
                or obj['transfer_bytes']%512 or obj['transfer_bytes']>0xffffffff
                or type(obj['logical_address']) is not int or obj['logical_address']<0
                or obj['logical_address']%512):
            raise ValueError('invalid whole-expert object')
    if len(set(initial_resident))!=len(initial_resident) or any(k not in objects for k in initial_resident):
        raise ValueError('invalid initial resident identity')
    if sum(objects[k]['transfer_bytes'] for k in initial_resident)>capacity_bytes:
        raise ValueError('INFEASIBLE initial residency exceeds capacity')
    prior_members = None
    for ordinal, node in enumerate(nodes):
        if (node['step'], node['layer']) != divmod(ordinal, layers):
            raise ValueError('trace must contain complete ordered step/layer coverage')
        if nonnegative(node['compute_ns'], 'compute interval') > MAX_TIME:
            raise ValueError('compute interval exceeds clock range')
        members = node['members']
        if not members or any(not isinstance(m, str) or not m for m in members):
            raise ValueError('invalid active sequence identities')
        if node['layer']==0:
            if prior_members is not None and not set(members)<=prior_members:
                raise ValueError('TRACE_COMPOSED cannot introduce new active members')
            prior_members=set(members)
        elif set(members)!=prior_members:
            raise ValueError('active members differ within one composed step')
        demand=set()
        for ids in members.values():
            if not isinstance(ids, list) or not ids or len(set(ids))!=len(ids):
                raise ValueError('invalid top-k route')
            for expert in ids:
                if type(expert) is not int or (node['layer'], expert) not in objects:
                    raise ValueError('route expert outside inventory')
                demand.add((node['layer'],expert))
        if sum(objects[k]['transfer_bytes'] for k in demand)>capacity_bytes:
            raise ValueError('INFEASIBLE batch whole-expert working set exceeds cache')
    if len(nodes)%layers:
        raise ValueError('incomplete final step')
    return layers


def replay(nodes, objects, *, capacity_bytes, initial_resident, policy, service):
    """Return an inspectable causal ledger; injected services are test oracles."""
    if policy not in POLICIES:
        raise ValueError('unsupported prefetch policy')
    layers=validate_nodes(nodes, objects, capacity_bytes, initial_resident)
    if service.now != 0:
        raise ValueError('replay requires a fresh service clock')
    cache=OrderedDict((k, dict(request_id=None, ready_ns=0)) for k in initial_resident)
    requests, history, output_nodes, evictions, skipped = [], {}, [], [], []
    pending=set()
    used=sum(objects[k]['transfer_bytes'] for k in cache)
    peak=used
    hits=misses=0

    def receive(horizon):
        before=service.now
        completion=service.until(horizon)
        if not before<=service.now<=horizon:
            raise ValueError('service clock violated horizon')
        if completion is not None:
            rid=completion['request_id']
            if rid not in pending:
                raise ValueError('duplicate/unknown completion')
            request=requests[rid-1]
            ready=completion['reported_complete']
            if not request['issue_ns']<=ready<=service.now:
                raise ValueError('nonmonotonic service completion')
            request['ready_ns']=ready
            cache[tuple(request['expert'])]['ready_ns']=ready
            pending.remove(rid)
        elif service.now!=horizon:
            raise ValueError('service returned before horizon without completion')
        return completion

    def advance(horizon):
        if horizon>MAX_TIME:
            raise ValueError('replay clock overflow')
        while receive(horizon) is not None:
            pass

    def wait_one():
        if not pending or receive(MAX_TIME) is None:
            raise ValueError('pending service failed to complete in clock range')

    def reserve(key, protected, speculative):
        nonlocal used
        size=objects[key]['transfer_bytes']
        if speculative:
            reclaimable=sum(objects[k]['transfer_bytes'] for k,entry in cache.items()
                            if k not in protected and entry['ready_ns'] is not None)
            if used+size-reclaimable>capacity_bytes:
                return False
        while used+size>capacity_bytes:
            victim=next((k for k,entry in cache.items()
                         if k not in protected and entry['ready_ns'] is not None), None)
            if victim is None:
                if speculative:
                    return False
                wait_one()
                continue
            entry=cache.pop(victim)
            used-=objects[victim]['transfer_bytes']
            evictions.append(dict(expert=list(victim), time_ns=service.now, request_id=entry['request_id']))
            if entry['request_id'] is not None:
                requests[entry['request_id']-1]['eviction_ns']=service.now
        used+=size
        return True

    def issue(key, origin, protected, basis=None):
        nonlocal peak
        if key in cache:
            return True
        if not reserve(key, protected, origin=='prefetch'):
            return False
        rid=len(requests)+1
        obj=objects[key]
        row=dict(request_id=rid, expert=list(key), origin=origin,
                 issue_ns=service.now, ready_ns=None, first_demand_ns=None,
                 consume_ns=None, eviction_ns=None, prediction_basis=basis,
                 bytes=obj['transfer_bytes'], expert_payload_bytes=obj['bytes'],
                 logical_address=obj['logical_address'], classification=None)
        service.submit(dict(request_id=rid, issue_ns=service.now,
                            logical_address=obj['logical_address'], bytes=obj['transfer_bytes'], operation='read'))
        requests.append(row)
        pending.add(rid)
        cache[key]=dict(request_id=rid, ready_ns=None)
        peak=max(peak, used)
        return True

    for node in nodes:
        # Drain same-time reports before classifying cache lookups.
        advance(service.now)
        visible=service.now
        layer=node['layer']
        demand=sorted({(layer,e) for ids in node['members'].values() for e in ids})
        protected=set(demand)
        for member, ids in node['members'].items():
            history[member,layer]=dict(step=node['step'], visible_at_ns=visible, experts=list(ids))
        for key in demand:
            entry=cache.get(key)
            if entry is not None and entry['ready_ns'] is not None:
                hits+=1
            else:
                misses+=1
            if entry is not None and entry['request_id'] is not None:
                row=requests[entry['request_id']-1]
                if row['first_demand_ns'] is None:
                    row['first_demand_ns']=visible
        for key in demand:
            issue(key, 'demand', protected)
            row_id=cache[key]['request_id']
            if row_id is not None and requests[row_id-1]['first_demand_ns'] is None:
                requests[row_id-1]['first_demand_ns']=visible
            if policy=='none':
                while cache[key]['ready_ns'] is None:
                    wait_one()
        while any(cache[k]['ready_ns'] is None for k in demand):
            wait_one()
        compute_start=service.now
        for key in demand:
            cache.move_to_end(key)
            rid=cache[key]['request_id']
            if rid is not None and requests[rid-1]['consume_ns'] is None:
                requests[rid-1]['consume_ns']=compute_start
        if policy=='one_layer_ahead':
            target=(layer+1)%layers
            predictions={}
            for member in sorted(node['members']):
                observed=history.get((member,target))
                if observed is not None:
                    for expert in sorted(observed['experts']):
                        predictions.setdefault((target,expert), []).append(dict(member=member, **observed))
            for key, evidence in sorted(predictions.items()):
                basis=dict(rule='LATEST_OBSERVED_SAME_LAYER_CURRENT_MEMBERS', observations=evidence,
                           visible_at_ns=max(r['visible_at_ns'] for r in evidence))
                if not issue(key, 'prefetch', protected, basis):
                    skipped.append(dict(expert=list(key), time_ns=service.now, reason='NO_UNPINNED_READY_SPACE'))
        end=compute_start+node['compute_ns']
        advance(end)
        output_nodes.append(dict(step=node['step'], layer=layer, active_sequences=len(node['members']),
                                 visible_ns=visible, compute_start_ns=compute_start, compute_end_ns=end,
                                 residual_ns=compute_start-visible, demand_experts=[list(k) for k in demand]))
    decode_complete=service.now
    while pending:
        wait_one()
    for row in requests:
        if row['origin']=='demand':
            row['classification']='demand'
        elif row['consume_ns'] is None:
            row['classification']='useless'
        else:
            row['classification']='useful' if row['ready_ns']<=row['first_demand_ns'] else 'late'
    if len(requests)!=len({r['request_id'] for r in requests}) or any(r['ready_ns'] is None for r in requests):
        raise ValueError('request conservation failed')
    return dict(schema_version=1, policy=policy, service_source=service.source,
                concurrency_kind='TRACE_COMPOSED', model='LAYER_SYNCHRONOUS_WHOLE_EXPERT_LRU',
                decode_complete_ns=decode_complete, drained_service_ns=service.now,
                compute_ns=sum(n['compute_ns'] for n in nodes), capacity_bytes=capacity_bytes,
                peak_reserved_bytes=peak, final_reserved_bytes=used,
                demand_lookups=hits+misses, cache_hits=hits, cache_misses=misses,
                traffic_bytes=sum(r['bytes'] for r in requests), requests=requests,
                nodes=output_nodes, evictions=evictions, prefetch_skipped=skipped,
                cache_scope='CPU_WHOLE_EXPERT_RESERVATIONS_NOT_GPU_ALLOCATOR_VALIDATION')
