#include <hbfsim/coverage.hpp>
#include <cstdio>
#include <cstdlib>
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)
int main()
{
    const std::string text=R"({"module_id":"future","kernel":"kernel","instrumented":true,
      "transform_mode":"timing_load_future_v1","future_requirements":{"abi_version":1,"struct_bytes":48,
      "token_bytes":64,"metadata_version":1,"metadata_bytes":32,"shared_control_abi":4,
      "maximum_thread_futures":16,"maximum_block_threads":1024,"required_capabilities":1,"reserved":0},
      "parameters":[],"unsupported_parameters":[]})";
    hbfsim::CoverageGate gate;gate.add_module(hbfsim::module_manifest_from_json(text));
    auto decision=gate.check_launch({.module_id="future",.kernel="kernel"});
    CHECK(!decision.allowed);CHECK(decision.reason=="timing_future_unit_incomplete");
    gate.add_range(0x1000,0x2000,hbfsim::RangePolicy::TimingBacked);
    CHECK(!gate.check_launch({.module_id="future",.kernel="kernel"}).allowed);
    bool conflict=false;
    try { gate.add_module({.module_id="future",.kernel="kernel",.instrumented=true}); }
    catch(const std::invalid_argument&) { conflict=true; }
    CHECK(conflict);
    CHECK(gate.check_launch({.module_id="native",.kernel="kernel"}).allowed);
    for(const std::string bad : {"\"token_bytes\":64","\"metadata_version\":1","\"shared_control_abi\":4"}) {
        auto malformed=text;auto start=malformed.find(bad);malformed.replace(start,bad.size(),bad.substr(0,bad.find(':')+1)+"99");
        bool rejected=false;try { (void)hbfsim::module_manifest_from_json(malformed); }
        catch(const std::exception&) { rejected=true; }CHECK(rejected);
    }
    for(const std::string value : {"4294967360","64.5","-4294967232"}) {
        auto malformed=text;auto start=malformed.find("\"token_bytes\":64");
        malformed.replace(start,16,"\"token_bytes\":"+value);
        bool rejected=false;try { (void)hbfsim::module_manifest_from_json(malformed); }
        catch(const std::exception&) { rejected=true; }CHECK(rejected);
    }
    std::puts("PASS: future requests cannot bypass closure through no-HBF/TIMING fallback");
}
