"""Download the PDF models the worker loads into /opt/docling-models.

The image sets DOCLING_ARTIFACTS_PATH to that directory. Runtime then reads
these files and does not call out for them on the first upload. The hf_cache
volume stays for embedding models and must not cover this path.

Docling fetches RapidOCR weights from modelscope.cn with connect timeout 5s.
urllib3 reports a slow TLS handshake as ``ReadTimeout (read timeout=5)``. From
the deploy host that handshake often takes about 3.4s and sometimes exceeds 5s.
RapidOCR's model registry has no Hugging Face URLs and no timeout setting.
When the registry publishes a SHA256, try a path-preserving Hugging Face mirror
and require that hash, then fall back to ModelScope with a longer timeout and
five attempts.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable, Iterable, Mapping
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import requests
from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions
from docling.models.stages.ocr import rapid_ocr_model
from docling.models.stages.table_structure.table_structure_model import (
    TableStructureModel,
)
from docling.models.utils.hf_model_download import download_hf_model
from docling.utils.model_downloader import download_models
from rapidocr.inference_engine.base import InferSession

DEST = Path("/opt/docling-models")
PREFETCH_CACHE_ENV = "DOCLING_PREFETCH_CACHE"
DOWNLOAD_ATTEMPTS = 5
CONNECT_TIMEOUT_SECONDS = 30
READ_TIMEOUT_SECONDS = 120
BACKOFF_SECONDS = (2, 4, 8, 16)
MODELSCOPE_RAPIDOCR_PREFIX = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
# Relative paths match the ModelScope repo. Bytes are accepted only when they
# match the SHA256 RapidOCR publishes next to the ModelScope URL.
HUGGINGFACE_RAPIDOCR_PREFIX = "https://huggingface.co/DjB314/RapidOCR/resolve/main/"


class DownloadResponse(Protocol):
    status_code: int

    def raise_for_status(self) -> None: ...

    def iter_content(self, chunk_size: int) -> Iterable[bytes]: ...

    def close(self) -> None: ...


Get = Callable[..., DownloadResponse]
Sleep = Callable[[float], None]


class ChecksumMismatch(Exception):
    """Downloaded bytes did not match the RapidOCR registry hash."""


def checksums_by_url(catalog: Mapping[str, object]) -> dict[str, str]:
    """Map RapidOCR ``model_dir`` / font ``path`` URLs to their SHA256."""
    found: dict[str, str] = {}

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            sha = node.get("SHA256")
            if isinstance(sha, str):
                for key in ("model_dir", "path"):
                    url = node.get(key)
                    if isinstance(url, str) and url.startswith("https://"):
                        found[url] = sha.lower()
            for child in node.values():
                walk(child)
        elif isinstance(node, list | tuple):
            for child in node:
                walk(child)

    walk(catalog)
    return found


def rapidocr_download_urls(url: str) -> tuple[str, ...]:
    """Hugging Face mirror first, then the ModelScope URL the registry names."""
    if url.startswith(MODELSCOPE_RAPIDOCR_PREFIX):
        relative = url[len(MODELSCOPE_RAPIDOCR_PREFIX) :]
        return (HUGGINGFACE_RAPIDOCR_PREFIX + relative, url)
    return (url,)


def candidate_urls(url: str, checksums: Mapping[str, str]) -> tuple[str, ...]:
    if url in checksums:
        return rapidocr_download_urls(url)
    return (url,)


def read_http_body(url: str, *, get: Get) -> bytes:
    response = get(
        url,
        stream=True,
        allow_redirects=True,
        timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
    )
    try:
        status = response.status_code
        if status in (404, 410):
            raise FileNotFoundError(f"{status} {url}")
        if status == 429 or status >= 500:
            raise requests.HTTPError(f"{status} {url}")
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(64 * 1024):
            if chunk:
                body.extend(chunk)
        if not body:
            raise requests.ConnectionError(f"empty body from {url}")
        return bytes(body)
    finally:
        response.close()


def fetch_model_bytes(
    url: str,
    *,
    checksums: Mapping[str, str],
    get: Get = requests.get,
    sleep: Sleep = time.sleep,
) -> bytes:
    expected = checksums.get(url)
    last_error: Exception | None = None
    for candidate in candidate_urls(url, checksums):
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            try:
                data = read_http_body(candidate, get=get)
            except FileNotFoundError as exc:
                last_error = exc
                print(f"docling prefetch: {candidate} is missing", flush=True)
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt == DOWNLOAD_ATTEMPTS:
                    print(
                        f"docling prefetch: gave up on {candidate} "
                        f"after {attempt} attempts ({exc})",
                        flush=True,
                    )
                    break
                delay = BACKOFF_SECONDS[attempt - 1]
                print(
                    f"docling prefetch: {candidate} failed ({exc}); retrying in {delay}s",
                    flush=True,
                )
                sleep(delay)
                continue
            digest = hashlib.sha256(data).hexdigest()
            if expected is not None and digest != expected:
                last_error = ChecksumMismatch(candidate)
                print(
                    f"docling prefetch: checksum mismatch for {candidate}",
                    flush=True,
                )
                break
            print(
                f"docling prefetch: fetched {Path(url).name} from {urlsplit(candidate).netloc}",
                flush=True,
            )
            return data
    if last_error is None:
        raise RuntimeError(f"no download attempted for {url}")
    raise last_error


def install_resilient_rapidocr_downloader(
    checksums: Mapping[str, str],
    *,
    get: Get = requests.get,
    sleep: Sleep = time.sleep,
) -> None:
    """Replace Docling's RapidOCR downloader for this process."""

    def download_url_with_progress(url: str, progress: bool = False) -> BytesIO:
        del progress
        return BytesIO(fetch_model_bytes(url, checksums=checksums, get=get, sleep=sleep))

    rapid_ocr_model.download_url_with_progress = download_url_with_progress


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def rapidocr_files(root: Path) -> list[tuple[Path, str]]:
    spec = rapid_ocr_model._resolve_rapidocr("ch", "torch")
    if spec.ppocr_version is None or spec.rapidocr_lang_token is None:
        raise RuntimeError("RapidOCR torch:ch did not resolve")
    artifacts = rapid_ocr_model._rapidocr_artifacts(
        root / rapid_ocr_model.RapidOcrModel._model_repo_folder,
        rapid_ocr_model._backend_to_engine_type("torch"),
        spec.ppocr_version,
        spec.rapidocr_lang_token,
    )
    files: list[tuple[Path, str]] = []
    for artifact in artifacts.values():
        files.extend(artifact.files.items())
    return files


def discard_invalid_files(
    files: Iterable[tuple[Path, str]],
    checksums: Mapping[str, str],
) -> None:
    for dest, url in files:
        if not dest.is_file():
            continue
        expected = checksums.get(url)
        if dest.stat().st_size == 0 or (expected is not None and sha256_file(dest) != expected):
            dest.unlink()


def require_rapidocr_files(files: Iterable[tuple[Path, str]], checksums: Mapping[str, str]) -> None:
    missing = [dest for dest, _url in files if not dest.is_file()]
    if missing:
        raise SystemExit(f"Docling prefetch incomplete: {missing}")
    mismatched = [
        dest
        for dest, url in files
        if (expected := checksums.get(url)) is not None and sha256_file(dest) != expected
    ]
    if mismatched:
        raise SystemExit(f"Docling prefetch checksum mismatch: {mismatched}")


def require_pipeline_paths(root: Path) -> None:
    spec = LayoutObjectDetectionOptions().model_spec
    required = (
        root / spec.repo_id.replace("/", "--"),
        root / TableStructureModel._model_repo_folder / TableStructureModel._model_path,
        root / rapid_ocr_model.RapidOcrModel._model_repo_folder,
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Docling prefetch incomplete: {missing}")


def copy_tree(source: Path, dest: Path) -> None:
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        target = dest / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def prefetch_into(root: Path, checksums: Mapping[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    files = rapidocr_files(root)
    discard_invalid_files(files, checksums)
    install_resilient_rapidocr_downloader(checksums)
    spec = LayoutObjectDetectionOptions().model_spec
    # Transformers is the engine this image runs. Skip the ONNX twin.
    download_hf_model(
        repo_id=spec.repo_id,
        revision=spec.revision,
        local_dir=root / spec.repo_id.replace("/", "--"),
        progress=True,
    )
    download_models(
        root,
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
    require_pipeline_paths(root)
    require_rapidocr_files(rapidocr_files(root), checksums)


def main() -> None:
    cache = os.environ.get(PREFETCH_CACHE_ENV, "").strip()
    stage = Path(cache) if cache else DEST
    checksums = checksums_by_url(InferSession.model_info)
    prefetch_into(stage, checksums)
    if stage.resolve() != DEST.resolve():
        DEST.mkdir(parents=True, exist_ok=True)
        copy_tree(stage, DEST)
        require_pipeline_paths(DEST)
        require_rapidocr_files(rapidocr_files(DEST), checksums)


if __name__ == "__main__":
    main()
