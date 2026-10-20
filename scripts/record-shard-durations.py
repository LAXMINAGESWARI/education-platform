#!/usr/bin/env python3
"""Regenerate backend/tests/shard_durations.txt from the fast pytest suite.

From the repo root:

    make -C backend test-shard-durations

Pass ``--junit path.xml`` to rewrite the file from an existing report instead
of running pytest again.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DURATIONS = BACKEND / "tests" / "shard_durations.txt"


def _file_seconds(junit: Path) -> dict[str, float]:
    root = ET.parse(junit).getroot()
    totals: dict[str, float] = defaultdict(float)
    for case in root.iter("testcase"):
        classname = case.attrib.get("classname", "")
        parts = classname.split(".")
        if len(parts) < 2 or parts[0] != "tests":
            raise SystemExit(f"Unexpected junit classname {classname!r} in {junit}")
        totals[f"{parts[1]}.py"] += float(case.attrib.get("time", "0"))
    if not totals:
        raise SystemExit(f"No testcases found in {junit}")
    return dict(totals)


def _write(durations: dict[str, float]) -> None:
    lines = [
        "# Fast-suite seconds per file (-m \"not ml\").",
        "# Regenerate: make -C backend test-shard-durations",
    ]
    for name, seconds in sorted(durations.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"{seconds:.3f} {name}")
    DURATIONS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, help="Use this junit xml instead of running pytest")
    args = parser.parse_args(argv)
    junit = args.junit
    if junit is None:
        junit = Path("/tmp/curioed-shard-durations.xml")
        subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                "-m",
                "not ml",
                "-n",
                "4",
                "--dist",
                "loadfile",
                "--no-cov",
                f"--junitxml={junit}",
                "-q",
            ],
            cwd=BACKEND,
            check=True,
        )
    _write(_file_seconds(junit))
    print(f"Wrote {DURATIONS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
