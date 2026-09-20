#include <hbfsim/eq3_thermal/cpu_service.hpp>
#include <json.hpp>
#include <fstream>
#include <iostream>
#include <stdexcept>
int main(int argc,char** argv) {
  try {
    if(argc!=2)throw std::invalid_argument("usage: cpu_pilot frozen_input.json");
    std::ifstream in(argv[1]);if(!in)throw std::runtime_error("input unavailable");
    nlohmann::json j;in>>j;
    hbfsim::eq3_thermal::CpuService service(j.at("config").dump());
    for(const auto& request:j.at("requests"))service.submit(request.dump());
    service.advance_to(j.at("end_ns").get<std::uint64_t>());
    std::cout<<service.report()<<'\n';return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 2;}
}
