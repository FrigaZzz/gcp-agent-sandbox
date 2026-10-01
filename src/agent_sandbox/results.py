"""Parsing of sandbox responses and safe saving of returned files.

Observed behaviour of ``sandboxes.execute_code`` (SDK 2.3.0, v1beta1):

* ``outputs`` is a list of chunks. One ``application/json`` chunk carries
  ``{"exit_status_int", "msg_out", "msg_err"}``; every other chunk is a file whose
  name is in ``chunk.metadata.attributes["file_name"]`` and whose bytes are in ``chunk.data``.
* Only files that were **created** during the call are returned (not uploaded files, not
  files that were merely touched or rewritten). Names may contain sub-directories.
* A Python exception yields ``exit_status_int`` 106 and the traceback in ``msg_err``.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional


@dataclass
class OutputFile:
    name: str
    data: bytes
    mime_type: Optional[str] = None


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    exit_status: int = 0
    files: List[OutputFile] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_status == 0

    def to_dict(self, saved: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_status": self.exit_status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "files": [
                {"name": f.name, "bytes": len(f.data), "saved_to": (saved or {}).get(f.name)}
                for f in self.files
            ],
        }


def parse_response(response: Any) -> ExecResult:
    """Turn an ``ExecuteSandboxEnvironmentResponse`` into an :class:`ExecResult`."""
    result = ExecResult()
    for chunk in getattr(response, "outputs", None) or []:
        attributes = chunk.metadata.attributes if getattr(chunk, "metadata", None) else None
        file_name = (attributes or {}).get("file_name")
        if file_name:
            if isinstance(file_name, bytes):
                file_name = file_name.decode("utf-8")
            result.files.append(
                OutputFile(name=file_name, data=chunk.data or b"", mime_type=chunk.mime_type)
            )
        elif chunk.mime_type == "application/json":
            try:
                message = json.loads((chunk.data or b"{}").decode("utf-8"))
            except json.JSONDecodeError:
                continue
            result.stdout += message.get("msg_out") or ""
            result.stderr += message.get("msg_err") or ""
            result.exit_status = int(message.get("exit_status_int") or result.exit_status)
        elif chunk.mime_type == "text/plain":
            result.stdout += (chunk.data or b"").decode("utf-8", errors="replace")
    return result


def safe_target(directory: Path, name: str) -> Path:
    """Resolve ``name`` under ``directory`` or raise ``ValueError`` if it could escape."""
    if "\\" in name or "\0" in name:
        raise ValueError(f"Security: insecure filename in output: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts or any(p in {"", ".", ".."} for p in pure.parts):
        raise ValueError(f"Security: insecure filename in output: {name!r}")

    root = directory.resolve()
    target = root.joinpath(*pure.parts)
    # Reject symlinks anywhere between the root and the target.
    probe = root
    for part in pure.parts:
        probe = probe / part
        if probe.is_symlink():
            raise ValueError(f"Security: symlink target rejected: {probe}")
    if os.path.commonpath([root, target.resolve()]) != str(root):
        raise ValueError(f"Security: output escapes target directory: {name!r}")
    return target


def save_files(files: List[OutputFile], directory: Path) -> List[Path]:
    """Write returned files under ``directory`` (sub-directories allowed, escapes rejected)."""
    directory.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []
    for item in files:
        target = safe_target(directory, item.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.data)
        saved.append(target)
    return saved
