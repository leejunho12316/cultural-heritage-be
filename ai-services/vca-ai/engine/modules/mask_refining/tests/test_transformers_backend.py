from __future__ import annotations

from typing import TYPE_CHECKING, Self

import torch

from modules.mask_refining import (
    CacheStatus,
    QwenBackendRequest,
    QwenInputView,
    QwenRuntime,
    QwenViewKind,
    TransformersQwenBackend,
    cache_input_views,
    load_transformers_qwen_backend,
)
from modules.mask_refining.tests.test_support import valid_observation_json

if TYPE_CHECKING:
    from pathlib import Path

    from modules.mask_refining.runtime.transformers_types import QwenChatMessage


class _FakeInputs(dict[str, torch.Tensor]):
    @property
    def input_ids(self) -> torch.Tensor:
        return self["input_ids"]

    def to(self, device: str) -> Self:
        _ = device
        return self


class _FakeProcessor:
    def __init__(self) -> None:
        self.messages: tuple[QwenChatMessage, ...] = ()
        self.generated_ids: torch.Tensor | None = None

    def apply_chat_template(
        self,
        messages: tuple[QwenChatMessage, ...],
        *,
        add_generation_prompt: bool,
        tokenize: bool,
        return_dict: bool,
        return_tensors: str,
    ) -> _FakeInputs:
        assert add_generation_prompt is True
        assert tokenize is True
        assert return_dict is True
        assert return_tensors == "pt"
        self.messages = messages
        return _FakeInputs(input_ids=torch.tensor([[1, 2]], dtype=torch.long))

    def batch_decode(
        self,
        generated_ids: torch.Tensor,
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> list[str]:
        assert skip_special_tokens is True
        assert clean_up_tokenization_spaces is False
        self.generated_ids = generated_ids
        return [valid_observation_json()]


class _FakeModel:
    def __init__(self) -> None:
        self.max_new_tokens: int = 0
        self.do_sample: bool = True

    def generate(self, **inputs: torch.Tensor | int | bool) -> torch.Tensor:
        self.max_new_tokens = int(inputs["max_new_tokens"])
        self.do_sample = bool(inputs["do_sample"])
        return torch.tensor([[1, 2, 3]], dtype=torch.long)


class _FakeRuntimeLoader:
    def __init__(self, runtime: QwenRuntime) -> None:
        self.runtime: QwenRuntime = runtime
        self.model_directory: Path | None = None
        self.device: str = ""

    def load(self, model_directory: Path, device: str) -> QwenRuntime:
        self.model_directory = model_directory
        self.device = device
        return self.runtime


def _views() -> tuple[QwenInputView, QwenInputView]:
    return (
        QwenInputView(
            view_id="candidate-001:masked",
            kind=QwenViewKind.MASKED_TARGET_CROP,
            asset_hash="a" * 64,
            media_type="image/png",
            relative_path="views/masked.png",
            call_order=1,
        ),
        QwenInputView(
            view_id="candidate-001:bounded",
            kind=QwenViewKind.BOUNDED_PADDED_CANDIDATE_CROP,
            asset_hash="b" * 64,
            media_type="image/png",
            relative_path="views/bounded.png",
            call_order=2,
        ),
    )


def test_transformers_backend_generates_visual_json_with_cached_view_hashes(
    tmp_path: Path,
) -> None:
    # Given: injected local model and processor doubles plus two rendered Qwen views.
    processor = _FakeProcessor()
    model = _FakeModel()
    backend = TransformersQwenBackend(processor, model, "cpu")
    views = _views()

    # When: the local backend observes the view pair.
    response = backend.observe(QwenBackendRequest(views, "cpu", tmp_path))

    # Then: it uses image-only prompt content and returns parser-boundary JSON evidence.
    assert response.raw_output == valid_observation_json()
    assert response.cache_status is CacheStatus.MISS
    assert response.cache_hash == cache_input_views(views)
    assert model.max_new_tokens > 0
    assert model.do_sample is False
    first_content = processor.messages[0]["content"][0]
    last_content = processor.messages[0]["content"][-1]
    assert last_content["type"] == "text"
    instruction = last_content["text"]
    assert first_content["type"] == "image"
    assert first_content["image"] == str((tmp_path / "views/masked.png").resolve())
    assert "morphology" in instruction
    assert "mask-internal anomaly morphology" in instruction
    assert "reason provenance-only" in instruction
    assert processor.generated_ids is not None
    assert torch.equal(processor.generated_ids, torch.tensor([[3]]))


def test_transformers_backend_factory_uses_injected_local_runtime(
    tmp_path: Path,
) -> None:
    # Given: an injected runtime loader for a local Qwen cache directory.
    runtime = QwenRuntime(_FakeProcessor(), _FakeModel())
    loader = _FakeRuntimeLoader(runtime)
    model_directory = tmp_path / "Qwen2.5-VL-3B-Instruct"

    # When: the backend factory is called for a local MPS run.
    backend = load_transformers_qwen_backend(model_directory, "mps", loader)

    # Then: it binds only the supplied local directory and requested device.
    assert backend.device == "mps"
    assert loader.model_directory == model_directory
    assert loader.device == "mps"
