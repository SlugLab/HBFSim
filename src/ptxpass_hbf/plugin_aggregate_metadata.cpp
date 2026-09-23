#include "hbfsim/durable_append.hpp"
#include "transform.hpp"
#include <hbfsim/timing_future_abi.hpp>
#include <hbfsim/coverage.hpp>
#if defined(HBFSIM_ENABLE_TIMING_FUTURES)
#include "future_transform.hpp"
#endif

#include <json.hpp>
#include <openssl/sha.h>

#include <array>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <iomanip>
#include <mutex>
#include <map>
#include <optional>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace {

nlohmann::json pass_config()
{
    return {
        {"name", "hbf_memory"},
        {"description",
         "Rewrite supported global memory accesses through HBFSim"},
        {"attach_points",
         {{"includes", {"^kprobe/.*$"}},
          {"excludes", {"^kprobe/__(?:hbfsim|bpftime)_.*$"}}}},
        {"attach_type", 8},
        {"parameters",
         {{"resolver", "__hbfsim_resolve"},
          {"fault_handler", "__hbfsim_fault"},
          {"emit_coverage", true}}},
        {"validation",
         {{"require_entry", true},
          {"require_ret", true},
          {"ptx_version_min", "8.7"}}},
    };
}

struct PassParameter {
    std::size_t index;
    std::size_t offset;
    std::size_t width;
    std::string name;
    std::string kind;
};

struct AggregatePointerMetadata {
    nlohmann::json pointer_fields = nlohmann::json::array();
    bool fields_complete = false;
    std::vector<std::string> opaque_reason;
};

std::size_t scalar_width(const std::string& type)
{
    if (type == "pred") {
        return 1;
    }
    static const std::regex bits(R"([A-Za-z]+([0-9]+))");
    std::smatch match;
    if (!std::regex_match(type, match, bits)) {
        return 0;
    }
    return static_cast<std::size_t>(std::stoul(match[1].str())) / 8;
}

std::size_t align_up(std::size_t value, std::size_t alignment)
{
    return alignment == 0 ? value
                          : (value + alignment - 1) / alignment * alignment;
}

bool parameter_feeds_memory_address(const std::string& body,
                                    const std::string& parameter_name)
{
    // NVCC 13 emits `.ptr` for the fixture parameters, while NVCC 12.8
    // emits plain `.u64` and makes the pointer nature explicit in the body:
    // ld.param -> cvta.to.global -> address arithmetic -> ld/st.global.
    // Track only values that cross cvta.to.global as pointer bases.  A plain
    // u64 scalar used as an address offset therefore remains scalar.
    const std::regex instruction(
        R"((?:^|\n)\s*(?:@!?%[A-Za-z0-9_$]+\s+)?([A-Za-z][A-Za-z0-9_.]*)\s+([^;]+);)");
    const std::regex register_token(R"(%[A-Za-z][A-Za-z0-9_$]*)");
    const std::regex parameter_source(
        R"(\[\s*)" + parameter_name + R"((?:\s*\+\s*0)?\s*\])");
    std::vector<std::string> generic_values;
    std::vector<std::string> pointer_values;
    const auto has = [](const std::vector<std::string>& values,
                        const std::string& value) {
        return std::find(values.begin(), values.end(), value) != values.end();
    };
    const auto erase = [](std::vector<std::string>& values,
                          const std::string& value) {
        values.erase(std::remove(values.begin(), values.end(), value),
                     values.end());
    };
    const auto registers = [&](const std::string& text) {
        std::vector<std::string> result;
        for (std::sregex_iterator it(text.begin(), text.end(), register_token),
                                  end;
             it != end; ++it) {
            result.push_back(it->str());
        }
        return result;
    };
    for (std::sregex_iterator it(body.begin(), body.end(), instruction), end;
         it != end; ++it) {
        const auto opcode = (*it)[1].str();
        const auto operands = (*it)[2].str();
        const auto regs = registers(operands);

        const auto bracket = operands.find('[');
        const bool memory_opcode =
            opcode.find(".global") != std::string::npos ||
            std::regex_match(opcode, std::regex(
                R"((?:ld|st|atom|red)\.(?:[subf][0-9]+|b[0-9]+))"));
        if (bracket != std::string::npos && memory_opcode &&
            (opcode.starts_with("ld.") || opcode.starts_with("st.") ||
             opcode.starts_with("atom.") || opcode.starts_with("red."))) {
            const auto close = operands.find(']', bracket + 1);
            const auto address = operands.substr(
                bracket + 1, close == std::string::npos
                                 ? std::string::npos
                                 : close - bracket - 1);
            for (const auto& reg : registers(address)) {
                // Retain the legacy direct ld.param->memory-address case,
                // while also accepting a base proven by cvta propagation.
                if (has(generic_values, reg) || has(pointer_values, reg))
                    return true;
            }
        }
        if (regs.empty()) continue;
        const auto& destination = regs.front();
        bool generic = false;
        bool pointer = false;
        if ((opcode == "ld.param.u64" || opcode == "ld.param.b64") &&
            std::regex_search(operands, parameter_source)) {
            generic = true;
        } else if (opcode == "cvta.to.global.u64") {
            for (std::size_t index = 1; index < regs.size(); ++index)
                pointer = pointer || has(generic_values, regs[index]) ||
                          has(pointer_values, regs[index]);
        } else if (opcode == "mov.u64" || opcode == "mov.b64") {
            for (std::size_t index = 1; index < regs.size(); ++index) {
                generic = generic || has(generic_values, regs[index]);
                pointer = pointer || has(pointer_values, regs[index]);
            }
        } else if (opcode == "add.u64" || opcode == "add.s64") {
            for (std::size_t index = 1; index < regs.size(); ++index)
                pointer = pointer || has(pointer_values, regs[index]);
        }
        // Kill only for instructions that actually write their first operand.
        // Predicated writes preserve the old value on the untaken path, so
        // retain its taint and merge the new classification conservatively.
        const bool predicated = it->str().find('@') != std::string::npos;
        const bool writes_destination =
            !opcode.starts_with("st.") && !opcode.starts_with("red.") &&
            !opcode.starts_with("bra") && !opcode.starts_with("ret") &&
            !opcode.starts_with("bar.") && !opcode.starts_with("membar.");
        if (writes_destination && !predicated) {
            erase(generic_values, destination);
            erase(pointer_values, destination);
        }
        if (generic) generic_values.push_back(destination);
        if (pointer) pointer_values.push_back(destination);
    }
    return false;
}

std::vector<PassParameter> decode_parameters(const std::string& declarations,
                                             const std::string& body)
{
    static const std::regex parameter(
        R"(\.param((?:\s+\.[A-Za-z][A-Za-z0-9_]*(?:\s+\d+)?)*)\s+([A-Za-z0-9_$.]+)(?:\[(\d+)\])?)");
    static const std::regex type_qualifier(
        R"(\.(pred|[A-Za-z]+[0-9]+)(?:\s|$))");
    static const std::regex alignment_qualifier(R"(\.align\s+(\d+))");
    std::vector<PassParameter> parameters;
    std::size_t index = 0;
    std::size_t offset = 0;
    for (std::sregex_iterator
             it(declarations.begin(), declarations.end(), parameter),
         last;
         it != last; ++it, ++index) {
        const auto qualifiers = (*it)[1].str();
        std::smatch type_match;
        if (!std::regex_search(qualifiers, type_match, type_qualifier)) {
            continue;
        }
        const auto type = type_match[1].str();
        const auto name = (*it)[2].str();
        const bool aggregate = (*it)[3].matched;
        const std::size_t element_width = scalar_width(type);
        const std::size_t width =
            aggregate ? element_width *
                            static_cast<std::size_t>(std::stoul((*it)[3].str()))
                      : element_width;
        const bool pointer_qualified =
            qualifiers.find(".ptr") != std::string::npos;
        std::smatch alignment_match;
        const std::size_t alignment =
            !pointer_qualified &&
                    std::regex_search(qualifiers, alignment_match,
                                      alignment_qualifier)
                ? static_cast<std::size_t>(
                      std::stoul(alignment_match[1].str()))
                : std::min<std::size_t>(width, 8);
        offset = align_up(offset, alignment);
        std::string kind = "scalar";
        if (aggregate) {
            kind = "opaque_aggregate";
        } else if ((type == "u64" || type == "b64") &&
                   (pointer_qualified ||
                    parameter_feeds_memory_address(body, name))) {
            kind = "pointer";
        }
        parameters.push_back({index, offset, width, name, std::move(kind)});
        offset += width;
    }
    return parameters;
}

// Preserve the legacy synchronous finder and decoding behavior.
std::vector<PassParameter> parameter_metadata(const std::string& ptx,
                                              const std::string& kernel)
{
    const auto entry=ptx.find(".entry "+kernel);
    if(entry==std::string::npos)return {};
    const auto begin=ptx.find('(',entry);
    const auto end=begin==std::string::npos ? std::string::npos : ptx.find(')',begin);
    if(end==std::string::npos)return {};
    const auto body_begin=ptx.find('{',end);
    auto body_end=std::string::npos;int depth=0;
    for(auto cursor=body_begin;cursor!=std::string::npos && cursor<ptx.size();++cursor) {
        if(ptx[cursor]=='{')++depth;
        else if(ptx[cursor]=='}' && --depth==0){body_end=cursor;break;}
    }
    if(body_begin==std::string::npos || body_end==std::string::npos)return {};
    return decode_parameters(ptx.substr(begin+1,end-begin-1),
                             ptx.substr(body_begin+1,body_end-body_begin-1));
}

std::map<std::size_t, AggregatePointerMetadata>
aggregate_pointer_metadata(const std::string& ptx, const std::string& kernel,
                           const std::vector<PassParameter>& parameters)
{
    std::map<std::size_t, AggregatePointerMetadata> result;
    std::map<std::string, PassParameter> aggregates;
    for (const auto& parameter : parameters) {
        if (parameter.kind == "opaque_aggregate") {
            aggregates.emplace(parameter.name, parameter);
            result.emplace(parameter.index, AggregatePointerMetadata{});
        }
    }
    if (aggregates.empty()) return result;

    const auto entry = ptx.find(".entry " + kernel);
    const auto begin = entry == std::string::npos ? std::string::npos
                                                  : ptx.find('(', entry);
    const auto end = begin == std::string::npos ? std::string::npos
                                                : ptx.find(')', begin);
    const auto body_begin = end == std::string::npos ? std::string::npos
                                                     : ptx.find('{', end);
    auto body_end = std::string::npos;
    int depth = 0;
    for (auto cursor = body_begin;
         cursor != std::string::npos && cursor < ptx.size(); ++cursor) {
        if (ptx[cursor] == '{') ++depth;
        else if (ptx[cursor] == '}' && --depth == 0) {
            body_end = cursor;
            break;
        }
    }
    if (body_begin == std::string::npos || body_end == std::string::npos) {
        for (auto& [index, metadata] : result)
            metadata.opaque_reason.push_back("entry_body_unavailable");
        return result;
    }

    struct AddressOrigin {
        std::size_t parameter_index;
        std::optional<std::int64_t> offset;
    };
    struct LoadedOrigin {
        std::size_t parameter_index;
        std::optional<std::int64_t> offset;
        std::size_t instruction;
    };
    std::map<std::string, AddressOrigin> addresses;
    std::map<std::string, LoadedOrigin> loaded;
    std::map<std::string, std::set<std::size_t>> taint;
    const std::regex instruction(
        R"((?:^|\n)\s*(@!?%[A-Za-z0-9_$]+\s+)?([A-Za-z][A-Za-z0-9_.]*)\s+([^;]+);)");
    const std::regex register_token(R"(%[A-Za-z][A-Za-z0-9_$]*)");
    const std::regex integer_token(R"(^\s*(-?[0-9]+)\s*$)");
    const std::regex memory_source(
        R"(\[\s*(%[A-Za-z][A-Za-z0-9_$]*|[A-Za-z0-9_$.]+)(?:\s*\+\s*(-?[0-9]+))?\s*\])");
    const auto body = ptx.substr(body_begin + 1, body_end - body_begin - 1);
    const auto registers = [&](const std::string& text) {
        std::vector<std::string> values;
        for (std::sregex_iterator it(text.begin(), text.end(), register_token),
                                  last;
             it != last; ++it)
            values.push_back(it->str());
        return values;
    };
    const auto reason = [&](std::size_t index, std::string value) {
        result[index].opaque_reason.push_back(std::move(value));
    };
    const auto clear_destination = [&](const std::string& destination) {
        addresses.erase(destination);
        taint.erase(destination);
        const auto previous = loaded.find(destination);
        if (previous != loaded.end()) {
            reason(previous->second.parameter_index,
                   "u64_parameter_value_redefined_before_cvta@" +
                       std::to_string(previous->second.instruction));
            loaded.erase(previous);
        }
    };

    std::size_t ordinal = 0;
    for (std::sregex_iterator it(body.begin(), body.end(), instruction), last;
         it != last; ++it) {
        ++ordinal;
        const bool predicated = (*it)[1].matched;
        const auto opcode = (*it)[2].str();
        const auto operands = (*it)[3].str();
        const auto regs = registers(operands);
        if (regs.empty()) continue;
        const auto destination = regs.front();

        if (opcode == "mov.b64" || opcode == "mov.u64") {
            const auto comma = operands.find(',');
            const auto source = comma == std::string::npos
                                    ? std::string{}
                                    : operands.substr(comma + 1);
            const auto found = std::find_if(
                aggregates.begin(), aggregates.end(), [&](const auto& pair) {
                    return std::regex_search(
                        source, std::regex("(^|[^A-Za-z0-9_$.])" +
                                           pair.first +
                                           "([^A-Za-z0-9_$.]|$)"));
                });
            if (found != aggregates.end()) {
                clear_destination(destination);
                if (predicated) {
                    reason(found->second.index,
                           "predicated_aggregate_base_definition@" +
                               std::to_string(ordinal));
                    continue;
                }
                addresses[destination] = {found->second.index, 0};
                taint[destination] = {found->second.index};
                continue;
            }
        }

        if ((opcode == "add.s64" || opcode == "add.u64") &&
            regs.size() >= 2 && addresses.contains(regs[1])) {
            const auto origin = addresses.at(regs[1]);
            const auto comma = operands.rfind(',');
            std::smatch immediate;
            std::optional<std::int64_t> offset;
            const auto immediate_text = comma == std::string::npos
                                            ? std::string{}
                                            : operands.substr(comma + 1);
            if (comma != std::string::npos &&
                std::regex_match(immediate_text, immediate, integer_token) &&
                origin.offset) {
                offset = *origin.offset + std::stoll(immediate[1].str());
            }
            clear_destination(destination);
            if (predicated) {
                reason(origin.parameter_index,
                       "predicated_aggregate_address_derivation@" +
                           std::to_string(ordinal));
                continue;
            }
            addresses[destination] = {origin.parameter_index, offset};
            taint[destination] = {origin.parameter_index};
            continue;
        }

        if (opcode.starts_with("ld.param.")) {
            std::smatch source;
            clear_destination(destination);
            if (std::regex_search(operands, source, memory_source)) {
                const auto base = source[1].str();
                const auto displacement = source[2].matched
                                              ? std::stoll(source[2].str())
                                              : 0;
                std::optional<AddressOrigin> origin;
                if (addresses.contains(base)) {
                    origin = addresses.at(base);
                    if (origin->offset) origin->offset = *origin->offset + displacement;
                } else if (aggregates.contains(base)) {
                    origin = AddressOrigin{aggregates.at(base).index,
                                           displacement};
                }
                if (origin) {
                    taint[destination] = {origin->parameter_index};
                    if (predicated) {
                        reason(origin->parameter_index,
                               "predicated_aggregate_parameter_load@" +
                                   std::to_string(ordinal));
                        continue;
                    }
                    if (opcode == "ld.param.u64") {
                        loaded[destination] = {origin->parameter_index,
                                               origin->offset, ordinal};
                        if (!origin->offset)
                            reason(origin->parameter_index,
                                   "dynamic_u64_parameter_offset@" +
                                       std::to_string(ordinal));
                    } else if (opcode.ends_with("64")) {
                        reason(origin->parameter_index,
                               "unsupported_64bit_parameter_load_opcode_" +
                                   opcode.substr(std::string("ld.param.").size()) +
                                   "@" + std::to_string(ordinal));
                    }
                }
            }
            continue;
        }

        if (opcode == "cvta.to.global.u64") {
            std::optional<LoadedOrigin> loaded_source;
            std::set<std::size_t> tainted_source;
            if (regs.size() >= 2 && loaded.contains(regs[1]))
                loaded_source = loaded.at(regs[1]);
            if (regs.size() >= 2 && taint.contains(regs[1]))
                tainted_source = taint.at(regs[1]);
            if (regs.size() >= 2 && destination == regs[1]) {
                addresses.erase(destination);
                loaded.erase(destination);
                taint.erase(destination);
            } else {
                clear_destination(destination);
            }
            if (predicated && (loaded_source || !tainted_source.empty())) {
                if (loaded_source)
                    reason(loaded_source->parameter_index,
                           "predicated_pointer_conversion@" +
                               std::to_string(ordinal));
                for (const auto index : tainted_source)
                    reason(index, "predicated_pointer_conversion@" +
                                      std::to_string(ordinal));
                if (regs.size() >= 2) {
                    loaded.erase(regs[1]);
                    taint.erase(regs[1]);
                }
                continue;
            }
            if (loaded_source) {
                const auto origin = *loaded_source;
                const auto& parameter = std::find_if(
                    parameters.begin(), parameters.end(), [&](const auto& p) {
                        return p.index == origin.parameter_index;
                    });
                if (!origin.offset) {
                    reason(origin.parameter_index,
                           "unresolved_cvta_source@" + std::to_string(ordinal));
                } else if (parameter == parameters.end() || *origin.offset < 0 ||
                           static_cast<std::size_t>(*origin.offset) + 8 >
                               parameter->width ||
                           *origin.offset % 8 != 0) {
                    reason(origin.parameter_index,
                           "out_of_bounds_or_unaligned_pointer_field@" +
                               std::to_string(origin.instruction));
                } else {
                    result[origin.parameter_index].pointer_fields.push_back(
                        {{"byte_offset", *origin.offset},
                         {"width", 8},
                         {"proof_kind",
                          "ptx_constant_param_load_to_cvta_global_v1"},
                         {"load_opcode", "ld.param.u64"},
                         {"conversion_opcode", "cvta.to.global.u64"},
                         {"load_instruction", origin.instruction},
                         {"conversion_instruction", ordinal}});
                }
                if (regs.size() >= 2) {
                    loaded.erase(regs[1]);
                    taint.erase(regs[1]);
                }
            } else if (!tainted_source.empty()) {
                for (const auto index : tainted_source)
                    reason(index,
                           "aggregate_derived_cvta_without_proven_u64_field@" +
                               std::to_string(ordinal));
            }
            continue;
        }

        const bool writes_destination =
            !opcode.starts_with("st.") && !opcode.starts_with("red.") &&
            !opcode.starts_with("bra") && !opcode.starts_with("ret") &&
            !opcode.starts_with("bar.") && !opcode.starts_with("membar.");
        if (writes_destination) {
            clear_destination(destination);
            std::set<std::size_t> sources;
            for (std::size_t index = 1; index < regs.size(); ++index) {
                if (taint.contains(regs[index]))
                    sources.insert(taint.at(regs[index]).begin(),
                                   taint.at(regs[index]).end());
            }
            if (!sources.empty()) taint[destination] = std::move(sources);
        }
        for (const auto& reg : regs) {
            if (!addresses.contains(reg)) continue;
            reason(addresses.at(reg).parameter_index,
                   std::string("unparsed_") +
                       (predicated ? "predicated" : "plain") +
                       "_aggregate_address_use@" + std::to_string(ordinal));
        }
    }
    for (const auto& [reg, origin] : loaded)
        reason(origin.parameter_index,
               "u64_parameter_load_not_proven_global_pointer@" +
                   std::to_string(origin.instruction));

    for (auto& [index, metadata] : result) {
        std::sort(metadata.opaque_reason.begin(), metadata.opaque_reason.end());
        metadata.opaque_reason.erase(
            std::unique(metadata.opaque_reason.begin(),
                        metadata.opaque_reason.end()),
            metadata.opaque_reason.end());
        std::set<std::string> seen;
        nlohmann::json unique = nlohmann::json::array();
        for (const auto& field : metadata.pointer_fields) {
            if (seen.insert(field.dump()).second) unique.push_back(field);
        }
        metadata.pointer_fields = std::move(unique);
        metadata.fields_complete = !metadata.pointer_fields.empty() &&
                                   metadata.opaque_reason.empty();
        if (metadata.pointer_fields.empty() && metadata.opaque_reason.empty())
            metadata.opaque_reason.push_back("no_proven_pointer_fields");
    }
    return result;
}

#if defined(HBFSIM_ENABLE_TIMING_FUTURES)
std::vector<PassParameter> future_parameter_metadata(const std::string& ptx,
                                                     const hbfsim::ptx::Function& function)
{
    const auto masked=hbfsim::ptx::masked_ptx_source(ptx);
    // The admitted scalar header is flat. Its only following attributes are
    // validated reqntid/maxntid dimensions, which contain no parentheses.
    // Anchor to that selected body's byte span, never to another name search.
    const auto end=masked.rfind(')',function.body_begin);
    const auto begin=end==std::string::npos ? std::string::npos : masked.rfind('(',end);
    if(begin==std::string::npos || begin>=end || end>=function.body_begin)
        throw std::invalid_argument("invalid validated future parameter span");
    auto parameters=decode_parameters(masked.substr(begin+1,end-begin-1),
        masked.substr(function.body_begin+1,function.body_end-function.body_begin-1));
    if(parameters.size()!=function.parameter_types.size())
        throw std::invalid_argument("future parameter manifest count mismatch");
    for(const auto& p:parameters) {
        const auto type=function.parameter_types.find(p.name);
        if(type==function.parameter_types.end() || scalar_width(type->second)!=p.width || p.kind=="opaque_aggregate")
            throw std::invalid_argument("future parameter manifest type mismatch");
    }
    return parameters;
}
#endif

constexpr std::string_view module_identity_symbol = "__hbfsim_module_identity";

std::array<unsigned char, SHA256_DIGEST_LENGTH> sha256(const std::string& text)
{
    std::array<unsigned char, SHA256_DIGEST_LENGTH> digest{};
    SHA256(reinterpret_cast<const unsigned char*>(text.data()), text.size(),
           digest.data());
    return digest;
}

std::string
hex_identity(const std::array<unsigned char, SHA256_DIGEST_LENGTH>& identity)
{
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const auto byte : identity) {
        output << std::setw(2) << static_cast<unsigned>(byte);
    }
    return output.str();
}

class TrustedModuleRegistry {
  public:
    struct Identity {
        std::string value;
        bool previously_emitted;
        std::string mode{"synchronous"};
        std::string original;
        std::vector<std::string> kernels;
        std::map<std::string,std::string> kernel_manifests;
    };

    Identity identity_for(const std::string& ptx,const std::string& mode)
    {
        const auto state = hex_identity(sha256(ptx));
        std::lock_guard lock(mutex_);
        if (ptx.find(module_identity_symbol) == std::string::npos) {
            if (mode=="synchronous") return {.value=state,.previously_emitted=false,.mode=mode};
            const auto contract=hbfsim::future_contract_json(state,hbfsim::ptx::embedded_device_helper_sha256());
            return {.value=hbfsim::future_contract_identity(contract),.previously_emitted=false,.mode=mode,.original=ptx};
        }
        const auto found = emitted_states_.find(state);
        if (found == emitted_states_.end()) {
            throw std::invalid_argument(
                "untrusted preexisting HBFSim module identity");
        }
        if (found->second.mode!=mode) throw std::invalid_argument("transform_mode_mismatch");
        auto identity=found->second;identity.previously_emitted=true;return identity;
    }

    void record(const std::string& emitted_ptx, const Identity& identity)
    {
        std::lock_guard lock(mutex_);
        emitted_states_.insert_or_assign(hex_identity(sha256(emitted_ptx)),
                                         identity);
    }

  private:
    std::mutex mutex_;
    std::unordered_map<std::string, Identity> emitted_states_;
};

TrustedModuleRegistry& trusted_modules()
{
    static TrustedModuleRegistry registry;
    return registry;
}

std::string inject_module_identity(std::string ptx, const std::string& identity,
                                  std::size_t safe_boundary=std::string::npos)
{
    if (ptx.find(module_identity_symbol) != std::string::npos) {
        return ptx;
    }
    const auto directives_end = ptx.find(".address_size");
    const auto newline = directives_end == std::string::npos
                             ? std::string::npos
                             : ptx.find('\n', directives_end);
    const auto insert = safe_boundary!=std::string::npos ? safe_boundary :
        (newline == std::string::npos ? 0 : newline + 1);
    std::ostringstream declaration;
    declaration << (safe_boundary==std::string::npos ? "" : "\n")
                << ".visible .const .align 8 .b8 " << module_identity_symbol
                << "[32] = {";
    for (std::size_t index = 0; index < SHA256_DIGEST_LENGTH; ++index) {
        if (index != 0) {
            declaration << ", ";
        }
        declaration << "0x" << identity.substr(index * 2, 2);
    }
    declaration << "};\n";
    ptx.insert(insert, declaration.str());
    return ptx;
}

std::string ptx_target(const std::string& ptx)
{
    static const std::regex target(R"(^\s*\.target\s+([^,\s]+))",
                                   std::regex::multiline);
    std::smatch match;
    return std::regex_search(ptx, match, target) ? match[1].str() : "";
}

void append_manifest(const nlohmann::json& manifest)
{
    const char* path = std::getenv("HBFSIM_PASS_MANIFEST_PATH");
    if (path == nullptr || path[0] == '\0') {
        return;
    }
    const std::string line = manifest.dump() + '\n';
    hbfsim::append_durable_line(path, line);
}

int copy_output(const std::string& text, int length, char* output)
{
    if (length <= 0 || output == nullptr ||
        text.size() + 1 > static_cast<std::size_t>(length)) {
        return 66;
    }
    std::memcpy(output, text.c_str(), text.size() + 1);
    return 0;
}

bool hbf_relevant_unsupported_opcode(const std::string& opcode)
{
    static const std::regex non_hbf_space(
        R"(^(?:ld|st)\.(?:param|local|shared|const)(?:\.|$))");
    return !std::regex_search(opcode, non_hbf_space);
}

}  // namespace

extern "C" void print_config(int length, char* output)
{
    if (length <= 0 || output == nullptr) {
        return;
    }
    const auto text = pass_config().dump();
    std::snprintf(output, static_cast<std::size_t>(length), "%s", text.c_str());
}

extern "C" int process_input(const char* input, int length, char* output)
{
    try {
        if (input == nullptr) {
            return 65;
        }
        const auto root = nlohmann::json::parse(input);
        const auto& request_json = root.at("input");
        const auto mode=request_json.value("transform_mode","synchronous");
        if (mode!="synchronous" && mode!=hbfsim::timing_future::kMode) {
            (void)copy_output(nlohmann::json{{"error","unsupported_transform_mode"}}.dump(),length,output);
            return 65;
        }
        hbfsim::ptx::TransformRequest request{
            .full_ptx = request_json.at("full_ptx").get<std::string>(),
            .to_patch_kernel = request_json.value("to_patch_kernel", ""),
            .global_ebpf_map_info_symbol =
                request_json.value("global_ebpf_map_info_symbol", "map_info"),
            .ebpf_communication_data_symbol = request_json.value(
                "ebpf_communication_data_symbol", "constData"),
            .transform_mode=mode,
        };
        if(mode==hbfsim::timing_future::kMode &&
            (request_json.contains("maximum_thread_futures") || request_json.contains("maximum_block_threads")))
            throw std::invalid_argument("fixed_future_resource_contract: thread=16 block=1024");
        auto trusted_identity =
            trusted_modules().identity_for(request.full_ptx,mode);
        if (mode==hbfsim::timing_future::kMode && !hbfsim::timing_future::kUnitComplete) {
            (void)copy_output(nlohmann::json{{"error","timing_future_unit_incomplete"},
                {"transform_identity",trusted_identity.value}}.dump(),length,output);
            return 65;
        }
        request.trusted_existing_helper =
            trusted_identity.previously_emitted;
        nlohmann::json future_fields=nlohmann::json::object();
        std::vector<PassParameter> parameters;
        std::size_t identity_boundary=std::string::npos;
#if defined(HBFSIM_ENABLE_TIMING_FUTURES)
        if (mode==hbfsim::timing_future::kMode) {
            request.full_ptx=trusted_identity.original;
            if (std::find(trusted_identity.kernels.begin(),trusted_identity.kernels.end(),request.to_patch_kernel)==trusted_identity.kernels.end())
                trusted_identity.kernels.push_back(request.to_patch_kernel);
            request.future_kernels=trusted_identity.kernels;
            const auto parsed=hbfsim::ptx::parse_module_spanned(request.full_ptx,request.to_patch_kernel);
            identity_boundary=parsed.address_size_directive.end;
            const auto emission=hbfsim::ptx::emit_timing_futures(request.full_ptx,request.to_patch_kernel);
            const auto& f=*std::find_if(parsed.functions.begin(),parsed.functions.end(),[&](const auto& f){return f.name==request.to_patch_kernel;});
            parameters=future_parameter_metadata(request.full_ptx,f);
            const auto dims=[](const auto& value) { return value.value_or(std::array<std::uint32_t,3>{}); };
            future_fields={
                {"transform_mode",mode},
                {"future_requirements",{{"abi_version",1},{"struct_bytes",48},{"token_bytes",64},
                    {"metadata_version",1},{"metadata_bytes",32},{"shared_control_abi",4},
                    {"maximum_thread_futures",16},{"maximum_block_threads",1024},
                    {"required_capabilities",1},{"reserved",0}}},
                {"future_contract",nlohmann::json::parse(hbfsim::future_contract_json(
                    hex_identity(sha256(request.full_ptx)),hbfsim::ptx::embedded_device_helper_sha256()))},
                {"future_kernel",{{"static_producers",emission.allocated_thread_futures},
                    {"maximum_block_threads",emission.assumed_maximum_block_threads},
                    {"required_threads",dims(f.required_thread_dimensions)},
                    {"maximum_threads",dims(f.maximum_thread_dimensions)}}}};
        }
#endif
        auto transformed = hbfsim::ptx::transform_ptx(request);
        const auto& identity = trusted_identity.value;
        transformed.output_ptx =
            inject_module_identity(std::move(transformed.output_ptx), identity,identity_boundary);
        if (mode==hbfsim::timing_future::kMode) {
            const hbfsim::timing_future::ModuleRequirements r{};
            std::ostringstream globals;
            const auto bytes=[&](const char* name,const unsigned char* data,std::size_t size) {
                globals<<"\n.visible .const .align 8 .b8 "<<name<<"["<<size<<"] = {";
                for(std::size_t i=0;i<size;++i)globals<<(i?", ":"")<<static_cast<unsigned>(data[i]);
                globals<<"};\n";
            };
            bytes("__hbfsim_timing_future_requirements_v1",reinterpret_cast<const unsigned char*>(&r),sizeof(r));
            const auto helper=sha256(std::string(hbfsim::ptx::embedded_device_helper()));
            bytes("__hbfsim_timing_future_helper_sha256_v1",helper.data(),helper.size());
            transformed.output_ptx.insert(identity_boundary,globals.str());
        }
        if(mode=="synchronous")parameters=parameter_metadata(request.full_ptx,request.to_patch_kernel);
        const auto aggregate_metadata =
            mode == "synchronous"
                ? aggregate_pointer_metadata(request.full_ptx,
                                             request.to_patch_kernel,
                                             parameters)
                : std::map<std::size_t, AggregatePointerMetadata>{};

        std::vector<std::string> relevant_unsupported;
        for (const auto& opcode : transformed.coverage.unsupported_opcodes) {
            if (hbf_relevant_unsupported_opcode(opcode)) {
                relevant_unsupported.push_back(opcode);
            }
        }

        nlohmann::json unsupported = nlohmann::json::array();
        if (!relevant_unsupported.empty()) {
            for (const auto& parameter : parameters) {
                if (parameter.kind != "scalar") {
                    unsupported.push_back(
                        {{"index", parameter.index},
                         {"operation", relevant_unsupported.front()}});
                }
            }
        }
        nlohmann::json parameters_json = nlohmann::json::array();
        for (const auto& parameter : parameters) {
            nlohmann::json parameter_json = {
                {"index", parameter.index},
                {"offset", parameter.offset},
                {"width", parameter.width},
                {"kind", parameter.kind},
            };
            if (parameter.kind == "opaque_aggregate") {
                const auto found = aggregate_metadata.find(parameter.index);
                parameter_json["pointer_fields"] =
                    found == aggregate_metadata.end()
                        ? nlohmann::json::array()
                        : found->second.pointer_fields;
                parameter_json["fields_complete"] =
                    found != aggregate_metadata.end() &&
                    found->second.fields_complete;
                parameter_json["opaque_reason"] =
                    found == aggregate_metadata.end()
                        ? nlohmann::json::array({"metadata_unavailable"})
                        : nlohmann::json(found->second.opaque_reason);
            }
            parameters_json.push_back(std::move(parameter_json));
        }
        nlohmann::json manifest={
            {"module_id", "ptx:sha256:" + identity},
            {"kernel", request.to_patch_kernel},
            {"ptx_target", ptx_target(request.full_ptx)},
            {"instrumented",
             transformed.modified && relevant_unsupported.empty()},
            {"cubin_only", false},
            {"parameters", std::move(parameters_json)},
            {"unsupported_parameters", std::move(unsupported)},
            {"rewritten_instructions",
             transformed.coverage.rewritten_instructions},
            {"unsupported_instructions", relevant_unsupported.size()},
            {"unsupported_opcodes", relevant_unsupported},
        };
        manifest.update(future_fields);
        if(mode==hbfsim::timing_future::kMode) {
            manifest["ptx_target"]="sm_120";
            manifest["rewritten_instructions"]=future_fields.at("future_kernel").at("static_producers");
            // Hash each exact complete manifest into module-owned read-only
            // storage. Original identity remains stable across selected kernels.
            trusted_identity.kernel_manifests[request.to_patch_kernel]=manifest.dump();
            std::ostringstream declarations;
            for(const auto& [kernel,text]:trusted_identity.kernel_manifests) {
                const auto hash=sha256(text);
                declarations<<"\n.visible .const .align 8 .b8 "<<hbfsim::future_kernel_contract_symbol(kernel)<<"[32] = {";
                for(std::size_t i=0;i<hash.size();++i)declarations<<(i?", ":"")<<static_cast<unsigned>(hash[i]);
                declarations<<"};\n";
            }
            transformed.output_ptx.insert(identity_boundary,declarations.str());
        }
        append_manifest(manifest);

        const auto response =
            nlohmann::json{
                {"output_ptx", transformed.output_ptx},
                {"modified", transformed.modified},
                {"transform_identity",identity},
                {"coverage",
                 {{"rewritten_instructions",
                   transformed.coverage.rewritten_instructions},
                  {"unsupported_instructions",
                   transformed.coverage.unsupported_instructions},
                  {"excluded_functions",
                   transformed.coverage.excluded_functions},
                  {"unsupported_opcodes",
                   transformed.coverage.unsupported_opcodes}}},
            }
                .dump();
        const auto status = copy_output(response, length, output);
        if (status == 0) {
            trusted_modules().record(transformed.output_ptx, trusted_identity);
        }
        return status;
    } catch (const nlohmann::json::exception& error) {
        std::fprintf(stderr, "ptxpass_hbf configuration error: %s\n",
                     error.what());
        return 64;
    } catch (const std::exception& error) {
        (void)copy_output(nlohmann::json{{"error",error.what()}}.dump(),length,output);
        std::fprintf(stderr, "ptxpass_hbf error: %s\n", error.what());
        return 70;
    }
}
