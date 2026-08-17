from collections.abc import Iterator, KeysView
from os import PathLike
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from PIL.Image import Image
from torch import Tensor

type PathInput = str | Path | PathLike[str]

class DetectionResult(TypedDict):
    scores: Tensor
    boxes: Tensor

class FlorenceGenerationResult(TypedDict):
    bboxes: tuple[tuple[float, float, float, float], ...]

class TensorBatch(Protocol):
    @property
    def input_ids(self) -> Tensor: ...
    def to(self, device: str) -> TensorBatch: ...
    def keys(self) -> object: ...
    def __getitem__(self, key: str) -> Tensor: ...

class QwenImageContent(TypedDict):
    type: Literal["image"]
    image: str

class QwenTextContent(TypedDict):
    type: Literal["text"]
    text: str

class QwenChatMessage(TypedDict):
    role: Literal["user"]
    content: list[QwenImageContent | QwenTextContent]

class QwenModelInputs(Protocol):
    @property
    def input_ids(self) -> Tensor: ...
    def to(self, device: str) -> QwenModelInputs: ...
    def keys(self) -> KeysView[str]: ...
    def __getitem__(self, key: str) -> Tensor: ...

class ModelOutput(Protocol):
    @property
    def last_hidden_state(self) -> Tensor: ...

class PreTrainedTokenizerProtocol(Protocol):
    def __call__(
        self,
        texts: tuple[str, ...],
        *,
        padding: bool,
        truncation: bool,
        return_tensors: str,
    ) -> dict[str, Tensor]: ...

class PreTrainedModelProtocol(Protocol):
    def eval(self) -> None: ...
    def to(self, device: str) -> PreTrainedModelProtocol: ...
    def __call__(self, **inputs: Tensor) -> ModelOutput: ...

class AutoTokenizer:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        local_files_only: bool,
        trust_remote_code: bool,
    ) -> PreTrainedTokenizerProtocol: ...

class AutoModel:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        local_files_only: bool,
        trust_remote_code: bool,
        use_safetensors: bool,
    ) -> PreTrainedModelProtocol: ...

class AutoModelForImageTextToText:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        local_files_only: bool,
    ) -> AutoModelForImageTextToText: ...
    def to(self, device: str) -> AutoModelForImageTextToText: ...
    def eval(self) -> AutoModelForImageTextToText: ...
    def generate(self, **inputs: Tensor | int | bool) -> Tensor: ...

class AutoModelForCausalLM:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        attn_implementation: str,
        local_files_only: bool,
        trust_remote_code: bool,
    ) -> AutoModelForCausalLM: ...
    def to(self, device: str) -> AutoModelForCausalLM: ...
    def eval(self) -> AutoModelForCausalLM: ...
    def generate(self, **inputs: Tensor | int | bool | str) -> Tensor: ...

class AutoModelForZeroShotObjectDetection:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        local_files_only: bool,
    ) -> AutoModelForZeroShotObjectDetection: ...
    def to(self, device: str) -> AutoModelForZeroShotObjectDetection: ...
    def eval(self) -> AutoModelForZeroShotObjectDetection: ...
    def __call__(self, **inputs: Tensor) -> Tensor: ...

class AutoProcessor:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        local_files_only: bool,
        use_fast: bool = ...,
        trust_remote_code: bool = ...,
    ) -> AutoProcessor: ...
    def apply_chat_template(
        self,
        messages: tuple[QwenChatMessage, ...],
        *,
        add_generation_prompt: bool,
        tokenize: bool,
        return_dict: bool,
        return_tensors: str,
    ) -> QwenModelInputs: ...
    def batch_decode(
        self,
        generated_ids: Tensor,
        *,
        skip_special_tokens: bool,
        clean_up_tokenization_spaces: bool = ...,
    ) -> list[str]: ...
    def __call__(
        self,
        *,
        text: str | list[list[str]],
        images: Image,
        return_tensors: str,
    ) -> TensorBatch: ...
    def post_process_generation(
        self,
        generated_text: str,
        *,
        task: str,
        image_size: tuple[int, int],
    ) -> dict[str, FlorenceGenerationResult]: ...
    def post_process_grounded_object_detection(
        self,
        outputs: Tensor,
        input_ids: Tensor,
        *,
        threshold: float,
        text_threshold: float,
        target_sizes: list[tuple[int, int]],
    ) -> list[DetectionResult]: ...

class Owlv2Processor:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        local_files_only: bool,
        use_fast: bool,
    ) -> Owlv2Processor: ...
    def __call__(
        self, *, text: list[list[str]], images: Image, return_tensors: str
    ) -> TensorBatch: ...
    def post_process_grounded_object_detection(
        self,
        *,
        outputs: Tensor,
        target_sizes: Tensor,
        threshold: float,
        text_labels: list[list[str]],
    ) -> list[DetectionResult]: ...

class Owlv2ForObjectDetection:
    @classmethod
    def from_pretrained(
        cls,
        pretrained_model_name_or_path: PathInput,
        *,
        local_files_only: bool,
    ) -> Owlv2ForObjectDetection: ...
    def to(self, device: str | object) -> Owlv2ForObjectDetection: ...
    def eval(self) -> Owlv2ForObjectDetection: ...
    def parameters(self) -> Iterator[Tensor]: ...
    def __call__(self, **inputs: Tensor | bool) -> Tensor: ...
