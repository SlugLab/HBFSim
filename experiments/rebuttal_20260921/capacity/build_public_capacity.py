#!/usr/bin/env python3
"""Build only the independent R3 page bench; never executes CUDA."""
import argparse,pathlib,subprocess
def main():
 p=argparse.ArgumentParser();p.add_argument("--source-root",type=pathlib.Path,required=True);p.add_argument("--dependency-build",type=pathlib.Path,required=True);p.add_argument("--output-dir",type=pathlib.Path,required=True);p.add_argument("--cuda-root",type=pathlib.Path,required=True);p.add_argument("--cuda-include-overlay",type=pathlib.Path,required=True);p.add_argument("--cxx",type=pathlib.Path,required=True);p.add_argument("--clang",type=pathlib.Path,required=True);a=p.parse_args()
 root=a.source_root.resolve();dep=a.dependency_build.resolve();out=a.output_dir.resolve();cuda=a.cuda_root.resolve();overlay=a.cuda_include_overlay.resolve();cxx=a.cxx.resolve();clang=a.clang.resolve();inc=cuda/"targets/x86_64-linux/include";lib=cuda/"targets/x86_64-linux/lib";source=root/"experiments/rebuttal_20260921/capacity/r3_public_capacity_bench.cpp";kernel=root/"experiments/rebuttal_20260921/capacity/r3_public_capacity_kernel.cu";probe_source=root/"experiments/rebuttal_20260921/capacity/r3_public_capacity_probe.bpf.c"
 required=[cxx,clang,probe_source,overlay/"cuda_runtime.h",inc/"cuda.h",inc/"cuda_runtime_api.h",lib/"libcudart.so",lib/"stubs/libcuda.so"]
 missing=[str(x) for x in required if not x.exists()]
 if missing:raise SystemExit("missing pinned inputs: "+", ".join(missing))
 out.mkdir(parents=True,exist_ok=True);obj=out/"r3_public_capacity_bench.o";binary=out/"r3_public_capacity_bench";ptx=out/"r3_public_capacity_kernel.ptx";probe=out/"r3_public_capacity_probe.bpf.o"
 subprocess.run([str(cxx),"-O3","-DNDEBUG","-std=gnu++20","-fPIC",f"-I{overlay}",f"-I{inc}",f"-I{inc/'cccl'}",f"-I{root/'include'}","-c",str(source),"-o",str(obj)],check=True,cwd=root)
 subprocess.run([str(cuda/"bin/nvcc"),"--ptx","--std=c++20","--gpu-architecture=compute_120",f"--compiler-bindir={cxx}",f"-I{overlay}",str(kernel),"-o",str(ptx)],check=True,cwd=root)
 subprocess.run([str(clang),"-target","bpf","-O2","-g","-c",str(probe_source),"-o",str(probe)],check=True,cwd=root)
 libs=["libhbfsim_core.a","libhbfsim_future_emitter.a","libhbfsim_eval_ptx.a"]
 missing=[str(dep/x) for x in libs if not (dep/x).exists()]
 if missing:raise SystemExit("missing dependency libraries: "+", ".join(missing))
 cmd=[str(cxx),"-O3","-o",str(binary),str(obj),*[str(dep/x) for x in libs],"/usr/lib/x86_64-linux-gnu/libcrypto.so",str(lib/"libcudart.so"),str(lib/"stubs/libcuda.so"),"-ldl","/usr/lib/x86_64-linux-gnu/librt.a",str(dep/"libmqsim_hbf.a"),"-pthread",f"-Wl,-rpath,{lib}"]
 subprocess.run(cmd,check=True,cwd=root);print(binary);print(ptx);print(probe);return 0
if __name__=="__main__":raise SystemExit(main())
