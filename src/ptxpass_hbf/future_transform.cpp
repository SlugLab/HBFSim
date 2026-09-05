#include "future_transform.hpp"

#include <algorithm>
#include <limits>
#include <regex>
#include <sstream>
#include <stdexcept>

namespace hbfsim::ptx {
namespace {
constexpr auto prefix="%__tf_";
std::string reg(std::string_view kind,std::uint32_t id) {
    return std::string(prefix)+std::string(kind)+std::to_string(id);
}
std::string symbol(std::string_view kind,std::uint32_t id) {
    return "__tf_"+std::string(kind)+std::to_string(id);
}
unsigned bits(const std::string& type) {
    return type=="pred" ? 1 : static_cast<unsigned>(std::stoul(type.substr(1)));
}
std::string type_of(const Function& function,const std::string& operand) {
    if(auto it=function.register_types.find(operand);it!=function.register_types.end()) return it->second;
    static const std::regex special(R"(^%(?:(?:tid|ntid|ctaid|nctaid)\.[xyz]|laneid|warpid|warpsize)$)");
    if(std::regex_match(operand,special)) return "u32";
    throw ParseError("undeclared or unsupported register: "+operand);
}
std::vector<std::string> parts(const std::string& op) {
    std::vector<std::string> result;std::size_t start=0;
    for(;;) {auto end=op.find('.',start);result.push_back(op.substr(start,end-start));if(end==std::string::npos)break;start=end+1;}
    return result;
}
void validate_types(const Function& f,const Instruction& i) {
    static const std::regex predicate(R"(^@!?%[A-Za-z][A-Za-z0-9_$]*$)");
    static const std::regex scalar(R"(^%[A-Za-z][A-Za-z0-9_$]*(?:\.[xyz])?$|^-?(?:[0-9]+|0[xX][0-9a-fA-F]+)$|^0[fF][0-9a-fA-F]{8}$|^0[dD][0-9a-fA-F]{16}$)");
    if(!i.predicate.empty()) {
        if(!std::regex_match(i.predicate,predicate) || type_of(f,i.predicate.substr(i.predicate[1]=='!'?2:1))!="pred")
            throw ParseError("invalid producer/consumer predicate");
    }
    for(const auto& r:i.defs) if(!f.register_types.contains(r)) throw ParseError("invalid destination register");
    for(const auto& r:i.uses) (void)type_of(f,r);
    const auto p=parts(i.opcode);const auto type=p.back();
    const auto operand_type=[&](const std::string& operand,const std::string& expected,bool wider=false) {
        if(!std::regex_match(operand,scalar)) throw ParseError("unknown scalar operand");
        if(!operand.starts_with('%')) {
            if(expected=="pred") {
                if(operand!="0" && operand!="1")throw ParseError("predicate literal must be boolean");
            } else if(operand.starts_with("0f") || operand.starts_with("0F") ||
                      operand.starts_with("0d") || operand.starts_with("0D")) {
                const auto literal_bits=(operand[1]=='f' || operand[1]=='F')?32U:64U;
                if(bits(expected)!=literal_bits || (expected.front()!='b' && expected.front()!='f'))
                    throw ParseError("floating bit literal type mismatch");
            } else {
                auto magnitude=operand.starts_with('-')?operand.substr(1):operand;
                const int radix=magnitude.starts_with("0x") || magnitude.starts_with("0X")?16:10;
                if(radix==16)magnitude=magnitude.substr(2);
                std::size_t count=0;
                try {(void)std::stoull(magnitude,&count,radix);}
                catch(const std::exception&) {throw ParseError("integer literal exceeds supported width");}
                if(count!=magnitude.size())throw ParseError("invalid integer literal");
            }
            return;
        }
        const auto actual=type_of(f,operand);
        if(actual=="pred" || expected=="pred") {
            if(actual!=expected)throw ParseError("predicate type mismatch");return;
        }
        if((wider ? bits(actual)<bits(expected) : bits(actual)!=bits(expected)) ||
            (expected.front()!='b' && actual.front()!='b' &&
             (expected.front()=='f')!=(actual.front()=='f')))
            throw ParseError("scalar register type/width mismatch");
    };
    if(i.memory) {
        const auto& m=*i.memory;
        operand_type(m.address_base,"u64");
        const auto& value=i.operands[m.kind==MemoryKind::Load?0:1];
        // Integer loads legally extend into a wider register. Floating loads
        // retain exactly f32/f64 bits; f16 data uses a b16 load instead.
        operand_type(value,type,type.front()!='f');
        if(m.kind==MemoryKind::Load && !f.register_types.contains(value)) throw ParseError("load destination is not a register");
        return;
    }
    if(p[0]=="ld") {
        if(!i.predicate.empty())throw ParseError("PTX parameter transfer cannot be predicated");
        static const std::regex parameter(R"(^\[([A-Za-z_$][A-Za-z0-9_$.]*)\]$)");
        std::smatch match;
        if(!std::regex_match(i.operands[1],match,parameter) || !f.parameter_types.contains(match[1].str()) ||
            f.parameter_types.at(match[1].str())!=type) throw ParseError("unbound parameter type");
        operand_type(i.operands[0],type,type.front()!='f');return;
    }
    if(p[0]=="ret" || p[0]=="exit" || p[0]=="fence" || p[0]=="membar") return;
    if(p[0]=="cvt") {
        operand_type(i.operands[0],p[1]);operand_type(i.operands[1],p[2]);return;
    }
    for(std::size_t n=0;n<i.operands.size();++n) {
        auto expected=type;
        if(p[0]=="setp" && n==0) expected="pred";
        if((p[0]=="mul" || p[0]=="mad") && p[1]=="wide" && (n==0 || n==3)) expected=type.substr(0,1)+std::to_string(2*bits(type));
        if((p[0]=="shl" || p[0]=="shr") && n==2) expected="u32";
        operand_type(i.operands[n],expected);
    }
}

struct Producer {
    const Instruction* instruction;
    std::string destination,type;
};
class Writer {
 public:
    std::ostringstream declarations,code;
    unsigned call_id{0};
    void emit(const std::string& instruction,const std::string& pred={}) {
        // PTX call-space parameter transfers cannot themselves be predicated.
        // Their callers own a forward guard covering marshal/call/unmarshal.
        const bool parameter=instruction.starts_with("st.param.") || instruction.starts_with("ld.param.");
        code<<"    "<<(pred.empty() || parameter?"":"@"+pred+" ")<<instruction<<";\n";
    }
    std::string begin_guard(const std::string& predicate) {
        const auto label=symbol("skip",call_id++);
        emit("bra "+label,"!"+predicate);return label;
    }
    void end_guard(const std::string& label) {code<<label<<":\n";}
    void declare_reg(const std::string& type,const std::string& name) {declarations<<"    .reg ."<<type<<' '<<name<<";\n";}
    std::string call(const std::string& helper,const std::vector<std::pair<unsigned,std::string>>& args,unsigned result_bytes,unsigned alignment,const std::string& pred) {
        const auto id=call_id++;const auto result=symbol("result",id);
        if(result_bytes==4)declarations<<"    .param .b32 "<<result<<";\n";
        else declarations<<"    .param .align "<<alignment<<" .b8 "<<result<<'['<<result_bytes<<"];\n";
        std::string argv;
        for(std::size_t n=0;n<args.size();++n) {
            const auto name=symbol("arg",id)+"_"+std::to_string(n);
            declarations<<"    .param .b"<<args[n].first<<' '<<name<<";\n";
            emit("st.param.b"+std::to_string(args[n].first)+" ["+name+"], "+args[n].second,pred);
            if(n)argv+=", ";argv+=name;
        }
        emit("call ("+result+"), "+helper+", ("+argv+")",pred);return result;
    }
    void verify(const std::string& value,unsigned expected,const std::string& execution) {
        const auto id=call_id++;const auto bad=reg("bad",id),status=symbol("fault",id);
        declare_reg("pred",bad);declarations<<"    .param .b32 "<<status<<";\n";
        emit("mov.pred "+bad+", 0");emit("setp.ne.u32 "+bad+", "+value+", "+std::to_string(expected),execution);
        emit("st.param.b32 ["+status+"], 6",bad);emit("call __hbfsim_fault, ("+status+")",bad);
    }
    std::string address(const Instruction& i,const std::string& execution) {
        const auto id=call_id++;const auto value=reg("address",id),bad=reg("overflow",id);
        declare_reg("b64",value);declare_reg("pred",bad);
        const auto& m=*i.memory;const auto base=m.address_base;
        emit("mov.pred "+bad+", 0");
        if(m.signed_offset>=0) {
            const auto offset=std::to_string(m.signed_offset);
            emit("add.u64 "+value+", "+base+", "+offset,execution);
            emit("setp.lt.u64 "+bad+", "+value+", "+base,execution);
        } else {
            const auto magnitude=std::uint64_t{0}-static_cast<std::uint64_t>(m.signed_offset);
            emit("setp.lt.u64 "+bad+", "+base+", "+std::to_string(magnitude),execution);
            emit("sub.u64 "+value+", "+base+", "+std::to_string(magnitude),execution);
        }
        const auto status=symbol("fault",id);declarations<<"    .param .b32 "<<status<<";\n";
        emit("st.param.b32 ["+status+"], 6",bad);emit("call __hbfsim_fault, ("+status+")",bad);
        return value;
    }
    void wait(const Producer& p,const std::string& execution,unsigned kind) {
        const auto id=p.instruction->instruction_id;const auto unique=call_id++;
        const auto go=reg("go",unique),status=reg("status",unique),state=reg("state",unique),returned=reg("returned",unique);
        declare_reg("pred",go);declare_reg("b32",status);declare_reg("b32",state);declare_reg("b64",returned);
        emit("and.pred "+go+", "+execution+", "+reg("valid",id));
        const auto skip=begin_guard(go);
        const auto result=call("__hbfsim_timing_future_wait_v1",{{64,reg("tokenptr",id)},{64,reg("metaptr",id)},{64,reg("nativebits",id)},{32,std::to_string(id)},{32,std::to_string(p.instruction->memory->bytes)},{32,std::to_string(kind)}},16,8,go);
        emit("ld.param.b64 "+returned+", ["+result+"]",go);
        emit("ld.param.b32 "+status+", ["+result+"+8]",go);
        emit("ld.param.b32 "+state+", ["+result+"+12]",go);
        verify(status,1,go);verify(state,4,go);
        const auto width=bits(p.type);
        if(width==64)emit("mov.b64 "+p.destination+", "+returned,go);
        else {
            const auto narrow=reg("narrow",unique);declare_reg("b"+std::to_string(width),narrow);
            emit("cvt.u"+std::to_string(width)+".u64 "+narrow+", "+returned,go);
            emit("mov.b"+std::to_string(width)+" "+p.destination+", "+narrow,go);
        }
        emit("mov.pred "+reg("valid",id)+", 0",go);
        end_guard(skip);
    }
};
const char* declarations=R"PTX(
// Private C6.1 compile/CPU output: public unit remains incomplete.
.extern .func (.param .align 16 .b8 __tf_issue_return[64]) __hbfsim_timing_future_issue_v1(.param .b64 __tf_i0, .param .b32 __tf_i1, .param .b32 __tf_i2, .param .b32 __tf_i3, .param .b64 __tf_i4);
.extern .func (.param .align 8 .b8 __tf_wait_return[16]) __hbfsim_timing_future_wait_v1(.param .b64 __tf_w0, .param .b64 __tf_w1, .param .b64 __tf_w2, .param .b32 __tf_w3, .param .b32 __tf_w4, .param .b32 __tf_w5);
.extern .func (.param .b32 __tf_store_return) __hbfsim_timing_future_native_store_guard_v1(.param .b64 __tf_s0, .param .b32 __tf_s1);
.extern .func __hbfsim_fault(.param .b32 __tf_error);
)PTX";
}

FutureEmission emit_timing_futures(std::string_view source,std::string_view kernel,const FutureEmissionOptions& options) {
    if(!options.maximum_thread_futures || options.maximum_thread_futures>16 ||
       !options.maximum_block_threads || options.maximum_block_threads>1024)throw ParseError("finite future/CTA bound required");
    if(source.find("__tf_")!=std::string_view::npos || source.find("__hbfsim_")!=std::string_view::npos)
        throw ParseError("reserved future/helper identifier collision");
    auto module=parse_module_spanned(source,kernel);const auto& f=module.function(kernel);
    if(!f.entry)throw ParseError("future emission requires an entry kernel");
    if(f.required_thread_dimensions) {
        const auto& dimensions=*f.required_thread_dimensions;
        const auto required=std::uint64_t{dimensions[0]}*dimensions[1]*dimensions[2];
        if(required>options.maximum_block_threads)
            throw ParseError("required PTX threads exceed declared future block bound");
    }
    auto plan=analyze_futures(f);
    if(!plan.exact_safe())throw ParseError("outside timing-future straight-line subset");
    std::vector<Producer> producers;
    for(const auto& i:f.instructions) {
        validate_types(f,i);
        if(i.memory && i.memory->kind==MemoryKind::Load) producers.push_back({&i,i.operands[0],f.register_types.at(i.operands[0])});
    }
    if(producers.empty() || producers.size()>options.maximum_thread_futures)
        throw ParseError("static producer allocation exceeds finite per-thread bound");
    Writer w;std::ostringstream init;
    for(const auto& p:producers) {
        const auto id=p.instruction->instruction_id;
        w.declarations<<"    .local .align 16 .b8 "<<symbol("token",id)<<"[64];\n"
            <<"    .local .align 8 .b8 "<<symbol("meta",id)<<"[32];\n";
        for(const auto* name:{"tokenptr","metaptr","localtoken","localmeta","nativebits","chunk"})w.declare_reg("b64",reg(name,id));
        w.declare_reg(p.type,reg("raw",id));w.declare_reg("pred",reg("valid",id));w.declare_reg("b32",reg("oldstate",id));
        w.declare_reg("b"+std::to_string(bits(p.type)),reg("rawbits",id));
        w.emit("mov.u64 "+reg("localtoken",id)+", "+symbol("token",id));
        w.emit("mov.u64 "+reg("localmeta",id)+", "+symbol("meta",id));
        w.emit("cvta.local.u64 "+reg("tokenptr",id)+", "+reg("localtoken",id));
        w.emit("cvta.local.u64 "+reg("metaptr",id)+", "+reg("localmeta",id));
        for(unsigned offset=0;offset<64;offset+=8)w.emit("st.local.b64 ["+reg("localtoken",id)+"+"+std::to_string(offset)+"], 0");
        for(unsigned offset=0;offset<32;offset+=8)w.emit("st.local.b64 ["+reg("localmeta",id)+"+"+std::to_string(offset)+"], 0");
        w.emit("mov.pred "+reg("valid",id)+", 0");w.emit("mov.u64 "+reg("nativebits",id)+", 0");
    }
    init<<w.code.str();w.code.str("");
    std::string transformed;std::size_t cursor=f.body_begin+1;
    for(const auto& i:f.instructions) {
        transformed+=source.substr(cursor,i.span.begin-cursor);
        const auto exec=reg("exec",i.instruction_id);w.declare_reg("pred",exec);
        if(i.predicate.empty())w.emit("mov.pred "+exec+", 1");
        else if(i.predicate.starts_with("@!"))w.emit("xor.pred "+exec+", "+i.predicate.substr(2)+", 1");
        else w.emit("mov.pred "+exec+", "+i.predicate.substr(1));
        for(const auto& p:producers) {
            const auto id=p.instruction->instruction_id;
            const bool drain=plan.drain_points.contains(id) && plan.drain_points.at(id).contains(i.instruction_id);
            const bool consume=plan.first_consumers.contains(id) && plan.first_consumers.at(id).contains(i.instruction_id);
            if(drain || consume) w.wait(p,exec,drain?1:0);
        }
        const auto original=std::string(source.substr(i.span.begin,i.span.end-i.span.begin));
        if(i.memory && i.memory->kind==MemoryKind::Load) {
            // Preserve original comments even though the validated load span is
            // replaced. No source outside that byte span is reconstructed.
            static const std::regex comment(R"(/\*[\s\S]*?\*/|//[^\n]*)");
            for(std::sregex_iterator it(original.begin(),original.end(),comment),end;it!=end;++it)w.code<<it->str()<<'\n';
            const auto skip=w.begin_guard(exec);
            const auto id=i.instruction_id;const auto address=w.address(i,exec);
            w.emit("ld.local.b32 "+reg("oldstate",id)+", ["+reg("localtoken",id)+"+56]",exec);
            const auto result=w.call("__hbfsim_timing_future_issue_v1",{{64,address},{32,std::to_string(i.memory->bytes)},{32,std::to_string(id)},{32,reg("oldstate",id)},{64,reg("metaptr",id)}},64,16,exec);
            for(unsigned offset=0;offset<64;offset+=8) {
                w.emit("ld.param.b64 "+reg("chunk",id)+", ["+result+"+"+std::to_string(offset)+"]",exec);
                w.emit("st.local.b64 ["+reg("localtoken",id)+"+"+std::to_string(offset)+"], "+reg("chunk",id),exec);
            }
            const auto state=reg("issuestate",id),status=reg("issuestatus",id),ok=reg("issueok",id),native=reg("isnative",id),modeled=reg("ismodeled",id),bad=reg("issuebad",id);
            for(const auto& r:{state,status})w.declare_reg("b32",r);
            for(const auto& r:{ok,native,modeled,bad})w.declare_reg("pred",r);
            w.emit("ld.param.b32 "+state+", ["+result+"+56]",exec);w.emit("ld.param.b32 "+status+", ["+result+"+60]",exec);
            w.emit("mov.pred "+bad+", 0");
            w.emit("setp.eq.u32 "+native+", "+state+", 1",exec);w.emit("setp.eq.u32 "+ok+", "+status+", 1",exec);w.emit("and.pred "+native+", "+native+", "+ok,exec);
            w.emit("setp.eq.u32 "+modeled+", "+state+", 2",exec);w.emit("setp.eq.u32 "+ok+", "+status+", 0",exec);w.emit("and.pred "+modeled+", "+modeled+", "+ok,exec);
            w.emit("or.pred "+ok+", "+native+", "+modeled,exec);w.emit("xor.pred "+bad+", "+ok+", 1",exec);
            const auto fault=symbol("issuefault",id);w.declarations<<"    .param .b32 "<<fault<<";\n";
            w.emit("st.param.b32 ["+fault+"], "+status,bad);w.emit("call __hbfsim_fault, ("+fault+")",bad);
            w.emit(i.opcode+" "+reg("raw",id)+", ["+address+"]",exec);
            const auto width=bits(f.register_types.at(i.operands[0]));
            w.emit("mov.b"+std::to_string(width)+" "+reg("rawbits",id)+", "+reg("raw",id),exec);
            w.emit((width==64?"mov.b64 ":"cvt.u64.u"+std::to_string(width)+" ")+reg("nativebits",id)+", "+reg("rawbits",id),exec);
            w.emit("mov.pred "+reg("valid",id)+", 1",exec);
            w.end_guard(skip);
        } else if(i.memory && i.memory->kind==MemoryKind::Store) {
            const auto skip=w.begin_guard(exec);
            const auto address=w.address(i,exec);
            const auto result=w.call("__hbfsim_timing_future_native_store_guard_v1",{{64,address},{32,std::to_string(i.memory->bytes)}},4,4,exec);
            const auto status=reg("storestatus",i.instruction_id);w.declare_reg("b32",status);
            w.emit("ld.param.b32 "+status+", ["+result+"]",exec);w.verify(status,1,exec);
            w.code<<original<<'\n';
            w.end_guard(skip);
        } else w.code<<original<<'\n';
        transformed+=w.code.str();w.code.str("");cursor=i.span.end;
    }
    transformed+=source.substr(cursor,f.body_end-cursor);
    const auto header_end=module.address_size_directive.end;
    if(!header_end || header_end>f.body_begin)throw ParseError("missing PTX address-size header");
    FutureEmission output;
    output.allocated_thread_futures=static_cast<std::uint32_t>(producers.size());
    output.assumed_maximum_block_threads=options.maximum_block_threads;
    output.allocated_cta_futures=output.allocated_thread_futures*options.maximum_block_threads;
    plan.maximum_live.cta_futures=plan.maximum_live.thread_futures*options.maximum_block_threads;
    output.plan=std::move(plan);
    output.ptx=std::string(source.substr(0,header_end))+declarations+
        std::string(source.substr(header_end,f.body_begin+1-header_end))+"\n"+w.declarations.str()+init.str()+transformed+std::string(source.substr(f.body_end));
    return output;
}
}
