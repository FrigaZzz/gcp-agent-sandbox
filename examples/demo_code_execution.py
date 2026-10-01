import sys
from pathlib import Path

# Ensure src/ is importable
repo_root = Path(__file__).resolve().parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agent_sandbox import CodeExecutionSandbox, get_stdout, load_environment

# Ensure root .env is loaded
load_environment(repo_root / ".env")

python_code = """
import csv
import json
from pathlib import Path

with open("input.csv", newline="", encoding="utf-8") as source:
    rows = list(csv.DictReader(source))

report = {
    "row_count": len(rows),
    "total_amount": sum(float(r["amount"]) for r in rows),
    "status": "PROCESSED_IN_CMEK_SANDBOX"
}

Path("report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(f"Generated report successfully: {report}")
"""

input_data = b"item,amount\nitem_a,15.5\nitem_b,34.5\n"
output_dir = repo_root / "results"

print("Launching CMEK Code Execution Sandbox...")
with CodeExecutionSandbox(display_name="demo-python-sandbox") as sandbox:
    print("Sandbox ready:", sandbox.sandbox_name)

    print("Executing Python script in sandbox with input.csv...")
    response = sandbox.execute(
        code=python_code,
        files=[{
            "name": "input.csv",
            "mimeType": "text/csv",
            "content": input_data,
        }],
    )

    stdout = get_stdout(response)
    if stdout:
        print("Remote stdout:", stdout.strip())

    saved_files = sandbox.save_outputs(response, output_dir)
    print("Downloaded output files:", saved_files)

    report_file = output_dir / "report.json"
    if report_file.exists():
        print("\n--- Local Report Content ---")
        print(report_file.read_text(encoding="utf-8"))

print("Sandbox cleaned up successfully.")

