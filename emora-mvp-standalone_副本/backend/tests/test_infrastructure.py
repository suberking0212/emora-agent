import os

import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from sqlalchemy import create_engine, text

from backend.agent import EmoraAgent, EmoraAgentGraph
from backend.app import create_app
from backend.config import Settings
from backend.storage import Database


def _settings(database_url: str) -> Settings:
    return Settings(database_url=database_url)


def test_health_and_frontend_resources_are_reachable(tmp_path):
    app = create_app(_settings(f"sqlite:///{tmp_path / 'app.db'}"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200


def test_graph_builds_and_compiles_without_model_call():
    class Provider:
        pass

    graph = EmoraAgentGraph(EmoraAgent(Provider()))
    assert graph.graph is not None


def test_sqlite_connection_is_reachable(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'probe.db'}")
    with database.engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    database.close()


def test_configured_provider_endpoint_is_reachable():
    base_url = os.getenv("EMORA_TEST_API_BASE")
    api_key = os.getenv("EMORA_TEST_API_KEY")
    if not base_url or not api_key:
        pytest.skip("analysis endpoint connectivity is not configured")
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=10, max_retries=0)
    try:
        client.models.list()
    finally:
        client.close()


def test_configured_qwen_guard_endpoint_is_reachable():
    base_url = os.getenv("EMORA_TEST_QWEN_GUARD_BASE_URL")
    if not base_url:
        pytest.skip("Qwen3Guard endpoint connectivity is not configured")
    client = OpenAI(
        base_url=base_url,
        api_key=os.getenv("EMORA_TEST_QWEN_GUARD_API_KEY", "EMPTY"),
        timeout=10,
        max_retries=0,
    )
    try:
        client.models.list()
    finally:
        client.close()
