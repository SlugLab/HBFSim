import dataclasses
import json
import pathlib
import sys

import pytest


ADAPTER = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADAPTER))

import hbfsim_loader as loader_module  # noqa: E402


@pytest.fixture(autouse=True)
def clean_instrumentation_policy(monkeypatch):
    monkeypatch.delenv("HBFSIM_INSTRUMENTATION_POLICY", raising=False)
    monkeypatch.setattr(
        loader_module, "_require_strict_runtime_capabilities", lambda: None)


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
        self.shape = (storage.nbytes() // 2,)
        self.dtype = "torch.bfloat16"
        self.requires_grad = False

    def untyped_storage(self):
        return self._storage

    def stride(self):
        return (1,)

    def storage_offset(self):
        return 0

    def element_size(self):
        return 2


class FakeModel:
    def __init__(self, parameters):
        self._parameters = parameters

    def named_parameters(self, recurse=True, remove_duplicate=True):
        assert recurse
        del remove_duplicate
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


def test_timing_model_is_explicit_and_validated(tmp_path):
    assert config(tmp_path).timing_model == "hybrid"
    assert config(tmp_path, timing_model="fast").timing_model == "fast"
    assert config(tmp_path, timing_model="reference").timing_model == "reference"
    with pytest.raises(ValueError, match="timing_model"):
        config(tmp_path, timing_model="unknown")


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


def test_registers_explicit_parameter_subset_and_range_prefix(tmp_path):
    model = FakeModel([
        ("model.layers.0.mlp.experts.w13_weight",
         FakeParameter(FakeStorage(0x1000, 0x20000))),
        ("model.layers.1.mlp.experts.w13_weight",
         FakeParameter(FakeStorage(0x40000, 0x20000))),
        ("lm_head.weight", FakeParameter(FakeStorage(0x80000, 0x10000))),
    ])
    session = FakeSession()

    manifest = loader_module.register_model_storages(
        model,
        config(
            tmp_path,
            parameter_regex=r"^model\.layers\.0\.mlp\.experts\.w13_weight$",
            max_bytes_per_storage=16384,
        ),
        session_factory=lambda _: session,
    )

    assert session.registered == [(0x1000, 16384)]
    assert manifest["discovered_storage_count"] == 3
    assert manifest["unique_storage_count"] == 1
    assert manifest["registered_bytes"] == 16384
    assert manifest["selection"] == {
        "mode": "include",
        "include_patterns": [
            r"^model\.layers\.0\.mlp\.experts\.w13_weight$"
        ],
        "exclude_patterns": [],
        "accept_storage_closure": False,
        "storage_closure_aliases": [],
        "instrumentation_policy": "partial",
        "parameter_regex": r"^model\.layers\.0\.mlp\.experts\.w13_weight$",
        "max_bytes_per_storage": 16384,
    }
    assert manifest["storages"][0]["address"] == 0x1000
    assert manifest["storages"][0]["bytes"] == 16384
    assert manifest["storages"][0]["tensors"][0]["dtype"] == "torch.bfloat16"


def test_rejects_empty_explicit_parameter_selection(tmp_path):
    model = FakeModel([
        ("lm_head.weight", FakeParameter(FakeStorage(0x1000, 0x1000))),
    ])
    session = FakeSession()

    with pytest.raises(loader_module.HbfSimError,
                       match="weight selection matched no CUDA storages"):
        loader_module.register_model_storages(
            model, config(tmp_path, parameter_regex=r"^model\.layers\.0\."),
            session_factory=lambda _: session,
        )

    assert not session.closed


def test_rejects_invalid_parameter_selection_configuration(tmp_path):
    with pytest.raises(ValueError, match="invalid weight selection pattern"):
        config(tmp_path, parameter_regex="[")
    with pytest.raises(ValueError, match="max_bytes_per_storage"):
        config(tmp_path, max_bytes_per_storage=-1)


def test_off_does_not_discover_or_create_session(tmp_path):
    class NativeOnlyModel:
        def named_parameters(self, **_kwargs):
            raise AssertionError("off must not discover parameters")

    def forbidden_factory(_config):
        raise AssertionError("off must not create a timing session")

    manifest = loader_module.register_model_storages(
        NativeOnlyModel(), config(tmp_path, weight_selection="off"),
        session_factory=forbidden_factory)
    assert manifest["status"] == "DISABLED"
    assert manifest["mode"] == "native"
    assert "HBFSIM_INSTRUMENTATION_POLICY" not in loader_module.os.environ


def test_direct_loader_activates_and_conflict_checks_policy(tmp_path):
    model = FakeModel([
        ("weight", FakeParameter(FakeStorage(0x1000, 0x1000))),
    ])
    loader_module.register_model_storages(
        model, config(tmp_path), session_factory=lambda _: FakeSession())
    assert loader_module.os.environ["HBFSIM_INSTRUMENTATION_POLICY"] == "strict"
    conflicting = config(
        tmp_path, instrumentation_policy="partial",
        parameter_regex=r"^weight$")
    with pytest.raises(loader_module.HbfSimError,
                       match="conflicting process-global"):
        loader_module.register_model_storages(
            model, conflicting, session_factory=lambda _: FakeSession())


def test_strict_runtime_capability_is_required_before_session(monkeypatch,
                                                              tmp_path):
    monkeypatch.undo()
    monkeypatch.delenv("HBFSIM_INSTRUMENTATION_POLICY", raising=False)
    monkeypatch.setattr(loader_module.ctypes, "CDLL", lambda _name: object())
    created = False

    def factory(_config):
        nonlocal created
        created = True
        return FakeSession()

    model = FakeModel([
        ("weight", FakeParameter(FakeStorage(0x1000, 0x1000))),
    ])
    with pytest.raises(loader_module.HbfSimError,
                       match="runtime capability missing"):
        loader_module.register_model_storages(
            model, config(tmp_path), session_factory=factory)
    assert created is False


def test_all_selection_can_exclude_a_whole_storage(tmp_path):
    model = FakeModel([
        ("keep.weight", FakeParameter(FakeStorage(0x1000, 0x1000))),
        ("drop.weight", FakeParameter(FakeStorage(0x4000, 0x1000))),
    ])
    session = FakeSession()
    manifest = loader_module.register_model_storages(
        model, config(tmp_path, exclude_patterns=[r"^drop\."]),
        session_factory=lambda _: session)
    assert session.registered == [(0x1000, 0x1000)]
    assert manifest["storages"][0]["aliases"] == ["keep.weight"]


def test_explicit_exclude_cannot_be_overridden_by_storage_closure(tmp_path):
    shared = FakeStorage(0x1000, 0x1000)
    model = FakeModel([
        ("keep.weight", FakeParameter(shared)),
        ("drop.weight", FakeParameter(shared)),
    ])
    with pytest.raises(loader_module.HbfSimError,
                       match="explicitly excluded aliases"):
        loader_module.register_model_storages(
            model, config(tmp_path, exclude_patterns=[r"^drop\."],
                          accept_storage_closure=True),
            session_factory=lambda _: FakeSession())


def test_direct_legacy_config_normalizes_without_expanding_scope(tmp_path):
    direct = loader_module.TimingConfig(
        profile_path="/profiles/nominal.json", report_dir=str(tmp_path),
        parameter_regex=r"^only\.", max_bytes_per_storage=16)
    assert direct.weight_selection == "include"
    assert direct.include_patterns == (r"^only\.",)
    assert direct.instrumentation_policy == "partial"


def test_explicit_strict_legacy_config_is_not_silently_downgraded(tmp_path):
    direct = loader_module.TimingConfig(
        profile_path="/profiles/nominal.json", report_dir=str(tmp_path),
        parameter_regex=r"^only\.", instrumentation_policy="strict")
    assert direct.instrumentation_policy == "strict"
    model = FakeModel([
        ("only.weight", FakeParameter(FakeStorage(0x1000, 0x1000))),
    ])
    with pytest.raises(loader_module.HbfSimError,
                       match="forbids truncated storage"):
        loader_module.register_model_storages(
            model, dataclasses.replace(direct, max_bytes_per_storage=16),
            session_factory=lambda _: FakeSession())


@pytest.mark.parametrize("key,value", [
    ("include_patterns", "not-a-list"),
    ("exclude_patterns", [1]),
    ("accept_storage_closure", "false"),
])
def test_new_selection_fields_have_strict_types(tmp_path, key, value):
    with pytest.raises(ValueError):
        config(tmp_path, **{key: value})


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
