#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#if __has_include("future_transform.hpp")
#include "future_transform.hpp"
#endif
int main(int argc,char**argv) {
 if(argc<2)return 2;
 try {
  std::ifstream input(argv[1]);if(!input)throw std::runtime_error("input unavailable");
  std::string text{std::istreambuf_iterator<char>(input),{}};
#if __has_include("future_transform.hpp")
  hbfsim::ptx::FutureEmissionOptions options;
  if(argc>2)options.maximum_thread_futures=std::stoul(argv[2]);
  if(argc>3)options.maximum_block_threads=std::stoul(argv[3]);
  auto output=hbfsim::ptx::emit_timing_futures(text,"kernel",options);
  std::cout<<output.ptx;
#else
  std::cout<<text;
#endif
 }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
