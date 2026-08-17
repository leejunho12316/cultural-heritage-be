from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest

from modules.rag.retrieval.embedding_backend import LocalTransformerTextEmbedder
from modules.shared import ContractValidationError

_PASSAGE_COUNT_EXCEEDING_ONE_BATCH: Final = 129
_EMBED_REPLACED_ERROR: Final = "test replaces LocalTransformerTextEmbedder._embed"

if TYPE_CHECKING:
    from pathlib import Path

    import torch


def test_embedding_backend_requires_local_inventory_model(
    tmp_path: Path,
) -> None:
    # Given: a model cache inventory without the RAG text embedding model.
    model_cache_root = tmp_path / "models"
    inventory_path = model_cache_root / "inventory" / "model_inventory.json"
    inventory_path.parent.mkdir(parents=True)
    _ = inventory_path.write_text(
        json.dumps({"models": []}, sort_keys=True), encoding="utf-8"
    )

    # When/Then: the backend fails closed before any transformer loading.
    with pytest.raises(ContractValidationError) as error:
        _ = LocalTransformerTextEmbedder.from_model_cache(model_cache_root, "cpu")
    assert error.value.field == "model_inventory.models"


def test_embed_passages_splits_large_requests_into_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: more passage texts than one transformer batch should receive.
    batch_sizes: list[int] = []
    batched_texts: list[str] = []

    def embed_batch(
        self: LocalTransformerTextEmbedder, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:
        _ = self
        batch_sizes.append(len(texts))
        batched_texts.extend(texts)
        return tuple((float(index),) for index, _ in enumerate(texts))

    embedder = LocalTransformerTextEmbedder(
        tokenizer=_UnusedTokenizer(),
        model=_UnusedModel(),
        model_id="test-embedder",
        device="cpu",
    )
    monkeypatch.setattr(LocalTransformerTextEmbedder, "_embed", embed_batch)
    passages = tuple(
        f"chunk {index}" for index in range(_PASSAGE_COUNT_EXCEEDING_ONE_BATCH)
    )

    # When: passage embeddings are requested.
    embeddings = embedder.embed_passages(passages)

    # Then: every passage is embedded in order without one unbounded batch.
    assert len(batch_sizes) > 1
    assert max(batch_sizes) <= 128
    assert batched_texts == [f"passage: {text}" for text in passages]
    assert len(embeddings) == len(passages)


def test_embed_moves_tokenizer_output_to_configured_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an embedder configured for a non-default device and a tensor.to
    # spy that records the requested device without touching real hardware.
    import torch  # noqa: PLC0415

    seen_devices: list[str] = []

    def recording_to(self: torch.Tensor, device: str) -> torch.Tensor:
        seen_devices.append(device)
        return self

    monkeypatch.setattr(torch.Tensor, "to", recording_to)

    class _RecordingTokenizer:
        def __call__(
            self,
            texts: tuple[str, ...],
            *,
            padding: bool,
            truncation: bool,
            return_tensors: str,
        ) -> dict[str, torch.Tensor]:
            _ = padding, truncation, return_tensors
            length = len(texts)
            return {
                "input_ids": torch.ones((length, 3), dtype=torch.long),
                "attention_mask": torch.ones((length, 3), dtype=torch.long),
            }

    class _RecordingModelOutput:
        last_hidden_state: torch.Tensor

        def __init__(self, hidden: torch.Tensor) -> None:
            self.last_hidden_state = hidden

    class _RecordingModel:
        def eval(self) -> None:
            pass

        def to(self, device: str) -> _RecordingModel:
            _ = device
            return self

        def __call__(self, **inputs: torch.Tensor) -> _RecordingModelOutput:
            batch = inputs["input_ids"].shape[0]
            return _RecordingModelOutput(torch.ones((batch, 3, 4)))

    embedder = LocalTransformerTextEmbedder(
        tokenizer=_RecordingTokenizer(),
        model=_RecordingModel(),
        model_id="test-embedder",
        device="mps",
    )

    # When: passages are embedded.
    _ = embedder.embed_passages(("a passage",))

    # Then: the tokenizer's tensor output was moved to the configured device.
    assert seen_devices
    assert all(device == "mps" for device in seen_devices)


class _UnusedTokenizer:
    def __call__(
        self,
        texts: tuple[str, ...],
        *,
        padding: bool,
        truncation: bool,
        return_tensors: str,
    ) -> dict[str, torch.Tensor]:
        _ = texts, padding, truncation, return_tensors
        raise AssertionError(_EMBED_REPLACED_ERROR)


class _UnusedModel:
    def eval(self) -> None:
        pass

    def to(self, device: str) -> _UnusedModel:
        _ = device
        raise AssertionError(_EMBED_REPLACED_ERROR)

    def __call__(self, **inputs: torch.Tensor) -> _UnusedModelOutput:
        _ = inputs
        raise AssertionError(_EMBED_REPLACED_ERROR)


class _UnusedModelOutput:
    @property
    def last_hidden_state(self) -> torch.Tensor:
        raise AssertionError(_EMBED_REPLACED_ERROR)
