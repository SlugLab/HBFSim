#!/usr/bin/env python3
"""Bounded EQ1-A known-delay acquisition. Default: print one matrix-cell plan.

--execute runs one native/matched-zero/selected-treatment triplet under the
project GPU_EXCLUSIVE guard, in a new immutable checkout-local directory.
Separate processes use identical deterministic data, K, warps and occupancy.
The selected logical treatment is the matrix profile (native/fast_logical/
hbf_logical); a positive normal runtime profile remains mandatory. D=0 uses
the opt-in module-local helper with injection disabled. No profile is edited.

Raw globaltimer chains, helper wait stamps, Event time, checksum and coverage
are validated separately. G2's fixed limits are reported; a single triplet
does not close the matrix-wide/repeated G2 gate. No long-form MEASURED rows or
formal sweep are exported here. Scheduler integration must avoid nested GPU
locks; this standalone tool deliberately provides no inherited-guard bypass.
The optional ``--trace-mode per_chain`` selects the default-off per-chain raw
diagnostic; ``legacy`` remains the default for existing acquisitions.
Library dependency injection is CPU TEST_ONLY and cannot write results/runs.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import hashlib
import io
import json
import math
import os
import pathlib
import signal
import stat
import statistics
import sys

from resource_guard import ResourceBusy, ResourceGuard
from run_manifest import (artifact_inventory, atomic_json, canonical_hash,
                          environment_snapshot, git_snapshot, now, sha256)
from run_matrix import FailedRun, InterruptedRun, run_child

ROOT = pathlib.Path(__file__).resolve().parents[2]


def regular_bytes(path):
    path=pathlib.Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('input must be a regular non-symlink file: '+str(path))
        return stream.read()


def make_plan(matrix,cell_id,replicate=1,hops=64):
    matrix=pathlib.Path(matrix).resolve();data=regular_bytes(matrix)
    rows=list(csv.DictReader(io.StringIO(data.decode())))
    selected=[row for row in rows if row['cell_id']==cell_id]
    if len(selected)!=1:raise ValueError('select exactly one unique matrix cell')
    row=selected[0]
    if row['group']!='gpu_delay' or row['eq']!='EQ1' or row['resource_class']!='GPU_EXCLUSIVE':
        raise ValueError('cell is not a GPU_EXCLUSIVE known-delay condition')
    if not 1<=replicate<=int(row['repeats']) or hops not in (1,16,64):raise ValueError('invalid replicate or K')
    if row['profile'] not in ('native','fast_logical','hbf_logical') or row['occupancy'] not in ('low','high'):
        raise ValueError('unsupported logical treatment/occupancy')
    delay=float(row['delay_us'])*1000
    if not math.isfinite(delay) or delay not in (0,500,1000,2000,5000,10000,20000) or int(row['warps']) not in (1,2,4,8,16):
        raise ValueError('unsupported known-delay matrix dimension')
    return dict(schema_version=1,execute=False,condition=row,matrix=str(matrix),matrix_sha256=hashlib.sha256(data).hexdigest(),
                condition_sha256=canonical_hash(row),replicate=replicate,hops=hops,delay_ns=int(delay),resource_class='GPU_EXCLUSIVE')


def observed_residency(intervals):
    events={};blocks=set()
    for row in intervals:
        if row['block'] in blocks or row['end_ns']<=row['begin_ns']:raise ValueError('invalid block interval')
        blocks.add(row['block'])
        events.setdefault(row['sm'],[]).extend(((row['begin_ns'],1),(row['end_ns'],-1)))
    peaks={}
    for sm,values in events.items():
        current=peak=0
        for _,change in sorted(values):current+=change;peak=max(peak,current)
        if current:raise ValueError('unbalanced block intervals')
        peaks[str(sm)]=peak
    return peaks


def _u64(value):
    return type(value) is int and 0 <= value < 2**64


def _disjoint_spans(spans):
    for index,(address,bytes_) in enumerate(spans):
        if not _u64(address) or not _u64(bytes_) or not address or not bytes_ or address+bytes_>=2**64:
            raise ValueError('invalid chain diagnostic span')
        for other_address,other_bytes in spans[index+1:]:
            if not (address+bytes_<=other_address or other_address+other_bytes<=address):
                raise ValueError('overlapping chain diagnostic spans')


def validate_chain_diagnostic(case, *, expected_epoch=2):
    """Validate actual row/event readback for one per-chain benchmark arm."""
    if type(expected_epoch) is not int or not 1 <= expected_epoch < 2**64:
        raise ValueError('invalid expected chain diagnostic epoch')
    diagnostic=case.get('chain_diagnostic')
    if case['treatment']=='native':
        if diagnostic!={'enabled':False,'reason':'native_uninstrumented','rows':[]}:
            raise ValueError('native per-chain diagnostic declaration invalid')
        if case['waits'] or any(case[k] for k in ('covered_accesses','covered_bytes','bypass_accesses','bypass_bytes','rejected_accesses','trace_overflow')):
            raise ValueError('native per-chain path contains instrumented observations')
        return
    required={'enabled','magic','symbol_bytes','version','config_bytes','delay_ns','launch_epoch',
              'grid_x','grid_y','grid_z','block_x','block_y','block_z','warps_per_block','hops',
              'row_count','row_stride','trace_capacity',
              'storage_address','storage_bytes','chain_output_address','chain_output_bytes',
              'block_output_address','block_output_bytes','rows'}
    if type(diagnostic) is not dict or set(diagnostic)!=required or diagnostic['enabled'] is not True:
        raise ValueError('chain diagnostic schema invalid')
    count=case['blocks']*case['warps'];capacity=case['hops']+7
    row_bytes=128+capacity*56;row_stride=(row_bytes+63)&~63
    scalar_expected={'magic':0x4556434841494e31,'symbol_bytes':136,'version':2,
                     'config_bytes':136,'delay_ns':case['applied_delay_ns'],
                     'launch_epoch':expected_epoch,
                     'grid_x':case['blocks'],'grid_y':1,'grid_z':1,
                     'block_x':case['warps']*32,'block_y':1,'block_z':1,
                     'warps_per_block':case['warps'],'hops':case['hops'],'row_count':count,
                     'row_stride':row_stride,'trace_capacity':capacity,
                     'storage_bytes':count*row_stride,'chain_output_bytes':count*32,
                     'block_output_bytes':case['blocks']*24}
    if any(type(diagnostic.get(key)) is not int or diagnostic[key]!=value for key,value in scalar_expected.items()):
        raise ValueError('chain diagnostic declaration mismatch')
    spans=[(case['input_base'],case['input_bytes']),
           (diagnostic['chain_output_address'],diagnostic['chain_output_bytes']),
           (diagnostic['block_output_address'],diagnostic['block_output_bytes']),
           (diagnostic['storage_address'],diagnostic['storage_bytes'])]
    _disjoint_spans(spans)
    if type(diagnostic['rows']) is not list or len(diagnostic['rows'])!=count:
        raise ValueError('chain diagnostic rows incomplete')
    chains={(row['block'],row['warp']):row for row in case['chains']}
    block_rows={row['block']:row for row in case['block_intervals']}
    if len(block_rows)!=case['blocks'] or set(block_rows)!=set(range(case['blocks'])):
        raise ValueError('chain diagnostic block observations incomplete')
    totals={key:0 for key in ('covered_accesses','covered_bytes','bypass_accesses','bypass_bytes','rejected_accesses','trace_overflow')}
    actual_waits=[]
    for index,row in enumerate(diagnostic['rows']):
        expected_keys={'row','covered_accesses','covered_bytes','bypass_accesses','bypass_bytes',
                       'rejected_accesses','trace_overflow','event_count','launch_epoch','writer_thread_id',
                       'writer_observed','reserved','unused_slots_zero','events'}
        if type(row) is not dict or set(row)!=expected_keys or type(row['row']) is not int or row['row']!=index:
            raise ValueError('chain diagnostic row schema/identity mismatch')
        block=index//case['warps'];warp=index%case['warps'];chain=chains[(block,warp)]
        bypass=7 if warp==0 else 4
        row_expected={'covered_accesses':case['hops'],'covered_bytes':case['hops']*4,
                      'bypass_accesses':bypass,'bypass_bytes':bypass*8,
                      'rejected_accesses':0,'trace_overflow':0,
                      'event_count':case['hops']+bypass,
                      'launch_epoch':expected_epoch,
                      'writer_thread_id':index*32,'writer_observed':1,'reserved':0}
        if any(type(row.get(key)) is not int or row[key]!=value for key,value in row_expected.items()) or row['unused_slots_zero'] is not True:
            raise ValueError('chain diagnostic row owner/counter/reset mismatch')
        for key in totals:totals[key]+=row[key]
        expected=[]
        if warp==0:
            base=diagnostic['block_output_address']+block*24
            expected.extend(((base,8,1,3),(base+16,8,1,3)))
        next_index=index&4095
        for _ in range(case['hops']):
            expected.append((case['input_base']+next_index*4096,4,0,1))
            next_index=(next_index*17+1)&4095
        base=diagnostic['chain_output_address']+index*32
        expected.extend((base+offset,8,1,2) for offset in (0,8,16,24))
        if warp==0:expected.append((diagnostic['block_output_address']+block*24+8,8,1,3))
        events=row['events']
        if type(events) is not list or len(events)!=len(expected):
            raise ValueError('chain diagnostic event count mismatch')
        previous=0
        for order,(event,wanted) in enumerate(zip(events,expected)):
            keys={'thread_id','address','order','begin_ns','end_ns','bytes','operation','event_class','status'}
            if type(event) is not dict or set(event)!=keys or any(not _u64(event[key]) for key in keys):
                raise ValueError('chain diagnostic event schema/type invalid')
            identity=(event['address'],event['bytes'],event['operation'],event['event_class'])
            if event['thread_id']!=index*32 or event['order']!=order or event['status']!=1 or identity!=wanted:
                raise ValueError('chain diagnostic event identity mismatch')
            if event['begin_ns']<previous:
                raise ValueError('chain diagnostic event time order mismatch')
            if event['event_class']==1:
                delay=case['applied_delay_ns']
                if event['begin_ns']<chain['begin_ns'] or event['end_ns']<event['begin_ns'] or event['end_ns']-event['begin_ns']<delay or (not delay and event['end_ns']!=event['begin_ns']) or event['end_ns']>chain['end_ns']:
                    raise ValueError('chain diagnostic load timing mismatch')
                actual_waits.append(dict(thread_id=event['thread_id'],address=event['address'],wait_enter_ns=event['begin_ns'],wait_exit_ns=event['end_ns'],delay_ns=delay))
            elif event['begin_ns']!=event['end_ns']:
                raise ValueError('chain diagnostic store timestamp mismatch')
            elif event['event_class']==2 and event['begin_ns']<chain['end_ns']:
                raise ValueError('chain diagnostic store precedes chain completion')
            elif event['event_class']==3:
                block_row=block_rows[block]
                recorded=block_row['end_ns'] if event['address']==diagnostic['block_output_address']+block*24+8 else block_row['begin_ns']
                if event['begin_ns']<recorded:
                    raise ValueError('chain diagnostic block store precedes timestamp')
            previous=event['end_ns']
    if any(case[key]!=value for key,value in totals.items()):
        raise ValueError('chain diagnostic aggregate counters mismatch')
    if case['waits']!=actual_waits:
        raise ValueError('chain diagnostic waits differ from actual events')


def analyze(cases):
    """Validate per-chain pairs, never sum parallel chains into one latency."""
    if set(cases)!={'native','matched_zero','target'}:raise ValueError('missing control triplet')
    baseline=cases['matched_zero'];target=cases['target'];native=cases['native']
    if any(type(case.get('trace_mode','legacy')) is not str for case in cases.values()):
        raise ValueError('invalid trace mode type')
    trace_modes={case.get('trace_mode','legacy') for case in cases.values()}
    if len(trace_modes)!=1 or next(iter(trace_modes)) not in ('legacy','per_chain'):
        raise ValueError('unmatched trace modes')
    per_chain=next(iter(trace_modes))=='per_chain'
    dimensions=('hops','warps','occupancy','blocks','sm_count','requested_delay_ns')
    if any(any(case[k]!=baseline[k] for k in dimensions) for case in cases.values()):raise ValueError('unmatched triplet dimensions')
    if native['treatment']!='native' or baseline['treatment']!='fast_logical':raise ValueError('wrong baseline treatment')
    if target['treatment'] not in ('native','fast_logical','hbf_logical'):raise ValueError('invalid target treatment')
    residency={}
    def integer(value):return type(value) is int and 0<=value<2**64
    for name,case in cases.items():
        hops=case['hops'];count=case['blocks']*case['warps'];eligible=count*hops*4
        if not isinstance(hops,int) or hops not in (1,16,64) or len(case['chains'])!=count:raise ValueError('incomplete chains')
        identities=[(r['block'],r['warp']) for r in case['chains']]
        if set(identities)!={(b,w) for b in range(case['blocks']) for w in range(case['warps'])}:raise ValueError('missing/duplicate chain identity')
        if any(not all(integer(r[k]) for k in ('begin_ns','end_ns','checksum','expected_checksum','sm')) or
               r['checksum']!=r['expected_checksum'] or r['end_ns']<=r['begin_ns'] for r in case['chains']):raise ValueError('checksum/time invalid')
        if not math.isfinite(case['event_ns']) or case['event_ns']<=0:raise ValueError('invalid CUDA Event time')
        if any(case[k]!=0 for k in ('rejected_accesses','trace_overflow','unsupported_instructions','unknown_bytes')):raise ValueError('incomplete coverage')
        instrumented=case['treatment']!='native'
        if per_chain:validate_chain_diagnostic(case)
        expected_delay=case['requested_delay_ns'] if case['treatment']=='hbf_logical' else 0
        if case['applied_delay_ns']!=expected_delay:raise ValueError('delay/treatment mismatch')
        if case['covered_bytes']!=(eligible if instrumented else 0) or case['covered_accesses']!=(count*hops if instrumented else 0):raise ValueError('dynamic coverage mismatch')
        if case['bypass_bytes']!=(count*32+case['blocks']*24 if instrumented else 0):raise ValueError('unknown dynamic bypass bytes')
        if instrumented and case['rewritten_instructions']<=0:raise ValueError('missing rewritten instructions')
        if not instrumented and (case['rewritten_instructions'] or case['waits']):raise ValueError('native path was instrumented')
        if len(case['waits'])!=(count*hops if instrumented else 0):raise ValueError('missing helper waits')
        threads=Counter(wait['thread_id'] for wait in case['waits'])
        if instrumented and threads!={chain*32:hops for chain in range(count)}:raise ValueError('missing/duplicate helper thread coverage')
        waits_by_thread={}
        for wait in case['waits']:
            if not all(integer(wait[k]) for k in ('thread_id','address','wait_enter_ns','wait_exit_ns','delay_ns')) or wait['delay_ns']!=expected_delay or wait['wait_exit_ns']<wait['wait_enter_ns']+expected_delay or (expected_delay==0 and wait['wait_exit_ns']!=wait['wait_enter_ns']):
                raise ValueError('invalid helper wait stamp')
            waits_by_thread.setdefault(wait['thread_id'],[]).append(wait)
        if case['input_bytes']!=4096*4096 or not integer(case['input_base']) or case['input_base']==0:raise ValueError('unmatched input geometry')
        for chain in case['chains']:
            index=chain['block']*case['warps']+chain['warp'];next_index=index&4095
            previous=chain['begin_ns']
            for wait in sorted(waits_by_thread.get(index*32,[]),key=lambda r:r['wait_enter_ns']):
                if wait['address']!=case['input_base']+next_index*4096 or wait['wait_enter_ns']<previous or wait['wait_exit_ns']>chain['end_ns']:
                    raise ValueError('helper stamps do not match dependent chain/address permutation')
                next_index=(next_index*17+1)&4095;previous=wait['wait_exit_ns']
        intervals=case['block_intervals']
        if not integer(case['sm_count']) or case['sm_count']==0 or any(not integer(r['sm']) for r in intervals):
            raise ValueError('invalid observed SM identity/count')
        block_sms={r['block']:r['sm'] for r in intervals}
        if len(intervals)!=case['blocks'] or set(block_sms)!=set(range(case['blocks'])):
            raise ValueError('incomplete block/SM observations')
        if any(chain['sm']!=block_sms[chain['block']] for chain in case['chains']):
            raise ValueError('inconsistent block/chain SM identity')
        # %smid is an opaque identifier: NVIDIA permits sparse numbering, so
        # its numeric value can exceed the physical multiprocessor count.
        residency[name]=observed_residency(intervals)
        if len(residency[name])>case['sm_count']:raise ValueError('observed SM count exceeds physical count')
        peak=max(residency[name].values(),default=0)
        if peak>case['theoretical_blocks_per_sm'] or (case['occupancy']=='low' and peak!=1) or (case['occupancy']=='high' and peak<2):
            raise ValueError('requested occupancy unsupported by observed residency')
    by_id=lambda case:{(r['block'],r['warp']):r for r in case['chains']}
    pairs=by_id(baseline);native_pairs=by_id(native)
    deltas=[]
    for ident,row in by_id(target).items():
        other=pairs[ident]
        if row['checksum']!=other['checksum'] or row['checksum']!=native_pairs[ident]['checksum']:raise ValueError('native/zero/target checksum differs')
        deltas.append(((row['end_ns']-row['begin_ns'])-(other['end_ns']-other['begin_ns']))/target['hops'])
    applied=target['applied_delay_ns'];errors=sorted(abs(value-applied) for value in deltas)
    mean=statistics.mean(errors);p95=errors[max(0,math.ceil(.95*len(errors))-1)]
    mean_limit=max(100,.1*applied);p95_limit=max(200,.2*applied)
    return dict(per_access_delta_ns=deltas,event_delta_ns=target['event_ns']-baseline['event_ns'],
        native_event_ns=native['event_ns'],matched_zero_event_ns=baseline['event_ns'],
        mean_absolute_error_ns=mean,p95_absolute_error_ns=p95,mean_absolute_error_limit_ns=mean_limit,
        p95_absolute_error_limit_ns=p95_limit,g2_cell_pass=(mean<=mean_limit and p95<=p95_limit) if applied else None,
        g2_gate_closed=False,scope='single triplet; matrix-wide repeated gate remains open',
        observed_peak_blocks_per_sm=residency,zero_delay_absolute_noise_ns=deltas if not applied else None)


def execute(plan,out,build,profile,gpu_uuid,*,trace_mode='legacy',gpu_probe=None,child_runner=None):
    out=pathlib.Path(out).resolve();build=pathlib.Path(build).resolve();profile=pathlib.Path(profile).resolve()
    test_only=gpu_probe is not None or child_runner is not None
    if trace_mode not in ('legacy','per_chain'):raise ValueError('invalid trace mode')
    if not out.is_relative_to(ROOT) or out.is_relative_to(ROOT/'results/runs'):
        raise ValueError('standalone outputs must stay in checkout, outside scheduler results/runs')
    out.mkdir(parents=True,exist_ok=False)
    manifest=dict(schema_version=1,created_at=now(),plan=plan,resource_class='GPU_EXCLUSIVE',gpu_uuid=gpu_uuid,
                  evidence='TEST_ONLY' if test_only else 'GPU_ACQUISITION',trace_mode=trace_mode,
                  git=git_snapshot(ROOT),inputs={},commands={})
    atomic_json(out/'manifest.json',manifest);atomic_json(out/'environment.json',environment_snapshot())
    status=dict(state='PLANNED',updated_at=now());atomic_json(out/'status.json',status)
    signals={'signal':None};old_handlers={};final='FAILED'
    def update(state,**fields):
        status.update(state=state,updated_at=now(),**fields);atomic_json(out/'status.json',status)
    def interrupted(number,_frame):signals['signal']=number
    try:
        for number in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):old_handlers[number]=signal.signal(number,interrupted)
        with ResourceGuard(ROOT/'results',out,dict(resource_class='GPU_EXCLUSIVE',gpu_uuid=gpu_uuid),gpu_probe=gpu_probe) as guard:
            paths=dict(binary=build/'benchmarks/cuda/hbf_dependent_delay',ptx=build/'benchmarks/cuda/hbf_dependent_delay.ptx',
                       helper=build/'generated/hbf_device.ptx',plugin=build/'libptxpass_hbf.so',gate=build/'libhbfsim_launch_gate.so',
                       daemon=build/'hbfsimd',build_config=build/'CMakeCache.txt',profile=profile,matrix=pathlib.Path(plan['matrix']),
                       runner=pathlib.Path(__file__).resolve(),benchmark_source=ROOT/'benchmarks/cuda/hbf_dependent_delay.cu',
                       helper_source=ROOT/'src/cuda_runtime/device/hbf_device.cu',helper_header=ROOT/'src/cuda_runtime/device/hbf_device.cuh')
            frozen={key:regular_bytes(path) for key,path in paths.items()}
            manifest['inputs']={key:dict(path=str(paths[key]),sha256=hashlib.sha256(data).hexdigest()) for key,data in frozen.items()}
            if manifest['inputs']['matrix']['sha256']!=plan['matrix_sha256']:raise ValueError('matrix changed since plan')
            if make_plan(paths['matrix'],plan['condition']['cell_id'],plan['replicate'],plan['hops'])!=plan:raise ValueError('plan binding mismatch')
            runtime_profile=json.loads(frozen['profile'])
            if runtime_profile.get('time_scale')!=1 or any(type(runtime_profile.get(k)) is not int or runtime_profile[k]<=0
                    for k in ('read_latency_ns','program_latency_ns','aggregate_bandwidth_bytes_per_s')):
                raise ValueError('positive normal profile and time_scale=1 required')
            for symbol in ('__hbfsim_eval_delay_config','__hbfsim_eval_delay_counters','__hbfsim_resolve'):
                if symbol.encode() not in frozen['helper']:raise ValueError('build lacks known-delay helper symbol '+symbol)
            if trace_mode=='per_chain' and b'__hbfsim_eval_chain_diagnostic_config' not in frozen['helper']:
                raise ValueError('build lacks per-chain diagnostic symbol')
            (out/'inputs').mkdir()
            for key in ('matrix','profile','helper','ptx','build_config'):(out/'inputs'/key).write_bytes(frozen[key])
            row=plan['condition'];cases={}
            for name,treatment in (('native','native'),('matched_zero','fast_logical'),('target',row['profile'])):
                for key,path in paths.items():
                    if hashlib.sha256(regular_bytes(path)).hexdigest()!=manifest['inputs'][key]['sha256']:raise ValueError('input changed: '+key)
                directory=out/name;directory.mkdir()
                argv=[str(paths['binary']),'--treatment',treatment,'--delay-ns',str(plan['delay_ns']),'--hops',str(plan['hops']),
                      '--warps',row['warps'],'--occupancy',row['occupancy'],'--profile',str(paths['profile']),
                      '--plugin',str(paths['plugin']),'--ptx',str(paths['ptx']),'--output',str(directory/'raw.json'),
                      '--report-dir',str(directory/'reports'),'--trace-mode',trace_mode]
                manifest['commands'][name]=argv;atomic_json(out/'manifest.json',manifest)
                env={k:v for k,v in os.environ.items() if k not in ('LD_PRELOAD','LD_AUDIT') and not k.startswith(('HBFSIM_','BPFTIME_','PTX_PASS_'))}
                env.update(CUDA_VISIBLE_DEVICES=gpu_uuid,HBFSIM_DAEMON_PATH=str(paths['daemon']),
                           HBFSIM_PASS_MANIFEST_PATH=str(directory/'pass.jsonl'),HBFSIM_COVERAGE_PATH=str(directory/'coverage.jsonl'))
                if treatment!='native':env['LD_PRELOAD']=str(paths['gate'])
                code=(child_runner or run_child)(argv,env,directory,'producer',guard,
                    lambda child,phase:update('RUNNING',child=child,phase=phase,case=name),signals,30,.1)
                if code:raise FailedRun('benchmark failed: '+name)
                files,rejected=artifact_inventory(out)
                if rejected:raise ValueError('unsafe attempt artifacts')
                cases[name]=json.loads(regular_bytes(directory/'raw.json'))
                expected=dict(treatment=treatment,hops=plan['hops'],warps=int(row['warps']),
                              occupancy=row['occupancy'],requested_delay_ns=plan['delay_ns'],trace_mode=trace_mode)
                for field,value in expected.items():
                    observed=cases[name].get(field)
                    if type(observed) is not type(value) or observed!=value:
                        raise ValueError('result '+field+' differs from selected plan/argv')
                if not test_only and cases[name].get('evidence')!='GPU_ACQUISITION':raise ValueError('nonphysical result rejected')
                if treatment!='native':
                    decisions=[json.loads(line) for line in regular_bytes(directory/'coverage.jsonl').splitlines() if line.strip()]
                    if not decisions or not all(d.get('allowed') is True and d.get('modeled') is True for d in decisions):raise ValueError('launch coverage gate failed')
            guard.check('end-acquisition')
            for key,path in paths.items():
                if hashlib.sha256(regular_bytes(path)).hexdigest()!=manifest['inputs'][key]['sha256']:raise ValueError('input changed: '+key)
            report=analyze(cases);atomic_json(out/'raw.analysis.json',dict(report,evidence=manifest['evidence']))
            if report['g2_cell_pass'] is False:raise ValueError('fixed G2 error limits exceeded')
            final='TEST_ONLY_DONE' if test_only else 'DONE'
    except ResourceBusy as error:final=error.state;status['error']=str(error)
    except (InterruptedRun,KeyboardInterrupt) as error:final='INTERRUPTED';status['error']=str(error)
    except Exception as error:final='INVALID_GOLD_GATE';status['error']=str(error)
    finally:
        for number,handler in old_handlers.items():signal.signal(number,handler)
        files,rejected=artifact_inventory(out)
        manifest['artifact_hashes']={}
        for name,path in files.items():
            data=regular_bytes(path);manifest['artifact_hashes'][name]=hashlib.sha256(data).hexdigest()
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('artifact changed type')
                os.fsync(fd)
            finally:os.close(fd)
        manifest['rejected_artifacts']=rejected
        if rejected:final='INVALID_GOLD_GATE'
        atomic_json(out/'manifest.json',manifest)
        update(final)
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix',type=pathlib.Path,default=ROOT/'docs/49-eval-audit/run-matrix.csv')
    parser.add_argument('--cell-id',required=True);parser.add_argument('--replicate',type=int,default=1)
    parser.add_argument('--hops',type=int,choices=(1,16,64),default=64)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--out',type=pathlib.Path);parser.add_argument('--build-dir',type=pathlib.Path)
    parser.add_argument('--profile',type=pathlib.Path);parser.add_argument('--gpu-uuid')
    parser.add_argument('--trace-mode',choices=('legacy','per_chain'),default='legacy')
    args=parser.parse_args();plan=make_plan(args.matrix,args.cell_id,args.replicate,args.hops)
    if not args.execute or args.dry_run:print(json.dumps(plan,indent=2));return 0
    if not all((args.out,args.build_dir,args.profile,args.gpu_uuid)):parser.error('execution requires --out --build-dir --profile --gpu-uuid')
    result=execute(plan,args.out,args.build_dir,args.profile,args.gpu_uuid,
                   trace_mode=args.trace_mode);print(json.dumps(result,indent=2))
    return 0 if result['state']=='DONE' else 2


if __name__=='__main__':raise SystemExit(main())
