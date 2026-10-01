"""Google Cloud Agent Platform Sandbox with CMEK.

Public names are imported lazily so that light-weight entry points (``agent-sandbox cleanup``)
do not pay for importing the Google SDK.
"""

import importlib
from typing import Any

_EXPORTS = {
    "get_agent_client": "client",
    "get_runtime_name": "client",
    "load_environment": "client",
    "persist_env_var": "client",
    "create_cmek_runtime": "runtime",
    "get_runtime_info": "runtime",
    "CodeExecutionSandbox": "code_execution",
    "get_stdout": "code_execution",
    "save_response_outputs": "code_execution",
    "ShellSandbox": "shell",
    "create_shell_template": "shell",
    "ExecResult": "results",
    "parse_response": "results",
    "StateStore": "state",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f".{module}", __name__), name)
