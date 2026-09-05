#!/usr/bin/env python3
"""One renderer for MOCK previews and audited results, with identical plotting functions."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import os
import pathlib
import sys
import tempfile
os.environ.setdefault('MPLCONFIGDIR',str(pathlib.Path(tempfile.gettempdir())/'hbfsim-eval-mpl'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from validate_results import load_validated

FIGURES = {
'fig-e1-hardware-fidelity': [
 ('gpu','delay_us','critical_delta_us','Target added delay (us)','Critical-path increment (us)','GPU dependent load chain'),
 ('ssd_p50','request_bytes','p50_us','Request size (KiB)','P50 latency (us)','Flash request latency'),
 ('ssd_p99','qd','p99_us','Queue depth','P99 latency (us)','Flash queueing'),
 ('ssd_rate','qd','throughput_gbs','Queue depth','Throughput (GB/s)','Flash saturation'),
 ('ssd_iops','qd','iops','Queue depth','IOPS','Flash I/O rate'),
 ('three_arm','delta_hw_us','delta_sim_us','Hardware increment (us)','Simulator increment (us)','Three-arm differential')],
'fig-e2-async-semantics': [
 ('residual','overlap_ratio','residual_norm','Independent work / D','Consume residual / D','Waiting at the consume point'),
 ('total','overlap_ratio','total_stall_norm','Independent work / D','Total exposed stall / D','Issue + consume waiting'),
 ('parity','oracle_residual_us','residual_us','Oracle residual (us)','Observed residual (us)','New-path residual parity')],
'fig-e3-hbf-feasibility': [(f'tr{d}','rho','decode_norm','Effective fast-tier residency rho','Sustainable service (GB/s)',f'tR = {d} us') for d in [1,2,4,5,10,20]],
'fig-e4-expert-union': [
 ('union','active_sequences','union_fraction','Active sequences','Unique expert union / E','Routing union'),
 ('entropy','active_sequences','routing_entropy','Active sequences','Routing entropy (bit)','Routing distribution'),
 ('reuse','active_sequences','jaccard','Active sequences','Inter-step Jaccard','Routing reuse')],
'fig-e5-workload-boundary': [
 ('moe','rho','decode_norm','Effective fast-tier residency rho','Normalized decode time','MoE'),
 ('dense_capacity','rho','decode_norm','Effective fast-tier residency rho','Normalized decode time','Capacity-matched dense'),
 ('dense_compute','rho','decode_norm','Effective fast-tier residency rho','Normalized decode time','Active-compute-matched dense'),
 ('concurrency','active_sequences','min_service_gbs','Active sequences','10% boundary service (GB/s)','Concurrency boundary at rho = 0.25')],
'fig-e6-robustness': [
 ('sensitivity','tR_us','decode_norm','Media latency (us)','Normalized decode time','Parameter sensitivity'),
 ('coverage','active_sequences','coverage_fraction','Active sequences','Eligible-byte coverage','Coverage envelope'),
 ('traffic','active_sequences','extra_traffic_ratio','Active sequences','Extra prefetch traffic / demand','Prefetch traffic cost')],
}
COLORS = ['#286C9B','#CE8329','#798743','#B25B85','#525252']
MARKERS = ['o','s','^','D','v','P','X']


def metric_points(rows, metric, x):
    """Pair sibling metrics within the same exact cell and run; never zip sorted lists."""
    by_cell = defaultdict(dict)
    for row in rows:
        key = tuple((k,v) for k,v in row.items() if k not in {'metric','value','unit'})
        if row['metric'] in by_cell[key]: raise ValueError('duplicate metric in cell')
        by_cell[key][row['metric']] = float(row['value'])
    points=[]
    for key, values in by_cell.items():
        if metric not in values: continue
        row = dict(key)
        if x in row:
            if row[x] == '': raise ValueError(f'missing x dimension {x}')
            xv = float(row[x])
        elif x in values: xv=values[x]
        else: raise ValueError(f'missing paired x metric {x}')
        points.append((row,xv,values[metric],values))
    if not points: raise ValueError(f'panel missing metric {metric}')
    return points


def summarize(points, x, varying=()):
    groups=defaultdict(list)
    contexts={}
    for row,xv,yv,values in points:
        # A selected paper panel is a frozen slice, not an implicit average over profiles.
        excluded={'run_id','replicate','source_file','source_function','source_line_start','source_line_end',x,*varying}
        context=tuple(sorted((k,v) for k,v in row.items() if k not in excluded))
        k=(row['series'],row['provenance'])
        old=contexts.setdefault(k,context)
        if old != context: raise ValueError(f'heterogeneous panel slice in {k}: split panels/series explicitly')
        groups[(k,xv)].append((int(row['replicate']),yv))
    output=defaultdict(list)
    for (key,xv),values in groups.items():
        if len({rep for rep,_ in values}) != len(values): raise ValueError('duplicate replicate at x/series')
        a=np.array([v for _,v in values]); n=len(a)
        rng=np.random.default_rng(49)
        if n>1:
            boot=np.mean(rng.choice(a,(2000,n),replace=True),axis=1)
            lo,hi=np.quantile(boot,[.025,.975])
        else: lo=hi=a[0]
        output[key].append((xv,float(a.mean()),lo,hi,n))
    return {k:sorted(v) for k,v in output.items()}


def plot_lines(ax, rows, spec):
    panel,x,metric,xlabel,ylabel,title=spec
    points=metric_points(rows,metric,x)
    if x in {'qd','active_sequences','request_bytes','rho'} and any(p[1] <= 0 for p in points):
        raise ValueError('logarithmic axis requires positive coordinates')
    vary={'overlap_ratio':('independent_work_us',), 'request_bytes':(), 'oracle_residual_us':('delay_us','overlap_ratio','independent_work_us')}.get(x,())
    if panel == 'parity':
        groups=defaultdict(list)
        contexts={}
        for row,xv,yv,metrics in points:
            key=(row['series'],row['provenance'])
            ignored={'run_id','replicate','source_file','source_function','source_line_start','source_line_end','delay_us','overlap_ratio','independent_work_us'}
            context=tuple(sorted((k,v) for k,v in row.items() if k not in ignored))
            if contexts.setdefault(key,context) != context:
                raise ValueError('heterogeneous parity series')
            groups[key].append((xv,yv))
        for i,((series,provenance),sample) in enumerate(sorted(groups.items())):
            ax.scatter([p[0] for p in sample],[p[1] for p in sample],s=16,
                marker=MARKERS[i%len(MARKERS)],color=COLORS[i%len(COLORS)],
                alpha=.7,label=series+' ['+provenance+']')
        hi=max(max(p[1],p[2]) for p in points)*1.05
        ax.plot([0,hi],[0,hi],':',color='#333333',label='y = x')
        ax.set(xlabel=xlabel,ylabel=ylabel,title=title)
        ax.legend(fontsize=6,frameon=False)
        return {'observations':len(points),'aggregation':'individual D/W observations'}
    curves=summarize(points,x,vary)
    for i,((series,provenance),samples) in enumerate(sorted(curves.items())):
        a=np.asarray(samples); xx=a[:,0]/1024 if x=='request_bytes' else a[:,0]
        label=series
        # Every final series discloses its evidence class; MOCK is additionally watermarked.
        if provenance!='MOCK': label += f' [{provenance}]'
        color='#555555' if series=='Analytical oracle' else COLORS[i%len(COLORS)]
        ax.errorbar(xx,a[:,1],yerr=[a[:,1]-a[:,2],a[:,3]-a[:,1]],color=color,
                    marker=MARKERS[i%len(MARKERS)],markersize=3,linewidth=1,
                    linestyle='--' if 'oracle' in series.lower() or 'null' in series.lower() else '-',
                    markerfacecolor='white' if 'Old' in series else color,capsize=2,label=label)
    if panel in {'gpu','three_arm','parity'}:
        lo,hi=ax.get_xlim(); lo=min(0,lo); ax.plot([lo,hi],[lo,hi],':',color='#333333',linewidth=1,label='y = x')
    if x in {'qd','active_sequences','request_bytes','rho'}:
        ax.set_xscale('log',base=2)
    if metric in {'decode_norm'}:
        for y in [1.05,1.10,1.20]: ax.axhline(y,color='#999999',linestyle=':',linewidth=.6)
    ax.set(xlabel=xlabel,ylabel=ylabel,title=title)
    ax.legend(fontsize=6,frameon=False,loc='best')
    return [{'series':k[0],'provenance':k[1],'points':len(v),'replicates_min':min(t[4] for t in v)} for k,v in curves.items()]


def plot_heatmap(ax,rows,spec,clim):
    panel,x,metric,xlabel,ylabel,title=spec
    points=metric_points(rows,metric,'rho')
    # Require one workload/profile/operation/provenance per heatmap panel.
    frozen=None; cells=defaultdict(list)
    for row,rho,value,metrics in points:
        if 'service_gbs' not in metrics: raise ValueError('boundary missing sustainable service_gbs')
        if rho <= 0 or metrics['service_gbs'] <= 0: raise ValueError('heatmap axes must be positive')
        key=tuple(sorted((k,v) for k,v in row.items() if k not in {'rho','parallel_units','run_id','replicate','source_line_start','source_line_end'}))
        if frozen is None: frozen=key
        elif frozen!=key: raise ValueError('heterogeneous feasibility panel')
        cells[(rho,metrics['service_gbs'])].append((int(row['replicate']),value))
    xs=sorted({p[0] for p in cells}); ys=sorted({p[1] for p in cells})
    if len(xs)<2 or len(ys)<2 or len(cells)!=len(xs)*len(ys): raise ValueError('incomplete feasibility grid; no interpolation across missing cells')
    z=np.zeros((len(ys),len(xs)))
    for j,yy in enumerate(ys):
        for i,xx in enumerate(xs):
            sample=cells[(xx,yy)]
            if len({r for r,_ in sample})!=len(sample): raise ValueError('duplicate heatmap replicate')
            z[j,i]=np.mean([v for _,v in sample])
    im=ax.pcolormesh(xs,ys,z,cmap='Blues',shading='nearest',vmin=clim[0],vmax=clim[1])
    levels=[v for v in [1.05,1.10,1.20] if z.min()<v<z.max()]
    if levels:
        cs=ax.contour(xs,ys,z,levels=levels,colors='#333333',linewidths=.8)
        ax.clabel(cs,fmt={1.05:'5%',1.10:'10%',1.20:'20%'},fontsize=6)
    provenance=rows[0]['provenance']
    if provenance in {'MOCK','PROJECTED'}: ax.text(.02,.97,'HYPOTHETICAL HBF PARAMETERS',transform=ax.transAxes,va='top',fontsize=5.7)
    ax.set(xscale='log',yscale='log',xlabel=xlabel,ylabel=ylabel,title=title)
    ax.set_xticks(xs,labels=[str(v) for v in xs],rotation=35,fontsize=6)
    plt.colorbar(im,ax=ax,label='Normalized decode time',shrink=.85)
    return {'grid':[len(xs),len(ys)],'contours':'linear interpolation in each sampled cell; no extrapolation','provenance':provenance}


def render(rows,out,watermark=False,selected=None):
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.titlesize':9,
        'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':170,'pdf.fonttype':42})
    out=pathlib.Path(out)
    plans={k:v for k,v in FIGURES.items() if selected is None or k in selected}
    unknown={r['figure'] for r in rows}-set(FIGURES)
    if unknown: raise ValueError('unknown figure: '+','.join(sorted(unknown)))
    # Validate every requested panel before creating any output.
    prepared={}
    for figure,specs in plans.items():
        rr=[r for r in rows if r['figure']==figure]
        if not rr: raise ValueError('missing figure '+figure)
        if {r['panel'] for r in rr} != {s[0] for s in specs}: raise ValueError('missing/extra panels for '+figure)
        prepared[figure]=rr
    reports={}
    # Temporary staging avoids leaving a partially rendered "final" bundle on failure.
    with tempfile.TemporaryDirectory(prefix='hbf-render-') as temporary:
        stage=pathlib.Path(temporary)
        for figure,specs in plans.items():
            n=len(specs); cols=3 if n in {3,6} else 2
            fig,axes=plt.subplots((n+cols-1)//cols,cols,figsize=(12 if cols==3 else 10,3.6*((n+cols-1)//cols)),squeeze=False)
            reports[figure]={}
            rr=prepared[figure]
            colors=[float(r['value']) for r in rr if r['metric']=='decode_norm']
            clim=(min([1.0]+colors),max([1.2]+colors))
            for ax,spec in zip(axes.flat,specs):
                subset=[r for r in rr if r['panel']==spec[0]]
                reports[figure][spec[0]]=plot_heatmap(ax,subset,spec,clim) if figure=='fig-e3-hbf-feasibility' else plot_lines(ax,subset,spec)
                ax.grid(axis='y',alpha=.16,linewidth=.5)
            for ax in list(axes.flat)[n:]: ax.remove()
            if any(r['provenance']=='MOCK' for r in rr) or watermark:
                fig.suptitle('MOCK DATA — NOT MEASURED',fontsize=15,color='#9A4D18',weight='bold')
                fig.text(.5,.50,'MOCK / NOT MEASURED',ha='center',va='center',fontsize=26,rotation=15,color='#9A4D18',alpha=.16)
            else:
                fig.suptitle(' / '.join(sorted({r['provenance'] for r in rr})),fontsize=10)
            note='Means with 95% run-bootstrap intervals where n > 1; preview intervals have no empirical meaning.' if any(r['provenance']=='MOCK' for r in rr) else 'Means with 95% run-bootstrap intervals where n > 1; n = 1 has no uncertainty estimate.'
            if figure=='fig-e2-async-semantics': note+=' Old-path consume residual excludes delay already paid at issue.'
            fig.text(.5,.012,note,ha='center',fontsize=6.4)
            fig.tight_layout(rect=(0,.04,1,.94))
            for suffix in ['png','pdf','svg']: fig.savefig(stage/f'{figure}.{suffix}',bbox_inches='tight')
            plt.close(fig)
        out.mkdir(parents=True,exist_ok=True)
        for p in stage.iterdir(): (out/p.name).write_bytes(p.read_bytes())
    return reports


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True); parser.add_argument('--out',required=True)
    parser.add_argument('--watermark',action='store_true'); parser.add_argument('--strict-no-mock',action='store_true')
    parser.add_argument('--manifest'); parser.add_argument('--figure',action='append',choices=FIGURES)
    args=parser.parse_args()
    try:
        # Any directory component called final is guarded, even when the flag is omitted.
        strict=args.strict_no_mock or 'final' in pathlib.Path(args.out).resolve().parts
        rows=load_validated(args.input,strict,args.manifest)
        if strict and args.watermark: raise ValueError('final rendering cannot request mock watermark')
        reports=render(rows,args.out,args.watermark,args.figure)
        report={'input_sha256':hashlib.sha256(pathlib.Path(args.input).read_bytes()).hexdigest(),
            'renderer_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
            'strict_no_mock':strict,'panels':reports,'matplotlib':matplotlib.__version__,'numpy':np.__version__}
        (pathlib.Path(args.out)/'render-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    except (ValueError,OSError,KeyError,TypeError) as e:
        print('RENDER FAILED: '+str(e),file=sys.stderr); return 2
    print(f'Rendered {len(reports)} figures (PNG/PDF/SVG) in {args.out}')
    return 0

if __name__=='__main__': raise SystemExit(main())
