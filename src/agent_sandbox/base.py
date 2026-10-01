"""Shared lifecycle for sandboxes: create, track, delete.

Sandboxes are billed per second while they exist. Everything here is built so that a
sandbox is deleted on the happy path *and* on errors/signals, and recorded in the local
:class:`~agent_sandbox.state.StateStore` so it can still be found if the process dies.
"""

import sys
import time
from typing import Any, Dict, Optional, Union

# pyrefly: ignore [missing-import]
import agentplatform

from .client import get_agent_client, get_runtime_name, project_root
from .state import StateStore, TrackedSandbox, default_ttl_seconds, parse_ttl


def _is_not_found(exc: Exception) -> bool:
    text = str(exc)
    return "404" in text and "NOT_FOUND" in text


class BaseSandbox:
    kind = "base"
    default_display_name = "sandbox"

    def __init__(
        self,
        runtime_name: Optional[str] = None,
        display_name: Optional[str] = None,
        ttl: Union[str, int, None] = None,
        client: Optional[agentplatform.Client] = None,
        tracker: Optional[StateStore] = None,
    ):
        self.client = client or get_agent_client()
        self.runtime_name = runtime_name or get_runtime_name()
        self.display_name = display_name or self.default_display_name
        self.ttl_seconds = parse_ttl(ttl if ttl is not None else default_ttl_seconds())
        self.tracker = tracker
        self.sandbox_name: Optional[str] = None

    @property
    def ttl(self) -> str:
        return f"{self.ttl_seconds}s"

    # -- hooks for subclasses -------------------------------------------------
    def _spec(self) -> Dict[str, Any]:
        raise NotImplementedError

    def _extra_config(self) -> Dict[str, Any]:
        return {}

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> "BaseSandbox":
        config: Dict[str, Any] = {
            "display_name": self.display_name,
            "ttl": self.ttl,
            "wait_for_completion": True,
            **self._extra_config(),
        }
        started = time.time()
        operation = self.client.sandboxes.create(
            name=self.runtime_name, spec=self._spec(), config=config
        )
        if operation.error or operation.response is None:
            raise RuntimeError(f"Failed to create {self.kind} sandbox: {operation}")
        self.sandbox_name = operation.response.name
        if self.tracker is not None:
            try:
                self.tracker.add(
                    TrackedSandbox(
                        name=self.sandbox_name,
                        kind=self.kind,
                        display_name=self.display_name,
                        runtime=self.runtime_name,
                        created_at=started,
                        ttl_seconds=self.ttl_seconds,
                        project_dir=str(project_root()),
                    )
                )
            except Exception:
                self.close()  # never leave a billed sandbox we cannot track
                raise
        return self

    def close(self) -> bool:
        """Delete the sandbox. Returns True when it is gone (including already-expired)."""
        if not self.sandbox_name:
            return True
        name = self.sandbox_name
        try:
            self.client.sandboxes.delete(name=name)
        except Exception as exc:  # noqa: BLE001 - SDK raises several error types
            if not _is_not_found(exc):
                print(f"Warning: failed to delete sandbox {name}: {exc}", file=sys.stderr)
                return False
        if self.tracker is not None:
            self.tracker.remove(name)
        self.sandbox_name = None
        return True

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @classmethod
    def attach(
        cls,
        sandbox_name: str,
        runtime_name: Optional[str] = None,
        client: Optional[agentplatform.Client] = None,
        tracker: Optional[StateStore] = None,
    ):
        """Wrap an already-running sandbox (e.g. one created by ``agent-sandbox start``)."""
        instance = cls(runtime_name=runtime_name, client=client, tracker=tracker)
        instance.sandbox_name = sandbox_name
        return instance

    def _require_running(self) -> str:
        if not self.sandbox_name:
            raise RuntimeError("Sandbox is not running.")
        return self.sandbox_name
