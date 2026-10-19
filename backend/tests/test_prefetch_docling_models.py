"""RapidOCR prefetch retries slow ModelScope downloads and prefers a hashed mirror."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
import requests

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "deploy" / "prefetch_docling_models.py"


def _load():
    spec = importlib.util.spec_from_file_location("prefetch_docling_models", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prefetch = _load()

_MODELSCOPE = (
    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/"
    "v3.9.2/torch/PP-OCRv6/det/PP-OCRv6_det_small.pth"
)
_HUGGINGFACE = (
    "https://huggingface.co/DjB314/RapidOCR/resolve/main/"
    "v3.9.2/torch/PP-OCRv6/det/PP-OCRv6_det_small.pth"
)


class _Body:
    def __init__(self, status: int, content: bytes) -> None:
        self.status_code = status
        self._content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size: int = 1):
        del chunk_size
        yield self._content

    def close(self) -> None:
        return None


def test_installed_rapidocr_registry_prefers_huggingface_for_torch_weights() -> None:
    from rapidocr.inference_engine.base import InferSession

    checksums = prefetch.checksums_by_url(InferSession.model_info)
    torch_urls = [url for url in checksums if "/torch/" in url and "modelscope.cn" in url]
    assert torch_urls
    mirror, official = prefetch.candidate_urls(torch_urls[0], checksums)
    assert mirror.startswith(prefetch.HUGGINGFACE_RAPIDOCR_PREFIX)
    assert official == torch_urls[0]
    assert len(checksums[official]) == 64


def test_checksum_catalog_binds_model_url_not_dictionary() -> None:
    catalog = {
        "rec": {
            "model_dir": "https://example.invalid/model.pth",
            "SHA256": "ABC",
            "dict_url": "https://example.invalid/dict.txt",
        }
    }
    assert prefetch.checksums_by_url(catalog) == {"https://example.invalid/model.pth": "abc"}


def test_hashed_modelscope_url_tries_huggingface_first() -> None:
    body = b"model-bytes"
    digest = hashlib.sha256(body).hexdigest()
    seen: list[str] = []

    def get(url: str, **kwargs: object) -> _Body:
        assert kwargs["timeout"] == (
            prefetch.CONNECT_TIMEOUT_SECONDS,
            prefetch.READ_TIMEOUT_SECONDS,
        )
        seen.append(url)
        return _Body(200, body)

    data = prefetch.fetch_model_bytes(_MODELSCOPE, checksums={_MODELSCOPE: digest}, get=get)
    assert data == body
    assert seen == [_HUGGINGFACE]


def test_unhashed_url_stays_on_the_registry_host() -> None:
    seen: list[str] = []

    def get(url: str, **_kwargs: object) -> _Body:
        seen.append(url)
        return _Body(200, b"dict")

    url = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/paddle/dict.txt"
    assert prefetch.fetch_model_bytes(url, checksums={}, get=get) == b"dict"
    assert seen == [url]


def test_read_timeout_retries_with_backoff() -> None:
    calls = 0

    def get(url: str, **kwargs: object) -> _Body:
        nonlocal calls
        del url
        assert kwargs["timeout"][0] > 5
        calls += 1
        if calls < prefetch.DOWNLOAD_ATTEMPTS:
            raise requests.Timeout("Read timed out. (read timeout=5)")
        return _Body(200, b"weights")

    delays: list[float] = []
    data = prefetch.fetch_model_bytes(
        "https://example.invalid/weights.bin",
        checksums={},
        get=get,
        sleep=delays.append,
    )
    assert data == b"weights"
    assert calls == 5
    assert delays == [2, 4, 8, 16]


def test_huggingface_miss_falls_back_to_modelscope() -> None:
    body = b"from-modelscope"
    digest = hashlib.sha256(body).hexdigest()
    seen: list[str] = []

    def get(url: str, **_kwargs: object) -> _Body:
        seen.append(url)
        if "huggingface.co" in url:
            return _Body(404, b"missing")
        return _Body(200, body)

    data = prefetch.fetch_model_bytes(_MODELSCOPE, checksums={_MODELSCOPE: digest}, get=get)
    assert data == body
    assert seen == [_HUGGINGFACE, _MODELSCOPE]


def test_huggingface_checksum_mismatch_falls_back_to_modelscope() -> None:
    body = b"official"
    digest = hashlib.sha256(body).hexdigest()
    seen: list[str] = []

    def get(url: str, **_kwargs: object) -> _Body:
        seen.append(url)
        if "huggingface.co" in url:
            return _Body(200, b"tampered")
        return _Body(200, body)

    data = prefetch.fetch_model_bytes(_MODELSCOPE, checksums={_MODELSCOPE: digest}, get=get)
    assert data == body
    assert seen == [_HUGGINGFACE, _MODELSCOPE]


def test_discard_invalid_files_removes_bad_or_empty_bytes(tmp_path: Path) -> None:
    url = "https://example.invalid/model.pth"
    bad = tmp_path / "bad.pth"
    bad.write_bytes(b"nope")
    empty = tmp_path / "empty.pth"
    empty.write_bytes(b"")
    good = tmp_path / "good.pth"
    payload = b"ok"
    good.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    prefetch.discard_invalid_files(
        [(bad, url), (empty, "https://example.invalid/empty.pth"), (good, url)],
        {url: digest},
    )
    assert not bad.exists()
    assert not empty.exists()
    assert good.read_bytes() == payload


def test_prefetch_layer_does_not_follow_app_source() -> None:
    dockerfile = (_REPO / "deploy" / "Dockerfile.api").read_text().splitlines()

    def line_no(snippet: str) -> int:
        return next(index for index, line in enumerate(dockerfile) if snippet in line)

    deps = line_no("install-python.sh deps")
    script_copy = line_no("COPY deploy/prefetch_docling_models.py")
    prefetch_run = line_no("python /tmp/prefetch_docling_models.py")
    app_copy = line_no("COPY backend /app/backend")
    assert deps < script_copy < prefetch_run < app_copy
    text = "\n".join(dockerfile)
    assert "DOCLING_ARTIFACTS_PATH=/opt/docling-models" in text
    assert "DOCLING_PREFETCH_CACHE=/var/cache/docling-models" in text
    assert "--mount=type=cache,target=/var/cache/docling-models" in text
    assert prefetch.PREFETCH_CACHE_ENV == "DOCLING_PREFETCH_CACHE"
    assert prefetch.DOWNLOAD_ATTEMPTS == 5
    assert prefetch.CONNECT_TIMEOUT_SECONDS > 5


def test_installer_replaces_docling_rapidocr_downloader() -> None:
    from docling.models.stages.ocr import rapid_ocr_model

    original = rapid_ocr_model.download_url_with_progress

    def get(url: str, **_kwargs: object) -> _Body:
        del url
        return _Body(200, b"abc")

    try:
        prefetch.install_resilient_rapidocr_downloader({}, get=get, sleep=lambda _delay: None)
        downloaded = rapid_ocr_model.download_url_with_progress("https://example.invalid/x.bin")
        assert downloaded.read() == b"abc"
    finally:
        rapid_ocr_model.download_url_with_progress = original


def test_modelscope_checksum_failure_is_not_accepted() -> None:
    url = "https://example.invalid/model.pth"

    def get(fetched: str, **_kwargs: object) -> _Body:
        del fetched
        return _Body(200, b"tampered")

    with pytest.raises(prefetch.ChecksumMismatch):
        prefetch.fetch_model_bytes(url, checksums={url: "a" * 64}, get=get)
