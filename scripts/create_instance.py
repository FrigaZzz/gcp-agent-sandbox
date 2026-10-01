"""Create the CMEK-protected parent Agent Runtime once and persist AGENT_RUNTIME_NAME.

Equivalent to `agent-sandbox runtime create`. Every call creates a NEW instance.
"""

import os
import sys
from pathlib import Path

# Allow running from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_sandbox import create_cmek_runtime, get_runtime_info, load_environment

load_environment()

print(f"Creating CMEK-enabled Runtime in project {os.environ['GOOGLE_CLOUD_PROJECT']} "
      f"({os.environ['GOOGLE_CLOUD_LOCATION']})...")
runtime_name = create_cmek_runtime(
    kms_key_name=os.environ["KMS_KEY_NAME"],
    display_name="sandbox-cmek-runtime",
)
info = get_runtime_info(runtime_name)
print("Runtime created successfully:")
print("  Name:", info["name"])
print("  Encryption Spec:", info["encryption_spec"])
print("AGENT_RUNTIME_NAME saved to the env file.")
