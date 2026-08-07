from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
VCA_AI_ROOT = REPO_ROOT / "ai-services" / "vca-ai"


def test_vca_image_when_built_uses_bundled_engine() -> None:
    # Given: the vca-ai Docker build context contains a vendored engine
    dockerfile = (VCA_AI_ROOT / "Dockerfile").read_text(encoding="utf-8")
    engine_pyproject = VCA_AI_ROOT / "engine" / "pyproject.toml"
    inventory = VCA_AI_ROOT / "engine" / "models" / "inventory" / "model_inventory.json"

    # When: the packaging contract is inspected
    # Then: Docker copies the engine into the in-container /vca_v2 contract
    assert engine_pyproject.is_file()
    assert inventory.is_file()
    assert "COPY engine/ /vca_v2/" in dockerfile
    assert "ENV ENGINE_ROOT=/vca_v2" in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/vca-uv-env" in dockerfile
    assert "UV_CACHE_DIR=/opt/vca-uv-cache" in dockerfile
    assert "UV_FROZEN=true" in dockerfile
    assert (
        'RUN uv sync --project "$ENGINE_ROOT" --group vision --no-install-project'
        in dockerfile
    )


def test_compose_when_started_does_not_require_sibling_vca_checkout() -> None:
    # Given: Windows users clone only BE and FE repos
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    override = (REPO_ROOT / "docker-compose.local-vca.override.yml").read_text(
        encoding="utf-8"
    )

    # When: compose files are inspected
    # Then: no service bind-mounts ../vca_v2 from the host anymore
    assert "../vca_v2" not in compose
    assert "../vca_v2" not in override
    assert "VCA_ENGINE_ROOT: /vca_v2" in compose
    assert "UV_PROJECT_ENVIRONMENT: /opt/vca-uv-env" in compose
    assert "VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES:" in compose
    assert "vca_uv_env:/opt/vca-uv-env" not in compose
    assert "vca_uv_env:" not in compose
    assert "vca_uv_cache:/opt/vca-uv-cache" in compose
    assert "vca_model_cache:/opt/vca-models" in compose


def test_windows_launcher_when_used_starts_frontend_with_powershell_syntax() -> None:
    # Given: Windows users run the bundled PowerShell launcher
    script = (REPO_ROOT / "scripts" / "launch-real-vca-env.ps1").read_text(
        encoding="utf-8"
    )

    # When: the frontend launch command is inspected
    # Then: it uses PowerShell Set-Location, not cmd.exe-only cd /d
    assert "Set-Location -LiteralPath" in script
    assert "cd /d" not in script
    assert "VCA_LOCAL_ALLOW_UNVERIFIED_MODEL_HASHES=true" in script
    assert "docker compose up --build -d" in script
    assert 'uv run --project /vca_v2 python -c "import torch; import torchvision; print(torch.__version__); print(torch.cuda.is_available())"' in script
    assert "vca_model_cache" not in script
    assert "docker volume rm" not in script


def test_compose_when_postgres_is_published_binds_to_localhost_only() -> None:
    # Given: local development exposes Postgres for host tools
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    # When: the Postgres port mapping is inspected
    # Then: default credentials are not exposed on every host interface
    assert '"127.0.0.1:5432:5432"' in compose
    assert '"5432:5432"' not in compose


def test_windows_launcher_when_real_mode_creates_document_corpus_folder() -> None:
    # Given: real VCA mode needs a mounted RAG document corpus directory
    script = (REPO_ROOT / "scripts" / "launch-real-vca-env.ps1").read_text(
        encoding="utf-8"
    )

    # When: the launcher prepares shared directories
    # Then: the corpus mount source exists before Docker Compose starts
    assert "shared\\vca\\document-corpus" in script


def test_byod_corpus_script_when_used_prepares_pdf_corpus_for_rag() -> None:
    # Given: users bring RAG documents from Google Drive as a local folder or zip
    script_path = REPO_ROOT / "scripts" / "prepare-rag-corpus.ps1"
    script = script_path.read_text(encoding="utf-8")

    # When: the script contract is inspected
    # Then: it targets the mounted corpus folder and copies only PDF corpus inputs
    assert script.lstrip().startswith("param(")
    assert "shared\\vca\\document-corpus" in script
    assert 'Filter "*.pdf"' in script
    assert "nrich_preservation_manifest.jsonl" in script
    assert "Expand-Archive" in script
    assert "Duplicate PDF filename detected" in script
