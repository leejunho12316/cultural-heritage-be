from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from modules.orchestration import startup
from modules.orchestration.stage_execution import StartupStageRunners
from modules.storage import (
    RdbStorageConfig,
    StoragePersistenceError,
    StorageWriteRequest,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from modules.orchestration.stage_execution import ProjectStageRequest


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(b"image")


def _successful_cli(arguments: tuple[str, ...]) -> int:
    _ = arguments
    return 0


def _failing_cli(arguments: tuple[str, ...]) -> int:
    _ = arguments
    return 9


def _successful_project_runner(request: ProjectStageRequest) -> int:
    _ = request
    return 0


def _runners() -> StartupStageRunners:
    return StartupStageRunners(
        preprocessing=_successful_cli,
        rough_masking=_successful_project_runner,
        visual_cue_generation=_successful_project_runner,
        rag=_successful_project_runner,
        prompt_generating=_successful_project_runner,
        mask_refining=_successful_cli,
        anomaly_grouping=_successful_project_runner,
        report_generating=_successful_project_runner,
    )


class _RecordingWriter:
    def __init__(self) -> None:
        self.requests: list[StorageWriteRequest] = []

    def persist(self, request: StorageWriteRequest) -> None:
        self.requests.append(request)


def test_request_uses_environment_url_for_rdb_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an RDB request whose database URL is supplied by the CLI environment.
    image_root = tmp_path / "inputs"
    _write_image(image_root / "source.jpg")
    artifact_id = uuid4()
    database_url = "postgresql://vca:secret@db/vca"
    monkeypatch.setenv("VCA_DATABASE_URL", database_url)

    # When: the startup CLI request is parsed.
    captured: list[RdbStorageConfig] = []

    def storage_writer_factory(config: RdbStorageConfig) -> _RecordingWriter:
        captured.append(config)
        return _RecordingWriter()

    exit_code = startup.run(
        (
            "rdb-project",
            str(image_root),
            "--dry-run",
            "--storage-mode",
            "rdb",
            "--artifact-id",
            str(artifact_id),
        ),
        workspace_root=tmp_path,
        stage_runners=_runners(),
        storage_writer_factory=storage_writer_factory,
    )
    output = capsys.readouterr()

    # Then: it creates a typed RDB configuration without exposing the URL in output.
    assert exit_code == 0
    assert captured == [
        RdbStorageConfig(
            artifact_id=artifact_id,
            database_url=database_url,
        )
    ]
    assert database_url not in output.out
    assert database_url not in output.err


def test_filesystem_default_ignores_environment_database_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the environment has an RDB URL but storage mode is omitted.
    image_root = tmp_path / "inputs"
    _write_image(image_root / "source.jpg")
    database_url = "postgresql://vca:secret@db/vca"
    monkeypatch.setenv("VCA_DATABASE_URL", database_url)
    captured: list[RdbStorageConfig] = []

    def storage_writer_factory(config: RdbStorageConfig) -> _RecordingWriter:
        captured.append(config)
        return _RecordingWriter()

    # When: startup runs with the default filesystem storage mode.
    exit_code = startup.run(
        ("filesystem-project", str(image_root), "--dry-run"),
        workspace_root=tmp_path,
        stage_runners=_runners(),
        storage_writer_factory=storage_writer_factory,
    )
    output = capsys.readouterr()

    # Then: no RDB writer is constructed and the URL stays out of CLI output.
    assert exit_code == 0
    assert captured == []
    assert database_url not in output.out
    assert database_url not in output.err


def test_startup_rejects_rdb_mode_without_an_artifact_id(tmp_path: Path) -> None:
    # Given: an image input with RDB mode but no artifact identifier.
    image_root = tmp_path / "inputs"
    _write_image(image_root / "source.jpg")

    # When: startup executes the malformed request.
    exit_code = startup.run(
        ("rdb-project", str(image_root), "--storage-mode", "rdb"),
        workspace_root=tmp_path,
    )

    # Then: it returns the established startup configuration failure code.
    assert exit_code == 2


def test_startup_writes_only_fe_startup_snapshot_in_rdb_mode(
    tmp_path: Path,
) -> None:
    # Given: RDB mode, successful stage fakes, and a recording storage writer.
    image_root = tmp_path / "inputs"
    _write_image(image_root / "source.jpg")
    writer = _RecordingWriter()

    def storage_writer_factory(config: RdbStorageConfig) -> _RecordingWriter:
        assert config.artifact_id == UUID("12345678-1234-5678-1234-567812345678")
        return writer

    # When: startup reaches its filesystem receipt and RDB persistence boundary.
    exit_code = startup.run(
        (
            "rdb-project",
            str(image_root),
            "--dry-run",
            "--device",
            "auto",
            "--storage-mode",
            "rdb",
            "--artifact-id",
            "12345678-1234-5678-1234-567812345678",
            "--db-url",
            "postgresql://vca:secret@db/vca",
        ),
        workspace_root=tmp_path,
        stage_runners=_runners(),
        storage_writer_factory=storage_writer_factory,
    )

    # Then: filesystem remains primary while one typed startup snapshot is dual-written.
    assert exit_code == 0
    assert (
        tmp_path / "output" / "result" / "rdb-project" / "receipts" / "startup.json"
    ).is_file()
    assert len(writer.requests) == 1
    write_request = writer.requests[0]
    assert write_request.legacy_project_name == "rdb-project"
    assert write_request.requested_device == "auto"
    assert write_request.resolved_device is None
    assert write_request.current_stage is None
    assert write_request.progress_percent == 100
    assert write_request.config_json == {
        "input_image_folder": str(image_root.resolve()),
        "output_root": str(
            tmp_path / "output" / "result" / "rdb-project"
        ),
        "image_count": 1,
    }
    assert not hasattr(write_request, "stage_runs")


def test_startup_persists_failed_stage_progress_without_receipt_payloads(
    tmp_path: Path,
) -> None:
    # Given: RDB mode and a preprocessing failure before later stages can run.
    image_root = tmp_path / "inputs"
    _write_image(image_root / "source.jpg")
    writer = _RecordingWriter()

    def storage_writer_factory(config: RdbStorageConfig) -> _RecordingWriter:
        _ = config
        return writer

    # When: startup stops at the failed stage and reaches its RDB boundary.
    exit_code = startup.run(
        (
            "rdb-project",
            str(image_root),
            "--storage-mode",
            "rdb",
            "--artifact-id",
            "12345678-1234-5678-1234-567812345678",
            "--db-url",
            "postgresql://vca:secret@db/vca",
        ),
        workspace_root=tmp_path,
        stage_runners=StartupStageRunners(preprocessing=_failing_cli),
        storage_writer_factory=storage_writer_factory,
    )

    # Then: the snapshot contains only failed-stage progress, not the receipt payload.
    assert exit_code == 9
    assert len(writer.requests) == 1
    write_request = writer.requests[0]
    assert write_request.status == "failed"
    assert write_request.current_stage == "preprocessing"
    assert write_request.progress_percent == 0
    assert not hasattr(write_request, "stage_runs")


def test_startup_returns_failure_code_when_rdb_persistence_fails(
    tmp_path: Path,
) -> None:
    # Given: a completed filesystem run and an RDB writer failure.
    image_root = tmp_path / "inputs"
    _write_image(image_root / "source.jpg")

    class FailingWriter:
        def persist(self, request: StorageWriteRequest) -> None:
            _ = request
            message = "assessment run persistence failed"
            raise StoragePersistenceError(message)

    def storage_writer_factory(config: RdbStorageConfig) -> FailingWriter:
        _ = config
        return FailingWriter()

    # When: the RDB writer cannot commit the run snapshot.
    exit_code = startup.run(
        (
            "rdb-project",
            str(image_root),
            "--dry-run",
            "--storage-mode",
            "rdb",
            "--artifact-id",
            str(uuid4()),
            "--db-url",
            "postgresql://vca:secret@db/vca",
        ),
        workspace_root=tmp_path,
        stage_runners=_runners(),
        storage_writer_factory=storage_writer_factory,
    )

    # Then: startup fails closed but retains the filesystem receipt.
    assert exit_code == 2
    assert (
        tmp_path / "output" / "result" / "rdb-project" / "receipts" / "startup.json"
    ).is_file()
