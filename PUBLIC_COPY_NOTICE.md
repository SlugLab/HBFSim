# Public copies and local paths

Published historical documents, logs, receipts and examples are path-normalized
copies. Host aliases, account names, device identifiers and private directories
have been replaced with neutral labels or configurable variables. Scientific
measurements, compiler versions, option order and recorded artifact hashes are
retained. A normalized log is a record for review, not a script to rerun.

Embedded hashes in historical receipts and manifests describe the original
execution files, which are retained privately. They do **not** authenticate the
edited public text. `PUBLIC_COPY_MANIFEST.json` separately records each public
path, byte size and SHA-256, plus the SHA-256 of its unchanged private original.
This is a publication check, not a new experiment or scientific PASS.

From the repository root, verify the public copies with:

```sh
sha256sum -c SHA256SUMS
```

Repository files use relative paths where their location is known. Run such
commands from the repository root unless the guide specifies another directory.
For external inputs, replace `${VARIABLE}` with your own verified absolute path;
JSON argv arrays require explicit variable expansion before execution.

| Variable | Value to supply |
|---|---|
| `HBFSIM_SOURCE` | This project checkout |
| `MODEL_ROOT`, `HF_HOME` | Model checkpoint directory and Hugging Face cache |
| `FRAMEWORK_CUDA_ROOT` | CUDA toolkit compatible with the framework CUDA domain |
| `AGENT_CUDA_ROOT`, `AGENT_DEP_CUDA_ROOT` | Separately selected HBF/agent toolkits |
| Other `*_SOURCE_ROOT`, `*_BUILD_ROOT`, `*_INCLUDE_ROOT`, `LLVM_ROOT` | Actual source, fresh build and dependency directories named by the command appendix |
| `EXPERIMENT_ROOT`, `RESEARCH_ROOT`, `DATA_ROOT`, `ARCHIVE_ROOT`, `BACKUP_ROOT` | Your local research inputs, output archive or backup directories |
| `OUTPUT_ROOT`, `LOCAL_EVIDENCE_ROOT`, `EVIDENCE_ROOT`, `LOCAL_WORKSPACE`, `HOME` | Fresh outputs, retained evidence, workspace or home directory |
| `NVME_MEM_SOURCE` | Separate NVMe memory project checkout, where required |
| `BUILD_HOST`, `HOSTNAME`, `HOST_ADDRESS`, `USER` | Your build host, connection address and account |
| `GPU_UUID`, `PCI_ADDRESS`, `GPU_PCI_ADDRESS`, `STORAGE_DEVICE`, `VMEM_DEVICE` | Locally verified device identities; never copy a historical device selection |
| `BOOT_OR_RUN_ID`, `BOOT_ID`, `BOOT_UUID`, `DEVICE_UUID`, `PCI_BUS_ID` | Fresh runtime identity required by the consumer |

JSON examples are templates. Fill null identity fields with freshly observed
values and regenerate source, library, model, PTX and process bindings from your
own inputs. Preserve the CUDA 12 framework / CUDA 13 agent separation where the
guide requires it. Path substitution and a matching filename do not establish
correct instrumentation, dynamic coverage or GPU behavior.
