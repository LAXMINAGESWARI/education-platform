"""Ready-marker barrier. A marker counts after it is first seen."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.conftest import (
    _finish_run_directory,
    _is_stale_worker_database,
    _remove_other_marker_dirs,
    _wait_for_worker_clones,
)


def test_seen_marker_still_counts_if_a_fast_worker_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = tmp_path / "run"
    ready.mkdir()
    (ready / "gw0").write_text("ready", encoding="utf-8")
    removed = False

    def sleep(_seconds: float) -> None:
        nonlocal removed
        if removed:
            return
        (ready / "gw0").unlink()
        (ready / "gw1").write_text("ready", encoding="utf-8")
        removed = True

    monkeypatch.setattr("tests.conftest.time.sleep", sleep)
    _wait_for_worker_clones(ready, 2)
    assert removed


def test_last_finisher_removes_the_directory_without_unlinking_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_TESTRUNUID", "barrier-unit")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "2")
    ready = Path("/tmp/curioed-xdist-barrier-unit")
    shutil.rmtree(ready, ignore_errors=True)
    ready.mkdir()
    (ready / "gw0").write_text("ready", encoding="utf-8")
    (ready / "gw1").write_text("ready", encoding="utf-8")

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    _finish_run_directory()
    assert (ready / "gw0").is_file()
    assert (ready / "gw1").is_file()
    assert (ready / "finished" / "gw0").is_file()

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    _finish_run_directory()
    assert not ready.exists()


def test_stale_marker_dir_is_removed_and_a_missing_root_is_safe(tmp_path: Path) -> None:
    stale = tmp_path / "curioed-xdist-interrupted"
    stale.mkdir()
    (stale / "gw0").write_text("ready", encoding="utf-8")
    current = tmp_path / "curioed-xdist-current"
    current.mkdir()
    (current / "gw1").write_text("ready", encoding="utf-8")

    _remove_other_marker_dirs(current, root=tmp_path)
    assert not stale.exists()
    assert (current / "gw1").is_file()

    _remove_other_marker_dirs(current, root=tmp_path / "does-not-exist")
    assert (current / "gw1").is_file()


def test_worker_clone_names_match_only_gw_suffixes() -> None:
    assert _is_stale_worker_database("education_test_gw0", "education_test")
    assert _is_stale_worker_database("education_test_gw12", "education_test")
    assert not _is_stale_worker_database("education_test", "education_test")
    assert not _is_stale_worker_database("education_test_gw", "education_test")
    assert not _is_stale_worker_database("other_gw0", "education_test")
