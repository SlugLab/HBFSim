"""Explicit engineering inputs; never launches a run or changes default profiles."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from eq3_basic_system import engineering_fixture

MODES = ('mixed_direct', 'relay', 'dash', 'all_hbf_direct')


def configuration(mode, *, pages_per_stack=65536, geometry="legacy16k"):
    """16 real MQSim dies/stack; explicit legacy or OCP-sized logical geometry."""
    if mode not in MODES:
        raise ValueError('unsupported topology')
    n = 8 if mode == 'all_hbf_direct' else 4
    if geometry not in ("legacy16k", "ocp4k16bank"):
        raise ValueError("unknown NAND geometry profile")
    page = 4096 if geometry == "ocp4k16bank" else 16384
    channel_per_stack = 16 if geometry == "ocp4k16bank" else 1
    die_per_channel = 1 if geometry == "ocp4k16bank" else 16
    planes = 16 if geometry == "ocp4k16bank" else 1
    if pages_per_stack % (16 * planes * 256) or pages_per_stack < 16 * planes * 256 * 8:
        raise ValueError('working region must be block aligned and provide >=8 blocks/physical plane')
    profile = dict(name=('EQ3_OCP4K_16BANK_FULL_CAPACITY' if geometry=='ocp4k16bank' and pages_per_stack*page==512*1024**3 else 'EQ3_EXPERIMENTAL_16DIE_FINITE_WORKING_REGION'),
                   capacity_bytes=n * pages_per_stack * page, page_bytes=page,
                   read_latency_ns=10000, program_latency_ns=100000,
                   channels=n*channel_per_stack, dies_per_channel=die_per_channel, planes_per_die=planes,
                   pages_per_block=256, channel_width_bits=8,
                   channel_transfer_rate_mtps=1600, queue_depth=256,
                   aggregate_bandwidth_bytes_per_s=512000000000,
                   hbm_cache_bytes=67108864, reference_sample_rate=0.01,
                   reference_warmup_requests=1024, time_scale=1,
                   timing_tolerance_ns=10000)
    mapping = dict(schema_version=1, physical_kind='HBF', route='direct',
                   address_layout='GLOBAL_PAGE_STRIPE_V1', plane_allocation_scheme='CWDP',
                   page_bytes=page, channels=n*channel_per_stack, dies_per_channel=die_per_channel,
                   evidence=('OCP512GiB_LOGICAL_CAPACITY_WITH_ENGINEERING_MAPPING' if pages_per_stack*page==512*1024**3 else 'ENGINEERING_16_DIE_FINITE_WORKING_REGION'),
                   stacks=[dict(id=f'hbf{i}', declared_dies=16, channels=list(range(i*channel_per_stack,(i+1)*channel_per_stack))) for i in range(n)])
    basic = engineering_fixture(mode, page)
    basic.pop('requests')
    for item in basic['fabric']['hbf'].values():
        # DASH paper physical 18MiB / usable16MiB split2x8MiB; engineering reuse on other paths.
        item['bank_capacity_bytes'] = 8 * 1024 * 1024
        for field in ('fill', 'direct_link', 'relay_link'):
            if item[field] is not None:
                item[field] = dict(latency_ns=10, bandwidth_bytes_per_s=1600000000000)
    for item in basic['fabric']['hbm'].values():
        item['bank_capacity_bytes'] = 4 * 1024 * 1024
        item['gpu_link'] = dict(latency_ns=10, bandwidth_bytes_per_s=2048000000000)
    if basic['hbm']:
        for item in basic['hbm']['stacks']:
            item['media_latency_ns'] = dict(read=50, write=50)
            item['media_bandwidth_Bps'] = dict(read=2048000000000, write=2048000000000)
            # Energy is accounted once in the experimental phase ledger below.
    return dict(profile=profile, stack_map=mapping, fabric=basic['fabric'], hbm=basic['hbm'],
                mode=mode, geometry_profile=geometry,
                geometry_evidence=dict(page_bytes='OCP070_SPECIFIED' if page==4096 else 'LEGACY_ENGINEERING',
                     host_channels_per_stack='OCP070_SPECIFIED16' if channel_per_stack==16 else 'ENGINEERING_AGGREGATION1',
                     dies_per_channel='SCENARIO_PROJECTION_16_DIES_OVER16_CHANNELS' if die_per_channel==1 else 'ENGINEERING_AGGREGATION16',
                     planes_per_die='BANK_TO_PLANE_1TO1_SCENARIO_ASSUMPTION' if planes==16 else 'ENGINEERING1',
                     pages_per_block='SCENARIO_ASSUMPTION256; OCP070_5.7_R3_PRODUCT_DEFINED',
                     product_stack_capacity_bytes=512*1024**3,product_stack_pages=512*1024**3//4096,
                     product_average_pages_per_die=512*1024**3//4096//16,
                     configured_stack_capacity_bytes=pages_per_stack*page,
                     configured_pages_per_die=pages_per_stack//16,
                     configured_blocks_per_plane=pages_per_stack//(16*planes*256),
                     physical_spare='BACKEND_FINITE_ALLOCATOR; NOT_TARGET_PRODUCT_SPECIFIED'),
                capacity_scope='FULL_OCP512GiB_LOGICAL_CAPACITY' if pages_per_stack*page==512*1024**3 else 'FINITE_WORKING_REGION; full product capacity not instantiated',
                evidence='CONDITIONAL_ENGINEERING_USE',
                media_geometry=f'16 MQSim dies per HBF stack; {channel_per_stack} channels/stack, {die_per_channel} dies/channel, {planes} planes/die; existing CWDP',
                hbm_geometry='12 physical thermal dies; parametric service die UNKNOWN',
                fabric_parameter_scope='DASH buffer anchor; latency10ns assumption, BW target not calibrated',
                external_gddr='UNAVAILABLE' if n == 8 else 'NOT_APPLICABLE')


def workload(mode, kind, *, total_hbf_rps, active_ns, burst_period_ns=20000000,
             hbm_rps_per_stack=10, pages_per_stack=65536):
    """Arrivals are independent of service and policy; Q4 equal TOTAL HBF demand."""
    if mode not in MODES or kind not in ('W1', 'W2'):
        raise ValueError('unknown topology/workload')
    if total_hbf_rps <= 0 or active_ns <= 0 or burst_period_ns <= 0:
        raise ValueError('positive rates and durations required')
    n = 8 if mode == 'all_hbf_direct' else 4
    requests, local = [], [0] * n
    count = int(total_hbf_rps * active_ns // 1000000000)
    for i in range(count):
        nominal = i * 1000000000 // total_hbf_rps
        # Fixed burst at each period, not a temperature-dependent synthetic power.
        arrival = nominal // burst_period_ns * burst_period_ns
        if kind == 'W1':
            s = i % n
        else:
            # 1/2 of traffic to a rotating hot stack, remaining round-robin.
            hot = (arrival // max(burst_period_ns, active_ns // n)) % n
            s = hot if i % 2 == 0 else (i // 2) % n
        index = local[s]; local[s] += 1
        route = 'relay' if mode == 'relay' or mode == 'dash' and index % 2 else 'direct'
        requests.append(dict(request_id=f'hbf-{i}', stack=f'hbf{s}', stack_local_page=index % pages_per_stack,
                             route=route, bytes=16384, arrival_ns=arrival, operation='read'))
    if n == 4 and hbm_rps_per_stack:
        for s in range(4):
            for i in range(int(hbm_rps_per_stack * active_ns // 1000000000)):
                requests.append(dict(request_id=f'hbm-{s}-{i}', stack=f'hbm{s}', route='direct',
                                     bytes=16384, arrival_ns=i*1000000000//hbm_rps_per_stack, operation='read'))
    return sorted(requests, key=lambda r:(r['arrival_ns'],r['request_id']))


def energy_profile():
    return dict(schema_version='eq3-stage-energy-engineering-v1', evidence='SCENARIO_ASSUMPTION',
                nand_media_w={'0':0.05, '1':0.05, '2':0.05}, nand_data_out_w=0.01, nand_command_transfer_w=0.01,
                hbm_array_j_per_byte=40e-12, fabric_endpoint_j_per_byte=2e-12,
                gpu_external_w=200.0, standby_w=0.0,
                standby_scope='NOT_MODELLED; zero increment is not measured zero device idle power',
                media_scope='per active physical die, share among simultaneous planes, not request occupancy',
                source_note='0.05W order-of-magnitude engineering coefficient, not HBF calibrated. Micron 2Gb M29B manufacturer datasheet typical15mA at3.3V is only an old-device plausibility anchor; no exact transfer of product current.',
                sources=['https://www.micron.com/sales-support/design-tools/nand-system-power-calculator',
                         'https://www.mouser.com/datasheet/2/671/2gb_nand_m29b-1879920.pdf'],
                hbm_note='40pJ/B array engineering allocation plus separate2pJ/B endpoint increments, informed only in scale by registered H200 aggregate46pJ/B; not calibrated decomposition',
                gpu_note='external prescribed compute load <= original200W envelope, no live GPU or inferred token causality')
