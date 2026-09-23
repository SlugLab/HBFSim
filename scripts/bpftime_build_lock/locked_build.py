#!/usr/bin/env python3
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;LOCK=json.loads((ROOT/'LOCK.json').read_text())
def H(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for x in iter(lambda:f.read(1<<20),b''):h.update(x)
 return h.hexdigest()
def CV(p):
 d={}
 for x in Path(p).read_text(errors='replace').splitlines():
  if x and x[0] not in '#/' and '=' in x:d[x.split('=',1)[0].split(':',1)[0]]=x.split('=',1)[1]
 return d
def WR(p,o):
 fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump(o,f,indent=2);f.write('\n')
def main():
 a=argparse.ArgumentParser();a.add_argument('mode',choices=('check','configure','build'));a.add_argument('--profile',required=True);a.add_argument('--source',required=True);a.add_argument('--build',required=True);a.add_argument('--receipt',required=True);a.add_argument('--target');a.add_argument('--jobs',type=int,default=2);a.add_argument('--dry-run',action='store_true');q=a.parse_args()
 r=Path(q.receipt).resolve();d={'schema':'hbfsim.locked_build_receipt.v1','mode':q.mode,'profile':q.profile,'source':str(Path(q.source).resolve()),'build':str(Path(q.build).resolve()),'time_ns':time.time_ns()}
 def no(msg):d.update(status='REJECTED',reason=msg,spawned=False);WR(r,d);print(msg,file=sys.stderr);return 2
 if r.exists():print('receipt exists',file=sys.stderr);return 2
 p=LOCK['profiles'].get(q.profile)
 if not p or p.get('status')!='READY':return no('profile not READY')
 for n,t in p['tools'].items():
  x=Path(t['path']);rp=Path(os.path.realpath(x))
  if str(rp)!=t['realpath'] or not rp.is_file() or H(rp)!=t['sha256']:return no('tool drift: '+n)
 for k in p['protected_env']:
  if k in os.environ and (k not in p['fixed_env'] or os.environ[k]!=p['fixed_env'][k]):return no('protected environment mismatch: '+k)
 s=Path(q.source).resolve();b=Path(q.build).resolve()
 if not (s/'CMakeLists.txt').is_file() or H(s/'CMakeLists.txt')!=p['source_cmakelists_sha256']:return no('source CMakeLists drift')
 env=os.environ.copy();env.update(p['fixed_env']);args=['/usr/bin/cmake','-S',str(s),'-B',str(b),'-G','Ninja']+[f'-D{k}={v}' for k,v in p['expected_cache'].items() if k!='CMAKE_GENERATOR']
 if q.mode=='configure':
  if b.exists() and any(b.iterdir()):return no('configure requires absent or empty build directory')
 else:
  if not (b/'CMakeCache.txt').is_file():return no('missing cache')
  c=CV(b/'CMakeCache.txt')
  for k,v in p['expected_cache'].items():
   if c.get(k)!=v:return no(f'cache mismatch {k}: {c.get(k)!r} != {v!r}')
  if c.get('CMAKE_HOME_DIRECTORY')!=str(s):return no('cache source mismatch')
  if not (b/'compile_commands.json').is_file():return no('missing compile_commands')
  rows=json.loads((b/'compile_commands.json').read_text());row=next((x for x in rows if x.get('file','').endswith('nv_attach_impl_frida_setup.cpp')),None)
  if not row:return no('missing attach compile command')
  for t in p['compile_command_invariants']:
   if t not in row.get('command',''):return no('compile invariant missing: '+t)
  if q.mode=='build':
   target=q.target or p['allowed_targets'][0]
   if target not in p['allowed_targets']:return no('target not allowed')
   if q.jobs<1 or q.jobs>4:return no('jobs outside 1..4')
   args=['/usr/bin/cmake','--build',str(b),'--target',target,'--parallel',str(q.jobs)]
 if q.mode=='check':d.update(status='PASS',spawned=False,cache_sha256=H(b/'CMakeCache.txt'),compile_commands_sha256=H(b/'compile_commands.json'))
 elif q.dry_run:d.update(status='PASS_DRY_RUN',spawned=False,argv=args,env={k:env[k] for k in p['fixed_env']})
 else:
  z=subprocess.run(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  post_error=''
  if z.returncode==0 and q.mode=='configure':
   if not (b/'CMakeCache.txt').is_file():post_error='post-config missing cache'
   else:
    c=CV(b/'CMakeCache.txt')
    for k,v in p['expected_cache'].items():
     if c.get(k)!=v:post_error=f'post-config cache mismatch {k}: {c.get(k)!r} != {v!r}';break
    if not post_error and c.get('CMAKE_HOME_DIRECTORY')!=str(s):post_error='post-config source mismatch'
   if not post_error and not (b/'compile_commands.json').is_file():post_error='post-config missing compile_commands'
   if not post_error:
    rows=json.loads((b/'compile_commands.json').read_text());row=next((x for x in rows if x.get('file','').endswith('nv_attach_impl_frida_setup.cpp')),None)
    if not row:post_error='post-config missing attach compile command'
    else:
     for t in p['compile_command_invariants']:
      if t not in row.get('command',''):post_error='post-config compile invariant missing: '+t;break
  status='PASS' if z.returncode==0 and not post_error else 'FAILED_POSTCONFIG' if post_error else 'FAILED'
  d.update(status=status,spawned=True,argv=args,returncode=z.returncode,stdout=z.stdout,stderr=z.stderr,post_error=post_error)
 d['lock_sha256']=H(ROOT/'LOCK.json');WR(r,d);return 0 if d['status'].startswith('PASS') else 1
if __name__=='__main__':raise SystemExit(main())
