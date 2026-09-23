# Clean HBFSim CPU build profiles

Start from the integrated source tree at commit `17d0fc58c0444789650ab40b4641802b5b6c2db8`
with the checked-in successful-source changes. The CUDA 13.0 compatibility
view changes only two `rsqrt` exception specifications for the current glibc;
the script checks the original toolkit hashes and does not modify it.

```sh
TREE=/path/to/integrated-main
CUDA13=/path/to/pinned/cuda-13.0
LLVM20=/path/to/pinned/llvm-20
VIEW=/path/to/new/cuda-13.0-view
python3 "$TREE/scripts/prepare_cuda13_glibc_overlay.py" \
  --cuda-root "$CUDA13" --output "$VIEW"
python3 "$TREE/scripts/reproduce_hbfsim_cpu_build.py" \
  --source "$TREE" --output /path/to/new/hbf-build \
  --cuda-root "$VIEW" --llvm-root "$LLVM20" --jobs 2
python3 "$TREE/scripts/reproduce_hbfsim_cpu_build.py" \
  --aggregate-only --source "$TREE" --output /path/to/new/aggregate-build \
  --cuda-root "$VIEW" --llvm-root "$LLVM20" --jobs 2
```

`--output` for the CUDA view is **the toolkit root itself**: the compiler is
`$VIEW/bin/nvcc`. The ordinary HBF build retains separate futures-ON runtime
and futures-OFF gate-core profiles. The optional aggregate pass build retains
futures ON but links the explicit system CUDA 12 `libcudart.so` selected by
the successful Embedding/Fill generator. The pass DSO's link command contains
that library; with `--as-needed`, it has no cudart `DT_NEEDED` because it does
not reference cudart symbols. These profiles are not interchangeable.

The actual clean aggregate build is preserved in
`reproduction/aggregate-pass/CLEAN_BUILD_RECEIPT.json`; its DSO SHA-256 is
`a6aa66064cc1251ae698845acb79e74b167cba394b576c26d30ca9f986867b3a`.
The separate norm pass DSO remains `1ffe053ee123b8c2689a7fc18cdb616cf6be3a35f1e8fce179475ec15c17cfcf`.
CPU construction and PTX generation alone do not establish GPU runtime
coverage; the execution package has its own results and identity checks.
