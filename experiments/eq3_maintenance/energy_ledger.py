"""Integrate observed activity; engineering coefficients are never command facts.

This owns no backend resources. Native media intervals, stack-level parametric
HBM intervals and real fabric intervals are disjoint energy scopes. Unknown
physical addresses are rejected, never inferred from a logical address.
"""
from collections import defaultdict
import math


class ActivityEnergyLedger:
    def __init__(self, profile, stack_map, component_ids, *, hbm_dies=None, gpu_stop_ns=None):
        self.profile = profile
        self.components = set(component_ids)
        self.mapping = stack_map
        self.channel = {}
        for group in stack_map['stacks']:
            for index, channel in enumerate(group['channels']):
                self.channel[channel] = (group['id'], index * stack_map['dies_per_channel'])
        self.hbm_dies = hbm_dies or {}
        self.gpu_stop = gpu_stop_ns
        self.now = 0
        self.active = {}
        self.closed = []
        self.rows = []
        self.total_j = 0.0
        self.physical_bytes = defaultdict(int)
        self._seen = set()

    def _add(self, key, start, end, powers, scope, source):
        if key in self._seen:
            raise ValueError('duplicate energy activity identity')
        if start < self.now or end is not None and end < start:
            raise ValueError('activity would rewrite already committed thermal energy')
        if not set(powers).issubset(self.components):
            raise ValueError('energy target missing from physical thermal model')
        if any(not math.isfinite(p) or p < 0 for p in powers.values()):
            raise ValueError('nonfinite or negative physical power')
        self._seen.add(key)
        item = dict(key=key,start=start,end=end,powers=powers,scope=scope,source=source)
        if end is None:
            self.active[key] = item
        else:
            self.closed.append(item)

    def _end(self, key, time):
        if key not in self.active:
            raise ValueError('native phase end without unique begin')
        item = self.active.pop(key)
        if time < self.now or time < item['start']:
            raise ValueError('native activity time moved backward')
        item['end'] = time
        self.closed.append(item)

    @staticmethod
    def _source(tr):
        if tr.get('maintenance_id') not in (None,0,'UNKNOWN'):
            return 'HBF_MAINTENANCE'
        return 'FOREGROUND' if tr.get('external_request_id') not in (None,0,'UNKNOWN') else 'BACKEND_BACKGROUND'

    def _placement(self, tr):
        channel, die, chip = tr.get('channel'), tr.get('die'), tr.get('chip')
        if channel not in self.channel or type(die) is not int or type(chip) is not int or chip != 0:
            raise ValueError('UNKNOWN or unsupported native physical die identity')
        if not 0 <= die < self.mapping['dies_per_channel']:
            raise ValueError('native die outside declared mapping')
        stack, offset = self.channel[channel]
        if tr.get('stack') not in (None,'UNKNOWN',stack):
            raise ValueError('native stack differs from channel ownership')
        return stack, f'{stack}.die{offset+die}'

    def native(self, event):
        phase, time, cid = event['phase'],event['time_ns'],event['command_id']
        transactions = event['transactions']
        if phase in (1,2):
            groups = defaultdict(list)
            for tr in transactions:
                stack,component = self._placement(tr)
                groups[component].append(tr)
            for component, trs in groups.items():
                key=('media',cid,component)
                if phase == 1:
                    types = {str(tr['type']) for tr in trs}
                    if len(types) != 1:
                        raise ValueError('one media command contains inconsistent operation types')
                    op = next(iter(types))
                    watt = self.profile['nand_media_w'][op]
                    sources = {self._source(tr) for tr in trs}
                    self._add(key,time,None,{component:watt},'NAND_MEDIA',
                              next(iter(sources)) if len(sources)==1 else 'MIXED_BACKGROUND_FOREGROUND')
                    for tr in trs:
                        self.physical_bytes[(self._source(tr),op)] += tr['bytes']
                else:
                    self._end(key,time)
        elif phase in (3,4):
            for tr in transactions:
                stack,_ = self._placement(tr)
                key=('nand_data_out',cid,tr['transaction_id'])
                if phase == 3:
                    self._add(key,time,None,{f'{stack}.base':self.profile['nand_data_out_w']},
                              'NAND_DATA_OUT_BASE',self._source(tr))
                else:
                    self._end(key,time)
        elif phase != 0:
            raise ValueError('unsupported native phase')

    def hbm(self, fact):
        if fact['phase'] != 'media_start':
            return
        stack=fact['stack_id']; dies=self.hbm_dies.get(stack)
        if not dies:
            raise ValueError('HBM thermal die distribution not explicitly configured')
        start,end=fact['time_ns'],fact['media_end_ns']
        if end <= start:
            raise ValueError('invalid HBM media interval')
        energy=fact['bytes']*self.profile['hbm_array_j_per_byte']
        powers={die:energy/len(dies)*1e9/(end-start) for die in dies}
        self._add(('hbm',fact['request_id']),start,end,powers,
                  'HBM_ARRAY_UNIFORM_SPATIAL_ASSUMPTION','FOREGROUND')

    def fabric(self, event, request):
        if event['kind'] != 'start':
            return
        stage=event['stage']; stack=request['stack']
        if stage in ('HBF_FILL','HBF_DIRECT'):
            endpoints=[stack]
        elif stage == 'HBF_RELAY':
            endpoints=[stack,request['partner']]
        elif stage == 'HBM_GPU':
            endpoints=[request.get('partner',stack) if stack.startswith('hbf') else stack]
        else:
            raise ValueError('unsupported fabric energy stage')
        start,end=event['start_ns'],event['end_ns']
        if end <= start:
            raise ValueError('invalid fabric interval')
        power=event['bytes']*self.profile['fabric_endpoint_j_per_byte']*1e9/(end-start)
        self._add(('fabric',event['request_id'],stage),start,end,
                  {f'{x}.base':power for x in set(endpoints)},'FABRIC_'+stage,'FOREGROUND')

    def flush(self, boundary):
        if type(boundary) is not int or boundary <= self.now:
            raise ValueError('thermal boundary must increase')
        totals=defaultdict(float)
        for row in [*self.closed,*self.active.values()]:
            begin=max(self.now,row['start'])
            end=min(boundary,row['end'] if row['end'] is not None else boundary)
            if end <= begin:
                continue
            for component,power in row['powers'].items():
                energy=power*(end-begin)*1e-9
                totals[component]+=energy
                self.rows.append(dict(start_ns=begin,end_ns=end,component=component,
                                      energy_j=energy,scope=row['scope'],source=row['source'],
                                      coefficient_evidence=self.profile['evidence']))
        gpu_end=min(boundary,self.gpu_stop) if self.gpu_stop is not None else boundary
        if gpu_end>self.now:
            energy=self.profile['gpu_external_w']*(gpu_end-self.now)*1e-9
            if 'gpu' not in self.components:
                raise ValueError('GPU thermal source missing')
            totals['gpu']+=energy
            self.rows.append(dict(start_ns=self.now,end_ns=gpu_end,component='gpu',energy_j=energy,
                                  scope='EXTERNAL_COMPUTE_PRESCRIBED',source='GPU_SCENARIO',
                                  coefficient_evidence=self.profile['evidence']))
        self.closed=[row for row in self.closed if row['end']>boundary]
        self.now=boundary
        self.total_j+=sum(totals.values())
        return dict(totals)
