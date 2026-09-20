"""Client for the isolated EQ3 MQSim maintenance experiment service."""
from __future__ import annotations

from scripts.eval.mqsim_service import MqsimService


class MaintenanceMqsimService(MqsimService):
    """Preserves the demand API and adds page-maintenance lifecycle facts.

    The caller still advances MQSim exclusively with ``until(horizon)``.  A
    maintenance request is accepted for later injection; this method does not
    run the event loop or wait for completion.
    """

    def __init__(self, *args, **kwargs):
        self.maintenance_requests = {}
        self.maintenance_events = []
        self.maintenance_completions = {}
        super().__init__(*args, **kwargs)
        if (self.header.get("maintenance_backend") !=
                "EXPERIMENTAL_OUT_OF_PLACE_PAGE_MAINTENANCE"
                or self.header.get("maintenance_data_semantics") !=
                "METADATA_VERSION_VALIDITY"
                or self.header.get("maintenance_payload_validation") != "UNAVAILABLE"):
            self.close()
            raise ValueError("isolated maintenance service capability mismatch")

    def read(self):
        response = super().read()
        self.maintenance_events.extend(response.get("maintenance_events", []))
        for completion in response.get("maintenance_completions", []):
            request_id = completion["request_id"]
            if (request_id not in self.maintenance_requests
                    or request_id in self.maintenance_completions):
                raise ValueError("duplicate or unknown maintenance completion")
            self.maintenance_completions[request_id] = completion
        return response

    def maintain(self, job=None, **kwargs):
        """Accept either one job dict or equivalent keyword arguments."""
        if job is not None:
            if not isinstance(job, dict) or kwargs:
                raise TypeError("maintain accepts one job dict or keyword arguments")
            kwargs = dict(job)
        request_id = kwargs.pop("request_id")
        stack = kwargs.pop("stack")
        stack_local_page = kwargs.pop("stack_local_page")
        due_ns = kwargs.pop("due_ns")
        deadline_ns = kwargs.pop("deadline_ns", 0)
        parent_id = kwargs.pop("parent_id", None)
        reclaim_source_block = kwargs.pop("reclaim_source_block", False)
        failure_injection = kwargs.pop("failure_injection", "none")
        trigger_reason = kwargs.pop("trigger_reason", "UNSPECIFIED")
        if kwargs:
            raise TypeError(f"unknown maintenance fields: {sorted(kwargs)}")
        if request_id in self.maintenance_requests:
            raise ValueError("duplicate maintenance request ID")
        request = dict(command="maintain", request_id=request_id,
                       parent_id=request_id if parent_id is None else parent_id,
                       stack=stack, stack_local_page=stack_local_page,
                       due_ns=due_ns, deadline_ns=deadline_ns,
                       reclaim_source_block=reclaim_source_block,
                       failure_injection=failure_injection,
                       trigger_reason=trigger_reason)
        response = self.command(request)
        if response.get("maintenance_accepted") != 1:
            raise ValueError("maintenance service did not accept one request")
        self.maintenance_requests[request_id] = request
        return response

    def finish(self):
        receipt = super().finish()
        if (set(self.maintenance_requests) != set(self.maintenance_completions)
                or receipt.get("maintenance_issued") != len(self.maintenance_requests)
                or receipt.get("maintenance_completed") != len(self.maintenance_requests)
                or receipt.get("pending_maintenance") != 0):
            raise ValueError("maintenance finish conservation failed")
        return receipt
