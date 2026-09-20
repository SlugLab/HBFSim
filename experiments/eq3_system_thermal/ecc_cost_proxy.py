"""Temperature-history to equivalent read effort, explicitly conditional.

No bits are invented, no HBF error probability is predicted, and lowering
instantaneous read temperature never erases already accumulated retention age.
"""
from copy import deepcopy
import math

DAY_NS=86_400_000_000_000

class ProxyDomainError(ValueError):
    pass

class ReadCostProxy:
    def __init__(self, profile, initial_by_stack):
        self.profile=deepcopy(profile)
        if profile['classification']!='CONDITIONAL_NAND_BEHAVIOR_PROXY_NOT_HBF_RBER':
            raise ValueError('explicit conditional classification required')
        strength=profile['transfer_strength']
        if not math.isfinite(strength) or strength<0:
            raise ValueError('invalid transfer strength')
        p=profile
        # Interpolant goes through19.9 at365days/2kPE and14.5 at90days/2kPE.
        at90=p['retention_retry_coefficient_at_zero_pe']+2*p['retention_retry_coefficient_per_1000_pe']
        extra=p['mean_retry_anchor_at_365_days_2000_pe']-2*p['fresh_pe_retry_per_1000_cycles']
        self.exponent=math.log(extra/at90)/math.log(365/p['mean_retry_anchor_age_days'])
        if self.exponent<=0:raise ValueError('retention exponent must be positive')
        self.states={}
        for stack,row in initial_by_stack.items():
            if not stack.startswith('hbf'):raise ValueError('NAND proxy cannot apply to HBM')
            self.states[stack]={'last_ns':0,'equivalent_age_ns':float(row['equivalent_age_days_30c'])*DAY_NS,
                'temperature_k':float(row['temperature_k']),'pe_cycles':float(row['pe_cycles']),
                'extrapolated_temperature_ns':0}
            self.cost(stack,0)

    def acceleration(self,temp):
        if not math.isfinite(temp) or temp<=0:raise ValueError('invalid Kelvin temperature')
        p=self.profile
        return math.exp(p['activation_energy_ev']/p['boltzmann_ev_per_k']*
                        (1/p['temperature_reference_k']-1/temp))

    def observe(self, start_ns, end_ns, temperatures):
        if end_ns<start_ns or set(temperatures)!=set(self.states):raise ValueError('temperature coverage/time mismatch')
        for stack,state in self.states.items():
            if state['last_ns']!=start_ns:raise ValueError('noncontiguous age history')
            state['equivalent_age_ns']+=(end_ns-start_ns)*self.acceleration(state['temperature_k'])
            if not 293.15<=state['temperature_k']<=343.15:
                state['extrapolated_temperature_ns']+=end_ns-start_ns
            state['last_ns']=end_ns;state['temperature_k']=float(temperatures[stack])
            self.cost(stack,end_ns)

    def cost(self,stack,at_ns):
        state=self.states[stack];p=self.profile
        if at_ns<state['last_ns']:raise ValueError('cannot query past retention state')
        age=(state['equivalent_age_ns']+(at_ns-state['last_ns'])*
             self.acceleration(state['temperature_k']))/DAY_NS
        pe=state['pe_cycles']
        if not p['retention_age_days_domain'][0]<=age<=p['retention_age_days_domain'][1]:
            raise ProxyDomainError(f'equivalent age {age}days outside published-proxy domain; no clamp')
        if not p['initial_pe_cycles_domain'][0]<=pe<=p['initial_pe_cycles_domain'][1]:
            raise ProxyDomainError('P/E outside proxy domain; no clamp')
        mean=(p['fresh_pe_retry_per_1000_cycles']*pe/1000+
              (p['retention_retry_coefficient_at_zero_pe']+
               p['retention_retry_coefficient_per_1000_pe']*pe/1000)*
              (age/p['mean_retry_anchor_age_days'])**self.exponent)
        mean*=p['transfer_strength']
        return {'equivalent_age_days_30c':age,'pe_cycles':pe,'expected_retry_steps':mean,
                'attempt_work_milli':1000+int(round(1000*mean)),
                'effort_quantization_absolute_retry_steps':0.0005,
                'instant_temperature_k':state['temperature_k'],
                'temperature_coupling':'HISTORY_TO_RETENTION_NOT_INSTANT_HOT_SLOWDOWN',
                'classification':p['classification']}

    def snapshot(self):
        return {'profile':self.profile,'retention_power_exponent':self.exponent,
                'states':deepcopy(self.states),
                'limitations':['NO_TARGET_HBF_RBER_OR_UECC_PREDICTION','INITIAL_PE_SCENARIO_NOT_FULL_FTL_LIFETIME',
                               'NO_INSTANT_TEMPERATURE_ERROR_PENALTY','NO_AUTO_RESET_ON_COOLING']}
