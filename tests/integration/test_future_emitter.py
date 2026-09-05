"""TEST_ONLY CPU interpreter of the emitted PTX, not a timing measurement.

Only device calls are modeled; every generated load, predicate, conversion,
parameter transfer and native instruction executes through the opcode table.
The compile-only companion checks that this same text assembles with real helpers.
"""
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
DRIVER=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else None
if len(sys.argv)>1: del sys.argv[1]


def program(body,decl='',load_type='u32'):
    return '''.version 9.0
.target sm_120
.address_size 64
.visible .func unrelated() { ret; }
.visible .entry kernel(.param .u64 input,.param .u64 output) {
.reg .b64 %rd<8>; .reg .b32 %r<12>; .reg .pred %p<4>;
'''+decl+'''
ld.param.u64 %rd0,[input]; ld.param.u64 %rd1,[output];
cvta.to.global.u64 %rd2,%rd0;
mov.u32 %r0,%tid.x; mul.wide.u32 %rd3,%r0,4;
add.u64 %rd4,%rd2,%rd3;
'''+body+'\nret;\n}\n'


def statements(text):
    text=re.sub(r'/\*[\s\S]*?\*/|//[^\n]*',lambda m:'\n'*m[0].count('\n'),text)
    start=text.index('{',text.index('.entry kernel'))
    body=text[start+1:text.rindex('}')]
    body=re.sub(r'(?m)^\s*\.loc\b[^\n]*\n','\n',body)
    body=re.sub(r'(?m)^(__tf_skip[0-9]+):',r'\1:;',body)
    return [s.strip() for s in body.split(';') if s.strip()]


def split_operands(text):
    depth=0;start=0;out=[]
    for i,c in enumerate(text):
        depth+=(c in '([{')-(c in ')]}')
        if c==',' and not depth:out.append(text[start:i].strip());start=i+1
    out.append(text[start:].strip())
    return out


class Machine:
    def __init__(self,text,lanes=1,base=0x1000,values=None,ranges=((0x1000,0x10000,4096),)):
        self.text=text;self.lanes=lanes;self.ranges=ranges
        self.reg=[{'%tid.x':i,'%ntid.x':lanes,'%ctaid.x':0} for i in range(lanes)]
        self.local=[{} for _ in range(lanes)];self.params=[{} for _ in range(lanes)]
        self.symbol={};self.types={};self.memory={};self.events=[];self.reservation=0
        self.count=dict(issued=0,pending=0,consumed=0,drained=0,native_loads=0,groups_issued=0,groups_completed=0)
        self.group_pending={};self.terminated=[False]*lanes
        for i in range(lanes):
            self.params[i].update(input=base,output=0x100000+i*8)
            self.write(self.memory,base+4*i,4,(values or [41]*lanes)[i])
    @staticmethod
    def write(space,addr,n,value):
        for i in range(n):space[addr+i]=(value>>(8*i))&255
    @staticmethod
    def read(space,addr,n):return sum(space.get(addr+i,0)<<(8*i) for i in range(n))
    def val(self,lane,operand):
        operand=operand.strip()
        if operand.startswith('%'):return self.reg[lane][operand]
        if operand in self.symbol:return self.symbol[operand]
        if operand.startswith('0f'):return int(operand[2:],16)
        if operand.startswith('0d'):return int(operand[2:],16)
        return int(operand,0)
    def put(self,lane,name,value):
        typ=self.types[name];bits=1 if typ=='pred' else int(typ[1:])
        self.reg[lane][name]=value&((1<<bits)-1)
    def address(self,lane,operand):
        expr=operand.strip()[1:-1].replace(' ','')
        m=re.fullmatch(r'([^+]+)(?:\+([0-9]+))?',expr)
        if not m:raise AssertionError('unsupported interpreter address '+expr)
        return self.val(lane,m[1])+int(m[2] or 0)
    def parameter(self,lane,name):return self.params[lane][name]
    def call(self,active,operands):
        if operands[0].startswith('('): result=operands[0][1:-1];name=operands[1];args=operands[2]
        else:result=None;name=operands[0];args=operands[1]
        names=[a.strip() for a in args[1:-1].split(',')]
        argv={l:[self.parameter(l,a) for a in names] for l in active}
        if name=='__hbfsim_fault':raise RuntimeError('device fault before payload')
        if name=='__hbfsim_timing_future_issue_v1':
            groups={}
            for l,a in argv.items():
                addr,n,inst,old,meta=a
                if old not in (0,4) or n not in (1,2,4,8) or not addr or addr+n>=(1<<64):
                    self.params[l][result]=struct.pack('<7Q2I',0,0,0,0,0,addr,0,5,6);continue
                found=next(((j,r) for j,r in enumerate(self.ranges) if r[0]<=addr<r[0]+r[1]),None)
                if found:groups.setdefault((found[0],(addr-found[1][0])//found[1][2]),[]).append(l)
                else:
                    self.params[l][result]=struct.pack('<7Q2I',1,1,100,100,1000,addr,0,1,1)
                    self.write_bytes(l,meta,struct.pack('<6IQ',1,32,inst,n,0,32,0));self.count['native_loads']+=1
                self.events.append((l,'issue',inst,addr))
            for group in groups.values():
                self.reservation+=1;rid=self.reservation;mask=sum(1<<l for l in group)
                self.count['groups_issued']+=1;self.group_pending[rid]=set(group)
                for l in group:
                    addr,n,inst,old,meta=argv[l]
                    self.params[l][result]=struct.pack('<7Q2I',1,1,100,300,1000,addr,rid,2,0)
                    self.write_bytes(l,meta,struct.pack('<6IQ',1,32,inst,n,mask,min(group),rid))
                    self.count['issued']+=1;self.count['pending']+=1
        elif name=='__hbfsim_timing_future_wait_v1':
            for l,(ptr,meta,native,inst,n,kind) in argv.items():
                f=struct.unpack('<7Q2I',self.bytes(l,ptr,64));m=struct.unpack('<6IQ',self.bytes(l,meta,32))
                assert m[0:4]==(1,32,inst,n) and m[-1]==f[6] and f[7] in (1,2)
                self.events.append((l,'wait',inst,kind,native))
                self.params[l][result]=struct.pack('<Q2I',native,1,4)
                self.write(self.local[l],ptr+56,4,4)
                if f[6]:
                    self.count['pending']-=1;self.count['consumed' if kind==0 else 'drained']+=1
                    self.group_pending[f[6]].remove(l)
                    if l==m[5]:self.count['groups_completed']+=1
        elif name=='__hbfsim_timing_future_native_store_guard_v1':
            for l,(addr,n) in argv.items():
                valid=n in (1,2,4,8) and addr>0 and addr+n<(1<<64)
                valid=valid and all(not (addr<base+length and base<addr+n) for base,length,page in self.ranges)
                self.params[l][result]=1 if valid else 6
                self.events.append((l,'store_guard',addr,n,valid))
        else:raise AssertionError('unknown helper '+name)
    def write_bytes(self,lane,addr,data):
        for i,value in enumerate(data):self.local[lane][addr+i]=value
    def bytes(self,lane,addr,n):return bytes(self.local[lane].get(addr+i,0) for i in range(n))
    def run(self):
        instructions=statements(self.text)
        labels={statement[:-1]:index for index,statement in enumerate(instructions) if statement.endswith(':')}
        skip_until=[0]*self.lanes
        for pc,statement in enumerate(instructions):
            if statement.endswith(':'):continue
            if statement.startswith('.reg '):
                m=re.fullmatch(r'\.reg\s+\.(\w+)\s+(.+)',statement);assert m,statement
                for reg in split_operands(m[2]):
                    q=re.fullmatch(r'(%\w+)(?:<(\d+)>)?',reg);assert q,reg
                    for i in range(int(q[2] or 1)):
                        name=q[1]+(str(i) if q[2] else '');self.types[name]=m[1]
                continue
            if statement.startswith('.local '):
                m=re.fullmatch(r'\.local\s+\.align\s+(\d+)\s+\.b8\s+(\w+)\[(\d+)\]',statement);assert m,statement
                self.symbol[m[2]]=0x400000+len(self.symbol)*256;continue
            if statement.startswith('.param '):continue
            predicate=None
            if statement.startswith('@'):predicate,statement=statement.split(None,1)
            op,_,argtext=statement.partition(' ');args=split_operands(argtext) if argtext else []
            active=[l for l in range(self.lanes) if pc>=skip_until[l] and not self.terminated[l] and (not predicate or bool(self.val(l,predicate.lstrip('@!')))!=(predicate.startswith('@!')))]
            if not active:continue
            if op=='bra':
                assert args[0].startswith('__tf_skip') and labels[args[0]]>pc,'only finite emitted forward guards'
                for l in active:skip_until[l]=labels[args[0]]
                continue
            if op in ('call','call.uni'):self.call(active,args);continue
            for l in active:self.operation(l,op,args)
        assert all(self.terminated) and self.count['pending']==0
        assert self.count['issued']==self.count['consumed']+self.count['drained']
        assert self.count['groups_issued']==self.count['groups_completed']
        return self
    def operation(self,l,op,a):
        parts=op.split('.');base=parts[0];typ=parts[-1]
        if base in ('ret','exit'):self.terminated[l]=True;self.events.append((l,'exit'));return
        if op.startswith('ld.param.'):
            m=re.fullmatch(r'\[([^+]+)(?:\+([0-9]+))?\]',a[1]);value=self.params[l][m[1]]
            if isinstance(value,bytes):value=int.from_bytes(value[int(m[2] or 0):int(m[2] or 0)+int(typ[1:])//8],'little')
            self.put(l,a[0],value);return
        if op.startswith('st.param.'):
            self.params[l][a[0][1:-1]]=self.val(l,a[1]);return
        if base in ('ld','st'):
            n=int(typ[1:])//8;space=self.local[l] if parts[1]=='local' else self.memory
            if base=='ld':
                addr=self.address(l,a[1]);value=self.read(space,addr,n)
                if typ[0]=='s' and value>>(n*8-1):value-=1<<(n*8)
                self.put(l,a[0],value)
                if parts[1]=='global':self.events.append((l,'native_load',addr,value))
            else:
                addr=self.address(l,a[0]);self.write(space,addr,n,self.val(l,a[1]))
                if parts[1]=='global':self.events.append((l,'native_store',addr,self.val(l,a[1])))
            return
        if base in ('membar','fence'):self.events.append((l,'fence'));return
        if base in ('mov','cvta'):self.put(l,a[0],self.val(l,a[1]));return
        if base=='cvt':
            value=self.val(l,a[1]);source=parts[-1];bits=int(source[1:]);value&=(1<<bits)-1
            if source[0]=='s' and value>>(bits-1):value-=1<<bits
            self.put(l,a[0],value);return
        x=self.val(l,a[1]);y=self.val(l,a[2]);bits=1 if typ=='pred' else int(typ[1:])
        if typ.startswith('s'):
            x=x-(1<<bits) if x>>(bits-1)&1 else x;y=y-(1<<bits) if y>>(bits-1)&1 else y
        if base=='setp':value={'eq':x==y,'ne':x!=y,'lt':x<y,'le':x<=y,'gt':x>y,'ge':x>=y}[parts[1]]
        else:
            table={'add':lambda:x+y,'sub':lambda:x-y,'mul':lambda:x*y,'mad':lambda:x*y+self.val(l,a[3]),'and':lambda:x&y,'or':lambda:x|y,'xor':lambda:x^y,'shl':lambda:x<<y,'shr':lambda:x>>y}
            if base not in table:raise AssertionError('unknown emitted opcode '+op)
            value=table[base]()
        self.put(l,a[0],int(value));self.events.append((l,'scalar',op,a[0],int(value)))


class EmitterTests(unittest.TestCase):
    def emit(self,text,limit=16,block=32,success=True):
        with tempfile.TemporaryDirectory(prefix='.c6-fixture-',dir=ROOT) as d:
            source=Path(d)/'input.ptx';source.write_text(text)
            result=subprocess.run([str(DRIVER),str(source),str(limit),str(block)],capture_output=True,text=True,timeout=10)
        if success:self.assertEqual(result.returncode,0,result.stderr);return result.stdout
        self.assertNotEqual(result.returncode,0,'unsupported source was admitted');return result.stderr
    def test_predicate_and_ordering_table(self):
        cases=[
          ('producer_changes','mov.u32 %r1,7; setp.eq.u32 %p0,%r0,0; @%p0 ld.global.u32 %r1,[%rd4]; setp.ne.u32 %p0,%r0,0; add.u32 %r2,%r1,1;',42,1,0),
          ('false_consumer','ld.global.u32 %r1,[%rd4]; setp.ne.u32 %p0,%r0,0; @%p0 add.u32 %r3,%r1,1; add.u32 %r2,%r1,1;',42,1,0),
          ('false_fence','ld.global.u32 %r1,[%rd4]; setp.ne.u32 %p0,%r0,0; @%p0 fence.acq_rel.gpu; add.u32 %r2,%r1,1;',42,1,0),
          ('overwrite_true','ld.global.u32 %r1,[%rd4]; setp.eq.u32 %p0,%r0,0; @%p0 mov.u32 %r1,9; add.u32 %r2,%r1,1;',10,0,1),
          ('overwrite_false','ld.global.u32 %r1,[%rd4]; setp.ne.u32 %p0,%r0,0; @%p0 mov.u32 %r1,9; add.u32 %r2,%r1,1;',42,1,0),
          ('same_destination','ld.global.u32 %r1,[%rd4]; ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;',42,1,1),
          ('independent','ld.global.u32 %r1,[%rd4]; ld.global.u32 %r3,[%rd4]; add.u32 %r2,%r1,%r3;',82,2,0),
          ('exit_drain','ld.global.u32 %r1,[%rd4]; mov.u32 %r2,9;',9,0,1),
        ]
        for name,body,want,consumed,drained in cases:
            with self.subTest(name=name):
                machine=Machine(self.emit(program(body))).run()
                self.assertEqual(machine.reg[0]['%r2'],want)
                self.assertEqual(machine.count['consumed'],consumed)
                self.assertEqual(machine.count['drained'],drained)
                issue=next(i for i,e in enumerate(machine.events) if e[1]=='issue')
                native=next(i for i,e in enumerate(machine.events) if e[1]=='native_load')
                wait=next(i for i,e in enumerate(machine.events) if e[1]=='wait')
                self.assertLess(issue,native);self.assertLess(native,wait)
    def test_predicate_combinations(self):
        for p in (0,1):
            for q in (0,1):
                body=f'ld.global.u32 %r1,[%rd4]; setp.eq.u32 %p0,{p},1; setp.eq.u32 %p1,{q},1; @%p0 add.u32 %r3,%r1,1; @%p1 add.u32 %r4,%r1,2; add.u32 %r2,%r1,3;'
                m=Machine(self.emit(program(body))).run();self.assertEqual(m.reg[0]['%r2'],44);self.assertEqual(m.count['consumed'],1)
    def test_false_producer_preserves_destination(self):
        m=Machine(self.emit(program('mov.u32 %r1,7; setp.ne.u32 %p0,%r0,0; @%p0 ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;'))).run()
        self.assertEqual(m.reg[0]['%r2'],8);self.assertEqual(m.count['issued'],0)
    def test_mixed_lanes_and_pages(self):
        text=self.emit(program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;'))
        m=Machine(text,lanes=8,base=0x1ff0,ranges=((0x1000,4096,4096),)).run()
        self.assertEqual(m.count['issued'],4);self.assertEqual(m.count['native_loads'],4);self.assertEqual(m.count['groups_issued'],1)
        self.assertTrue(all(l['%r2']==42 for l in m.reg))
        text=self.emit(program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;').replace('mul.wide.u32 %rd3,%r0,4','mul.wide.u32 %rd3,%r0,4096'))
        m=Machine(text,lanes=3,ranges=((0x1000,8192,4096),)).run()
        self.assertEqual(m.count['groups_issued'],2);self.assertEqual(m.count['native_loads'],1)
    def test_reject_before_native_load(self):
        m=Machine(self.emit(program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;')),base=(1<<64)-2)
        with self.assertRaises(RuntimeError):m.run()
        self.assertFalse(any(e[1]=='native_load' for e in m.events))
    def test_native_output_store_guard(self):
        text=self.emit(program('ld.global.u32 %r1,[%rd4]; st.global.u32 [%rd1],%r1;'))
        m=Machine(text).run();self.assertEqual(m.read(m.memory,0x100000,4),41);self.assertEqual(m.count['drained'],1)
        for output in (0x1000,0xffe,(1<<64)-2):
            m=Machine(text);m.params[0]['output']=output
            with self.assertRaises(RuntimeError):m.run()
            self.assertFalse(any(e[1]=='native_store' for e in m.events))
    def test_spans_and_comments(self):
        text=program('ld.global.u32 /* retain exactly */ %r1,\n[%rd4]; add.u32 %r2,%r1,1;')
        emitted=self.emit(text);self.assertIn('.visible .func unrelated() { ret; }',emitted);self.assertIn('/* retain exactly */',emitted)
        self.assertEqual(Machine(emitted).run().reg[0]['%r2'],42)
    def test_rejection_table(self):
        for body in ('bra foo;','@%p0 ret;','call foo;','fma.rn.f32 %r1,%r2,%r3,%r4;','ld.global.f16 %r1,[%rd4];','ld.volatile.global.u32 %r1,[%rd4];','ld.global.v2.u32 {%r1,%r2},[%rd4];','atom.global.add.u32 %r1,[%rd4],1;','cp.async.ca.shared.global [%r1],[%rd4],4;','ret; mov.u32 %r1,1;','mov.weird %r1,1;'):
            with self.subTest(body=body):self.emit(program(body),success=False)
        for limit in (0,17):self.emit(program('ld.global.u32 %r1,[%rd4];'),limit=limit,success=False)
        for block in (0,1025):self.emit(program('ld.global.u32 %r1,[%rd4];'),block=block,success=False)
    def test_actual_wait_return_controls_consumer(self):
        text=self.emit(program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;'))
        for status in (1,6):
            machine=Machine(text);original=machine.call
            def replace_result(active,operands):
                original(active,operands)
                if '__hbfsim_timing_future_wait_v1' in operands:
                    for lane in active:machine.params[lane][operands[0][1:-1]]=struct.pack('<Q2I',99,status,4 if status==1 else 5)
            machine.call=replace_result
            if status==1:
                machine.run();self.assertEqual(machine.reg[0]['%r2'],100)
            else:
                with self.assertRaises(RuntimeError):machine.run()
                self.assertNotIn('%r2',machine.reg[0])
    def test_mixed_producer_and_consumer_predicates(self):
        body='mov.u32 %r1,7; setp.lt.u32 %p0,%r0,4; @%p0 ld.global.u32 %r1,[%rd4]; setp.ge.u32 %p0,%r0,2; @%p0 add.u32 %r3,%r1,1; add.u32 %r2,%r1,2;'
        m=Machine(self.emit(program(body)),lanes=8).run()
        self.assertEqual([r['%r2'] for r in m.reg],[43]*4+[9]*4)
        self.assertEqual(m.count['issued'],4);self.assertEqual(m.count['consumed'],4);self.assertEqual(m.count['groups_issued'],1)
        waits=[e[0] for e in m.events if e[1]=='wait'];self.assertEqual(waits,[2,3,0,1])

    def test_unsupported_types_and_parameter_identity(self):
        text=program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;')
        variants=[text.replace('ld.param.u64 %rd0,[input]','ld.param.u64 %rd0,[missing]'),
                  text.replace('ld.global.u32 %r1','ld.global.u64 %r1'),
                  text.replace('ld.global.u32 %r1','ld.global.u32 %missing'),
                  text.replace('add.u32 %r2,%r1,1','add.fancy.u32 %r2,%r1,1'),
                  text.replace('add.u32 %r2,%r1,1','add.u32 %r2,%rd1,1'),
                  text.replace('add.u32 %r2,%r1,1','add.u32 %r2,unknown,1'),
                  text.replace('mov.u32 %r0,%tid.x','mov.u32 %r0,%tid.w')]
        for variant in variants:self.emit(variant,success=False)
        self.emit(program('ld.global.u32 %r1,[%rd4];'*17),success=False)
    def test_executing_fence_and_predicated_store(self):
        for execute in (0,1):
            for ordering in ('fence.acq_rel.gpu','st.global.u32 [%rd1],9'):
                body=f'ld.global.u32 %r1,[%rd4]; setp.eq.u32 %p0,{execute},1; @%p0 {ordering}; add.u32 %r2,%r1,1;'
                m=Machine(self.emit(program(body))).run()
                self.assertEqual(m.reg[0]['%r2'],42)
                self.assertEqual(m.count['drained'],execute)
                self.assertEqual(m.count['consumed'],1-execute)
    def test_invalid_parameter_and_ordering_forms(self):
        for body in ('fence.acq_rel.gpu %r1;','membar.gl 1;','mov.pred %p0,7;','mov.b32 %r3,0xffffffffffffffffffffffffffff;'):
            self.emit(program('ld.global.u32 %r1,[%rd4]; '+body),success=False)
        text=program('ld.global.u32 %r1,[%rd4];').replace('ld.param.u64 %rd0','@%p0 ld.param.u64 %rd0')
        self.emit(text,success=False)

    def test_unrelated_entry_is_opaque(self):
        unrelated='.visible .entry other() { .reg .b32 %r; again: bar.sync 0; bra again; }'
        text=program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;')+unrelated
        emitted=self.emit(text);self.assertIn(unrelated,emitted)

    def test_predicated_second_producer_same_destination(self):
        for predicate,lanes,executed in [('mov.pred %p0,0',1,0),('mov.pred %p0,1',1,1),('setp.lt.u32 %p0,%r0,4',8,4)]:
            body=predicate+'; ld.global.u32 %r1,[%rd4]; @%p0 ld.global.u32 %r1,[%rd4+64]; add.u32 %r2,%r1,1;'
            with self.subTest(predicate=predicate):
                machine=Machine(self.emit(program(body)),lanes=lanes)
                for lane in range(lanes):machine.write(machine.memory,0x1040+4*lane,4,99)
                machine.run()
                self.assertEqual([r['%r2'] for r in machine.reg],[100]*executed+[42]*(lanes-executed))
                self.assertEqual(machine.count['issued'],lanes+executed)
                self.assertEqual(machine.count['drained'],executed)
                self.assertEqual(machine.count['consumed'],lanes)

    def test_selected_statement_strings_reject(self):
        for load in ('ld.global.u32 "not an admitted operand" %r1,[%rd4];','"prefix" ld.global.u32 %r1,[%rd4];','ld.global.u32 %r1,[%rd4] "suffix";'):
            with self.subTest(load=load):
                self.emit(program(load+' add.u32 %r2,%r1,1;'),success=False)
        metadata='.file 1 "quoted.metadata.ptx"'
        text=program('ld.global.u32 /* "preserved comment" */ %r1,[%rd4]; add.u32 %r2,%r1,1;').replace('.address_size 64','.address_size 64\n'+metadata)
        emitted=self.emit(text)
        self.assertIn(metadata,emitted);self.assertIn('/* "preserved comment" */',emitted)
        self.assertEqual(Machine(emitted).run().reg[0]['%r2'],42)

    def test_validated_address_header(self):
        text=program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;')
        for prefix in ('// header .address_size 64 below\n','/* header .address_size 64 below */\n'):
            emitted=self.emit(prefix+text)
            self.assertGreater(emitted.index('.extern .func'),emitted.index('\n.address_size 64\n'))
        for bad in (text.replace('.address_size 64','.address_size 32'),text.replace('.address_size 64','.address_size 64\n.address_size 64'),text.replace('.address_size 64','// .address_size 64')):
            self.emit(bad,success=False)

    def test_explicit_launch_geometry(self):
        text=program('ld.global.u32 %r1,[%rd4]; add.u32 %r2,%r1,1;')
        for directive in ('.reqntid 64,1,1','.reqntid 8,8,1','.reqntid 0,1,1','.maxntid 4294967296,1,1','.reqntid 32 .reqntid 32','.maxntid 16 .reqntid 32','.reqntid 4294967295,4294967295,4294967295'):
            with self.subTest(directive=directive):
                self.emit(text.replace('output) {','output) '+directive+' {'),block=32,success=False)
        for directive in ('.reqntid 32,1,1','.maxntid 8,4,1','.maxntid 64,1,1'):
            emitted=self.emit(text.replace('output) {','output) '+directive+' {'),block=32)
            self.assertIn(directive,emitted)

    def test_integer_and_float_bits(self):
        cases=[(kind+str(width),width,max(16,width),(1<<(width-1))+1) for kind in 'usb' for width in (8,16,32,64)]
        cases += [('s8',8,32,0x80),('s8',8,64,0x80),('u8',8,32,0x80),('s16',16,64,0x8001),('u16',16,32,0x8001),('s32',32,64,0x80000001),('b32',32,32,0xdeadbeef),('u64',64,64,0xfedcba9876543210),('f32',32,32,0x7fc01234),('f64',64,64,0x7ff8123456789abc)]
        for typ,width,destbits,bits in cases:
            with self.subTest(type=typ):
                regtype=typ if typ.startswith('f') else 'b'+str(destbits)
                body=f'ld.global.{typ} %v,[%rd4]; mov.b{destbits} %got,%v;'
                text=self.emit(program(body,f'.reg .{regtype} %v; .reg .b{destbits} %got;'))
                m=Machine(text);m.write(m.memory,0x1000,width//8,bits);m.run()
                expected=bits
                if typ.startswith('s') and bits>>(width-1):expected|=((1<<destbits)-1)^((1<<width)-1)
                self.assertEqual(m.reg[0]['%got'],expected);self.assertEqual(m.count['consumed'],1)

if __name__=='__main__':unittest.main()
