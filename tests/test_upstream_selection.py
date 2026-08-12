import socket

import pytest

import chassis
import proxy


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def no_proxy(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(socket, "create_connection", refuse)


def test_proxy_upstream_defaults_to_openrouter(clean_env):
    assert proxy.upstream_url() == "https://openrouter.ai/api/v1/chat/completions"


def test_proxy_upstream_honors_llm_base_url(clean_env):
    clean_env.setenv("LLM_BASE_URL", "http://host.docker.internal:5000/v1")
    assert proxy.upstream_url() == "http://host.docker.internal:5000/v1/chat/completions"


def test_proxy_upstream_strips_trailing_slash(clean_env):
    clean_env.setenv("LLM_BASE_URL", "http://host.docker.internal:5000/v1/")
    assert proxy.upstream_url() == "http://host.docker.internal:5000/v1/chat/completions"


def test_proxy_key_is_openrouter_key_by_default(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    assert proxy.upstream_api_key() == "sk-real"


def test_proxy_key_is_llm_key_when_llm_base_url_set(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("LLM_BASE_URL", "http://host.docker.internal:5000/v1")
    clean_env.setenv("LLM_API_KEY", "sk-local-key")
    assert proxy.upstream_api_key() == "sk-local-key"


def test_proxy_key_empty_for_no_auth_local_server(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("LLM_BASE_URL", "http://host.docker.internal:5000/v1")
    assert proxy.upstream_api_key() == ""


def test_chassis_requires_a_key_without_llm_base_url(clean_env, no_proxy):
    with pytest.raises(SystemExit):
        chassis.build_client()


def test_chassis_openrouter_mode(clean_env, no_proxy):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("OPENROUTER_MODEL", "some/model")
    client, model = chassis.build_client()
    assert model == "some/model"
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


def test_chassis_llm_mode_without_key(clean_env, no_proxy):
    clean_env.setenv("LLM_BASE_URL", "http://localhost:5000/v1")
    clean_env.setenv("LLM_MODEL", "local-model")
    client, model = chassis.build_client()
    assert model == "local-model"
    assert str(client.base_url).rstrip("/") == "http://localhost:5000/v1"


def test_chassis_llm_model_takes_precedence(clean_env, no_proxy):
    clean_env.setenv("LLM_BASE_URL", "http://localhost:5000/v1")
    clean_env.setenv("LLM_MODEL", "local-model")
    clean_env.setenv("OPENROUTER_MODEL", "some/model")
    _, model = chassis.build_client()
    assert model == "local-model"


def test_chassis_prefers_detected_recorder_over_direct(clean_env, monkeypatch):
    clean_env.setenv("LLM_BASE_URL", "http://localhost:5000/v1")
    clean_env.setenv("OPENROUTER_BASE_URL", "http://recorder:8088/api/v1")
    client, _ = chassis.build_client()
    assert str(client.base_url).rstrip("/") == "http://recorder:8088/api/v1"
