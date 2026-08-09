"""Local-only transformer text embedding backend for RAG vector retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from modules.shared import ContractValidationError, PathSafetyError, resolve_model_path
from modules.shared.json_object import JsonObject, parse_json_object_for_field

if TYPE_CHECKING:
    import torch

    from modules.rag.retrieval.vector_index import FloatMatrix, FloatVector

RAG_TEXT_EMBEDDING_MODEL_KEY: Final = "rag.text_embedding"
RAG_TEXT_EMBEDDING_REPO_ID: Final = "intfloat/multilingual-e5-small"
_PINNED_REVISION: Final = re.compile(r"(?:sha256:[0-9a-f]{64}|[0-9a-f]{40})")
_PASSAGE_EMBED_BATCH_SIZE: Final = 128

class _Tokenizer(Protocol):
    def __call__(
        self,
        texts: tuple[str, ...],
        *,
        padding: bool,
        truncation: bool,
        return_tensors: str,
    ) -> dict[str, torch.Tensor]: ...


class _ModelOutput(Protocol):
    @property
    def last_hidden_state(self) -> torch.Tensor: ...


class _Model(Protocol):
    def eval(self) -> None: ...

    def __call__(self, **inputs: torch.Tensor) -> _ModelOutput: ...


@dataclass(frozen=True, slots=True)
class _EmbeddingModelEntry:
    key: str
    repo_id: str
    revision: str
    local_dir: Path


@dataclass(frozen=True, slots=True)
class LocalTransformerTextEmbedder:
    """Local Hugging Face encoder used for startup vector retrieval."""

    tokenizer: _Tokenizer
    model: _Model
    model_id: str

    @classmethod
    def from_model_cache(cls, model_cache_root: Path) -> LocalTransformerTextEmbedder:
        """Load the configured embedding model from the local model cache only."""
        entry = _embedding_model_entry(model_cache_root)
        _validate_embedding_model_entry(model_cache_root, entry)
        return _load_local_transformer(entry)

    def embed_passages(self, texts: tuple[str, ...]) -> FloatMatrix:
        """Embed document chunks with the passage prefix used by E5 models."""
        passages = tuple(f"passage: {text}" for text in texts)
        vectors: list[FloatVector] = []
        for start in range(0, len(passages), _PASSAGE_EMBED_BATCH_SIZE):
            vectors.extend(
                self._embed(passages[start : start + _PASSAGE_EMBED_BATCH_SIZE])
            )
        return tuple(vectors)

    def embed_query(self, text: str) -> FloatVector:
        """Embed one query with the query prefix used by E5 models."""
        vectors = self._embed((f"query: {text}",))
        return vectors[0]

    def _embed(self, texts: tuple[str, ...]) -> FloatMatrix:
        import torch  # noqa: PLC0415

        encoded = self.tokenizer(
            texts, padding=True, truncation=True, return_tensors="pt"
        )
        with torch.inference_mode():
            output = self.model(**encoded)
        pooled = _mean_pool(output.last_hidden_state, encoded["attention_mask"])
        matrix = _normalize(pooled.detach().cpu())
        return _float_matrix(matrix)


def _load_local_transformer(
    entry: _EmbeddingModelEntry,
) -> LocalTransformerTextEmbedder:
    from transformers import AutoModel, AutoTokenizer  # noqa: PLC0415

    tokenizer: _Tokenizer = AutoTokenizer.from_pretrained(
        entry.local_dir, local_files_only=True, trust_remote_code=False
    )
    model: _Model = AutoModel.from_pretrained(
        entry.local_dir,
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
    )
    model.eval()
    return LocalTransformerTextEmbedder(
        tokenizer=tokenizer,
        model=model,
        model_id=f"{entry.repo_id}@{entry.revision}",
    )


def _embedding_model_entry(model_cache_root: Path) -> _EmbeddingModelEntry:
    inventory_path = model_cache_root / "inventory" / "model_inventory.json"
    raw = parse_json_object_for_field(
        inventory_path.read_text(encoding="utf-8"), "model_inventory"
    )
    models = raw.get("models")
    if not isinstance(models, list):
        field = "model_inventory.models"
        reason = "must be a list"
        raise ContractValidationError(field, reason)
    for item in models:
        if isinstance(item, dict) and item.get("key") == RAG_TEXT_EMBEDDING_MODEL_KEY:
            return _entry_from_json(item, model_cache_root)
    field = "model_inventory.models"
    reason = f"missing required key: {RAG_TEXT_EMBEDDING_MODEL_KEY}"
    raise ContractValidationError(field, reason)


def _entry_from_json(item: JsonObject, model_cache_root: Path) -> _EmbeddingModelEntry:
    key = _string_field(item, "key")
    return _EmbeddingModelEntry(
        key=key,
        repo_id=_string_field(item, "repo_id"),
        revision=_string_field(item, "revision"),
        local_dir=resolve_model_path(
            key, Path(_string_field(item, "local_dir")), model_cache_root
        ),
    )


def _string_field(item: JsonObject, field_name: str) -> str:
    value = item.get(field_name)
    if not isinstance(value, str) or not value.strip():
        field = f"model_inventory.models.{field_name}"
        reason = "must be a non-empty string"
        raise ContractValidationError(field, reason)
    return value


def _validate_embedding_model_entry(
    model_cache_root: Path, entry: _EmbeddingModelEntry
) -> None:
    _validate_expected_model(entry)
    _validate_pinned_revision(entry)
    _validate_local_model_path(model_cache_root, entry)


def _validate_expected_model(entry: _EmbeddingModelEntry) -> None:
    if entry.repo_id != RAG_TEXT_EMBEDDING_REPO_ID:
        field = f"model_inventory.models.{entry.key}.repo_id"
        raise ContractValidationError(field, RAG_TEXT_EMBEDDING_REPO_ID)


def _validate_pinned_revision(entry: _EmbeddingModelEntry) -> None:
    if _PINNED_REVISION.fullmatch(entry.revision) is None:
        field = f"model_inventory.models.{entry.key}.revision"
        reason = "must be a pinned commit hash or sha256 digest"
        raise ContractValidationError(field, reason)


def _validate_local_model_path(
    model_cache_root: Path, entry: _EmbeddingModelEntry
) -> None:
    resolved_root = model_cache_root.expanduser().resolve()
    resolved_model = entry.local_dir.expanduser().resolve()
    if not resolved_model.is_relative_to(resolved_root):
        reason = "embedding model path escapes model cache root"
        raise PathSafetyError(str(entry.local_dir), reason)
    if _has_symlink_component(entry.local_dir, model_cache_root):
        reason = "embedding model path uses symlinks"
        raise PathSafetyError(str(entry.local_dir), reason)
    if not entry.local_dir.is_dir():
        field = f"model_inventory.models.{entry.key}.local_dir"
        reason = "local cache path does not exist"
        raise ContractValidationError(field, reason)


def _has_symlink_component(path: Path, stop: Path) -> bool:
    current = path.expanduser()
    boundary = stop.expanduser()
    while True:
        if current.is_symlink():
            return True
        if current in (boundary, current.parent):
            return False
        current = current.parent


def _mean_pool(hidden: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    expanded_mask = mask.unsqueeze(-1).expand(hidden.size()).float()
    summed = (hidden * expanded_mask).sum(dim=1)
    counts = expanded_mask.sum(dim=1).clamp(min=1e-9)
    return summed / counts


def _normalize(matrix: torch.Tensor) -> torch.Tensor:
    import torch  # noqa: PLC0415

    return torch.nn.functional.normalize(matrix, p=2.0, dim=1, eps=1e-12)


def _float_matrix(matrix: torch.Tensor) -> FloatMatrix:
    return tuple(
        tuple(
            float(matrix[row_index, column_index].item())
            for column_index in range(matrix.size(1))
        )
        for row_index in range(matrix.size(0))
    )
