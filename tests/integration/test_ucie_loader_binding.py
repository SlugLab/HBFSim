"""CPU-only check that the production loader binds its actual registration list."""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile

repo = pathlib.Path(os.environ.get(
    "HBFSIM_TEST_REPO", pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(repo / "adapters" / "vllm"))
import hbfsim_loader as loader  # noqa: E402


class Storage:
    def __init__(self, size: int):
        self._size = size

    def data_ptr(self) -> int:
        return 0x100000000

    def nbytes(self) -> int:
        return self._size


class Parameter:
    device = type("Device", (), {"type": "cuda", "index": 0})()
    dtype = "torch.bfloat16"
    requires_grad = False

    def __init__(self, storage: Storage):
        self._storage = storage
        self.shape = (storage.nbytes() // 2,)

    def untyped_storage(self) -> Storage:
        return self._storage

    def stride(self) -> tuple[int]:
        return (1,)

    def storage_offset(self) -> int:
        return 0

    def element_size(self) -> int:
        return 2


class Model:
    def __init__(self, alias: str, size: int):
        self.alias = alias
        self.parameter = Parameter(Storage(size))

    def named_parameters(self, recurse=True, remove_duplicate=False):
        assert recurse and not remove_duplicate
        return iter([(self.alias, self.parameter)])


class Session:
    def __init__(self):
        self.registered = []

    def register_storage(self, address, size):
        self.registered.append((address, size))

    def close(self):
        pass


def main() -> None:
    canonical_path = repo / "adapters" / "vllm" / "canonical-147.json"
    canonical = json.loads(canonical_path.read_text())
    row = min((x for x in canonical["placements"] if len(x["aliases"]) == 1),
              key=lambda x: x["storage_bytes"])
    alias, size = row["aliases"][0], row["storage_bytes"]
    loader._require_strict_runtime_capabilities = lambda: None
    for wait_mode, policy in (("nominal", "partial"),
                              ("zero_injected", "partial"),
                              ("nominal", "strict")):
        with tempfile.TemporaryDirectory(prefix="ucie-loader-") as directory:
            os.environ.pop("HBFSIM_INSTRUMENTATION_POLICY", None)
            os.environ["HBFSIM_UCIE_TOP_PROFILE"] = str(
                repo / "configs" / "profiles" / "ucie" /
                "hbf-stage3-balanced-independent-top.json")
            os.environ["HBFSIM_UCIE_WORKER"] = "/unused/fake-worker"
            os.environ["HBFSIM_UCIE_WAIT_MODE"] = wait_mode
            os.environ["HBFSIM_UCIE_CANONICAL_LAYOUT"] = str(canonical_path)
            os.environ.pop("HBFSIM_UCIE_PLACEMENT_MANIFEST", None)
            config = loader.TimingConfig(
                profile_path=str(repo / "configs" / "profiles" / "nominal.json"),
                report_dir=directory,
                weight_selection="include",
                include_patterns=(re.escape(alias),),
                timing_model="hybrid",
                instrumentation_policy=policy)
            session = Session()
            result = loader.register_model_storages(
                Model(alias, size), config, lambda _: session)
            manifest_path = pathlib.Path(
                os.environ["HBFSIM_UCIE_PLACEMENT_MANIFEST"])
            placement = json.loads(manifest_path.read_text())["placements"]
            bindings = json.loads(
                (pathlib.Path(directory) /
                 "ucie-storage-bindings.json").read_text())["bindings"]
            assert session.registered == [(0x100000000, size)]
            assert len(placement) == len(bindings) == 1
            assert placement[0]["range_id"] == 1
            assert placement[0]["file_offset"] == 0
            assert placement[0]["registered_address"] == 0x100000000
            assert placement[0]["canonical_physical_address"] == (
                row["canonical_physical_address"])
            assert bindings[0]["aliases"] == [alias]
            assert result["ucie_placement"]["manifest_sha256"]
            print(wait_mode, policy, "PASS", alias, size)


if __name__ == "__main__":
    main()
