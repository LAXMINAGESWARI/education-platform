"""Custom-format backup used by deploy/migrate.sh before topic promotion.

Prints the pg_dump and server versions, refuses a major-version mismatch, and
deletes a dump that pg_dump did not finish or that pg_restore cannot list.
Connection passwords stay in the pg_dump environment and are not printed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from education_platform.core.config import get_settings
from education_platform.db.url import to_sync_url

Runner = Callable[..., subprocess.CompletedProcess[str]]


def pg_dump_major(version_text: str) -> int:
    """Major version from ``pg_dump --version``."""
    marker = "(PostgreSQL)"
    if marker not in version_text:
        raise ValueError(f"unrecognized pg_dump version: {version_text.strip()}")
    major = version_text.split(marker, 1)[1].strip().split(".", 1)[0]
    if not major.isdigit():
        raise ValueError(f"unrecognized pg_dump version: {version_text.strip()}")
    return int(major)


def server_major(version_text: str) -> int:
    """Major version from ``SHOW server_version`` or ``server_version_num``."""
    text = version_text.strip()
    if text.isdigit():
        number = int(text)
        if number >= 10000:
            return number // 10000
        return number
    major = text.split(".", 1)[0]
    if not major.isdigit():
        raise ValueError(f"unrecognized server version: {text}")
    return int(major)


def require_matching_majors(pg_dump_version: str, server_version: str) -> None:
    """Print both versions and exit when their major numbers differ."""
    dump_major = pg_dump_major(pg_dump_version)
    server = server_major(server_version)
    print(f"pg_dump version: {pg_dump_version.strip()}")
    print(f"postgres server version: {server_version.strip()}")
    if dump_major != server:
        print(
            f"pg_dump major {dump_major} does not match server major {server}. "
            "Topic promotion was not started.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _fail(outfile: Path, message: str, code: int) -> None:
    outfile.unlink(missing_ok=True)
    print(message, file=sys.stderr)
    raise SystemExit(code)


def write_custom_dump(
    outfile: Path,
    command: Sequence[str],
    env: Mapping[str, str],
    *,
    run: Runner,
) -> None:
    """Write a custom-format dump and keep it only when pg_restore can list it."""
    completed = run(list(command), env=dict(env), check=False)
    if completed.returncode != 0:
        _fail(
            outfile,
            "Database backup failed. Topic promotion was not started.",
            completed.returncode or 1,
        )
    if not outfile.is_file() or outfile.stat().st_size <= 0:
        _fail(outfile, "Database backup was empty. Topic promotion was not started.", 1)
    listed = run(
        ["pg_restore", "--list", os.fspath(outfile)],
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        detail = listed.stderr or ""
        if isinstance(detail, bytes):
            detail = detail.decode(errors="replace")
        detail = detail.strip()
        message = "Database backup could not be read. Topic promotion was not started."
        if detail:
            message = f"{message} {detail}"
        _fail(outfile, message, listed.returncode or 1)
    print(f"Database backup written: {outfile.name} ({outfile.stat().st_size} bytes)")


def libpq_url(sync_url: str) -> str:
    """psycopg and libpq want postgresql://, not the SQLAlchemy driver suffix."""
    prefix = "postgresql+psycopg://"
    if sync_url.startswith(prefix):
        return "postgresql://" + sync_url.removeprefix(prefix)
    return sync_url


def fetch_server_version(sync_url: str) -> str:
    with psycopg.connect(libpq_url(sync_url), connect_timeout=10) as conn:
        row = conn.execute("SHOW server_version").fetchone()
    if row is None or row[0] is None:
        print(
            "Could not read the server version. Topic promotion was not started.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return str(row[0])


def dump_command(sync_url: str, outfile: Path) -> tuple[list[str], dict[str, str]]:
    parsed = urlparse(libpq_url(sync_url))
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--file",
        os.fspath(outfile),
        "-h",
        parsed.hostname or "localhost",
        "-p",
        str(parsed.port or 5432),
        "-U",
        parsed.username or "education",
        "-d",
        (parsed.path or "/education").lstrip("/"),
    ]
    return command, env


def main() -> None:
    outfile = Path(os.environ["BACKUP_FILE"])
    sync_url = to_sync_url(get_settings().database_url)
    version = subprocess.run(
        ["pg_dump", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if version.returncode != 0:
        detail = (version.stderr or version.stdout or "").strip()
        print(
            f"pg_dump --version failed. Topic promotion was not started. {detail}",
            file=sys.stderr,
        )
        raise SystemExit(version.returncode or 1)
    try:
        server_version = fetch_server_version(sync_url)
    except SystemExit:
        raise
    except Exception:
        print(
            "Could not read the server version. Topic promotion was not started.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    require_matching_majors(version.stdout.strip(), server_version)
    command, env = dump_command(sync_url, outfile)
    write_custom_dump(outfile, command, env, run=subprocess.run)


if __name__ == "__main__":
    main()
