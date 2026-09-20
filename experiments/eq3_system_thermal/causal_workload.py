"""Online, architecture-derived causal weight-consumption model.

This is deliberately not a NAND simulator.  Storage jobs are offered to an
external service and dependencies unlock only when that service reports their
actual completion timestamp.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable


CATALOG = Path(__file__).parents[1] / "eq3_maintenance" / "sources" / "qwen2_5_weight_models.json"
TRACE_ORIGIN = "SYNTHETIC_ARCHITECTURE_DEPENDENCY_FROM_OFFICIAL_METADATA"


def load_architecture(model_id: str) -> dict[str, Any]:
    models = json.loads(CATALOG.read_text(encoding="utf-8"))["models"]
    if model_id not in models:
        raise ValueError(f"unsupported official model metadata {model_id!r}")
    item = deepcopy(models[model_id])
    item["model_id"] = model_id
    return item


def _tensor_groups(meta: dict[str, Any]) -> list[dict[str, Any]]:
    a, scalar = meta["architecture"], meta["bytes_per_tensor_element"]
    h, inter = a["hidden_size"], a["intermediate_size"]
    heads, kv, layers, vocab = (a["num_attention_heads"], a["num_key_value_heads"],
                                a["num_hidden_layers"], a["vocab_size"])
    if h % heads:
        raise ValueError("hidden size is not divisible by attention heads")
    hd = h // heads
    groups = [{"tensor_id": "model.embed_tokens", "kind": "embedding",
               "bytes": vocab * h * scalar, "layer": None}]
    for layer in range(layers):
        groups.extend([
            {"tensor_id": f"model.layers.{layer}.attention_bundle", "kind": "attention",
             "bytes": ((2 * h * h + h) + 2 * (h * kv * hd + kv * hd)
                       + h) * scalar, "layer": layer},
            {"tensor_id": f"model.layers.{layer}.mlp_bundle", "kind": "mlp",
             "bytes": (3 * h * inter + h) * scalar, "layer": layer},
        ])
    groups.extend([
        {"tensor_id": "model.norm", "kind": "final_norm", "bytes": h * scalar,
         "layer": None},
        {"tensor_id": "lm_head", "kind": "output_embedding",
         "bytes": vocab * h * scalar, "layer": None},
    ])
    if sum(x["bytes"] for x in groups) != meta["tensor_payload_bytes"]:
        raise ValueError("derived tensor groups do not match official payload bytes")
    address = 0
    for group in groups:
        group['logical_address_bytes'] = address
        address = ((address + group['bytes'] + 1048575) // 1048576) * 1048576
    return groups


def build_architecture_trace(config: dict[str, Any]) -> dict[str, Any]:
    """Build a compact dependency DAG; it is not a captured framework trace."""
    model_id = config["model_id"]
    meta = load_architecture(model_id)
    required = ("batch_intervals", "batch_size", "batch_interval_ns", "prefetch_layers",
                "attention_compute_ns_per_token", "mlp_compute_ns_per_token",
                "output_compute_ns_per_token", "embedding_access")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"explicit causal scenario fields required: {missing}")
    intervals = int(config["batch_intervals"])
    batch_size = int(config["batch_size"])
    interval_ns = int(config["batch_interval_ns"])
    attention_compute_ns = int(config["attention_compute_ns_per_token"])
    mlp_compute_ns = int(config["mlp_compute_ns_per_token"])
    output_compute_ns = int(config["output_compute_ns_per_token"])
    prefetch = int(config["prefetch_layers"])
    embedding_access = config["embedding_access"]
    if embedding_access not in {"selected_token_rows", "full_weight_stress"}:
        raise ValueError("embedding_access must be selected_token_rows or full_weight_stress")
    if min(intervals, batch_size, attention_compute_ns, mlp_compute_ns,
           output_compute_ns) <= 0 or interval_ns < 0 or prefetch < 0:
        raise ValueError("trace counts/durations must be positive; interval/prefetch non-negative")
    groups = _tensor_groups(meta)
    by_id = {x["tensor_id"]: x for x in groups}
    layers = meta["architecture"]["num_hidden_layers"]
    batches = []
    first_interval = int(config.get('first_interval', 0))
    for interval in range(first_interval, first_interval + intervals):
        prefix = f"interval{interval}"
        tasks: list[dict[str, Any]] = []
        embedding = deepcopy(by_id["model.embed_tokens"])
        if embedding_access == "selected_token_rows":
            unique_rows = int(config.get("embedding_unique_rows_per_interval", batch_size))
            if not 1 <= unique_rows <= batch_size:
                raise ValueError("embedding unique rows must be within the batch size")
            embedding["full_tensor_bytes"] = embedding["bytes"]
            embedding["bytes"] = (meta["architecture"]["hidden_size"]
                                  * meta["bytes_per_tensor_element"] * unique_rows)
            embedding["tensor_id"] += f":interval{interval}:selected_rows"
            embedding["access_semantics"] = "SELECTED_TOKEN_ROWS_SYNTHETIC_IDENTITIES"
        else:
            embedding["access_semantics"] = "EXPLICIT_SYNTHETIC_FULL_WEIGHT_STRESS"
        tasks.append({"task_id": prefix + ":embed_read", "type": "storage",
                      "tensor": embedding, "issue_after": [],
                      "consume_after": [], "consumer_count": batch_size})
        previous = prefix + ":embed_read"
        layer_compute_ids: list[str] = []
        for layer in range(layers):
            issue_parent_index = layer - prefetch - 1
            issue_parent = ([layer_compute_ids[issue_parent_index]]
                            if issue_parent_index >= 0 else [])
            attn_read = prefix + f":l{layer}:attn_read"
            attn_compute = prefix + f":l{layer}:attn_compute"
            mlp_read = prefix + f":l{layer}:mlp_read"
            mlp_compute = prefix + f":l{layer}:mlp_compute"
            tasks.extend([
                {"task_id": attn_read, "type": "storage",
                 "tensor": by_id[f"model.layers.{layer}.attention_bundle"],
                 "issue_after": issue_parent, "consume_after": [previous],
                 "consumer_count": batch_size},
                {"task_id": attn_compute, "type": "compute",
                 "duration_ns": attention_compute_ns * batch_size,
                 "depends_on": [previous, attn_read]},
                {"task_id": mlp_read, "type": "storage",
                 "tensor": by_id[f"model.layers.{layer}.mlp_bundle"],
                 "issue_after": [previous], "consume_after": [attn_compute],
                 "consumer_count": batch_size},
                {"task_id": mlp_compute, "type": "compute",
                 "duration_ns": mlp_compute_ns * batch_size,
                 "depends_on": [attn_compute, mlp_read]},
            ])
            previous = mlp_compute
            layer_compute_ids.append(mlp_compute)
        for suffix, tensor_id in (("norm_read", "model.norm"), ("head_read", "lm_head")):
            task_id = prefix + ":" + suffix
            tasks.append({"task_id": task_id, "type": "storage", "tensor": by_id[tensor_id],
                          "issue_after": [previous], "consume_after": [previous],
                          "consumer_count": batch_size})
            previous = task_id
        final = prefix + ":token_complete"
        tasks.append({"task_id": final, "type": "compute",
                      "duration_ns": output_compute_ns * batch_size,
                      "depends_on": [previous], "is_token_terminal": True})
        for task in tasks:
            task["batch_interval_id"] = interval
        batches.append({"interval_id": interval, "arrival_ns": interval * interval_ns,
                        "batch_size": batch_size, "token_ids": [
                            f"{prefix}:token{i}" for i in range(batch_size)], "tasks": tasks,
                        "terminal_task_id": final})
    return {
        "schema_version": "eq3-causal-architecture-trace-v1",
        "trace_origin": TRACE_ORIGIN, "model_id": model_id,
        "embedding_access": embedding_access,
        "compute_cost_evidence": "EXPLICIT_SCENARIO_INPUT_NOT_RUNTIME_TRACE",
        "official_metadata": {k: meta[k] for k in (
            "revision", "resolved_commit", "tensor_payload_bytes", "architecture", "sources")},
        "prefetch_layers": prefetch, "batches": batches,
        "limitations": ["not a PyTorch or hardware runtime trace", "no NAND timing model",
                        "activation and KV-cache traffic unavailable"],
    }


class CausalExecutor:
    """Incrementally connect a causal trace to an external topology service."""

    def __init__(self, trace: dict[str, Any], config: dict[str, Any],
                 placement_provider: Callable[[dict[str, Any], str, int], dict[str, Any]] | None = None):
        if trace.get("trace_origin") != TRACE_ORIGIN:
            raise ValueError("unclassified causal trace")
        self.trace = deepcopy(trace)
        self.cache_capacity = int(config.get("cache_capacity_bytes", 0))
        self.migration_mode = config.get("migration_mode", "fixed")
        self.migration_threshold = int(config.get("migration_access_threshold", 2))
        self.cache_mode = config.get("cache_mode")
        self.coalescing_enabled = config.get("coalescing_enabled")
        self.prefetch_wait_mode = config.get("prefetch_wait_mode")
        if self.cache_capacity < 0 or self.migration_mode not in {"fixed", "basic"}:
            raise ValueError("invalid cache or migration mode")
        self.migration_capacity_bytes = int(config.get('migration_capacity_bytes', 0))
        self.migration_used_bytes = 0
        self.tensor_versions = {}
        if self.migration_mode == 'basic' and self.migration_capacity_bytes <= 0:
            raise ValueError('basic migration requires finite explicit destination capacity')
        if self.migration_mode == 'basic' and (not config.get('fast_stripe_targets') or
                any(not r['stack'].startswith('hbf') for r in config['fast_stripe_targets'])):
            raise ValueError('basic placement migration currently requires explicit HBF destinations')
        if self.cache_mode not in {"disabled", "ideal_metadata_only", "external_hbm"}:
            raise ValueError("unsupported cache_mode")
        if self.cache_mode == "disabled" and self.cache_capacity != 0:
            raise ValueError("disabled cache requires zero capacity")
        if not isinstance(self.coalescing_enabled, bool):
            raise ValueError("coalescing_enabled must be explicit boolean")
        if self.prefetch_wait_mode not in {"wait_at_consumption", "stall_at_issue"}:
            raise ValueError("explicit prefetch_wait_mode is required")
        self.stripe_unit_bytes = int(config.get("stripe_unit_bytes", 0))
        if self.stripe_unit_bytes <= 0:
            raise ValueError("stripe_unit_bytes must be an explicit positive value")
        if placement_provider is None:
            self._placement_provider, self.target_count = self._default_placement(config)
        else:
            self._placement_provider = placement_provider
            self.target_count = int(config.get("placement_target_count", 0))
            if self.target_count <= 0:
                raise ValueError("custom placement_provider requires placement_target_count")
        self.tasks = {t["task_id"]: deepcopy(t) for b in trace["batches"] for t in b["tasks"]}
        self.tensor_specs = {t['tensor']['tensor_id']:deepcopy(t['tensor'])
                             for t in self.tasks.values() if t['type']=='storage'}
        self.arrival = {t["task_id"]: b["arrival_ns"] for b in trace["batches"] for t in b["tasks"]}
        self.done: dict[str, int] = {}
        self.offered: set[str] = set()
        self.job_group: dict[str, str] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.job_bytes: dict[str, int] = {}
        self.job_arrival: dict[str, int] = {}
        self.pending_tensor: dict[str, str] = {}
        self.cache: OrderedDict[str, int] = OrderedDict()
        self.cache_bytes = 0
        self.access_count: dict[str, int] = {}
        self.tensor_tier: dict[str, str] = {}
        self.pending_migrations: dict[str, str] = {}
        self.jobs: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.compute_running = None
        self.compute_available_ns = 0
        self.now_ns = 0
        self.deferred_jobs = []
        self.cache_reservations = {}
        self.deferred_source_erase = []
        self.completed_batches = []
        self.known_batch_ids = {b.get('interval_id', i) for i,b in enumerate(trace['batches'])}
        if self.cache_mode == "external_hbm":
            targets = config.get("fast_stripe_targets", [])
            if not targets or any(not row["stack"].startswith("hbm") for row in targets):
                raise ValueError("service-backed cache requires explicit HBM targets")

    def append_trace(self, trace):
        """Append an arrived batch; callers retain uninstantiated arrival backlog."""
        for key in ('trace_origin','model_id','embedding_access','prefetch_layers'):
            if trace.get(key) != self.trace.get(key):
                raise ValueError('streaming trace scientific identity changed')
        for batch in trace['batches']:
            if batch['interval_id'] in self.known_batch_ids:
                raise ValueError('duplicate streaming batch')
            self.known_batch_ids.add(batch['interval_id'])
            for task in batch['tasks']:
                task_id=task['task_id']
                if task_id in self.tasks:
                    raise ValueError('duplicate streaming batch/task')
                self.tasks[task_id]=deepcopy(task);self.arrival[task_id]=batch['arrival_ns']
                if task['type']=='storage':self.tensor_specs[task['tensor']['tensor_id']]=deepcopy(task['tensor'])
            self.trace['batches'].append(deepcopy(batch))

    def retire_completed_batches(self, now_ns):
        """Retire finished DAG storage, preserving token completion and cache state."""
        self._resolve_ready_storage(now_ns)
        retained=[]; completed=[]
        referenced={task for group in self.groups.values() for task in group.get('task_ids',[])}
        for batch in self.trace['batches']:
            task_ids={task['task_id'] for task in batch['tasks']}
            terminal=batch['terminal_task_id']
            if terminal not in self.done or task_ids & referenced:
                retained.append(batch);continue
            fact={'interval_id':batch['interval_id'],'arrival_ns':batch['arrival_ns'],
                  'completion_ns':self.done[terminal],'token_count':len(batch['token_ids'])}
            self.completed_batches.append(fact);completed.append(fact)
            for task_id in task_ids:
                self.tasks.pop(task_id);self.arrival.pop(task_id);self.done.pop(task_id,None)
                self.offered.discard(task_id)
        self.trace['batches']=retained
        return completed

    def drain_observations(self):
        """Return immutable-window facts once; avoid retaining full run duplicates."""
        result={'events':self.events,'submissions':self.jobs}
        self.events=[];self.jobs=[]
        return result

    @staticmethod
    def _default_placement(config: dict[str, Any]):
        targets = deepcopy(config.get("stripe_targets"))
        if targets is None and config.get("default_placement") is not None:
            targets = [deepcopy(config["default_placement"])]
        if not targets:
            raise ValueError("explicit stripe_targets are required")
        fast_targets = deepcopy(config.get("fast_stripe_targets", targets))
        if not fast_targets:
            raise ValueError("fast_stripe_targets cannot be empty")
        if len(fast_targets) != len(targets):
            raise ValueError("source and fast stripe target counts must match")
        def provider(tensor: dict[str, Any], tier: str, partition: int) -> dict[str, Any]:
            selected = fast_targets if tier == "fast" else targets
            value = selected[partition % len(selected)]
            required = {"stack", "channel", "route"}
            if not required.issubset(value):
                raise ValueError("placement lacks stack/channel/route")
            return deepcopy(value)
        return provider, len(targets)

    def _stripe_parts(self, size: int) -> list[tuple[int, int]]:
        """Aggregate round-robin stripe units into one child per physical target."""
        full, tail = divmod(size, self.stripe_unit_bytes)
        base, extra = divmod(full, self.target_count)
        parts = []
        for index in range(self.target_count):
            child = (base + (index < extra)) * self.stripe_unit_bytes
            if tail and index == full % self.target_count:
                child += tail
            if child:
                parts.append((index, child))
        if sum(value for _, value in parts) != size:
            raise AssertionError("stripe partition did not conserve tensor bytes")
        return parts

    def _deps_time(self, ids: list[str]) -> int | None:
        if any(item not in self.done for item in ids):
            return None
        return max((self.done[item] for item in ids), default=0)

    def _insert_cache(self, tensor: str, size: int) -> None:
        if size > self.cache_capacity or self.cache_capacity == 0:
            return
        if tensor in self.cache:
            self.cache_bytes -= self.cache.pop(tensor)
        while self.cache and self.cache_bytes + size > self.cache_capacity:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= removed
        self.cache[tensor] = size
        self.cache_bytes += size

    def _offer_cache_fill(self, tensor: str, size: int, now_ns: int) -> None:
        if self.cache_mode != "external_hbm" or size > self.cache_capacity:
            return
        if tensor in self.cache or tensor in self.cache_reservations:
            return
        reserved = sum(self.cache_reservations.values())
        while self.cache and self.cache_bytes + reserved + size > self.cache_capacity:
            pinned = {group['tensor_id'] for group in self.groups.values() if group.get('cache_hit')}
            evicted = next((key for key in self.cache if key not in pinned), None)
            if evicted is None:
                return
            removed = self.cache.pop(evicted)
            self.cache_bytes -= removed
            self.events.append({"kind":"cache_evict","tensor_id":evicted,"at_ns":now_ns,"bytes":removed})
        if reserved + self.cache_bytes + size > self.cache_capacity:
            return  # Pending fills already own the finite capacity.
        self.cache_reservations[tensor] = size
        group_id = f"cache-fill:{tensor}:{now_ns}"
        group = {"operation":"cache_fill", "tensor_id":tensor,"tensor_bytes":size,
                 "pending":set(),"task_ids":[],"completion_ns":now_ns}
        self.groups[group_id] = group
        for partition, child_bytes in self._stripe_parts(size):
            place = self._placement_provider({"tensor_id":tensor,"bytes":size},"fast",partition)
            job_id = group_id + f":part{partition}"
            job = {"job_id":job_id,"operation":"hbm_fill","bytes":child_bytes,
                   "arrival_ns":now_ns,**place,
                   "metadata":{"parent_group_id":group_id,"tensor_id":tensor,
                               "purpose":"CACHE_FILL_AFTER_SOURCE_DELIVERY"}}
            group["pending"].add(job_id)
            self.job_group[job_id] = group_id
            self.job_bytes[job_id] = child_bytes
            self.job_arrival[job_id] = now_ns
            self.jobs.append(deepcopy(job))
            self.deferred_jobs.append(job)

    def _settle_compute(self, now_ns: int) -> None:
        if self.compute_running is not None:
            task_id, start, finish = self.compute_running
            if finish <= now_ns:
                self.done[task_id] = finish
                self.compute_available_ns = finish
                self.compute_running = None
                self.events.append({"kind":"compute_complete", "task_id":task_id,
                                    "start_ns":start,"completion_ns":finish,
                                    "duration_ns":finish-start})

    def _start_compute(self, now_ns: int) -> None:
        if self.compute_running is not None:
            return
        if any(group.get("blocks_compute") for group in self.groups.values()):
            return
        candidates = []
        for task_id, task in self.tasks.items():
            if task_id in self.done or task["type"] != "compute":
                continue
            ready = self._deps_time(task["depends_on"])
            if ready is not None and max(ready, self.arrival[task_id]) <= now_ns:
                candidates.append((max(ready,self.arrival[task_id]), task_id))
        if candidates:
            _, task_id = min(candidates)
            start = max(now_ns, self.compute_available_ns)
            finish = start + self.tasks[task_id]["duration_ns"]
            self.compute_running = (task_id,start,finish)
            self.events.append({"kind":"compute_start","task_id":task_id,
                                "start_ns":start,"scheduled_end_ns":finish,
                                "resource":"single_scenario_gpu_compute"})

    def next_internal_event_ns(self) -> int | None:
        candidates = [self.compute_running[2]] if self.compute_running is not None else []
        for task_id, task in self.tasks.items():
            if task_id in self.done:
                continue
            if task["type"] == "storage" and task_id not in self.offered:
                ready = self._deps_time(task["issue_after"])
                if ready is not None:
                    candidates.append(max(ready, self.arrival[task_id]))
        return min(candidates) if candidates else None

    def poll(self, now_ns: int) -> list[dict[str, Any]]:
        """Return newly eligible jobs at exact ``now_ns``; never self-complete them."""
        if not isinstance(now_ns, int) or now_ns < 0:
            raise ValueError("now_ns must be non-negative integer ns")
        if now_ns < self.now_ns:
            raise ValueError("causal clock cannot go backward")
        self.now_ns = now_ns
        self._resolve_ready_storage(now_ns)
        result, self.deferred_jobs = self.deferred_jobs, []
        pending = []
        for transfer in self.deferred_source_erase:
            pinned = any(g.get('operation')=='read' and g.get('tier')=='source' and
                         g['tensor_id']==transfer['tensor_id'] for g in self.groups.values())
            if pinned:pending.append(transfer)
            else:result.extend(self._migration_jobs(transfer,now_ns))
        self.deferred_source_erase = pending
        for task_id, task in self.tasks.items():
            if task_id in self.done or task_id in self.offered or task["type"] != "storage":
                continue
            issue_ready = self._deps_time(task["issue_after"])
            if issue_ready is None:
                continue
            logical_issue_ns = max(issue_ready, self.arrival[task_id])
            if logical_issue_ns > now_ns:
                continue
            issue_ns = now_ns  # A queued batch cannot submit retrospectively.
            tensor = task["tensor"]["tensor_id"]
            consume_ready = self._deps_time(task["consume_after"])
            if self.cache_mode == "ideal_metadata_only" and tensor in self.cache:
                self.cache.move_to_end(tensor)
                self.offered.add(task_id)
                if consume_ready is None:
                    task["external_ready_ns"] = issue_ns
                    task["ready_source"] = "cache"
                else:
                    self.done[task_id] = max(issue_ns, consume_ready)
                    self.events.append({"kind": "storage_consumed", "task_id": task_id,
                                        "tensor_id": tensor, "ready_ns": issue_ns,
                                        "consume_ns": self.done[task_id], "source": "cache"})
                self.events.append({"kind": "cache_hit", "task_id": task_id,
                                    "tensor_id": tensor, "ready_ns": issue_ns,
                                    "completion_ns": self.done.get(task_id)})
                continue
            if self.coalescing_enabled and tensor in self.pending_tensor:
                group_id = self.pending_tensor[tensor]
                self.groups[group_id]["task_ids"].append(task_id)
                self.offered.add(task_id)
                self.events.append({"kind": "coalesced", "task_id": task_id,
                                    "group_id": group_id, "logical_issue_ns": issue_ns})
                continue
            external_hit = self.cache_mode == "external_hbm" and tensor in self.cache
            if external_hit:
                self.cache.move_to_end(tensor)
                self.events.append({"kind":"cache_hit","task_id":task_id,"tensor_id":tensor,
                                    "at_ns":now_ns,"requires_hbm_service":True})
            tier = "fast" if external_hit else self.tensor_tier.get(tensor, "source")
            group_id = "causal:" + task_id
            parts = self._stripe_parts(task["tensor"]["bytes"])
            self.offered.add(task_id)
            self.pending_tensor[tensor] = group_id
            self.groups[group_id] = {"task_ids": [task_id], "pending": set(),
                                     "completion_ns": issue_ns, "tensor_id": tensor,
                                     "tensor_bytes": task["tensor"]["bytes"],
                                     "child_count":len(parts),
                                     "operation": "read",
                                     "cache_hit": external_hit,
                                     "tier": tier,
                                     "blocks_compute": (
                                         self.prefetch_wait_mode == "stall_at_issue"
                                         and consume_ready is None)}
            for partition, child_bytes in parts:
                place = self._placement_provider(task["tensor"], tier, partition)
                job_id = group_id + f":part{partition}"
                job = {"job_id": job_id, "operation": "read", "bytes": child_bytes,
                       "arrival_ns": issue_ns, **place,
                       "metadata": {"logical_issue_ns": logical_issue_ns, "tensor_id": tensor,
                                    "parent_group_id": group_id,
                                    "partition_index": partition,
                                    "partition_count": len(parts),
                                    "stripe_unit_bytes": self.stripe_unit_bytes,
                                    "stripe_semantics": "ROUND_ROBIN_UNITS_AGGREGATED_PER_TARGET",
                                    "batch_consumer_count": task["consumer_count"],
                                    "batch_interval_id": task["batch_interval_id"]}}
                self.groups[group_id]["pending"].add(job_id)
                self.job_group[job_id] = group_id
                self.job_bytes[job_id] = child_bytes
                self.job_arrival[job_id] = issue_ns
                self.jobs.append(deepcopy(job)); result.append(job)
        self._resolve_ready_storage(now_ns)
        self._start_compute(now_ns)
        return result

    def complete(self, job_id: str, completion_ns: int, completed_bytes: int) -> None:
        """Consume an actual external completion and unlock dependent work."""
        if job_id not in self.job_bytes or completed_bytes != self.job_bytes[job_id]:
            raise ValueError("unknown job or byte-incomplete external completion")
        if not isinstance(completion_ns, int) or completion_ns < 0:
            raise ValueError("completion_ns must be non-negative integer ns")
        if completion_ns < self.job_arrival[job_id]:
            raise ValueError("external completion precedes causal job arrival")
        self.job_bytes.pop(job_id)
        self.job_arrival.pop(job_id)
        group_id = self.job_group.pop(job_id)
        group = self.groups[group_id]
        group["pending"].remove(job_id)
        group["completion_ns"] = max(group["completion_ns"], completion_ns)
        self.events.append({"kind": "storage_child_complete", "job_id": job_id,
                            "group_id": group_id, "completion_ns": completion_ns,
                            "completed_bytes": completed_bytes})
        if group["pending"]:
            return
        if group['operation'].startswith('migration_'):
            self._complete_migration_phase(group_id, group, completion_ns)
            return
        if group["operation"] == "cache_fill":
            tensor = group["tensor_id"]
            self.cache_reservations.pop(tensor)
            self._insert_cache(tensor, group["tensor_bytes"])
            self.events.append({"kind":"cache_fill_complete","tensor_id":tensor,
                                "completion_ns":completion_ns,"bytes":group["tensor_bytes"]})
            self.groups.pop(group_id)
            return
        tasks = group["task_ids"]
        tensor = group["tensor_id"]
        if self.pending_tensor.get(tensor) == group_id:
            self.pending_tensor.pop(tensor)
        completion_ns = group["completion_ns"]
        if group["blocks_compute"]:
            self.events.append({"kind": "prefetch_issue_stall_released",
                                "group_id": group_id, "completion_ns": completion_ns})
        if self.cache_mode == "ideal_metadata_only":
            self._insert_cache(tensor, group["tensor_bytes"])
        elif not group["cache_hit"]:
            self._offer_cache_fill(tensor,group["tensor_bytes"],completion_ns)
        for task_id in tasks:
            consume_ready = self._deps_time(self.tasks[task_id]["consume_after"])
            if consume_ready is None:
                # Storage is ready; consumption is finalized when its dependency resolves.
                self.tasks[task_id]["external_ready_ns"] = completion_ns
                self.tasks[task_id]["ready_source"] = "external_service"
            else:
                self.done[task_id] = max(completion_ns, consume_ready)
                self.events.append({"kind": "storage_consumed", "task_id": task_id,
                                    "tensor_id": tensor, "ready_ns": completion_ns,
                                    "consume_ns": self.done[task_id],
                                    "source": "external_service"})
        self.access_count[tensor] = self.access_count.get(tensor, 0) + 1
        self.events.append({"kind": "storage_complete", "group_id": group_id,
                            "tensor_id": tensor, "completion_ns": completion_ns,
                            "completed_bytes": group["tensor_bytes"],
                            "child_count": group['child_count'],
                            "task_ids": list(tasks)})
        self.groups.pop(group_id)
        self._resolve_ready_storage(completion_ns)

    def _resolve_ready_storage(self, now_ns: int) -> None:
        changed = True
        while changed:
            changed = False
            self._settle_compute(now_ns)
            for task_id, task in self.tasks.items():
                if task_id in self.done or "external_ready_ns" not in task:
                    continue
                consume_ready = self._deps_time(task["consume_after"])
                if consume_ready is not None:
                    ready_ns = task.pop("external_ready_ns")
                    source = task.pop("ready_source")
                    self.done[task_id] = max(ready_ns, consume_ready)
                    self.events.append({"kind": "storage_consumed", "task_id": task_id,
                                        "tensor_id": task["tensor"]["tensor_id"],
                                        "ready_ns": ready_ns, "consume_ns": self.done[task_id],
                                        "source": source})
                    changed = True

    def offer_migrations(self, now_ns: int) -> list[dict[str, Any]]:
        """Move repeatedly accessed immutable weights through explicit copy phases."""
        if self.migration_mode != "basic":
            return []
        result = []
        tensors = self.tensor_specs
        for tensor, count in sorted(self.access_count.items()):
            if count < self.migration_threshold or self.tensor_tier.get(tensor) == "fast" \
                    or tensor in self.pending_migrations:
                continue
            spec = tensors[tensor]
            allocation = sum(((size+1048575)//1048576)*1048576 for _,size in self._stripe_parts(spec['bytes']))
            if self.migration_used_bytes + allocation > self.migration_capacity_bytes:
                continue
            group_id = "causal:migration:" + tensor
            self.pending_migrations[tensor] = group_id
            self.migration_used_bytes += allocation
            transfer = {'tensor_id':tensor,'tensor_bytes':spec['bytes'],
                        'allocation_bytes':allocation,
                        'spec':deepcopy(spec),'expected_version':self.tensor_versions.get(tensor,0),
                        'root_id':group_id,'operation':'migration_source'}
            result.extend(self._migration_jobs(transfer,now_ns))
        return result

    def _migration_jobs(self, transfer, now_ns):
        phase = transfer['operation']
        group_id = transfer['root_id'] + ':' + phase
        group = {**transfer,'task_ids':[],'pending':set(),'completion_ns':now_ns}
        self.groups[group_id] = group
        jobs = []
        tier = 'source' if phase in ('migration_source','migration_erase_old') else 'fast'
        for partition,size in self._stripe_parts(transfer['tensor_bytes']):
            place = self._placement_provider(transfer['spec'],tier,partition)
            if phase == 'migration_source':operation='read'
            elif phase == 'migration_destination':
                operation='hbm_fill' if place['stack'].startswith('hbm') else 'migration_program'
            else:operation='erase'
            block_count = (size + 1048575)//1048576
            if operation=='erase':size=block_count*1048576
            job_id=group_id+f':part{partition}'
            job={'job_id':job_id,'maintenance_id':job_id,'operation':operation,
                 'bytes':size,'arrival_ns':now_ns,**place,
                 'metadata':{'purpose':'MIGRATION','parent_group_id':group_id,
                             'tensor_id':transfer['tensor_id'],'phase':phase,
                             'logical_address_bytes':transfer['spec'].get('logical_address_bytes'),
                             'block_count':block_count,'block_bytes':1048576,
                             'placement_evidence':'CONDITIONAL_DEDICATED_BLOCK_RANGES_NO_NATIVE_FTL_CLAIM'}}
            group['pending'].add(job_id)
            self.job_group[job_id]=group_id;self.job_bytes[job_id]=size;self.job_arrival[job_id]=now_ns
            self.jobs.append(deepcopy(job));jobs.append(job)
        return jobs

    def _complete_migration_phase(self, group_id, group, now_ns):
        phase=group['operation'];tensor=group['tensor_id']
        self.groups.pop(group_id)
        self.events.append({'kind':'migration_phase_complete','tensor_id':tensor,'phase':phase,
                            'completion_ns':now_ns,'bytes':group['tensor_bytes']})
        if phase=='migration_source':
            group['operation']='migration_destination'
        elif phase=='migration_destination':
            valid=self.tensor_versions.get(tensor,0)==group['expected_version']
            if valid:
                self.tensor_tier[tensor]='fast'
                self.tensor_versions[tensor]=group['expected_version']+1
                group['operation']='migration_erase_old'
            else:
                group['operation']='migration_erase_destination'
            self.events.append({'kind':'migration_commit','tensor_id':tensor,'at_ns':now_ns,
                                'committed':valid,'source_valid_until_commit':True})
        else:
            if phase=='migration_erase_destination':self.migration_used_bytes-=group['allocation_bytes']
            self.pending_migrations.pop(tensor,None)
            return
        if group['operation']=='migration_erase_old':
            self.deferred_source_erase.append(group)
        else:
            self.deferred_jobs.extend(self._migration_jobs(group,now_ns))

    def result(self, now_ns: int) -> dict[str, Any]:
        self._resolve_ready_storage(now_ns)
        tokens = []
        for batch in self.trace["batches"]:
            terminal = batch["terminal_task_id"]
            for token_id in batch["token_ids"]:
                tokens.append({"token_id": token_id,
                               "completion_ns": self.done.get(terminal),
                               "complete": terminal in self.done})
        return {"schema_version": "eq3-causal-execution-v1",
                "trace_origin": TRACE_ORIGIN, "tokens": tokens,
                "retired_completed_batches":deepcopy(self.completed_batches),
                "jobs": deepcopy(self.jobs), "events": deepcopy(self.events),
                "pending_external_jobs": sorted(self.job_bytes),
                "capabilities": {"coalescing_enabled": self.coalescing_enabled,
                                 "prefetch_wait_mode": self.prefetch_wait_mode,
                                 "cache": {"ideal_metadata_only":"IDEAL_DIAGNOSTIC_NOT_EXTERNAL_HBM_SERVICE",
                                           "external_hbm":"SERVICE_BACKED_HBM_FILL_AND_HIT",
                                           "disabled":"DISABLED"}[self.cache_mode],
                                 "basic_migration": "SOURCE_READ_DESTINATION_WRITE_VERSION_COMMIT_OLD_ERASE"},
                "unavailable": ["TOKEN_PER_SECOND_CALIBRATION", "NAND_COMMAND_TIMING"]}


__all__ = ["CausalExecutor", "build_architecture_trace", "load_architecture"]
