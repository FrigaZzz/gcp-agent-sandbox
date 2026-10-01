import os
from pathlib import Path
from typing import Any, Dict, Optional

# pyrefly: ignore [missing-import]
import agentplatform

from .client import get_agent_client, load_environment, persist_env_var


def create_cmek_runtime(
    display_name: str = "sandbox-cmek-runtime",
    kms_key_name: Optional[str] = None,
    client: Optional[agentplatform.Client] = None,
    update_env_file: bool = True,
    env_path: Optional[Path] = None,
) -> str:
    """Create a parent Agent Runtime instance protected by CMEK.

    Every call creates a *new* instance, so run it once and reuse ``AGENT_RUNTIME_NAME``.

    Returns:
        The full resource name of the created Agent Runtime instance.
    """
    load_environment()
    cli = client or get_agent_client()
    key = kms_key_name or os.environ.get("KMS_KEY_NAME")

    if not key:
        raise ValueError(
            "KMS Key Name not specified. Set KMS_KEY_NAME in .env or pass kms_key_name."
        )

    runtime = cli.runtimes.create(
        config={
            "display_name": display_name,
            "encryption_spec": {"kms_key_name": key},
        },
    )

    runtime_name = runtime.api_resource.name

    if update_env_file:
        persist_env_var("AGENT_RUNTIME_NAME", runtime_name, env_path)

    return runtime_name


def get_runtime_info(
    runtime_name: str,
    client: Optional[agentplatform.Client] = None,
) -> Dict[str, Any]:
    """Retrieve details and encryption specification of a runtime instance."""
    cli = client or get_agent_client()
    runtime = cli.runtimes.get(name=runtime_name)
    return {
        "name": runtime.api_resource.name,
        "display_name": runtime.api_resource.display_name,
        "encryption_spec": runtime.api_resource.encryption_spec,
    }
