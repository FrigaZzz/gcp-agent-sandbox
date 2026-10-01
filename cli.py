"""Source-checkout launcher for the `agent-sandbox` CLI (no install needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

try:
    from agent_sandbox.cli import main
except ModuleNotFoundError as e:
    if "agentplatform" in str(e):
        print(
            "\n[Error] The 'google-cloud-aiplatform[agent_engines]' package is not installed in this Python environment.\n"
            "Activate the sandbox virtual environment first:\n\n"
            "  source .venv-sandbox/bin/activate\n"
            "  python cli.py --help\n"
        )
        sys.exit(1)
    raise

if __name__ == "__main__":
    sys.exit(main())
