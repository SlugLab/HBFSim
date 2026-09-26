"""CPU mocks only: selected-device commands and configured resource thresholds."""
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TASK=Path(__file__).resolve().parent
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
guard=module("selected_guard",TASK/"resource_guard_selected_gpu.py")
controller=module("selected_controller",TASK/"run_bounded_selected_gpu_stage.py")
launcher=module("mixed_launcher",TASK/"root_mixed3_launcher.py")

def fake_run(command,**kwargs):
    assert command[:3]==["nvidia-smi","-i","2"]
    if "--query-gpu=uuid,memory.total,memory.free" in command:
        return SimpleNamespace(stdout="GPU-observed, 48000, 5000\n")
    assert "--query-compute-apps=pid" in command
    return SimpleNamespace(stdout="123\n")

def main():
    with patch.object(guard.subprocess,"run",side_effect=fake_run):
        assert guard.gpu_state(2)==("GPU-observed",48000,5000)
        assert guard.compute_pids(2)=={123}
    with patch.object(guard.subprocess,"run",return_value=SimpleNamespace(
            stdout="GPU-observed, 123\nGPU-other, 456\nGPU-other, 789\n")):
        assert guard.wrong_device_worker_pids("GPU-observed",55,
            getpgid=lambda pid:55 if pid in (123,456) else 99)=={456}
    plan=json.loads((TASK/"MIXED3_PLAN_TEMPLATE.json").read_text())
    selector=re.compile(plan["command"][plan["command"].index("--hbf-include-pattern")+1])
    mixed=json.loads((TASK/"prepared-v2/scope-mixed3.json").read_text())
    assert all(selector.fullmatch(row["alias"]) for row in mixed["targets"])
    ledger=json.loads((TASK.parent/"full-coverage-ledger-v1/TARGET_LEDGER.json").read_text())
    assert sum(bool(selector.fullmatch(alias)) for row in ledger["entries"]
               for alias in row["aliases"])==3
    plan.update({"configured_gpu_index":2,"reservation_pid":123,
                 "reservation_start_ticks":99,"reserve_deadline_unix":9999999999})
    with patch.object(launcher.subprocess,"run",side_effect=lambda command,**kwargs:
                      SimpleNamespace(stdout="0, GPU-other, 48000, 20000\n2, GPU-observed, 48000, 5000\n")
                      if "--query-gpu=index,uuid,memory.total,memory.free" in command else
                      SimpleNamespace(stdout="GPU-other, 456\nGPU-observed, 123\n")):
        assert launcher.gpu_selected(2)==("GPU-observed",48000,5000)
    cmd=controller.guard_command(plan,Path("/tmp/worker-start.json"),
                                 Path("/tmp/guard.jsonl"),9999999000)
    assert cmd[cmd.index("--gpu-index")+1]=="2" and "--gpu-uuid" not in cmd
    assert cmd[cmd.index("--minimum-free-mib")+1]==str(plan["minimum_free_mib"])
    receipt={"schema":"hbfsim.selected_gpu_cpu_portability.v1",
             "status":"PASS_CPU_MOCK_NO_GPU","cases":["multi_gpu_inventory_selected_index",
             "selected_gpu_only_guard_query","own_group_wrong_gpu_vs_foreign_other_gpu",
             "configured_guard_thresholds_and_index_argv","exact_three_alias_selector"],
             "limitation":"Mocked commands; no real nvidia-smi, process, signal or GPU call."}
    (TASK/"SELECTED_GPU_CPU_RESULT.json").write_text(json.dumps(receipt,indent=2)+"\n")

if __name__=="__main__":main()
