"""Optional aggregate base-managed retry effort on the existing causal ledger.

Only successful payload crosses the external fabric. Extra internal NAND reads
and ECC/base work compete with other jobs; no post-hoc completion-time edits.
The adapter is experimental and never imported by the default backend.
"""
from copy import deepcopy
from causal_service import CausalTopologyService

class _ActivityRules:
    def __init__(self, base, owner):
        self.base=base;self.owner=owner;self.carry={}
    def __getattr__(self,name):return getattr(self.base,name)
    def _activity_rows(self,job,byte_count,start,end):
        rows=self.base._activity_rows(job,byte_count,start,end)
        cost=job.metadata.get('reliability_cost_proxy')
        if cost is None:return rows
        work=byte_count*(cost['attempt_work_milli']-1000)+self.carry.get(job.job_id,0)
        retry_bytes,self.carry[job.job_id]=divmod(work,1000)
        template=next(row for row in rows if row['phase']=='media_read')
        if retry_bytes:
            retry=deepcopy(template);retry.update(operation='retry_internal',bytes=retry_bytes,
                semantics='BASE_MANAGED_EXPECTED_RETRY_MEDIA_BYTES_NO_DUPLICATE_HOST_DELIVERY')
            rows.append(retry)
        rows.append({'operation':'ecc_decode','phase':'ecc_decode','stack':job.stack,
            'channel':job.channel,'bytes':byte_count+retry_bytes,'route':job.route,
            'resource':job.stack+':ecc-decoder','start_ns':start,'end_ns':end,
            'energy_semantics':'INCLUDED_IN_10PJ_BASE_PER_PHYSICAL_READ_ATTEMPT_NOT_ADDED_AGAIN'})
        return rows

class ReliabilityCausalService(CausalTopologyService):
    def __init__(self,config,provider):
        super().__init__(config)
        self.provider=provider;self.decision_events=[];self.discarded_fractional_retry_byte_milli=0
        # Exact fixed-point service effort; physical capacities remain unchanged.
        self._config['work_scale']*=1000
        self._rules._config['work_scale']*=1000
        self._rules=_ActivityRules(self._rules,self)
        self.decoder_capacity={s:int(sum(ch.values())*provider.profile['ecc_decoder_headroom_over_fresh_media'])
                               for s,ch in self._config['channels'].items() if s.startswith('hbf')}
        if any(v<=0 for v in self.decoder_capacity.values()):raise ValueError('decoder capacity must be positive')

    def _phase_resources(self,job):
        phases=super()._phase_resources(job)
        if job.stack not in self.provider.states or job.operation!='read' or job.maintenance_id is not None:
            return phases
        if 'reliability_cost_proxy' not in job.metadata:
            cost=self.provider.cost(job.stack,self._now)
            job.metadata['reliability_cost_proxy']=cost
            self.decision_events.append({'job_id':job.job_id,'stack':job.stack,'channel':job.channel,
                'at_ns':self._now,'bytes':job.total_bytes,**cost})
        cost=job.metadata['reliability_cost_proxy'];attempts=cost['attempt_work_milli']
        resource,stage,coefficient=phases[0]
        if coefficient%1000:raise AssertionError('fixed-point media scale is not exact')
        phases[0]=(resource,stage,coefficient//1000*attempts)
        phases.append((job.stack+':ecc-decoder',
                       {'latency_ns':0,'bandwidth_bytes_per_s':self.decoder_capacity[job.stack]},
                       self._config['work_scale']//1000*attempts))
        return phases

    def _retire_completed(self,completion_ids):
        for job_id in completion_ids:
            self.discarded_fractional_retry_byte_milli+=self._rules.carry.pop(job_id,0)
        return super()._retire_completed(completion_ids)

    def drain_cost_decisions(self):
        rows=self.decision_events;self.decision_events=[];return rows

    def immutable_facts(self):
        facts=super().immutable_facts()
        facts['reliability_proxy']={
            'profile':self.provider.profile,'admission_sampling':'AT_FIRST_SERVICE_RESOURCE_ALLOCATION',
            'active_work':'FROZEN_PER_JOB_NO_RETROACTIVE_COST_OR_COMPLETION_CHANGE',
            'retry_path':'INTERNAL_MEDIA_PLUS_BASE_ECC;EXTERNAL_FABRIC_VALID_PAYLOAD_ONCE',
            'scope':'FOREGROUND_HBF_READS_ONLY;HBM_AND_MAINTENANCE_DATA_AGE_NOT_INFERRED',
            'decoder_capacity_bytes_per_s':self.decoder_capacity,
            'physical_byte_rounding':'FLOOR_WITH_PER_JOB_CARRY;LESS_THAN_ONE_RETRY_BYTE_PER_JOB',
            'discarded_fractional_retry_byte_milli':self.discarded_fractional_retry_byte_milli,
            'UECC':'UNKNOWN_CONDITIONAL_SUCCESSFUL_READ_COST_ONLY'}
        return facts
