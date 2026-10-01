import sys
from pathlib import Path

# Ensure src/ is importable
repo_root = Path(__file__).resolve().parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agent_sandbox import get_agent_client, get_runtime_info, get_runtime_name, load_environment

load_environment(repo_root / ".env")

client = get_agent_client()
runtime_name = get_runtime_name()

print("=== Agent Runtime Instance ===")
info = get_runtime_info(runtime_name, client=client)
print("Name:", info["name"])
print("Display Name:", info["display_name"])
print("Encryption Spec:", info["encryption_spec"])

print("\n=== Active Sandboxes ===")
sandboxes = list(client.sandboxes.list(name=runtime_name))
if not sandboxes:
    print("No active sandboxes.")
for sandbox in sandboxes:
    print(f"  - {sandbox.name} [{sandbox.state}]")

print("\n=== Sandbox Templates ===")
templates = list(client.sandboxes.templates.list(name=runtime_name))
if not templates:
    print("No templates.")
for template in templates:
    print(f"  - {template.name} [{template.state}]")

