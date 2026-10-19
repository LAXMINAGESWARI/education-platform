"""The migration graph must upgrade with a single ``alembic upgrade head``."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import Script, ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# These four revisions joined older branches. The graph from the current head
# back through Jev enforce and the gateway table is a straight line on top of them.
_HISTORICAL_MERGES = frozenset(
    {
        "daf396e7122c",
        "a0b1c2d3e4f5",
        "7eb14ce798c5",
        "a352c5d4a076",
    }
)


def _script() -> ScriptDirectory:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def _parents(revision: Script) -> tuple[str, ...]:
    down = revision.down_revision
    if down is None:
        return ()
    if isinstance(down, tuple):
        return down
    return (down,)


def test_alembic_script_has_one_head_and_a_linear_tip() -> None:
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1
    assert len(script.get_bases()) == 1

    head_id = heads[0]
    reachable: set[str] = set()
    pending = [head_id]
    while pending:
        revision_id = pending.pop()
        if revision_id in reachable:
            continue
        reachable.add(revision_id)
        revision = script.get_revision(revision_id)
        assert revision is not None
        pending.extend(_parents(revision))

    every_revision = {revision.revision for revision in script.walk_revisions()}
    assert reachable == every_revision

    tip: list[str] = []
    current: Script | None = script.get_revision(head_id)
    while current is not None and isinstance(current.down_revision, str):
        tip.append(current.revision)
        parent_id = current.down_revision
        current = script.get_revision(parent_id)
    assert tip[0] == "b3c4d5e6f7a8"
    assert tip[1] == "d6e7f8a9b0c1"
    assert tip[2] == "c5d6e7f8a9b0"

    merges = {
        revision.revision
        for revision in script.walk_revisions()
        if isinstance(revision.down_revision, tuple)
    }
    assert merges == _HISTORICAL_MERGES
