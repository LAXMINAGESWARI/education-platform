"""Load the versioned chat-guard eval set (and the optional golden-eval xlsx)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

LABELS = frozenset({"injection", "off_topic", "benign"})
SURFACES = ("policy", "review", "data")
DEFAULT_DATASET = Path(__file__).resolve().parent / "datasets" / "chat_guard_eval_v1.json"

# Golden-eval xlsx rows that are injection attacks (see the text_to_sql
# injection_guard / question_validator docstrings).
XLSX_INJECTION_IDS = frozenset({35, 36, 37, 38})


@dataclass(frozen=True, slots=True)
class Row:
    id: str
    text: str
    label: str
    category: str
    surfaces: tuple[str, ...]
    history: tuple[tuple[str, str], ...] = ()
    source: str = "handwritten"
    note: str | None = None

    def in_scope(self, surface: str) -> bool:
        """Injection rows apply everywhere; scope labels only on listed surfaces."""
        return self.label == "injection" or surface in self.surfaces


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    version: str
    sha256: str
    rows: tuple[Row, ...]
    sources: dict[str, int] = field(default_factory=dict)


def _row(raw: dict[str, object]) -> Row:
    label = str(raw["label"])
    if label not in LABELS:
        raise ValueError(f"row {raw.get('id')}: unknown label {label!r}")
    surfaces = tuple(str(s) for s in raw.get("surfaces") or ())  # type: ignore[union-attr]
    unknown = set(surfaces) - set(SURFACES)
    if unknown or not surfaces:
        raise ValueError(f"row {raw.get('id')}: bad surfaces {surfaces!r}")
    history_raw = raw.get("history") or []
    history = tuple(
        (str(turn["role"]), str(turn["content"]))  # type: ignore[index]
        for turn in history_raw  # type: ignore[union-attr]
    )
    note = raw.get("note")
    return Row(
        id=str(raw["id"]),
        text=str(raw["text"]),
        label=label,
        category=str(raw["category"]),
        surfaces=surfaces,
        history=history,
        source=str(raw.get("source") or "handwritten"),
        note=str(note) if note is not None else None,
    )


def load_dataset(
    path: Path = DEFAULT_DATASET,
    *,
    xlsx: Path | None = None,
    xlsx_off_topic_ids: frozenset[int] = frozenset(),
) -> Dataset:
    data = path.read_bytes()
    payload = json.loads(data)
    rows = [_row(item) for item in payload["rows"]]
    if xlsx is not None:
        rows.extend(load_xlsx_rows(xlsx, off_topic_ids=xlsx_off_topic_ids))
    ids = [row.id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate row ids in the eval set")
    sources: dict[str, int] = {}
    for row in rows:
        sources[row.source] = sources.get(row.source, 0) + 1
    digest = hashlib.sha256(data)
    if xlsx is not None:
        digest.update(xlsx.read_bytes())
    return Dataset(
        name=str(payload["name"]),
        version=str(payload["version"]),
        sha256=digest.hexdigest(),
        rows=tuple(rows),
        sources=sources,
    )


def load_xlsx_rows(path: Path, *, off_topic_ids: frozenset[int] = frozenset()) -> list[Row]:
    """Text-to-SQL golden eval sheet ("Eval Questions", data from row 3; column 0 = id,
    3 = question, 11 = validated SQL, the same columns ``eval_live._xlsx_rows`` reads).

    Ids 35-38 are attacks and are kept even without validated SQL (``eval_live`` skips
    rows without SQL, which can drop the attacks, and tags every row ``injection``). Ids in
    ``off_topic_ids`` (``--xlsx-off-topic-ids``) are labelled off_topic for the data
    surface. Every other row with validated SQL is a benign data question.
    """
    import openpyxl  # type: ignore[import-untyped]  # transitive dep via docling

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = workbook["Eval Questions"]
    rows: list[Row] = []
    for record in sheet.iter_rows(min_row=3, values_only=True):
        qid, question = record[0], record[3]
        validated_sql = record[11] if len(record) > 11 else None
        if qid is None or not question:
            continue
        number = int(qid)
        if number in XLSX_INJECTION_IDS:
            label, category, surfaces = "injection", "xlsx_injection", SURFACES
        elif number in off_topic_ids:
            label, category, surfaces = "off_topic", "off_topic", ("data",)
        elif validated_sql:
            label, category, surfaces = "benign", "benign_teacher", ("data",)
        else:
            continue
        rows.append(
            Row(
                id=f"xlsx-{number}",
                text=str(question),
                label=label,
                category=category,
                surfaces=tuple(surfaces),
                source="golden_eval_xlsx",
            )
        )
    return rows
