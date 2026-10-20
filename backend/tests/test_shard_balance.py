"""Fast CI shards are packed by recorded file duration."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.conftest import _assign_file_shards, _load_shard_durations


def test_longest_files_land_on_opposite_shards() -> None:
    durations = {"slow.py": 100.0, "also_slow.py": 90.0, "fast.py": 1.0}
    assignment = _assign_file_shards(list(durations), durations, 2)
    assert assignment["slow.py"] == 0
    assert assignment["also_slow.py"] == 1
    assert assignment["fast.py"] == 1


def test_unrecorded_file_still_gets_a_shard() -> None:
    assignment = _assign_file_shards(["known.py", "new.py"], {"known.py": 10.0}, 2)
    assert assignment["known.py"] == 0
    assert assignment["new.py"] == 1


def test_missing_durations_keep_the_filename_hash() -> None:
    names = ["a.py", "b.py"]
    assignment = _assign_file_shards(names, {}, 2)
    for name in names:
        bucket = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % 2
        assert assignment[name] == bucket


def test_duration_file_comments_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "shard_durations.txt"
    path.write_text("# comment\n1.5 test_a.py\n\n2.0 test_b.py  # note\n", encoding="utf-8")
    assert _load_shard_durations(path) == {"test_a.py": 1.5, "test_b.py": 2.0}


def test_committed_durations_balance_two_shards() -> None:
    durations = _load_shard_durations()
    assert len(durations) >= 2
    assignment = _assign_file_shards(list(durations), durations, 2)
    totals = [0.0, 0.0]
    for name, seconds in durations.items():
        totals[assignment[name]] += seconds
    assert abs(totals[0] - totals[1]) / max(totals) < 0.05
    heaviest = sorted(durations, key=lambda name: (-durations[name], name))[:2]
    assert assignment[heaviest[0]] != assignment[heaviest[1]]
