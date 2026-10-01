import json
import mimetypes
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .base import BaseSandbox
from .results import ExecResult, OutputFile, parse_response, save_files

REMOTE_HOME = "/home/bard"
_RC_MARKER = "__ASBX_RC__="


def save_response_outputs(response: Any, directory: Path) -> List[Path]:
    """Parse and save files returned from a sandbox response (raises on remote error)."""
    parsed = parse_response(response)
    if not parsed.ok:
        raise RuntimeError(f"Remote execution error: {parsed.stderr}")
    return save_files(parsed.files, directory)


def get_stdout(response: Any) -> str:
    """Extract standard output text from a sandbox response."""
    return parse_response(response).stdout


class CodeExecutionSandbox(BaseSandbox):
    """Python Code Execution sandbox (works under CMEK; the verified default).

    Use as a context manager for guaranteed cleanup, or ``start()``/``close()`` /
    ``attach()`` when the sandbox must outlive the process.
    """

    kind = "code"
    default_display_name = "python-worker-sandbox"

    def _spec(self) -> Dict[str, Any]:
        return {"code_execution_environment": {"code_language": "LANGUAGE_PYTHON"}}

    # -- raw SDK access (kept for backwards compatibility) --------------------
    def execute(
        self,
        code: str,
        files: Optional[List[Dict[str, Union[str, bytes]]]] = None,
        raise_on_error: bool = True,
    ) -> Any:
        """Execute Python code and return the raw SDK response.

        Raises ``RuntimeError`` (with the remote traceback) on a non-zero exit status
        unless ``raise_on_error`` is False. Writing to stderr alone is not an error.
        """
        name = self._require_running()
        payload: Dict[str, Any] = {"code": code}
        if files:
            payload["files"] = files
        response = self.client.sandboxes.execute_code(name=name, input_data=payload)
        if raise_on_error:
            parsed = parse_response(response)
            if not parsed.ok:
                raise RuntimeError(f"Remote execution error: {parsed.stderr}")
        return response

    # -- structured API -------------------------------------------------------
    def run(
        self,
        code: str,
        files: Optional[List[Dict[str, Union[str, bytes]]]] = None,
    ) -> ExecResult:
        """Execute Python and return stdout, stderr, exit status and created files."""
        return parse_response(self.execute(code, files=files, raise_on_error=False))

    def execute_code(
        self,
        code: str,
        files: Optional[List[Dict[str, Union[str, bytes]]]] = None,
    ) -> str:
        """Execute Python code and return captured stdout (raises on failure)."""
        return get_stdout(self.execute(code, files=files, raise_on_error=True))

    def run_bash(
        self, command: str, cwd: Optional[str] = None, timeout: Optional[int] = None
    ) -> ExecResult:
        """Run a bash command inside the sandbox; ``exit_status`` is the command's own."""
        runner = f"""
import subprocess, sys
try:
    res = subprocess.run(['bash', '-c', {command!r}], capture_output=True, text=True,
                         cwd={cwd!r}, timeout={timeout!r})
    out, err, rc = res.stdout, res.stderr, res.returncode
except subprocess.TimeoutExpired as exc:
    out, err, rc = (exc.stdout or ''), 'timeout expired', 124
if isinstance(out, bytes): out = out.decode('utf-8', 'replace')
sys.stdout.write(out)
sys.stderr.write(err if isinstance(err, str) else '')
sys.stdout.write('\\n{_RC_MARKER}%d' % rc)
"""
        result = self.run(runner)
        if _RC_MARKER in result.stdout:
            body, _, rc = result.stdout.rpartition("\n" + _RC_MARKER)
            result.stdout = body
            result.exit_status = int(rc.strip() or 0)
        return result

    def run_command(self, command: str, cwd: Optional[str] = None) -> str:
        """Run a bash command and return stdout; raises ``RuntimeError`` on non-zero exit."""
        result = self.run_bash(command, cwd=cwd)
        if not result.ok:
            raise RuntimeError(
                f"Command exited with code {result.exit_status}: {result.stderr.strip()}"
            )
        return result.stdout

    # -- files ----------------------------------------------------------------
    def put(self, remote_path: str, content: bytes) -> None:
        """Upload bytes to ``remote_path`` (relative to the sandbox home).

        Files travel as attachments to an execute call, so the path is created
        relative to the working directory (``/home/bard``).
        """
        mime = mimetypes.guess_type(remote_path)[0]
        entry: Dict[str, Union[str, bytes]] = {"name": remote_path, "content": content}
        if mime:
            entry["mimeType"] = mime
        result = self.run("pass", files=[entry])
        if not result.ok:
            raise RuntimeError(f"Upload of {remote_path} failed: {result.stderr}")

    def fetch(self, remote_path: str) -> OutputFile:
        """Download a file (or a directory as ``.tar.gz``) from the sandbox.

        The code-execution API only returns files *created* during a call, so the file
        is copied to a fresh scratch directory, returned, and the copy removed.
        """
        # Must not be a dot-directory: hidden paths are not returned as outputs.
        scratch = f"asbx_dl_{secrets.token_hex(4)}"
        copy_code = f"""
import os, shutil
src, dst = {remote_path!r}, {scratch!r}
os.makedirs(dst)
if os.path.isdir(src):
    shutil.make_archive(os.path.join(dst, os.path.basename(os.path.normpath(src)) or 'dir'), 'gztar', src)
elif os.path.isfile(src):
    shutil.copy(src, dst)
else:
    raise FileNotFoundError(src)
"""
        try:
            result = self.run(copy_code)
        finally:
            try:
                self.run(f"import shutil; shutil.rmtree({scratch!r}, ignore_errors=True)")
            except Exception:  # noqa: BLE001 - cleanup of scratch copy is best effort
                pass
        if not result.ok:
            raise RuntimeError(f"Download of {remote_path} failed: {result.stderr.strip()}")
        if len(result.files) != 1:
            raise RuntimeError(f"Expected one file for {remote_path}, got {len(result.files)}")
        item = result.files[0]
        return OutputFile(name=item.name.split("/", 1)[-1], data=item.data, mime_type=item.mime_type)

    # Friendly aliases used in the README.
    def upload_file(self, remote_path: str, content: bytes) -> None:
        self.put(remote_path, content)

    def download_file(self, remote_path: str) -> bytes:
        return self.fetch(remote_path).data

    def save_outputs(self, response: Any, directory: Path) -> List[Path]:
        """Convenience method to save files from a response."""
        return save_response_outputs(response, directory)
