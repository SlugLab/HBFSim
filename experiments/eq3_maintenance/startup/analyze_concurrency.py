#!/usr/bin/env python3
"""Verify bounded-window write concurrency from immutable probe CSV facts."""
import argparse,csv,json
from collections import defaultdict
from pathlib import Path

def analyze(raw: Path):
    with (raw/'requests.csv').open() as stream: requests=list(csv.DictReader(stream))
    with (raw/'request-observations.csv').open() as stream: observations=list(csv.DictReader(stream))
    with (raw/'native-events.csv').open() as stream: events=list(csv.DictReader(stream))
    writes={int(r['request_id']) for r in requests if r['phase']=='STARTUP_WRITE'}
    write_rows=[r for r in requests if r['phase']=='STARTUP_WRITE']
    by_command=defaultdict(dict)
    resources=defaultdict(set)
    transaction_ids=set()
    for row in events:
        rid=int(row['external_request_id'])
        if rid not in writes or int(row['maintenance_request_id']) or int(row['type'])!=1:
            continue
        phase=int(row['phase']);command=int(row['command_id'])
        if phase in (1,2):
            if phase in by_command[command] and by_command[command][phase]!=int(row['time_ns']):
                raise ValueError('one command has inconsistent media phase times')
            by_command[command][phase]=int(row['time_ns'])
            resources[command].add((int(row['channel']),int(row['chip']),int(row['die']),int(row['plane'])))
        transaction_ids.add(int(row['transaction_id']))
    intervals=[]
    for command,phase in by_command.items():
        if set(phase)!={1,2} or phase[2]<phase[1]: raise ValueError('incomplete/nonmonotonic media interval')
        intervals.append((phase[1],phase[2],command,resources[command]))
    begin=min(int(r['arrival_ns']) for r in write_rows);end=max(int(r['completion_ns']) for r in write_rows)
    elapsed=end-begin
    def max_active(key):
        timeline=[]
        for start,end,command,command_resources in intervals:
            for resource in command_resources:
                timeline.append((start,1,key(resource)));timeline.append((end,-1,key(resource)))
        active=defaultdict(int);maximum=0
        for _,delta,resource in sorted(timeline,key=lambda x:(x[0],x[1])):
            active[resource]+=delta
            if not active[resource]: del active[resource]
            maximum=max(maximum,len(active))
        return maximum
    def active_integral(key):
        timeline=[]
        for start,stop,command,command_resources in intervals:
            for resource in {key(value) for value in command_resources}:
                timeline.append((start,1,resource));timeline.append((stop,-1,resource))
        active=defaultdict(int);integral=0;prior=begin
        for time,delta,resource in sorted(timeline,key=lambda x:(x[0],x[1])):
            integral+=len(active)*(time-prior);prior=time
            active[resource]+=delta
            if not active[resource]:del active[resource]
        integral+=len(active)*(end-prior)
        return integral
    sweep=[]
    for start,end,command,_ in intervals:sweep.extend(((start,1,command),(end,-1,command)))
    active=0;max_commands=0
    for _,delta,_ in sorted(sweep,key=lambda x:(x[0],x[1])):
        active+=delta;max_commands=max(max_commands,active)
    die_integral=active_integral(lambda r:r[:3]);plane_integral=active_integral(lambda r:r)
    command_media_sum=sum(stop-start for start,stop,_,_ in intervals)
    result={
      'schema_version':'eq3-startup-write-concurrency-v1','status':'PASS',
      'write_requests':len(writes),'unique_program_transactions':len(transaction_ids),
      'program_commands':len(intervals),'peak_device_outstanding':max(int(r['device_outstanding']) for r in observations),
      'max_concurrent_program_commands':max_commands,
      'max_active_channel_chip_die_resources':max_active(lambda r:r[:3]),
      'max_active_channel_chip_die_plane_resources':max_active(lambda r:r),
      'distinct_program_resources':len(set().union(*resources.values())),
      'write_phase_start_ns':begin,'write_phase_end_ns':end,'write_phase_elapsed_ns':elapsed,
      'achieved_program_bytes_per_s':len(writes)*int(write_rows[0]['bytes'])*1e9/elapsed,
      'summed_program_command_media_ns':command_media_sum,
      'mean_active_program_commands':command_media_sum/elapsed,
      'observed_peak_command_utilization':command_media_sum/(elapsed*max_commands),
      'time_weighted_mean_active_channel_chip_die_resources':die_integral/elapsed,
      'time_weighted_mean_active_channel_chip_die_plane_resources':plane_integral/elapsed,
      'configured_channel_chip_die_resources':64,
      'time_weighted_die_resource_fraction':die_integral/(elapsed*64),
      'concurrency_verified':max_commands>1 and max_active(lambda r:r[:3])>1,
      'count_semantics':'operations=unique transaction IDs; commands=unique command IDs; CSV rows repeat identities across phases',
      'interval_semantics':'half-open MEDIA_BEGIN to MEDIA_END; endings processed before starts at equal timestamps'}
    if len(writes)!=len(transaction_ids) or not intervals:raise ValueError('program operation conservation failed')
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    result=analyze(a.raw);a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
