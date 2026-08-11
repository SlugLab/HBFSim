import json
import pathlib
import sys

import pytest


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import hbfsim_loader as loader_module  # noqa: E402


class FakeDevice:
    def __init__(self, index=0, kind="cuda"):
        self.type = kind
        self.index = index


class FakeStorage:
    def __init__(self, address, size):
        self._address = address
        self._size = size

    def data_ptr(self):
        return self._address

    def nbytes(self):
        return self._size


class FakeParameter:
    def __init__(self, storage, device=0):
        self._storage = storage
        self.device = FakeDevice(device)

    def untyped_storage(self):
        return self._storage


class FakeModel:
    def __init__(self, parameters):
        self._parameters = parameters

    def named_parameters(self, recurse=True):
        assert recurse
        return iter(self._parameters)


class FakeSession:
    def __init__(self, fail_address=None):
        self.fail_address = fail_address
        self.registered = []
        self.closed = False

    def register_storage(self, address, size):
        if address == self.fail_address:
            raise loader_module.HbfSimError("injected registration failure")
        self.registered.append((address, size))

    def close(self):
        self.closed = True


def config(tmp_path, **overrides):
    values = {
        "profile_path": "/profiles/nominal.json",
        "report_dir": str(tmp_path),
        "ring_capacity": 8,
        "request_timeout_ns": 1_000_000_000,
    }
    values.update(overrides)
    return loader_module.TimingConfig.from_mapping(values)


def test_discovers_full_storages_and_deduplicates_aliases(tmp_path):
    shared = FakeStorage(0x1000, 0x1000)
    model = FakeModel([
        ("embed.weight", FakeParameter(shared)),
        ("lm_head.weight", FakeParameter(shared)),
        ("layer.weight", FakeParameter(FakeStorage(0x4000, 0x2000))),
    ])
    session = FakeSession()

    manifest = loader_module.register_model_storages(
        model, config(tmp_path), session_factory=lambda _: session)

    assert session.registered == [(0x1000, 0x1000), (0x4000, 0x2000)]
    assert manifest["parameter_count"] == 3
    assert manifest["unique_storage_count"] == 2
    assert manifest["registered_bytes"] == 0x3000
    assert manifest["storages"][0]["aliases"] == [
        "embed.weight", "lm_head.weight"
    ]
    assert model._hbfsim_timing_session is session
    persisted = json.loads((tmp_path / "registration.json").read_text())
    assert persisted == manifest


@pytest.mark.parametrize(
    "parameters, message",
    [
        ([
            ("a", FakeParameter(FakeStorage(0x1000, 0x1000), 0)),
            ("b", FakeParameter(FakeStorage(0x1800, 0x1000), 0)),
        ], "overlapping CUDA storages"),
        ([
            ("a", FakeParameter(FakeStorage(0x1000, 0x1000), 0)),
            ("b", FakeParameter(FakeStorage(0x4000, 0x1000), 1)),
        ], "mixed CUDA devices"),
    ],
)
def test_rejects_unsafe_storage_sets(tmp_path, parameters, message):
    with pytest.raises(loader_module.HbfSimError, match=message):
        loader_module.register_model_storages(
            FakeModel(parameters), config(tmp_path),
            session_factory=lambda _: FakeSession())


def test_partial_registration_failure_closes_session(tmp_path):
    model = FakeModel([
        ("a", FakeParameter(FakeStorage(0x1000, 0x1000))),
        ("b", FakeParameter(FakeStorage(0x4000, 0x1000))),
    ])
    session = FakeSession(fail_address=0x4000)

    with pytest.raises(loader_module.HbfSimError,
                       match="injected registration failure"):
        loader_module.register_model_storages(
            model, config(tmp_path), session_factory=lambda _: session)

    assert session.closed
    assert not hasattr(model, "_hbfsim_timing_session")
    assert not (tmp_path / "registration.json").exists()


def test_explicit_model_close_is_idempotent(tmp_path):
    model = FakeModel([
        ("weight", FakeParameter(FakeStorage(0x1000, 0x1000))),
    ])
    session = FakeSession()
    loader_module.register_model_storages(
        model, config(tmp_path), session_factory=lambda _: session)

    loader_module.close_model_session(model)
    loader_module.close_model_session(model)

    assert session.closed
    assert not hasattr(model, "_hbfsim_timing_session")


def test_loader_delegates_then_registers_finalized_model(monkeypatch, tmp_path):
    from vllm.config.load import LoadConfig

    load_config = LoadConfig(
        load_format="hbfsim",
        model_loader_extra_config={
            "profile_path": "/profiles/nominal.json",
            "report_dir": str(tmp_path),
            "underlying_load_format": "safetensors",
        },
    )
    loader = loader_module.HbfSimModelLoader(load_config)
    model = FakeModel([])
    events = []

    class Delegate:
        def load_model(self, **kwargs):
            events.append("delegate")
            return model

        def download_model(self, model_config):
            return None

        def load_weights(self, model, model_config):
            return None

    loader._delegate = Delegate()

    def register(finalized, timing_config):
        assert finalized is model
        events.append("register")
        return {"unique_storage_count": 1}

    monkeypatch.setattr(loader_module, "register_model_storages", register)
    assert loader.load_model(vllm_config=object(), model_config=object()) is model
    assert events == ["delegate", "register"]
    assert loader._delegate_config.load_format == "safetensors"


def test_plugin_registration_is_idempotent(tmp_path):
    from vllm.config.load import LoadConfig
    from vllm.model_executor.model_loader import get_model_loader

    loader_module.register()
    loader_module.register()
    instance = get_model_loader(LoadConfig(
        load_format="hbfsim",
        model_loader_extra_config={
            "profile_path": "/profiles/nominal.json",
            "report_dir": str(tmp_path),
        },
    ))
    assert isinstance(instance, loader_module.HbfSimModelLoader)
