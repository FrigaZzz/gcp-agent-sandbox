"""Create a reusable shell sandbox template and persist SANDBOX_TEMPLATE_NAME.

Equivalent to `agent-sandbox templates create`.
"""

import sys
from pathlib import Path

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_sandbox import create_shell_template, get_runtime_name, load_environment, persist_env_var

load_environment()

runtime_name = get_runtime_name()
print(f"Creating shell sandbox template under runtime {runtime_name}...")
template_name = create_shell_template(runtime_name=runtime_name)
print("Template created successfully:", template_name)
print("SANDBOX_TEMPLATE_NAME saved to", persist_env_var("SANDBOX_TEMPLATE_NAME", template_name))
