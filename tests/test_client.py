import os
import pytest
from pathlib import Path

from agent_sandbox.client import get_agent_client, get_runtime_name, load_environment


def test_load_environment(tmp_path: Path):
    """Verify that load_environment parses exports correctly and resolves vars."""
    test_env = tmp_path / ".env"
    test_env.write_text(
        "export TEST_PROJECT='demo-proj'\n"
        "export TEST_LOC='europe-west8'\n"
        "export TEST_NAME='projects/${TEST_PROJECT}/locations/${TEST_LOC}'\n"
    )

    load_environment(test_env)
    assert os.environ.get("TEST_PROJECT") == "demo-proj"
    assert os.environ.get("TEST_LOC") == "europe-west8"
    assert os.environ.get("TEST_NAME") == "projects/demo-proj/locations/europe-west8"


@pytest.mark.e2e
def test_get_agent_client(agent_client):
    """Verify client initialization with valid project and location."""
    assert agent_client is not None
    assert agent_client._api_client.project == os.environ["GOOGLE_CLOUD_PROJECT"]
    assert agent_client._api_client.location == os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west8")


@pytest.mark.e2e
def test_get_runtime_name(runtime_name):
    """Verify runtime resource name matches expected GCP format."""
    assert runtime_name.startswith("projects/")
    assert "reasoningEngines/" in runtime_name
