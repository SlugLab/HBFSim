"""Common-sensor comparisons; never solve or change predeclared criteria."""
import argparse,csv,json,math
from pathlib import Path

def read(path):
    result={}
    with path.open() as f:
        for row in csv.DictReader(f):
            sid=row['sensor_id'];t=float(row['time_s']);v=float(row['temperature_k'])
            if not math.isfinite(v):raise ValueError('nonfinite')
            seq=result.setdefault(sid,[])
            if seq and t<=seq[-1][0]:raise ValueError('time order')
            seq.append((t,v))
    return result

def crossings(seq,threshold):
    # All transitions, both heating and cooling, linear interpolation of the
    # registered0.1s observations; t0 explicitly from declared300K initial state.
    out=[];previous=(0.,300.)
    for current in seq:
        t0,v0=previous;t,v=current
        if (v0<threshold<=v) or (v<threshold<=v0):out.append(('up' if v>v0 else 'down',t0+(t-t0)*(threshold-v0)/(v-v0)))
        previous=current
    return out

def compare(reference,candidate,mode):
    ref=read(reference);cand=read(candidate)
    if set(ref)!=set(cand):raise ValueError('sensor coverage differs')
    rows=[]
    for sid,rv in sorted(ref.items()):
        cv=cand[sid]
        if len(rv)!=len(cv) or any(abs(a[0]-b[0])>1e-9 for a,b in zip(rv,cv)):raise ValueError('time mismatch')
        errors=[abs(a[1]-b[1]) for a,b in zip(rv,cv)];mae=math.fsum(errors)/len(errors);maximum=max(errors)
        scale=max(max(v for t,v in rv)-min(v for t,v in rv),1.)
        crossing=[]
        for threshold in [301,330]:
            a=crossings(rv,threshold);b=crossings(cv,threshold)
            ok=len(a)==len(b) and all(x[0]==y[0] and abs(x[1]-y[1])<=max(.2,.05*x[1]) for x,y in zip(a,b))
            crossing.append({'probe_k':threshold,'reference':a,'candidate':b,'status':'NOT_APPLICABLE' if not a and not b else 'PASS' if ok else 'FAIL'})
        ok=maximum<=.25 if mode=='reference' else (mae<=1 and mae/scale<=.05 and ('hotspot' not in sid or maximum<=2) and all(x['status']!='FAIL' for x in crossing))
        rows.append({'sensor_id':sid,'mae_k':mae,'max_abs_k':maximum,'normalized_mae':mae/scale,'reference_range_k':scale,'crossings':crossing,'passed':ok})
    return {'status':'PASS' if all(r['passed'] for r in rows) else 'NUMERICAL_FAIL','mode':mode,'max_abs_k':max(r['max_abs_k'] for r in rows),'worst_mae_k':max(r['mae_k'] for r in rows),'worst_normalized_mae':max(r['normalized_mae'] for r in rows),'sensors':rows,'crossing_semantics':'all ascending and descending transitions interpolated on0.1s observations; declared initial300K','physical_calibration':False}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reference',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--mode',choices=['reference','rc'],required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=compare(a.reference,a.candidate,a.mode)
    with a.output.open('x') as f:json.dump(r,f,indent=2)
    print(json.dumps({k:v for k,v in r.items() if k!='sensors'}))
