"""Local ledger of sandboxes started through this package.

Sandboxes are billed per second while they exist, so every sandbox created by the
CLI is recorded here. That lets ``stop`` / ``cleanup`` (and the Claude Code
SessionEnd hook) find and delete them even after the process that created them
is gone. The server-side TTL remains the final backstop.
"""

import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional

DEFAULT_TTL_SECONDS = 900
DEFAULT_MAX_TTL_SECONDS = 3600
DISPLAY_NAME_PREFIX = "asbx-"

_TTL_RE = re.compile(r"^(\d+)\s*([smh]?)$")


def parse_ttl(value: "str | int", max_seconds: Optional[int] = None) -> int:
    """Parse ``900``, ``900s``, ``15m`` or ``1h`` into seconds and enforce the cap.

    The cap is ``max_seconds`` or ``$AGENT_SANDBOX_MAX_TTL`` (default 3600).
    """
    text = str(value).strip().lower()
    match = _TTL_RE.match(text)
    if not match:
        raise ValueError(f"Invalid TTL {value!r}; use e.g. 900, 900s, 15m or 1h")
    seconds = int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    cap = max_seconds or int(os.environ.get("AGENT_SANDBOX_MAX_TTL", DEFAULT_MAX_TTL_SECONDS))
    if seconds <= 0:
        raise ValueError("TTL must be positive")
    if seconds > cap:
        raise ValueError(
            f"TTL {seconds}s exceeds the cap of {cap}s (sandboxes are billed per second). "
            "Raise AGENT_SANDBOX_MAX_TTL if you really need longer."
        )
    return seconds


def default_ttl_seconds() -> int:
    return int(os.environ.get("AGENT_SANDBOX_DEFAULT_TTL", DEFAULT_TTL_SECONDS))


@dataclass
class TrackedSandbox:
    name: str
    kind: str
    display_name: str
    runtime: str
    created_at: float
    ttl_seconds: int
    project_dir: str = ""

    @property
    def short_id(self) -> str:
        return self.name.rsplit("/", 1)[-1]

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl_seconds

    @property
    def age_seconds(self) -> int:
        return int(time.time() - self.created_at)


def default_state_path() -> Path:
    override = os.environ.get("AGENT_SANDBOX_STATE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agent-sandbox" / "state.json"


class StateStore:
    """JSON ledger with file locking and atomic writes (safe for concurrent CLI calls)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or default_state_path()

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".lock")
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> Dict:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data.setdefault("sandboxes", {})
        data.setdefault("current", None)
        return data

    def _write(self, data: Dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp, self.path)

    def add(self, sandbox: TrackedSandbox, make_current: bool = True) -> None:
        with self._locked():
            data = self._read()
            data["sandboxes"][sandbox.name] = asdict(sandbox)
            if make_current:
                data["current"] = sandbox.name
            self._write(data)

    def remove(self, name: str) -> None:
        with self._locked():
            data = self._read()
            data["sandboxes"].pop(name, None)
            if data["current"] == name:
                data["current"] = None
            self._write(data)

    def list(self) -> List[TrackedSandbox]:
        with self._locked():
            data = self._read()
        return [TrackedSandbox(**item) for item in data["sandboxes"].values()]

    def current(self) -> Optional[str]:
        with self._locked():
            return self._read()["current"]

    def set_current(self, name: Optional[str]) -> None:
        with self._locked():
            data = self._read()
            data["current"] = name
            self._write(data)

    def resolve(self, ref: Optional[str] = None) -> TrackedSandbox:
        """Resolve a full name, a short id, or (when ``ref`` is None) the current sandbox."""
        tracked = self.list()
        if ref is None:
            current = self.current()
            if current:
                ref = current
            elif len(tracked) == 1:
                return tracked[0]
            elif not tracked:
                raise LookupError("No tracked sandbox. Start one with `agent-sandbox start`.")
            else:
                raise LookupError(
                    "Several sandboxes are tracked and none is current; pass --sandbox. "
                    "See `agent-sandbox status`."
                )
        matches = [t for t in tracked if t.name == ref or t.short_id == ref]
        if not matches:
            raise LookupError(f"Sandbox {ref!r} is not tracked. See `agent-sandbox status`.")
        return matches[0]
