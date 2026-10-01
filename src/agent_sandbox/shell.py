import base64
import os
import posixpath
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

# pyrefly: ignore [missing-import]
import agentplatform

from .base import BaseSandbox
from .client import get_agent_client, get_runtime_name
from .results import ExecResult, OutputFile

REMOTE_WORKSPACE = "/workspace"


def create_shell_template(
    display_name: str = "shell-sandbox-template",
    runtime_name: Optional[str] = None,
    client: Optional[agentplatform.Client] = None,
) -> str:
    """Create a reusable shell sandbox template and return its full resource name.

    Without an explicit template the service may create a new default template on every
    sandbox ``create()``; deleting a sandbox does not delete its template.
    """
    cli = client or get_agent_client()
    operation = cli.sandboxes.templates.create(
        name=runtime_name or get_runtime_name(),
        display_name=display_name,
        config={
            "default_container_environment": {
                "default_container_category": "DEFAULT_CONTAINER_CATEGORY_SHELL_SANDBOX",
            },
            "wait_for_completion": True,
        },
    )
    if operation.error or operation.response is None:
        raise RuntimeError(f"Template creation failed: {operation}")
    return operation.response.name


class ShellSandbox(BaseSandbox):
    """Shell sandbox (bash). Needs a template; see docs on CMEK limits in preview regions."""

    kind = "shell"
    default_display_name = "shell-worker-sandbox"

    def __init__(self, *args: Any, template_name: Optional[str] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.template_name = template_name or os.environ.get("SANDBOX_TEMPLATE_NAME")

    def _spec(self) -> Dict[str, Any]:
        return {"shell_environment": {}}

    def _extra_config(self) -> Dict[str, Any]:
        if self.template_name:
            return {"sandbox_environment_template": self.template_name}
        return {}

    def run_bash(
        self, command: str, cwd: str = REMOTE_WORKSPACE, timeout: Optional[int] = 60
    ) -> ExecResult:
        """Run a bash command; non-zero exit is reported in the result, not raised."""
        name = self._require_running()
        raw = self.client.sandboxes.execute_bash(
            name=name, command=command, cwd=cwd, timeout=timeout or 60
        )
        return ExecResult(
            stdout=raw.get("stdout", "") or "",
            stderr=raw.get("stderr", "") or "",
            exit_status=int(raw.get("returncode") or 0),
        )

    def execute_bash(self, command: str, cwd: str = REMOTE_WORKSPACE, timeout: int = 60) -> Dict[str, Any]:
        """Execute a bash command in the remote sandbox (raises on non-zero exit)."""
        name = self._require_running()
        result = self.client.sandboxes.execute_bash(
            name=name, command=command, cwd=cwd, timeout=timeout
        )
        if result.get("returncode") != 0:
            raise RuntimeError(f"Bash command failed ({result.get('returncode')}): {result}")
        return result

    # -- files (base64 over bash; suitable for small files only) --------------
    def put(self, remote_path: str, content: bytes) -> None:
        encoded = base64.b64encode(content).decode("ascii")
        parent = posixpath.dirname(remote_path)
        mkdir = f"mkdir -p {shlex.quote(parent)} && " if parent else ""
        self.execute_bash(
            f"{mkdir}printf '%s' {shlex.quote(encoded)} | base64 --decode > {shlex.quote(remote_path)}"
        )

    def fetch(self, remote_path: str) -> OutputFile:
        quoted = shlex.quote(remote_path)
        probe = self.run_bash(f"test -d {quoted}")
        if probe.ok:
            res = self.execute_bash(f"tar czf - -C {quoted} . | base64")
            name = posixpath.basename(remote_path.rstrip("/")) + ".tar.gz"
        else:
            res = self.execute_bash(f"base64 {quoted}")
            name = posixpath.basename(remote_path)
        data = base64.b64decode("".join(res["stdout"].split()), validate=True)
        return OutputFile(name=name, data=data)

    def upload_file(self, content: bytes, remote_path: str) -> None:
        """Upload file content into the remote sandbox filesystem."""
        self.put(remote_path, content)

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file from the remote sandbox filesystem."""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.fetch(remote_path).data)
