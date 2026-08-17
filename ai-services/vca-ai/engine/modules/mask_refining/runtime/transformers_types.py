"""Typed contracts at the optional Transformers runtime boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict

if TYPE_CHECKING:
    from collections.abc import KeysView
    from pathlib import Path

    import torch


class QwenImageContent(TypedDict):
    """A local rendered image passed to the Qwen chat template."""

    type: Literal["image"]
    image: str


class QwenTextContent(TypedDict):
    """The fixed visual-only instruction passed to the Qwen chat template."""

    type: Literal["text"]
    text: str


class QwenChatMessage(TypedDict):
    """One visual-only Qwen user message."""

    role: Literal["user"]
    content: list[QwenImageContent | QwenTextContent]


class QwenModelInputs(Protocol):
    """Tensor batch returned from the Qwen processor."""

    @property
    def input_ids(self) -> torch.Tensor:
        """Return token IDs used to trim generated continuations."""
        ...

    def to(self, device: str) -> QwenModelInputs:
        """Move the complete batch to the selected accelerator."""
        ...

    def keys(self) -> KeysView[str]:
        """Return the input tensor field names accepted by model generation."""
        ...

    def __getitem__(self, key: str) -> torch.Tensor:
        """Return the tensor associated with one model input field."""
        ...


class QwenProcessor(Protocol):
    """Minimal processor capability needed for local Qwen inference."""

    def apply_chat_template(
        self,
        messages: tuple[QwenChatMessage, ...],
        *,
        add_generation_prompt: bool,
        tokenize: bool,
        return_dict: bool,
        return_tensors: str,
    ) -> QwenModelInputs:
        """Encode a visual chat request into a tensor batch."""
        ...

    def batch_decode(
        self,
        generated_ids: torch.Tensor,
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool,
    ) -> list[str]:
        """Decode generated continuation tokens into raw model text."""
        ...


class QwenGenerationModel(Protocol):
    """Minimal generation capability needed for local Qwen inference."""

    def generate(self, **inputs: torch.Tensor | int | bool) -> torch.Tensor:
        """Generate token IDs from an encoded local visual request."""
        ...


class QwenLoadedGenerationModel(QwenGenerationModel, Protocol):
    """Locally loaded Qwen model before it is stored in the runtime."""

    def to(self, device: str) -> QwenLoadedGenerationModel:
        """Move the model to the selected accelerator."""
        ...

    def eval(self) -> QwenLoadedGenerationModel:
        """Set the model to deterministic inference mode."""
        ...


@dataclass(frozen=True, slots=True)
class QwenRuntime:
    """Typed model and processor pair used by the local backend."""

    processor: QwenProcessor
    model: QwenGenerationModel


class QwenRuntimeLoader(Protocol):
    """Load Qwen runtime components exclusively from a local model directory."""

    def load(self, model_directory: Path, device: str) -> QwenRuntime:
        """Load the local processor and model for one selected device."""
        ...


class QwenProcessorFactory(Protocol):
    """Load a processor from a local Transformers model directory."""

    @staticmethod
    def from_pretrained(
        pretrained_model_name_or_path: Path,
        *,
        local_files_only: bool,
        use_fast: bool,
    ) -> QwenProcessor:
        """Return a processor matching the backend protocol."""
        ...


class QwenModelLoader(Protocol):
    """Load a generation model from a local Transformers directory."""

    def __call__(
        self,
        pretrained_model_name_or_path: Path,
        *,
        local_files_only: bool,
    ) -> QwenLoadedGenerationModel:
        """Return a local model capable of Qwen generation."""
        ...
