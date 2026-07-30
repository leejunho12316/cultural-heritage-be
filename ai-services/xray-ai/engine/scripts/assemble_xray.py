from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xray_assembler import AssemblyConfig, run_assembly


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "컬러 완성본의 외곽 형태를 기준으로 X-ray 파편을 회전·이동만 하여 조립합니다. "
            "X-ray 파편에는 scale, 비선형 변형, 생성형 보정, 블렌딩을 적용하지 않습니다."
        )
    )
    parser.add_argument("--reference", required=True, help="컬러 원본 완성본 이미지")
    parser.add_argument("--fragments", required=True, help="X-ray 파편 이미지 폴더")
    parser.add_argument("--output", required=True, help="결과 저장 폴더")
    parser.add_argument("--config", help="JSON 설정 파일")
    parser.add_argument("--reference-mask", help="선택: 컬러 완성본 수동 binary mask")
    parser.add_argument("--fragment-masks", help="선택: 파편별 binary mask 폴더")
    parser.add_argument("--seed", type=int, help="설정 파일의 random_seed 덮어쓰기")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config = AssemblyConfig.from_json(args.config)
        if args.seed is not None:
            config.random_seed = args.seed
        report = run_assembly(
            reference_path=Path(args.reference),
            fragments_dir=Path(args.fragments),
            output_dir=Path(args.output),
            config=config,
            reference_mask_path=Path(args.reference_mask) if args.reference_mask else None,
            fragment_masks_dir=Path(args.fragment_masks) if args.fragment_masks else None,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
