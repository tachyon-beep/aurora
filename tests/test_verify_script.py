from pathlib import Path


def _script():
    return Path("scripts/verify_container.sh").read_text(encoding="utf-8")


def test_verifier_masks_real_upstreams_and_uses_an_isolated_project():
    text = _script()
    assert 'export OPENROUTER_API_KEY="sk-verify-dummy"' in text
    assert 'export LLM_BASE_URL="http://127.0.0.1:1"' in text
    assert 'export LLM_API_KEY=""' in text
    assert 'AURORA_VERIFY_PROJECT="aurora_verify_$$"' in text
    assert 'export COMPOSE_PROJECT_NAME="$AURORA_VERIFY_PROJECT"' in text
    assert "docker compose down -v" not in text


def test_verifier_checks_the_workshop_and_state_contract():
    text = _script()
    for phrase in (
        "agent has the workshop runtime packages",
        "garden contains only two read-only documents",
        "agent state starts empty and is writable",
        "state survives tracked-code recovery",
        "state survives agent container recreation without executing stored code",
        "state marker is absent from other services",
    ):
        assert phrase in text


def test_verifier_cleanup_names_only_its_isolated_volumes():
    text = _script()
    assert '"${COMPOSE_PROJECT_NAME}_state"' in text
    assert '"${COMPOSE_PROJECT_NAME}_diode"' in text
    assert '"${COMPOSE_PROJECT_NAME}_transcripts"' in text
