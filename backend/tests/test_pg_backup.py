"""pg_dump must match the Postgres 16 server, and a bad dump must not be kept."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from education_platform.db import pg_backup
from education_platform.db.pg_backup import (
    dump_command,
    fetch_server_version,
    pg_dump_major,
    require_matching_majors,
    server_major,
    write_custom_dump,
)

_REPO = Path(__file__).resolve().parents[2]


def test_client_major_matches_compose_postgres_image() -> None:
    dockerfile = (_REPO / "deploy" / "Dockerfile.api").read_text()
    compose = (_REPO / "deploy" / "docker-compose.prod.yml").read_text()
    script = (_REPO / "deploy" / "migrate.sh").read_text()
    image = re.search(r"image:\s*pgvector/pgvector:pg(\d+)", compose)
    client = re.search(r"postgresql-client-(\d+)", dockerfile)
    assert image is not None and client is not None
    assert image.group(1) == client.group(1) == "16"
    assert "postgresql-client \\" not in dockerfile
    assert "python -m education_platform.db.pg_backup" in script


def test_version_majors() -> None:
    assert pg_dump_major("pg_dump (PostgreSQL) 16.15 (Debian 16.15-1.pgdg12+2)") == 16
    assert pg_dump_major("pg_dump (PostgreSQL) 15.19 (Debian 15.19-0+deb12u1)") == 15
    assert server_major("16.15 (Debian 16.15-1.pgdg12+2)") == 16
    assert server_major("160015") == 16


def test_mismatch_prints_both_versions_without_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        require_matching_majors(
            "pg_dump (PostgreSQL) 15.19 (Debian 15.19-0+deb12u1)",
            "16.15 (Debian 16.15-1.pgdg12+2)",
        )
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "pg_dump version: pg_dump (PostgreSQL) 15.19" in captured.out
    assert "postgres server version: 16.15" in captured.out
    assert "pg_dump major 15 does not match server major 16" in captured.err
    assert "password" not in captured.out + captured.err
    assert "://" not in captured.out + captured.err


def test_matching_majors_print_versions(capsys: pytest.CaptureFixture[str]) -> None:
    require_matching_majors(
        "pg_dump (PostgreSQL) 16.15 (Debian 16.15-1.pgdg12+2)",
        "16.15 (Debian 16.15-1.pgdg12+2)",
    )
    captured = capsys.readouterr()
    assert "pg_dump version: pg_dump (PostgreSQL) 16.15" in captured.out
    assert "postgres server version: 16.15" in captured.out
    assert captured.err == ""


def test_failed_dump_deletes_partial_file(tmp_path: Path) -> None:
    outfile = tmp_path / "education-empty.dump"

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        outfile.write_bytes(b"")
        return subprocess.CompletedProcess(command, 1)

    with pytest.raises(SystemExit) as caught:
        write_custom_dump(outfile, ["pg_dump", "--file", str(outfile)], {}, run=run)
    assert caught.value.code == 1
    assert not outfile.exists()


def test_empty_dump_is_deleted(tmp_path: Path) -> None:
    outfile = tmp_path / "education-empty.dump"

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        outfile.write_bytes(b"")
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(SystemExit) as caught:
        write_custom_dump(outfile, ["pg_dump"], {}, run=run)
    assert caught.value.code == 1
    assert not outfile.exists()


def test_unlistable_dump_is_deleted(tmp_path: Path) -> None:
    outfile = tmp_path / "education-bad.dump"

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "pg_restore":
            return subprocess.CompletedProcess(command, 1, stderr="did not look like a dump")
        outfile.write_bytes(b"not-a-dump")
        return subprocess.CompletedProcess(command, 0)

    with pytest.raises(SystemExit) as caught:
        write_custom_dump(outfile, ["pg_dump"], {}, run=run)
    assert caught.value.code == 1
    assert not outfile.exists()


def test_dump_command_keeps_the_password_out_of_argv() -> None:
    command, env = dump_command(
        "postgresql+psycopg://education:s3cret@postgres:5432/education",
        Path("/backups/education.dump"),
    )
    rendered = " ".join(command)
    assert "s3cret" not in rendered
    assert "://" not in rendered
    assert env["PGPASSWORD"] == "s3cret"
    assert command[command.index("-h") + 1] == "postgres"
    assert command[command.index("-p") + 1] == "5432"
    assert command[command.index("-U") + 1] == "education"
    assert command[command.index("-d") + 1] == "education"
    assert command[command.index("--file") + 1] == "/backups/education.dump"


def test_fetch_server_version_reads_show_server_version(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def fetchone(self) -> tuple[str]:
            return ("16.15 (Debian 16.15-1.pgdg12+2)",)

    class _Conn:
        def execute(self, sql: str) -> _Result:
            assert sql == "SHOW server_version"
            return _Result()

        def __enter__(self) -> _Conn:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    def connect(url: str, **_kwargs: object) -> _Conn:
        assert url == "postgresql://education:s3cret@postgres/education"
        return _Conn()

    monkeypatch.setattr(pg_backup.psycopg, "connect", connect)
    assert (
        fetch_server_version("postgresql+psycopg://education:s3cret@postgres/education")
        == "16.15 (Debian 16.15-1.pgdg12+2)"
    )


def test_main_stops_before_writing_when_majors_differ(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outfile = tmp_path / "education.dump"
    monkeypatch.setenv("BACKUP_FILE", str(outfile))
    monkeypatch.setattr(
        pg_backup, "fetch_server_version", lambda _url: "16.15 (Debian 16.15-1.pgdg12+2)"
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["pg_dump", "--version"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="pg_dump (PostgreSQL) 15.19 (Debian 15.19-0+deb12u1)\n",
        )

    monkeypatch.setattr(pg_backup.subprocess, "run", run)
    with pytest.raises(SystemExit) as caught:
        pg_backup.main()
    assert caught.value.code == 1
    assert not outfile.exists()
    captured = capsys.readouterr()
    assert "pg_dump version: pg_dump (PostgreSQL) 15.19" in captured.out
    assert "postgres server version: 16.15" in captured.out
    assert "password" not in captured.out + captured.err
    assert "://" not in captured.out + captured.err


def test_listed_dump_is_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = tmp_path / "education-ok.dump"

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "pg_restore":
            assert command[1:] == ["--list", str(outfile)]
            return subprocess.CompletedProcess(command, 0, stdout="TOC\n")
        outfile.write_bytes(b"PGDMP")
        return subprocess.CompletedProcess(command, 0)

    write_custom_dump(outfile, ["pg_dump"], {}, run=run)
    assert outfile.read_bytes() == b"PGDMP"
    assert "education-ok.dump (5 bytes)" in capsys.readouterr().out
