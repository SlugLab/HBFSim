#!/usr/bin/env python3
"""Causal layer-synchronous TRACE_COMPOSED projection, with one shared service.

The controller models whole-expert reservations and pins each batch's weights
through its supplied compute interval. It does not model GPU allocators or live
serving. `none` serializes each miss; `on_demand` issues all batch misses before
waiting; `one_layer_ahead` predicts from the latest already observed route of
each currently active member at the next layer. Unknown first-layer routes
produce no prediction. Even final speculative traffic is drained and reported.
The opt-in route horizon mode observes the last demand without servicing it;
the missing successor stays missing and later drain is accounting only.
"""
from __future__ import annotations

from collections import OrderedDict

POLICIES = ('none', 'on_demand', 'one_layer_ahead')
MAX_TIME = (1 << 63)-1


def nonnegative(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(name+' must be a nonnegative integer')
    return value


def validate_nodes(nodes, objects, capacity_bytes, initial_resident, *, route_horizon=False):
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
        if route_horizon:
            if 'compute_ns' in node or 'route_gap_ns' not in node:
                raise ValueError('route horizon requires separate gap semantics')
            if ordinal == len(nodes)-1:
                if node['route_gap_ns'] is not None:
                    raise ValueError('terminal route gap must remain missing')
            elif nonnegative(node['route_gap_ns'], 'route gap') > MAX_TIME:
                raise ValueError('route gap exceeds clock range')
        elif nonnegative(node['compute_ns'], 'compute interval') > MAX_TIME:
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


def replay(nodes, objects, *, capacity_bytes, initial_resident, policy, service, route_horizon=False):
    """Return an inspectable causal ledger; injected services are test oracles."""
    if policy not in POLICIES:
        raise ValueError('unsupported prefetch policy')
    if type(route_horizon) is not bool:
        raise ValueError('route horizon mode must be an explicit boolean')
    layers=validate_nodes(nodes, objects, capacity_bytes, initial_resident, route_horizon=route_horizon)
    if service.now != 0:
        raise ValueError('replay requires a fresh service clock')
    cache=OrderedDict((k, dict(request_id=None, ready_ns=0)) for k in initial_resident)
    requests, history, output_nodes, evictions, skipped = [], {}, [], [], []
    pending=set()
    used=sum(objects[k]['transfer_bytes'] for k in cache)
    peak=used
    hits=misses=0
    prefix_miss_bytes=0

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

    for ordinal,node in enumerate(nodes):
        # Drain same-time reports before classifying cache lookups.
        advance(service.now)
        visible=service.now
        layer=node['layer']
        demand=sorted({(layer,e) for ids in node['members'].values() for e in ids})
        protected=set(demand)
        terminal=route_horizon and ordinal==len(nodes)-1
        lookup=[]
        for member, ids in node['members'].items():
            history[member,layer]=dict(step=node['step'], visible_at_ns=visible, experts=list(ids))
        for key in demand:
            entry=cache.get(key)
            if entry is not None and entry['ready_ns'] is not None:
                hits+=1
            else:
                misses+=1
                if not terminal:prefix_miss_bytes+=objects[key]['transfer_bytes']
            if route_horizon:
                lookup.append(dict(expert=list(key), bytes=objects[key]['transfer_bytes'],
                    state='READY' if entry is not None and entry['ready_ns'] is not None else
                          'PENDING' if entry is not None else 'MISSING',
                    request_id=entry['request_id'] if entry is not None else None))
            if entry is not None and entry['request_id'] is not None:
                row=requests[entry['request_id']-1]
                if row['first_demand_ns'] is None:
                    row['first_demand_ns']=visible
        if terminal:
            # Observe all final demand, including promotion evidence for an
            # existing prefetch, without servicing/consuming a missing endpoint.
            candidate={}
            target=(layer+1)%layers
            for member in sorted(node['members']):
                observed=history.get((member,target))
                if observed is not None:
                    for expert in sorted(observed['experts']):
                        candidate.setdefault((target,expert),[]).append(dict(member=member,**observed))
            output_nodes.append(dict(step=node['step'],layer=layer,
                active_sequences=len(node['members']),visible_ns=visible,
                route_gap_ns=None,residual_ns=None,modeled_consume_ns=None,
                next_visible_ns=None,availability='MISSING',reason='NO_SUCCESSOR_ROUTE',
                demand_experts=[list(k) for k in demand],demand_lookup=lookup,
                terminal_unserved_demand_bytes=sum(r['bytes'] for r in lookup),
                terminal_not_ready_bytes=sum(r['bytes'] for r in lookup if r['state']!='READY'),
                candidate_not_issued=[dict(expert=list(k),observations=v) for k,v in sorted(candidate.items())]))
            break
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
        end=compute_start+node['route_gap_ns' if route_horizon else 'compute_ns']
        advance(end)
        if route_horizon:
            output_nodes.append(dict(step=node['step'],layer=layer,active_sequences=len(node['members']),
                visible_ns=visible,modeled_consume_ns=compute_start,next_visible_ns=end,
                route_gap_ns=node['route_gap_ns'],residual_ns=compute_start-visible,
                availability='OBSERVED',reason=None,demand_experts=[list(k) for k in demand],
                demand_lookup=lookup))
        else:
            output_nodes.append(dict(step=node['step'], layer=layer, active_sequences=len(node['members']),
                                     visible_ns=visible, compute_start_ns=compute_start, compute_end_ns=end,
                                     residual_ns=compute_start-visible, demand_experts=[list(k) for k in demand]))
    decode_complete=service.now
    pending_at_horizon=sorted(pending)
    if route_horizon:
        for row in requests:row['ready_at_horizon_ns']=row['ready_ns']
    while pending:
        wait_one()
    for row in requests:
        if row['origin']=='demand':
            row['classification']='demand'
        elif row['consume_ns'] is None:
            if not route_horizon:row['classification']='useless'
            elif row['first_demand_ns'] is not None:row['classification']='censored_terminal'
            elif row['eviction_ns'] is not None:row['classification']='useless_prefix'
            else:row['classification']='censored_horizon'
        else:
            row['classification']='useful' if row['ready_ns']<=row['first_demand_ns'] else 'late'
    if len(requests)!=len({r['request_id'] for r in requests}) or any(r['ready_ns'] is None for r in requests):
        raise ValueError('request conservation failed')
    if route_horizon:
        span=sum(n['route_gap_ns'] for n in nodes[:-1])
        residual=sum(n['residual_ns'] for n in output_nodes[:-1])
        if span+residual!=decode_complete or len(output_nodes)!=len(nodes):
            raise ValueError('route horizon clock/coverage conservation failed')
        return dict(schema_version=1,policy=policy,service_source=service.source,
            concurrency_kind='TRACE_COMPOSED',model='LAYER_SYNCHRONOUS_WHOLE_EXPERT_LRU',
            scope='PROJECTED_ROUTE_INTERVAL_PREFIX_REPLAY',
            time_assumption='OBSERVED_ROUTE_GAPS_FIXED_UNDER_ADDED_MODEL_MEDIA_WAIT',
            observed_route_span_ns=span,projected_terminal_visible_ns=decode_complete,
            prefix_residual_ns=residual,drain_end_ns=service.now,
            prefix_nodes=len(nodes)-1,terminal_demand_observed=True,terminal_consumption_observed=False,
            pending_at_horizon=pending_at_horizon,capacity_bytes=capacity_bytes,
            peak_reserved_bytes=peak,final_reserved_bytes=used,
            demand_lookups=hits+misses,cache_hits=hits,cache_misses=misses,
            prefix_ready_miss_bytes=prefix_miss_bytes,
            terminal_unserved_demand_bytes=output_nodes[-1]['terminal_unserved_demand_bytes'],
            traffic_bytes=sum(r['bytes'] for r in requests),requests=requests,nodes=output_nodes,
            evictions=evictions,prefetch_skipped=skipped,
            cache_scope='CPU_WHOLE_EXPERT_RESERVATIONS_NOT_GPU_ALLOCATOR_VALIDATION')
    return dict(schema_version=1, policy=policy, service_source=service.source,
                concurrency_kind='TRACE_COMPOSED', model='LAYER_SYNCHRONOUS_WHOLE_EXPERT_LRU',
                decode_complete_ns=decode_complete, drained_service_ns=service.now,
                compute_ns=sum(n['compute_ns'] for n in nodes), capacity_bytes=capacity_bytes,
                peak_reserved_bytes=peak, final_reserved_bytes=used,
                demand_lookups=hits+misses, cache_hits=hits, cache_misses=misses,
                traffic_bytes=sum(r['bytes'] for r in requests), requests=requests,
                nodes=output_nodes, evictions=evictions, prefetch_skipped=skipped,
                cache_scope='CPU_WHOLE_EXPERT_RESERVATIONS_NOT_GPU_ALLOCATOR_VALIDATION')
