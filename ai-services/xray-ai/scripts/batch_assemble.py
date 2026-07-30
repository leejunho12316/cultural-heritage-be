from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xray_assembler.batch import (
    _write_csv,
    load_manifest,
    load_mapping,
    prepare_color_root,
    prepare_dataset_root,
    run_batch_assembly,
    run_reference_mask_preview,
    select_samples,
    validate_mapping,
)
from xray_assembler.config import AssemblyConfig
from xray_assembler.image_ops import save_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="manifest의 X-ray 파편과 컬러 전면 이미지를 매핑하여 여러 유물을 일괄 테스트합니다."
    )
    dataset = parser.add_mutually_exclusive_group(required=True)
    dataset.add_argument("--dataset-root", type=Path, help="dataset_manifest.json이 있는 데이터 루트")
    dataset.add_argument("--dataset-zip", type=Path, help="x-ray artifact_with_manifest.zip")
    color = parser.add_mutually_exclusive_group(required=True)
    color.add_argument("--color-root", type=Path, help="컬러 완성본 이미지 폴더")
    color.add_argument("--color-zip", type=Path, help="컬러 완성본 ZIP")
    parser.add_argument("--mapping", type=Path, default=Path("mapping.color_front.json"))
    parser.add_argument("--config", type=Path, default=Path("config.batch_fast.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cache-root",
        type=Path,
        help=(
            "ZIP 입력 해제 캐시 폴더. 미지정 시 output 폴더 내부가 아니라 "
            "output의 상위 폴더 아래 .xray_input_cache를 사용합니다."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["validate", "masks", "assemble"],
        default="assemble",
        help="validate=경로만 검사, masks=컬러 mask만 검토, assemble=전체 조립",
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--all", action="store_true", help="enabled인 21개 sample 전부")
    selector.add_argument("--ids", help="artifact_003,artifact_008 형식")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)

        # Windows의 전통적인 MAX_PATH(260자) 제한을 피하기 위해 입력 ZIP 캐시는
        # 긴 output 폴더 내부에 두지 않는다. 필요하면 --cache-root로 더 짧은
        # 절대 경로를 명시할 수 있다.
        cache = (
            args.cache_root.resolve()
            if args.cache_root is not None
            else (output.parent / ".xray_input_cache").resolve()
        )
        cache.mkdir(parents=True, exist_ok=True)

        dataset_root = prepare_dataset_root(args.dataset_root, args.dataset_zip, cache)
        color_root = prepare_color_root(args.color_root, args.color_zip, cache)
        mapping = load_mapping(args.mapping)
        manifest = load_manifest(dataset_root)

        validation = validate_mapping(dataset_root, color_root, manifest, mapping)
        _write_csv(output / "mapping_validation.csv", validation)
        save_json(output / "mapping_validation.json", validation)
        errors = [row for row in validation if row["status"] != "ok"]
        print(f"mapping validation: {len(validation) - len(errors)}/{len(validation)} OK")
        if errors:
            for row in errors:
                print(f"[MAPPING ERROR] {row['artifact_id']}: {row['error']}", file=sys.stderr)
            return 2
        if args.mode == "validate":
            return 0

        selected_ids = None
        if args.ids:
            selected_ids = {item.strip() for item in args.ids.split(",") if item.strip()}
        samples = select_samples(manifest, mapping, selected_ids, args.all)
        if not samples:
            raise ValueError("선택된 sample이 없습니다. --all 또는 --ids를 지정하거나 defaultTest를 확인하세요.")
        print("selected:", ", ".join(str(s["id"]) for s in samples))

        config = AssemblyConfig.from_json(args.config)
        run_metadata = {
            "mode": args.mode,
            "datasetRoot": str(dataset_root),
            "colorRoot": str(color_root),
            "cacheRoot": str(cache),
            "mapping": str(args.mapping.resolve()),
            "config": str(args.config.resolve()),
            "selectedIds": [str(s["id"]) for s in samples],
            "resolvedConfig": config.to_dict(),
        }
        save_json(output / "run_metadata.json", run_metadata)

        if args.mode == "masks":
            rows = run_reference_mask_preview(samples, color_root, mapping, output, config)
        else:
            rows = run_batch_assembly(
                samples=samples,
                dataset_root=dataset_root,
                color_root=color_root,
                mapping=mapping,
                output_root=output,
                config=config,
                resume=not args.no_resume,
                continue_on_error=not args.stop_on_error,
            )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if all(row.get("status") != "error" for row in rows) else 1
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
