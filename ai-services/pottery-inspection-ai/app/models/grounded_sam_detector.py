from pathlib import Path

import numpy as np
import torch
from PIL import Image
from sam2.sam2_image_predictor import SAM2ImagePredictor
from transformers import (
    AutoModelForZeroShotObjectDetection,
    AutoProcessor,
)


class GroundedSAMDetector:
    def __init__(
        self,
        grounding_model_id: str = (
            "IDEA-Research/grounding-dino-base"
        ),
        sam2_model_id: str = (
            "facebook/sam2.1-hiera-tiny"
        ),
        text_prompt: str = (
            "rusted metal artifact. "
            "corrosion. "
            "rusted metal surface. "
            "damaged metal surface."
        ),
        box_threshold: float = 0.25,
        text_threshold: float = 0.20,
        iou_threshold: float = 0.60,
        max_regions: int = 1,
    ) -> None:
        self.grounding_model_id = grounding_model_id
        self.sam2_model_id = sam2_model_id
        self.text_prompt = text_prompt
        self.box_threshold = box_threshold
        self.text_threshold = text_threshold
        self.iou_threshold = iou_threshold
        self.max_regions = max_regions

        self.device = (
            "cuda"
            if torch.cuda.is_available()
            else "cpu"
        )

        print(
            f"[GroundedSAMDetector] device={self.device}"
        )

        self.processor = AutoProcessor.from_pretrained(
            self.grounding_model_id
        )

        self.grounding_model = (
            AutoModelForZeroShotObjectDetection
            .from_pretrained(
                self.grounding_model_id
            )
            .to(self.device)
        )
        self.grounding_model.eval()

        self.sam2_predictor = (
            SAM2ImagePredictor.from_pretrained(
                self.sam2_model_id,
                device=self.device,
            )
        )

        try:
            self.sam2_predictor.model.to(
                self.device
            )
            self.sam2_predictor.model.eval()
        except Exception as error:
            print(
                "[GroundedSAMDetector] "
                f"SAM2 device 이동 생략: {error}"
            )

    def predict(
        self,
        image_path: str,
    ) -> list[dict]:
        path = Path(image_path)

        if not path.exists():
            raise FileNotFoundError(
                f"이미지를 찾을 수 없습니다: {image_path}"
            )

        image = Image.open(path).convert("RGB")

        candidate_boxes = self._detect_boxes(
            image=image
        )

        if not candidate_boxes:
            return []

        self.sam2_predictor.set_image(
            np.array(image)
        )

        regions: list[dict] = []

        for index, candidate in enumerate(
            candidate_boxes,
            start=1,
        ):
            box = candidate["box"]

            mask = self._generate_mask(
                box=box
            )

            regions.append(
                {
                    "damage_type": "corroded_metal_artifact",
                    "label": candidate["label"],
                    "confidence": candidate["score"],
                    "bounding_box": {
                        "x1": int(box[0]),
                        "y1": int(box[1]),
                        "x2": int(box[2]),
                        "y2": int(box[3]),
                    },
                    "mask": mask,
                    "region_index": index,
                }
            )

        return regions

    def _detect_boxes(
        self,
        image: Image.Image,
    ) -> list[dict]:
        inputs = self.processor(
            images=image,
            text=self.text_prompt,
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            outputs = self.grounding_model(
                **inputs
            )

        results = (
            self.processor
            .post_process_grounded_object_detection(
                outputs,
                inputs.input_ids,
                threshold=self.box_threshold,
                text_threshold=self.text_threshold,
                target_sizes=[
                    image.size[::-1]
                ],
            )
        )

        result = results[0]

        boxes = result["boxes"]
        scores = result["scores"]
        labels = result.get(
            "text_labels",
            result.get("labels", []),
        )

        candidates: list[dict] = []

        for box, score, label in zip(
            boxes,
            scores,
            labels,
        ):
            box_list = [
                int(value)
                for value in box.tolist()
            ]

            candidates.append(
                {
                    "box": box_list,
                    "score": float(
                        score.item()
                    ),
                    "label": str(label),
                }
            )

        candidates = sorted(
            candidates,
            key=lambda item: item["score"],
            reverse=True,
        )

        filtered = self._filter_overlapping_boxes(
            candidates
        )

        return filtered[: self.max_regions]

    def _generate_mask(
        self,
        box: list[int],
    ) -> np.ndarray:
        input_box = np.array(
            box,
            dtype=np.float32,
        )

        masks, scores, _ = (
            self.sam2_predictor.predict(
                box=input_box,
                multimask_output=True,
            )
        )

        if masks is None or len(masks) == 0:
            raise RuntimeError(
                "SAM 2가 마스크를 생성하지 못했습니다."
            )

        best_index = int(
            np.argmax(scores)
        )

        best_mask = np.asarray(
            masks[best_index]
        ).squeeze()

        return best_mask.astype(bool)

    def _filter_overlapping_boxes(
        self,
        candidates: list[dict],
    ) -> list[dict]:
        filtered: list[dict] = []

        for candidate in candidates:
            keep = True

            for chosen in filtered:
                iou = self._compute_iou(
                    candidate["box"],
                    chosen["box"],
                )

                if iou >= self.iou_threshold:
                    keep = False
                    break

            if keep:
                filtered.append(candidate)

        return filtered

    @staticmethod
    def _compute_iou(
        box_a: list[int],
        box_b: list[int],
    ) -> float:
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_width = max(
            0,
            inter_x2 - inter_x1,
        )
        inter_height = max(
            0,
            inter_y2 - inter_y1,
        )
        inter_area = (
            inter_width * inter_height
        )

        area_a = max(0, ax2 - ax1) * max(
            0,
            ay2 - ay1,
        )
        area_b = max(0, bx2 - bx1) * max(
            0,
            by2 - by1,
        )

        union_area = (
            area_a + area_b - inter_area
        )

        if union_area <= 0:
            return 0.0

        return inter_area / union_area