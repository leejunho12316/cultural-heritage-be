"""Local-only Transformers execution for Qwen visual observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from modules.mask_refining.contracts.models import BackendExecutionKind, CacheStatus
from modules.mask_refining.runtime.backend import (
    BackendResponse,
    QwenBackendRequest,
    cache_input_views,
)
from modules.mask_refining.runtime.transformers_types import (
    QwenChatMessage,
    QwenGenerationModel,
    QwenImageContent,
    QwenModelLoader,
    QwenProcessor,
    QwenProcessorFactory,
    QwenRuntime,
    QwenRuntimeLoader,
    QwenTextContent,
)
from modules.shared import QWEN_BACKEND_DEPENDENCY, QWEN_BACKEND_KIND, QWEN_MODEL_ID

if TYPE_CHECKING:
    from pathlib import Path

_MAX_NEW_TOKENS = 256


@dataclass(frozen=True, slots=True)
class TransformersQwenBackend:
    """Independent local Qwen backend using only rendered view files."""

    processor: QwenProcessor
    model: QwenGenerationModel
    device: str

    @property
    def execution_kind(self) -> BackendExecutionKind:
        """Return production provenance for local raw-image execution."""
        return BackendExecutionKind.INDEPENDENT_RAW_IMAGE

    @property
    def backend_kind(self) -> str:
        """Return the locked production backend kind."""
        return QWEN_BACKEND_KIND

    @property
    def backend_dependency(self) -> str:
        """Return the locked local backend dependency identifier."""
        return QWEN_BACKEND_DEPENDENCY

    @property
    def model_id(self) -> str:
        """Return the locked local Qwen model identifier."""
        return QWEN_MODEL_ID

    def observe(self, request: QwenBackendRequest) -> BackendResponse:
        """Generate untrusted visual JSON and ordered input-view cache evidence."""
        # Keep package imports available without the optional vision dependency group.
        import torch  # noqa: PLC0415

        inputs = self.processor.apply_chat_template(
            _messages(request),
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            generated_ids = self.model.generate(
                **inputs,
                max_new_tokens=_MAX_NEW_TOKENS,
                do_sample=False,
            )
        output_ids = generated_ids[:, inputs.input_ids.shape[1] :]
        output = self.processor.batch_decode(
            output_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        return BackendResponse(
            output,
            CacheStatus.MISS,
            cache_input_views(request.input_views),
        )


def load_transformers_qwen_backend(
    model_directory: Path,
    device: str,
    runtime_loader: QwenRuntimeLoader | None = None,
) -> TransformersQwenBackend:
    """Load Qwen from a local directory without remote model resolution."""
    runtime = (
        _load_local_qwen_runtime(model_directory, device)
        if runtime_loader is None
        else runtime_loader.load(model_directory, device)
    )
    return TransformersQwenBackend(runtime.processor, runtime.model, device)


# 두 개의 입력 뷰 이미지와 고정 지시문을 하나의 user 메시지로 묶는다.
# observe()가 processor.apply_chat_template에 넘길 입력을 만들 때 호출한다.
def _messages(request: QwenBackendRequest) -> tuple[QwenChatMessage, ...]:
    images: list[QwenImageContent | QwenTextContent] = [
        {
            "type": "image",
            "image": str((request.asset_root / view.relative_path).resolve()),
        }
        for view in request.input_views
    ]
    images.append({"type": "text", "text": _visual_json_instruction()})
    return ({"role": "user", "content": images},)


# Qwen에 매 호출마다 동일하게 보내는 고정 지시문이다. 출력 스키마
# (키 목록), 허용된 morphology 값, 진단/치료 언급 금지 등 응답
# 형식을 강제한다.
def _visual_json_instruction() -> str:
    return (
        "Inspect only the two supplied images. Output exactly one raw JSON object. "
        "Do not wrap it in markdown fences. Do not add prose before or after it. "
        "Use only these keys: observation_id, observation_text, selected_terms, "
        "rejected_terms, extracted_descriptors, morphology, confidence, and reason. "
        "morphology is required and must be one of line, spot, hole_pit, crust, "
        "powder, flaking_patch, broad_patch, or unknown. Describe only "
        "mask-internal anomaly morphology, never the object, material, or background. "
        "Do not use smooth, shiny, polished, round, metallic, wood, stone, or "
        "surface as morphology; use unknown when uncertain. reason provenance-only: "
        "state visual support, never query terms. Do not include diagnosis, treatment, "
        "severity, medical text, or instructions."
    )


# 로컬 모델 디렉터리에서만 processor와 model을 로드한다
# (local_files_only=True로 원격 조회를 하지 않는다).
def _load_local_qwen_runtime(model_directory: Path, device: str) -> QwenRuntime:
    from transformers import (  # noqa: PLC0415
        AutoModelForImageTextToText,
        AutoProcessor,
    )

    processor = _load_processor(AutoProcessor, model_directory)
    model_loader = cast(
        "QwenModelLoader",
        AutoModelForImageTextToText.from_pretrained,
    )
    model = model_loader(model_directory, local_files_only=True)
    loaded_model = model.to(device)
    _ = loaded_model.eval()
    return QwenRuntime(processor, loaded_model)


def _load_processor(
    auto_processor: QwenProcessorFactory, model_directory: Path
) -> QwenProcessor:
    return auto_processor.from_pretrained(
        model_directory,
        local_files_only=True,
        use_fast=False,
    )
