import json
from pathlib import Path
import pytest

from agent_sandbox.code_execution import CodeExecutionSandbox

pytestmark = pytest.mark.e2e


def test_code_execution_sandbox_e2e(
    runtime_name: str,
    sample_csv_content: bytes,
    sample_analysis_code: str,
    tmp_path: Path,
):
    """End-to-end test executing Python script with file input in a CMEK sandbox."""
    output_dir = tmp_path / "sandbox_results"

    with CodeExecutionSandbox(
        runtime_name=runtime_name,
        display_name="pytest-e2e-worker",
    ) as sandbox:
        assert sandbox.sandbox_name is not None
        assert "sandboxEnvironments/" in sandbox.sandbox_name

        response = sandbox.execute(
            code=sample_analysis_code,
            files=[
                {
                    "name": "transactions.csv",
                    "mimeType": "text/csv",
                    "content": sample_csv_content,
                }
            ],
        )

        saved_files = sandbox.save_outputs(response, output_dir)
        assert len(saved_files) >= 1

        summary_file = output_dir / "analysis_summary.json"
        assert summary_file.exists()

        data = json.loads(summary_file.read_text(encoding="utf-8"))
        assert data["status"] == "ANALYSIS_COMPLETE"
        assert data["transaction_count"] == 5
        assert data["total_revenue"] == 700.0
        assert "Engineering" in data["by_department"]
        assert data["by_department"]["Engineering"] == 470.0


def test_code_execution_error_handling(runtime_name: str):
    """Verify that runtime errors in sandbox raise RuntimeError and clean up properly."""
    failing_code = """
raise ValueError("Deliberate test error for error handling assertion")
"""
    with pytest.raises(RuntimeError) as exc_info:
        with CodeExecutionSandbox(
            runtime_name=runtime_name,
            display_name="pytest-error-test",
        ) as sandbox:
            sandbox.execute(code=failing_code)

    assert "Deliberate test error" in str(exc_info.value)


def test_sandbox_run_command_and_stdout(runtime_name: str):
    """Verify that run_command executes shell commands and returns stdout cleanly."""
    with CodeExecutionSandbox(
        runtime_name=runtime_name,
        display_name="pytest-shell-command-test",
    ) as sandbox:
        # Test direct Python code stdout extraction
        stdout_py = sandbox.execute_code("print('TEST_PYTHON_OUTPUT_123')")
        assert "TEST_PYTHON_OUTPUT_123" in stdout_py

        # Test shell command execution inside sandbox container
        stdout_sh = sandbox.run_command("pwd && whoami")
        assert "/home/bard" in stdout_sh
        assert "root" in stdout_sh



def test_file_roundtrip_and_bash_exit_status(runtime_name: str):
    """put/fetch round-trip bytes exactly; run_bash reports the command's own exit status."""
    payload = bytes(range(256)) * 10
    with CodeExecutionSandbox(runtime_name=runtime_name, display_name="asbx-pytest-roundtrip", ttl="300s") as sandbox:
        sandbox.put("nested/blob.bin", payload)
        assert sandbox.fetch("nested/blob.bin").data == payload
        archive = sandbox.fetch("nested")
        assert archive.name == "nested.tar.gz" and len(archive.data) > 0

        failing = sandbox.run_bash("echo out; echo err >&2; exit 4")
        assert (failing.exit_status, failing.stdout.strip(), failing.stderr.strip()) == (4, "out", "err")
