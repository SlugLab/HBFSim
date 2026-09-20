#!/usr/bin/env python3
"""Read-only six-panel timeline of actual raw observations; no solver calls."""
import argparse
import json
import math
from pathlib import Path
from analyze_points import analyze_point, _rows, _integer, _cell

def plot(point, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    evidence = analyze_point(point)
    rows = evidence['time_series']
    x = [(r['start_ns']+r['end_ns'])/2e9 for r in rows]
    def values(key, scale=1):
        out=[]
        for r in rows:
            value=r
            for field in key.split('.'): value=value.get(field) if isinstance(value,dict) else None
            out.append(float(value)/scale if value is not None else math.nan)
        return out
    latest={}
    for row in _rows(point/'maintenance.csv'):
        if row.get('request_id'): latest[row['request_id']]=row
    maintenance=[]
    for row in latest.values():
        completion=_cell(row.get('completion'));completion=completion if isinstance(completion,dict) else {}
        reset=_integer(row.get('age_reset_ns'))
        if reset is None:reset=_integer(completion.get('age_reset_ns'))
        maintenance.append((_integer(row.get('due_ns')),reset,_integer(row.get('bytes'))))
    backlog=[];rate=[]
    for row in rows:
        a,b=row['start_ns'],row['end_ns']
        backlog.append(sum(d is not None and d<=b and (r is None or r>b) for d,r,_ in maintenance))
        rate.append(sum(size for _,r,size in maintenance if size is not None and r is not None and a<=r<b)/((b-a)/1e9))
    plt.rcParams.update({'font.size':9,'axes.grid':True,'grid.alpha':.2})
    fig,axes=plt.subplots(6,1,figsize=(11,14),sharex=True,constrained_layout=True)
    fig.suptitle(point.name+' — conditional CPU simulation; no token-rate claim')
    axes[0].step(x,values('offered_bytes_per_s',1e6),label='All foreground offered',where='mid',color='gray')
    physical_rate=[r['physical_delivered_bytes']/((r['end_ns']-r['start_ns'])/1e9)/1e6 if r['physical_delivered_bytes'] is not None else math.nan for r in rows]
    axes[0].step(x,physical_rate,label='All foreground physical delivery',where='mid')
    axes[0].set_ylabel('MB/s');axes[0].legend(loc='upper right')
    for key,label in [('gpu','GPU'),('hbf_max','HBF hotspot'),('hbm_max','HBM hotspot')]:
        axes[1].plot(x,values('temperatures_k.'+key),label=label)
    axes[1].axhline(383.15,ls=':',color='red',label='GPU stop proxy (110 C)')
    axes[1].set_ylabel('K');axes[1].legend(ncol=4)
    axes[2].step(x,values('control_permit_bytes',2**20),where='mid',label='Future permit')
    axes[2].set_ylabel('MiB/window');axes[2].legend()
    axes[3].step(x,backlog,where='mid',label='Due pages without commit (includes rejected)')
    right=axes[3].twinx();right.plot(x,[v/1024 for v in rate],color='tab:orange',label='Committed KiB/s')
    axes[3].set_ylabel('Pages');right.set_ylabel('Commit KiB/s');axes[3].legend(loc='upper left');right.legend(loc='upper right')
    axes[4].plot(x,values('tail_latency_p95_ns',1e9),label='Completed-window P95')
    right=axes[4].twinx();right.plot(x,values('incomplete_or_censored_requests'),color='tab:red',label='Incomplete at window end')
    axes[4].set_ylabel('Latency s');right.set_ylabel('Requests');axes[4].legend(loc='upper left');right.legend(loc='upper right')
    axes[5].step(x,values('effective_delivered_hbf_bytes_per_s',1e6),where='mid',label='HBF effective final delivery')
    axes[5].step(x,values('effective_delivered_hbm_bytes_per_s',1e6),where='mid',label='HBM effective final delivery')
    axes[5].set_ylabel('MB/s');axes[5].legend();axes[5].set_xlabel('Simulation time (s)')
    for axis in axes:axis.axvline(evidence['scope']['active_ns']/1e9,color='black',ls=':',lw=.8)
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise ValueError('create-only plot output')
    fig.savefig(output,dpi=150);plt.close(fig)
    output.with_suffix('.json').write_text(json.dumps({'source':str(point.resolve()),'raw_modified':False,'maintenance_age_scope':'declared pages only','analysis':evidence},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--point',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();plot(a.point,a.output)
