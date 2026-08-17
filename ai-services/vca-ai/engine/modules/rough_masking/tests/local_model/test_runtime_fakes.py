from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, ClassVar, Self

import numpy as np

if TYPE_CHECKING:
    import pytest
    from numpy.typing import NDArray


class FakeLocalLoader:
    calls: ClassVar[list[tuple[str, Path, dict[str, bool]]]] = []
    source: ClassVar[str]

    @classmethod
    def from_pretrained(cls, model_dir: Path, **kwargs: bool) -> Self:
        cls.calls.append((cls.source, model_dir, kwargs))
        return cls()

    def to(self, device: str) -> Self:
        _ = device
        return self

    def eval(self) -> Self:
        return self


class FakeProcessorLoader(FakeLocalLoader):
    source: ClassVar[str] = "processor"


class FakeModelLoader(FakeLocalLoader):
    source: ClassVar[str] = "model"


class SmallAndLargeMaskPredictor:
    def __init__(self) -> None:
        self.prediction_count: int = 0

    def set_image(self, image: NDArray[np.uint8]) -> None:
        assert image.shape == (8, 10, 3)

    def predict(
        self, *, box: NDArray[np.float32], multimask_output: bool
    ) -> tuple[NDArray[np.bool_], NDArray[np.float32], NDArray[np.float32]]:
        assert box.shape == (4,)
        assert multimask_output is False
        self.prediction_count += 1
        mask = np.zeros((8, 10), dtype=np.bool_)
        if self.prediction_count == 1:
            mask[0, 0] = True
        else:
            mask[:, :] = True
        return (
            np.asarray([mask], dtype=np.bool_),
            np.asarray([1.0], dtype=np.float32),
            np.asarray([0.0], dtype=np.float32),
        )


def install_model_loader_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[Path, Path, str]]:
    FakeLocalLoader.calls.clear()
    transformers_module = ModuleType("transformers")
    for attribute, loader in (
        ("Owlv2Processor", FakeProcessorLoader),
        ("Owlv2ForObjectDetection", FakeModelLoader),
        ("AutoProcessor", FakeProcessorLoader),
        ("AutoModelForCausalLM", FakeModelLoader),
        ("AutoModelForZeroShotObjectDetection", FakeModelLoader),
    ):
        monkeypatch.setattr(transformers_module, attribute, loader, raising=False)
    sam2_package = ModuleType("sam2")
    sam2_package.__path__ = []
    sam2_build_module = ModuleType("sam2.build_sam")
    sam2_predictor_module = ModuleType("sam2.sam2_image_predictor")
    sam2_calls: list[tuple[Path, Path, str]] = []

    def fake_build_sam2(config_path: str, checkpoint_path: str, *, device: str) -> str:
        sam2_calls.append((Path(config_path), Path(checkpoint_path), device))
        return "fake-sam2-model"

    class FakeSam2ImagePredictor:
        def __init__(self, model: str) -> None:
            assert model == "fake-sam2-model"

    monkeypatch.setattr(sam2_build_module, "build_sam2", fake_build_sam2, raising=False)
    monkeypatch.setattr(
        sam2_predictor_module,
        "SAM2ImagePredictor",
        FakeSam2ImagePredictor,
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers_module)
    monkeypatch.setitem(sys.modules, "sam2", sam2_package)
    monkeypatch.setitem(sys.modules, "sam2.build_sam", sam2_build_module)
    monkeypatch.setitem(sys.modules, "sam2.sam2_image_predictor", sam2_predictor_module)
    return sam2_calls
