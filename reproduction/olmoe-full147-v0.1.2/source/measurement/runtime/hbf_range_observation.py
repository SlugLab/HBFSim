"""One bounded read-only settled-registration capture; no poll/retry or device API."""
from pathlib import Path
import hashlib,json,os,struct
HEADER={'magic':(0,8),'abi_version':(8,4),'header_bytes':(12,4),'region_bytes':(16,8),
        'range_capacity':(28,4),'range_count':(36,4),'range_offset':(40,8),
        'request_offset':(48,8),'daemon_pid':(128,8),'control_generation':(168,8)}
RECORD={'base':(0,8),'length':(8,8),'file_offset':(16,8),'range_id':(24,4),
        'mode':(28,4),'permissions':(32,4),'cache_policy':(36,4),'stream_id':(40,4),
        'flags':(44,4),'page_bytes':(48,8),'reserved1':(56,8)}
def need(value,message):
    if not value: raise RuntimeError(message)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):
    def unique(pairs):
        result={}
        for k,v in pairs:
            need(k not in result,'duplicate observed/layout JSON field '+k);result[k]=v
        return result
    return json.loads(Path(path).read_bytes(),object_pairs_hook=unique)
def decode(data,fields):
    return {k:struct.unpack_from('<I' if size==4 else '<Q',data,offset)[0]
            for k,(offset,size) in fields.items()}
def layout_contract(layout):
    need(layout.get('schema')=='hbfsim.range_layout_offsets.v1' and
         layout.get('header_bytes')==384 and layout.get('record_bytes')==64 and
         layout.get('header')=={k:list(v) for k,v in HEADER.items()} and
         layout.get('record')=={k:list(v) for k,v in RECORD.items()},
         'actual matching compiler layout differs from reviewed source expectations')
    need(layout.get('policy')=={'timing':1,'read':1,'none':0,'stream':0,'flags':0,'reserved1':0},
         'matching compiled API timing/read/NONE constants differ')
    return layout
def bound_environment(key):
    path=Path(os.environ[key+'_PATH']);pin=os.environ[key+'_SHA256']
    need(path.is_absolute() and len(pin)==64 and sha(path)==pin,'missing/changed actual range binding '+key)
    return path,pin,load(path)
def bindings():
    path,pin,layout=bound_environment('HBFSIM_RANGE_LAYOUT')
    layout_contract(layout)
    build_path,build_pin,build=bound_environment('HBFSIM_RANGE_LAYOUT_BUILD_RECEIPT')
    sources={role:os.environ['HBFSIM_RANGE_'+role.upper()+'_SOURCE_SHA256']
             for role in ('header','api','generator')}
    for role,value in sources.items():
        source=Path(os.environ['HBFSIM_RANGE_'+role.upper()+'_SOURCE_PATH'])
        need(source.is_absolute() and sha(source)==value,'range compiler source pin differs '+role)
    need(build.get('schema')=='hbfsim.range_layout_build_join.v1' and
         build.get('source_sha256')==sources and build.get('layout_sha256')==pin,
         'actual compiled layout source/output join missing')
    # Bound real compiler invocation/result receipt is retained, never synthesized
    # as an AST-only PASS. Its compiler artifact must join the layout generator.
    compiler=Path(build['compiler_receipt_path'])
    need(compiler.is_absolute() and sha(compiler)==build['compiler_receipt_sha256'],
         'real matching offsetof compiler receipt absent')
    generator=Path(build['generator_artifact_path'])
    need(generator.is_absolute() and sha(generator)==build['generator_artifact_sha256'],
         'actual offsetof generator artifact identity absent')
    production_path,production_pin,production=bound_environment('HBFSIM_RANGE_PRODUCTION_BUILD_RECEIPT')
    extension=Path(os.environ['HBFSIM_VLLM_EXTENSION']).resolve(strict=True)
    extension_sha=os.environ['HBFSIM_RANGE_OBSERVATION_EXTENSION_SHA256']
    need(sha(extension)==extension_sha and production.get('extension_sha256')==extension_sha and
         production.get('daemon_sha256')==os.environ['HBFSIM_NORMAL_DAEMON_SHA256'] and
         production.get('layout_header_sha256')==sources['header'] and
         production.get('api_header_sha256')==sources['api'],
         'matching loaded production/layout source join absent')
    original=Path(production['actual_build_receipt_path'])
    need(original.is_absolute() and sha(original)==production['actual_build_receipt_sha256'],
         'actual production compiler receipt missing')
    return {'layout_path':str(path),'layout_sha256':pin,'layout_build_receipt_path':str(build_path),
            'layout_build_receipt_sha256':build_pin,'production_join_path':str(production_path),
            'production_join_sha256':production_pin,'sources':sources},layout,extension,extension_sha
def capture(descriptor,header,daemon,target_pid,target_start,ticks,registration):
    bound,layout,extension,extension_sha=bindings()
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    stat=os.fstat(descriptor);extension_stat=extension.stat()
    maps=Path(f'/proc/{target_pid}/maps').read_text()
    mapped=[line for line in maps.splitlines() if len(line.split())>=6 and
            line.split()[3]==f'{os.major(extension_stat.st_dev):02x}:{os.minor(extension_stat.st_dev):02x}' and
            int(line.split()[4])==extension_stat.st_ino and Path(line.split()[5])==extension]
    need(mapped and ticks(target_pid)==target_start and ticks(daemon['pid'])==daemon['start_ticks'],
         'loaded extension/owned target/daemon identity missing at range capture')
    identity=(stat.st_dev,stat.st_ino,stat.st_size)
    need(identity==(header['control_device'],header['control_inode'],header['region_bytes']),
         'range fd differs from existing owned header')
    before=os.pread(descriptor,layout['header_bytes'],0)
    need(len(before)==layout['header_bytes'],'truncated range header before')
    relevant=decode(before,layout['header'])
    need(relevant['magic']==0x48424653494d3031 and relevant['abi_version']==5 and
         relevant['header_bytes']==layout['header_bytes'] and relevant['region_bytes']==stat.st_size and
         relevant['daemon_pid']==daemon['pid'] and relevant['control_generation']==header['control_generation'],
         'range identity/header generation differs')
    count=relevant['range_count'];capacity=relevant['range_capacity'];offset=relevant['range_offset']
    stride=layout['record_bytes'];size=count*stride;end=offset+size
    need(type(count) is int and 0<count<=capacity==32768 and count==registration['unique_storage_count'] and
         layout['header_bytes']<=offset<=end<=(1<<64)-1 and
         end<=relevant['region_bytes'] and end<=relevant['request_offset']<=relevant['region_bytes'],
         'published range count/capacity/table bounds differ')
    first=os.pread(descriptor,size,offset);second=os.pread(descriptor,size,offset)
    after=os.pread(descriptor,layout['header_bytes'],0);final=os.fstat(descriptor)
    need(len(first)==len(second)==size and first==second and len(after)==layout['header_bytes'] and
         decode(after,layout['header'])==relevant and
         (final.st_dev,final.st_ino,final.st_size)==identity,
         'unstable/truncated settled-registration range observation')
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip()==boot and
         ticks(target_pid)==target_start and ticks(daemon['pid'])==daemon['start_ticks'] and
         (Path(f"/proc/{daemon['pid']}/exe").resolve(strict=True))==Path(daemon['exe']) and
         sha(daemon['exe'])==daemon['exe_sha256'] and
         sha(extension)==extension_sha and
         (extension.stat().st_dev,extension.stat().st_ino,extension.stat().st_size)==
             (extension_stat.st_dev,extension_stat.st_ino,extension_stat.st_size),
         'range capture boot/PID/start/executable identity changed')
    return {'schema':'hbfsim.settled_registration_ranges.v1','status':'OBSERVED_DOUBLE_READ_STABLE',
            **bound,'boot_id':boot,'target_pid':target_pid,'target_start_ticks':target_start,
            'daemon_pid':daemon['pid'],'daemon_start_ticks':daemon['start_ticks'],
            'control_device':stat.st_dev,'control_inode':stat.st_ino,'region_bytes':stat.st_size,
            'relevant_header':relevant,'header_before_hex':before.hex(),'header_after_hex':after.hex(),
            'range_raw_hex':first.hex(),'range_second_sha256':hashlib.sha256(second).hexdigest(),
            'range_raw_sha256':hashlib.sha256(first).hexdigest(),'count':count,'range_offset':offset,'stride':stride,
            'rows':[decode(first[i*stride:(i+1)*stride],layout['record']) for i in range(count)],
            'loaded_extension':{'path':str(extension),'sha256':extension_sha,'device':extension_stat.st_dev,
                                'inode':extension_stat.st_ino,'bytes':extension_stat.st_size,'maps':mapped},
            'limit':'One registration-complete callback; double read is evidence stability, not a general concurrency lock.'}
def validate(observed,preparation,plan,registration,scope):
    need(observed.get('schema')=='hbfsim.settled_registration_ranges.v1' and
         observed.get('status')=='OBSERVED_DOUBLE_READ_STABLE','actual settled range evidence absent')
    env=plan['env'];header=preparation['control_header']
    for key,envkey in (('layout','HBFSIM_RANGE_LAYOUT'),('layout_build_receipt','HBFSIM_RANGE_LAYOUT_BUILD_RECEIPT'),
                       ('production_join','HBFSIM_RANGE_PRODUCTION_BUILD_RECEIPT')):
        need(observed[key+'_path']==env[envkey+'_PATH'] and observed[key+'_sha256']==env[envkey+'_SHA256'] and
             sha(observed[key+'_path'])==observed[key+'_sha256'],'range capture source/compiled receipt pin differs')
    layout=layout_contract(load(observed['layout_path']))
    need(observed['boot_id']==plan['bound_boot_id'] and
         (observed['target_pid'],observed['target_start_ticks'])==(preparation['target_pid'],preparation['target_start_ticks']) and
         (observed['daemon_pid'],observed['daemon_start_ticks'])==(preparation['daemon']['pid'],preparation['daemon']['start_ticks']) and
         (observed['control_device'],observed['control_inode'],observed['region_bytes'])==
             (header['control_device'],header['control_inode'],header['region_bytes']),
         'range observed boot/process/context join differs')
    before=bytes.fromhex(observed['header_before_hex']);after=bytes.fromhex(observed['header_after_hex'])
    need(len(before)==len(after)==layout['header_bytes'],'observed raw header lengths differ')
    relevant=decode(before,layout['header'])
    need(relevant==observed['relevant_header']==decode(after,layout['header']) and
         relevant['magic']==0x48424653494d3031 and relevant['abi_version']==5 and
         relevant['header_bytes']==layout['header_bytes'] and relevant['region_bytes']==observed['region_bytes'] and
         relevant['daemon_pid']==observed['daemon_pid'] and
         relevant['control_generation']==header['control_generation'] and relevant['range_count']==observed['count'] and
         relevant['range_offset']==observed['range_offset'],'raw range header decode differs')
    count=observed['count'];stride=layout['record_bytes'];raw=bytes.fromhex(observed['range_raw_hex'])
    end=relevant['range_offset']+count*stride
    need(observed['stride']==stride and 0<count<=relevant['range_capacity']==32768 and len(raw)==count*stride and
         384<=relevant['range_offset']<=end<=relevant['request_offset']<=relevant['region_bytes'] and
         hashlib.sha256(raw).hexdigest()==observed['range_raw_sha256']==observed['range_second_sha256'],
         'raw range snapshot count/bounds/hash differs')
    rows=[decode(raw[i*stride:(i+1)*stride],layout['record']) for i in range(count)]
    need(rows==observed['rows'] and count==scope['registered_count'],'raw decoded range table differs')
    # Physical publication is GPU-base sorted; registration IDs/offsets encode
    # the alias prefix. Table index is never used as a range ID.
    previous=0;by_address={}
    for row in rows:
        key=(row['base'],row['length'])
        need(row['base']>=previous and row['base']>0 and row['length']>0 and
             row['base']+row['length']<(1<<64) and key not in by_address,
             'observed range duplicates/overlap/invalid GPU interval')
        previous=row['base']+row['length'];by_address[key]=row
    cursor=0;joined=[];used=set()
    for ordinal,storage in enumerate(registration['storages'],1):
        key=(storage['address'],storage['bytes']);need(key in by_address and key not in used,'range/storage bijection missing')
        used.add(key);row=by_address[key]
        need(row['range_id']==ordinal and row['file_offset']==cursor and row['page_bytes']==16384 and
             row['mode']==layout['policy']['timing'] and row['permissions']==layout['policy']['read'] and
             row['cache_policy']==layout['policy']['none'] and row['stream_id']==layout['policy']['stream'] and
             row['flags']==layout['policy']['flags'] and row['reserved1']==layout['policy']['reserved1'],
             'actual range ID/prefix media offset/timing/read/NONE policy differs')
        cursor+=((row['length']+16383)//16384)*16384
        joined.append({'alias':storage['aliases'][0],**row})
    need(len(used)==len(by_address)==count and cursor<=2199023255552,'unexpected ranges or physical capacity exceeded')
    extension=observed['loaded_extension']
    need(extension['sha256']==plan['new_extension_sha256'] and extension['device']>0 and extension['inode']>0 and
         extension['bytes']>0 and extension['maps'] and
         extension['path']==str(Path(env['HBFSIM_VLLM_EXTENSION']).resolve(strict=True)),
         'observed range loaded extension differs from production')
    return {'status':'ACTUAL_RANGE_ALIAS_PREFIX_VALIDATED','rows':joined,'count':count,
            'raw_sha256':observed['range_raw_sha256'],'layout_sha256':observed['layout_sha256'],
            'limit':'Actual per-run registration and policy. Numeric GPU VAs are not compared across runs.'}
