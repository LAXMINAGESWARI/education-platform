"""Download the PDF models the worker loads into /opt/docling-models.

The image sets DOCLING_ARTIFACTS_PATH to that directory. Runtime then reads
these files and does not call Hugging Face on the first upload. The hf_cache
volume stays for embedding models and must not cover this path.
"""

from __future__ import annotations

from pathlib import Path

from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions
from docling.models.stages.ocr.rapid_ocr_model import RapidOcrModel
from docling.models.stages.table_structure.table_structure_model import (
    TableStructureModel,
)
from docling.models.utils.hf_model_download import download_hf_model
from docling.utils.model_downloader import download_models

DEST = Path("/opt/docling-models")


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    spec = LayoutObjectDetectionOptions().model_spec
    # Transformers is the engine this image runs. Skip the ONNX twin.
    download_hf_model(
        repo_id=spec.repo_id,
        revision=spec.revision,
        local_dir=DEST / spec.repo_id.replace("/", "--"),
        progress=True,
    )
    download_models(
        DEST,
        progress=True,
        with_layout=False,
        with_tableformer=True,
        with_code_formula=False,
        with_picture_classifier=False,
        with_rapidocr=True,
        # No onnxruntime in the image, so auto OCR selects RapidOCR's torch backend.
        rapidocr_models=["torch:ch"],
        with_easyocr=False,
    )
    required = (
        DEST / spec.repo_id.replace("/", "--"),
        DEST / TableStructureModel._model_repo_folder / TableStructureModel._model_path,
        DEST / RapidOcrModel._model_repo_folder,
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Docling prefetch incomplete: {missing}")


if __name__ == "__main__":
    main()
