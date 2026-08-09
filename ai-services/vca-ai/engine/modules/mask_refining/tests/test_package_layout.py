from pathlib import Path


def test_top_level_runtime_files_stay_limited_to_facade_and_cli() -> None:
    package_root = Path(__file__).parents[1]
    runtime_files = sorted(
        path.name for path in package_root.glob("*.py") if path.name != "__init__.py"
    )

    assert runtime_files == ["refinement_cli.py"]
