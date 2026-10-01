import sys
from pathlib import Path

# Ensure src/ is importable
repo_root = Path(__file__).resolve().parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agent_sandbox import ShellSandbox, load_environment

load_environment(repo_root / ".env")

output_dir = repo_root / "results-shell"

print("Launching CMEK Shell Sandbox...")
with ShellSandbox(display_name="demo-shell-sandbox") as sandbox:
    print("Shell sandbox ready:", sandbox.sandbox_name)

    sandbox.execute_bash("mkdir -p /workspace/input /workspace/output")
    sandbox.upload_file(b"Hello from secure CMEK sandbox!\n", "/workspace/input/input.txt")

    script = b"""#!/bin/bash
set -euo pipefail
tr '[:lower:]' '[:upper:]' < /workspace/input/input.txt > /workspace/output/output.txt
tar -czf /workspace/output/archive.tar.gz -C /workspace/output output.txt
"""
    sandbox.upload_file(script, "/workspace/process.sh")
    res = sandbox.execute_bash("bash /workspace/process.sh")
    print("Executed script with result:", res)

    listing = sandbox.execute_bash("ls -lh /workspace/output")
    print("Remote directory contents:\n", listing["stdout"])

    sandbox.download_file("/workspace/output/output.txt", output_dir / "output.txt")
    sandbox.download_file("/workspace/output/archive.tar.gz", output_dir / "archive.tar.gz")
    print("Downloaded output text content:\n", (output_dir / "output.txt").read_text(encoding="utf-8"))

print("Shell sandbox deleted successfully.")

