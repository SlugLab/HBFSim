#!/usr/bin/env python3
"""Read-only selected historical hash/content audit; outputs only under report root."""
import csv, hashlib, json, pathlib, resource, subprocess, sys, time
resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
ROOT=pathlib.Path('/root/hbfsim-exp')
OUT=ROOT/'reports/eq-replan-20260908'
SOURCE=ROOT/'prototype-revalidation/sources/diagnostics/cell-c-phase2-adaptation-001'
RESULTS=ROOT/'prototype-revalidation/results'
NAMES=['final-requirement-audit-002','final-campaign-audit-001','vllm-2x2-001','cell-a-023','cell-c-002','app0-002','golden-0-001','cl-0-002','bw-0-001','hbm-0-001','workload-matrix-001','lit-1-001','lit-2-001']
cache={}; checks=[]; budget=128*1024*1024; bytes_read=0
start=time.monotonic()
def load(p): return json.loads(p.read_text())
def sha(p):
 global bytes_read
 p=pathlib.Path(p)
 if str(p) not in cache:
  if not p.is_file(): return None
  n=p.stat().st_size
  if bytes_read+n>budget: return 'DEFERRED_READ_BUDGET'
  h=hashlib.sha256()
  with p.open('rb') as f:
   for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
  bytes_read+=n;cache[str(p)]=h.hexdigest()
 return cache[str(p)]
def check(p,expected=None,label=''):
 p=pathlib.Path(p);actual=sha(p)
 checks.append({'path':str(p),'label':label,'expected_sha256':expected,'actual_sha256':actual,'status':('MISSING' if actual is None else 'DEFERRED' if actual=='DEFERRED_READ_BUDGET' else 'HASHED' if expected is None else 'MATCH' if expected==actual else 'MISMATCH')})
def walk(d):
 if isinstance(d,dict):
  if isinstance(d.get('path'),str) and isinstance(d.get('sha256'),str):check(d['path'],d['sha256'],'embedded path/hash')
  if isinstance(d.get('evidence_path'),str) and isinstance(d.get('evidence_sha256'),str):check(d['evidence_path'],d['evidence_sha256'],'evidence path/hash')
  for v in d.values():walk(v)
 elif isinstance(d,list):
  for v in d:walk(v)
loaded={}
for n in NAMES:
 file='core-provenance.json' if n.startswith('cell-') else 'app0-provenance.json' if n=='app0-002' else 'result.json'
 p=RESULTS/n/file;check(p,label='requested historical result');d=load(p);loaded[n]=d;walk(d)
 for rel,expected in d.get('artifact_sha256',{}).items():check(p.parent/rel,expected,'raw result artifact')
 if n.startswith('cell-'):
  target=p.parent/('gate-summary.json' if n=='cell-a-023' else 'cell-c-summary.json')
  check(target,d['evidence_sha256'],'core provenance target')
 if n=='app0-002':check(p.parent/'app0-summary.json',d['summary_sha256'],'APP0 provenance target')
for n in ['cl-0-002','bw-0-001']:
 walk(load(RESULTS/n/'experiment-manifest.json'))
bound=loaded['bw-0-001']['historical_boundary'];check(bound['summary_path'],bound['summary_sha256'],'historical boundary')
for n in ['cell-a-023','cell-c-002']:
 bm=load(RESULTS/n/'binary-manifest.json');s=pathlib.Path(bm['source']['path'])
 check(s/'CMakeLists.txt',bm['source']['cmake_lists_sha256'],'source build config')
# W0-W3 summaries bind separate raw provenance; validate their digest and referenced inputs.
for i,case in enumerate(loaded['workload-matrix-001']['cases']):
 p=RESULTS/f'w{i}-001/workload-provenance.json';check(p,case['provenance_sha256'],'workload provenance');walk(load(p))
observations={}
for n in ['cl-0-002','bw-0-001']:
 rows=list(csv.DictReader((RESULTS/n/'package-thermal-timeline.csv').open()))
 r=load(RESULTS/n/'runner-result.json'); times=[int(x['thermal_time_ns']) for x in rows]
 transitions=[int(x['thermal_time_ns']) for x in rows if x['gate_open']=='0']
 first=transitions[0] if transitions else None
 recovery=next((int(x['thermal_time_ns']) for x in rows if first is not None and int(x['thermal_time_ns'])>first and x['gate_open']=='1'),None)
 tail=rows[-1]; useful=int(tail['completed_requests'])*r['request_bytes']; physical=sum(int(x['MQSim_read_bytes'])+int(x['MQSim_program_bytes']) for x in rows)
 observations[n]={'rows':len(rows),'time_monotonic':all(a<b for a,b in zip(times,times[1:])),'first_time_ns':times[0],'last_time_ns':times[-1],'gate_time_ns':first,'recovery_time_ns':recovery,'completed_requests':int(tail['completed_requests']),'offered_requests':int(tail['submitted_requests']),'queued_requests':int(tail['queue_depth']),'useful_bytes_from_completions':useful,'runner_served_bytes':r['served_bytes'],'useful_byte_rate_whole_window':useful*1e9/r['modeled_duration_ns'],'physical_bytes_from_media_bins':physical,'media_events':sum(int(x['MQSim_events_this_bin']) for x in rows),'max_hotspot_c':max(float(x['T_hbf_hotspot']) for x in rows),'peak_hbf_w':max(float(x['P_hbf_total']) for x in rows)}
 if useful!=r['served_bytes']:raise RuntimeError('useful bytes mismatch')
 if not observations[n]['time_monotonic']:raise RuntimeError('nonmonotonic time')
states={}
for label,p in [('current',ROOT/'eval-base-integration'),('prototype',SOURCE)]:
 states[label]={'path':str(p),'head':subprocess.check_output(['git','-C',str(p),'rev-parse','HEAD'],text=True).strip(),'status':subprocess.check_output(['git','-C',str(p),'status','--short','--untracked-files=no'],text=True).splitlines()}
result={'audit_scope':'read-only selected historical hashes and CSV recount, not new thermal/GPU measurement','resource_budget':{'read_bytes_cap':budget,'address_space_bytes_cap':512*1024*1024},'bytes_hashed':bytes_read,'elapsed_wall_seconds':time.monotonic()-start,'source_states':states,'checks':checks,'counts':{s:sum(x['status']==s for x in checks) for s in ['HASHED','MATCH','MISMATCH','MISSING','DEFERRED']},'raw_recounts':observations,'limits':['Does not rehash model weight tensors or all historical binary components.','Stored fingerprints are historical unless their explicit constituent path/hash pair is verified here.','No GPU or new physical/thermal experiment executed.']}
(OUT/'thermal-check.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['counts','bytes_hashed','elapsed_wall_seconds','raw_recounts']},indent=2))
if result['counts']['MISMATCH'] or result['counts']['MISSING']:sys.exit(1)
