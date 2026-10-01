"""In-memory stand-in for ``agentplatform.Client`` so CLI/library logic is testable offline."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

RUNTIME = "projects/1/locations/europe-west8/reasoningEngines/2"


def json_chunk(stdout: str = "", stderr: str = "", exit_status: int = 0) -> SimpleNamespace:
    payload = {"exit_status_int": exit_status, "msg_out": stdout, "msg_err": stderr}
    return SimpleNamespace(
        mime_type="application/json", data=json.dumps(payload).encode(), metadata=None
    )


def file_chunk(name: str, data: bytes, mime: Optional[str] = "text/plain") -> SimpleNamespace:
    return SimpleNamespace(
        mime_type=mime,
        data=data,
        metadata=SimpleNamespace(attributes={"file_name": name.encode()}),
    )


class FakeSandboxes:
    def __init__(self) -> None:
        self.live: Dict[str, SimpleNamespace] = {}
        self.deleted: List[str] = []
        self.created: List[Dict[str, Any]] = []
        self.next_response: List[SimpleNamespace] = [json_chunk(stdout="ok\n")]
        self.calls: List[Dict[str, Any]] = []
        self.delete_error: Optional[Exception] = None
        self._counter = 0

    def create(self, *, name: str, spec: Any = None, config: Any = None, **_: Any) -> SimpleNamespace:
        self._counter += 1
        sandbox_name = f"{name}/sandboxEnvironments/{1000 + self._counter}"
        now = datetime.now(timezone.utc)
        ttl = int(str(config["ttl"]).rstrip("s"))
        self.live[sandbox_name] = SimpleNamespace(
            name=sandbox_name,
            display_name=config["display_name"],
            state="SandboxState.STATE_RUNNING",
            create_time=now,
            expire_time=now + timedelta(seconds=ttl),
        )
        self.created.append({"name": sandbox_name, "spec": spec, "config": config})
        return SimpleNamespace(error=None, response=SimpleNamespace(name=sandbox_name))

    def delete(self, *, name: str, **_: Any) -> None:
        if self.delete_error:
            raise self.delete_error
        if name not in self.live:
            raise RuntimeError("ClientError 404 NOT_FOUND. resource gone")
        del self.live[name]
        self.deleted.append(name)

    def list(self, *, name: str, **_: Any):
        return iter(list(self.live.values()))

    def execute_code(self, *, name: str, input_data: Dict[str, Any], **_: Any) -> SimpleNamespace:
        self.calls.append({"name": name, **input_data})
        return SimpleNamespace(outputs=list(self.next_response))


class FakeClient:
    def __init__(self) -> None:
        self.sandboxes = FakeSandboxes()
