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
    # An empty value selects the direct upstream, which is what the existing
    # provider-selection tests below are about.
    monkeypatch.setenv("LLM_SOCKET_PATH", "")
    return monkeypatch


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


def test_chassis_requires_a_key_without_llm_base_url(clean_env):
    with pytest.raises(SystemExit):
        chassis.build_client()


def test_chassis_openrouter_mode(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("OPENROUTER_MODEL", "some/model")
    client, model = chassis.build_client()
    assert model == "some/model"
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


def test_chassis_llm_mode_without_key(clean_env):
    clean_env.setenv("LLM_BASE_URL", "http://localhost:5000/v1")
    clean_env.setenv("LLM_MODEL", "local-model")
    client, model = chassis.build_client()
    assert model == "local-model"
    assert str(client.base_url).rstrip("/") == "http://localhost:5000/v1"


def test_chassis_llm_model_takes_precedence(clean_env):
    clean_env.setenv("LLM_BASE_URL", "http://localhost:5000/v1")
    clean_env.setenv("LLM_MODEL", "local-model")
    clean_env.setenv("OPENROUTER_MODEL", "some/model")
    _, model = chassis.build_client()
    assert model == "local-model"


def test_unset_socket_path_selects_socket_mode(clean_env, tmp_path, monkeypatch):
    # The container case: an unset variable must not fall back to a network the
    # container does not have.
    monkeypatch.delenv("LLM_SOCKET_PATH", raising=False)
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    monkeypatch.setattr(chassis, "SOCKET_WAIT_SECONDS", 0)

    with pytest.raises(chassis.EnvironmentFailure):
        chassis.build_client()


def test_present_socket_builds_a_uds_client(clean_env, tmp_path):
    path = tmp_path / "core.sock"
    path.write_bytes(b"")
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("OPENROUTER_MODEL", "some/model")
    clean_env.setenv("LLM_SOCKET_PATH", str(path))

    client, model = chassis.build_client()

    assert model == "some/model"
    assert str(client.base_url).rstrip("/") == "http://localhost/api/v1"


def test_absent_socket_raises_environment_failure(clean_env, tmp_path, monkeypatch):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("LLM_SOCKET_PATH", str(tmp_path / "missing.sock"))
    monkeypatch.setattr(chassis, "SOCKET_WAIT_SECONDS", 0)

    with pytest.raises(chassis.EnvironmentFailure):
        chassis.build_client()


def test_wait_for_socket_returns_true_once_the_path_exists(tmp_path):
    path = tmp_path / "core.sock"
    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        path.write_bytes(b"")

    assert chassis.wait_for_socket(str(path), timeout=5, sleep=fake_sleep) is True
    assert len(calls) == 1


def test_wait_for_socket_gives_up_at_the_timeout(tmp_path):
    assert (
        chassis.wait_for_socket(str(tmp_path / "never"), timeout=0, sleep=lambda s: None) is False
    )
