# Reproducible build environment

Run from the repository root. Choose fresh build/output directories outside the checkout. The examples use `$PWD/../eval-artifacts`; changing that location is safe. No model weights or generated results belong in Git.

```bash
git submodule update --init third_party/bpftime third_party/mqsim
cmake -S . -B ../eval-artifacts/build-cpu -G Ninja   -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON   -DCMAKE_BUILD_TYPE=Debug -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON
cmake --build ../eval-artifacts/build-cpu -j8
ctest --test-dir ../eval-artifacts/build-cpu --output-on-failure
cmake -S . -B ../eval-artifacts/build-tools -G Ninja   -DHBFSIM_ENABLE_CUDA=OFF -DHBFSIM_ENABLE_MQSIM=ON   -DCMAKE_BUILD_TYPE=Debug -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON   -DHBFSIM_ENABLE_EVAL_TOOLS=ON
cmake --build ../eval-artifacts/build-tools -j8
ctest --test-dir ../eval-artifacts/build-tools --output-on-failure
```

The PkgConfig override is shared by baseline and candidate CPU builds: upstream hybrid creates the CUDA llama-probe module when libbpf/clang are found even with CUDA disabled, producing `CMake can not determine linker language for target: hbfsim_llama_probe_module`. This disables optional BPF probe discovery for CPU tests; it does not disable the MQSim engine, PTX coverage tests or fake CUDA gate tests. This pre-existing build issue is not silently changed in the runtime integration.

Pinned dependencies: bpftime ec26daecc8e787fb80fd95dd596a576404a5e36e, MQSim 51f0f2d3fed92d88ef4a0fa61a38024b07bf9d16. The private paper/Overleaf submodule is not needed for code testing and is not initialized or changed.

CUDA static checks require a toolkit and compatible host compiler; live checks additionally require a working, compatible and idle GPU. Use the exact tested compiler/toolkit in the integration manifest; no CPU build substitutes for GPU parity. Do not install or change the machine driver as part of reproduction.
