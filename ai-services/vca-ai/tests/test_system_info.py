import subprocess

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services import system_info


client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test controls its own probe outcome, so the cache can't leak across tests."""
    system_info._cached_system_info = None
    yield
    system_info._cached_system_info = None


def test_system_info_endpoint_reports_probed_engine_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"os": "Darwin 25.6.0 (arm64)", "pythonVersion": "3.13.9", '
                '"device": "mps", "libraries": {"torch": "2.13.0"}, '
                '"models": [{"key": "sam2.segmenter", "repoId": "facebook/sam2-hiera-large", '
                '"revision": "abc123"}]}'
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    response = client.get("/system-info")

    assert response.status_code == 200
    body = response.json()
    assert body["os"] == "Darwin 25.6.0 (arm64)"
    assert body["device"] == "mps"
    assert body["libraries"] == {"torch": "2.13.0"}
    assert body["models"] == [
        {"key": "sam2.segmenter", "repoId": "facebook/sam2-hiera-large", "revision": "abc123"}
    ]


def test_system_info_falls_back_when_the_engine_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="uv", timeout=20)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    response = client.get("/system-info")

    assert response.status_code == 200
    body = response.json()
    assert body["device"] == "알 수 없음"
    assert body["libraries"] == {}
    assert body["models"] == []


def test_system_info_probe_result_is_cached_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def counting_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"os": "x", "pythonVersion": "3.13", "device": "cpu", "libraries": {}, "models": []}',
        )

    monkeypatch.setattr(subprocess, "run", counting_run)

    system_info.get_system_info()
    system_info.get_system_info()

    assert call_count == 1
