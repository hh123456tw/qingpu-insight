from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol

import pymysql


class ProcessResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class UnsafeRestoreTarget(ValueError):
    """Raised when a database name fails safety validation for restore drill."""


class RestoreCleanupFailed(RuntimeError):
    def __init__(self, database_name: str) -> None:
        super().__init__(f"restore cleanup failed: {database_name}")
        self.database_name = database_name


class RepositoryError(RuntimeError):
    """Raised when repository metadata operations fail."""


@dataclass(frozen=True)
class RestoreEvidence:
    database_name: str
    table_names: list[str]
    row_counts: list[int]
    published_versions: dict[str, str] | None
    checked_at: datetime


def validate_restore_database(name: str) -> None:
    if not re.match(r"\Aqingpu_restore_drill_[a-f0-9]{16}\Z", name):
        raise UnsafeRestoreTarget(
            f"Database name {name!r} is not a safe restore target",
        )


def build_restore_database(backup_id: str, attempt_id: str) -> str:
    backup_hex = uuid.UUID(backup_id).hex[:8]
    attempt_hex = uuid.UUID(attempt_id).hex[:8]
    return f"qingpu_restore_drill_{backup_hex}{attempt_hex}"


def resolve_backup_path(backup_dir: Path, stored_path: str, backup_id: str) -> Path:
    root = backup_dir.resolve()
    candidate = (root / stored_path).resolve()
    candidate.relative_to(root)
    if candidate.name != f"{backup_id}.sql":
        raise ValueError(f"Backup file name mismatch: {candidate.name}")
    return candidate


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ProcessRunner(Protocol):
    def run(self, args: Sequence[str], env: Mapping[str, str],
            stdin: str | None = None,
            stdin_path: Path | None = None) -> ProcessResult: ...


class RealRunner:
    def __init__(self, timeout_seconds: int = 300) -> None:
        self._timeout_seconds = timeout_seconds

    def run(self, args: Sequence[str], env: Mapping[str, str],
            stdin: str | None = None,
            stdin_path: Path | None = None) -> ProcessResult:
        try:
            if stdin_path is not None:
                with stdin_path.open("rb") as stream:
                    result = subprocess.run(
                        args, env=env, stdin=stream, capture_output=True, shell=False,
                        timeout=self._timeout_seconds,
                    )
            else:
                result = subprocess.run(
                    args, env=env, input=stdin, capture_output=True, text=True, shell=False,
                    timeout=self._timeout_seconds,
                )
        except subprocess.TimeoutExpired:
            return ProcessResult(124, "", "process_timeout")
        return ProcessResult(result.returncode, result.stdout, result.stderr)


class RecordingRunner:
    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        *,
        dump_content: bytes = b"-- MySQL dump snapshot\n",
        returncodes: list[int] | None = None,
    ) -> None:
        self._returncode = returncode
        self._returncodes = returncodes
        self._call_count = 0
        self._stdout = stdout
        self._stderr = stderr
        self._dump_content = dump_content
        self.args: list[str] = []
        self.env: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.stdin_paths: list[Path | None] = []

    def run(self, args: Sequence[str], env: Mapping[str, str],
            stdin: str | None = None,
            stdin_path: Path | None = None) -> ProcessResult:
        self.args = list(args)
        self.env = dict(env)
        self.calls.append(list(args))
        self.stdin_paths.append(stdin_path)
        if self._dump_content is not None:
            for i, arg in enumerate(args):
                if arg == "--result-file" and i + 1 < len(args):
                    p = Path(args[i + 1])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(self._dump_content)
                    break
        if self._returncodes is not None:
            codes = self._returncodes
            rc = codes[self._call_count] if self._call_count < len(codes) else self._returncode
            self._call_count += 1
            return ProcessResult(rc, self._stdout, self._stderr)
        return ProcessResult(self._returncode, self._stdout, self._stderr)


@dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    status: str
    path: str
    sha256: str
    size_bytes: int
    created_at: datetime
    restore_status: str | None = None
    restore_checked_at: datetime | None = None


class BackupService:
    def __init__(
        self,
        config: object,
        runner: ProcessRunner,
        repository: object,
        backup_dir: Path,
        pymysql_connect: Callable[..., pymysql.Connection] = pymysql.connect,
    ) -> None:
        self._config = config
        self._runner = runner
        self._repository = repository
        self._backup_dir = backup_dir
        self._pymysql_connect = pymysql_connect

    def _safe_mark_restore(self, backup_id: str, status: str, checked_at: datetime) -> None:
        try:
            self._repository.mark_restore(backup_id, status, checked_at)
        except Exception:
            pass

    def create(self) -> BackupRecord:
        backup_id = str(uuid.uuid4())
        partial_path = self._backup_dir / f"{backup_id}.sql.partial"
        final_path = self._backup_dir / f"{backup_id}.sql"
        now = datetime.now(UTC)

        args = [
            "mysqldump",
            f"--host={self._config.mysql_host}",
            f"--port={self._config.mysql_port}",
            f"--user={self._config.mysql_user}",
            "--result-file",
            str(partial_path),
            self._config.mysql_database,
        ]
        env = {
            "MYSQL_PWD": self._config.mysql_password,
        }

        result = self._runner.run(args, env)

        if result.returncode != 0:
            _cleanup(str(partial_path))
            record = BackupRecord(
                backup_id=backup_id,
                status="dump_failed",
                path=f"{backup_id}.sql",
                sha256="",
                size_bytes=0,
                created_at=now,
            )
            self._repository.create(record)
            return record

        try:
            size = partial_path.stat().st_size
        except FileNotFoundError:
            record = BackupRecord(
                backup_id=backup_id,
                status="dump_failed",
                path=f"{backup_id}.sql",
                sha256="",
                size_bytes=0,
                created_at=now,
            )
            self._repository.create(record)
            return record

        if size == 0:
            _cleanup(str(partial_path))
            record = BackupRecord(
                backup_id=backup_id,
                status="checksum_failed",
                path=f"{backup_id}.sql",
                sha256="",
                size_bytes=0,
                created_at=now,
            )
            self._repository.create(record)
            return record

        sha256 = hash_file(partial_path)
        os.replace(str(partial_path), str(final_path))

        record = BackupRecord(
            backup_id=backup_id,
            status="completed",
            path=f"{backup_id}.sql",
            sha256=sha256,
            size_bytes=size,
            created_at=now,
        )
        self._repository.create(record)
        return record


    def restore_drill(self, backup_id: str) -> RestoreEvidence:
        record = self._repository.get(backup_id)
        if record is None:
            raise ValueError(f"Backup {backup_id} not found")
        backup_path = resolve_backup_path(self._backup_dir, record.path, backup_id)
        if not backup_path.exists():
            raise ValueError(f"Backup file {backup_path} not found")
        actual_sha = hash_file(backup_path)
        attempt_id = str(uuid.uuid4())
        drill_db = build_restore_database(backup_id, attempt_id)
        validate_restore_database(drill_db)
        checked_at = datetime.now(UTC)
        if actual_sha != record.sha256:
            self._repository.mark_restore(backup_id, "failed", checked_at)
            raise ValueError("Checksum mismatch")
        env = {"MYSQL_PWD": self._config.mysql_password}
        created = False
        primary_error: Exception | None = None
        try:
            create_args = ["mysql", f"--host={self._config.mysql_host}",
                           f"--port={self._config.mysql_port}",
                           f"--user={self._config.mysql_user}",
                           "-e", f"CREATE DATABASE `{drill_db}`"]
            result = self._runner.run(create_args, env)
            if result.returncode != 0:
                raise RuntimeError("Failed to create database")
            created = True
            import_args = ["mysql", f"--host={self._config.mysql_host}",
                           f"--port={self._config.mysql_port}",
                           f"--user={self._config.mysql_user}",
                           drill_db]
            result = self._runner.run(import_args, env, stdin_path=backup_path)
            if result.returncode != 0:
                raise RuntimeError("Import failed")
            drill_conn = self._pymysql_connect(
                host=self._config.mysql_host,
                port=self._config.mysql_port,
                user=self._config.mysql_user,
                password=self._config.mysql_password,
                database=drill_db,
                charset="utf8mb4",
            )
            try:
                tables = ["job_runs", "dataset_versions",
                          "published_datasets", "listing_current"]
                row_counts: list[int] = []
                with drill_conn.cursor() as cur:
                    for table in tables:
                        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                        row = cur.fetchone()
                        if row is None:
                            raise RuntimeError(f"Table {table} not found or empty")
                        row_counts.append(int(row[0]))
                    cur.execute(
                        "SELECT dataset_key, version"
                        " FROM published_datasets"
                    )
                    pub_rows = cur.fetchall()
                    versions: dict[str, str] = {}
                    for pr in (pub_rows or []):
                        versions[str(pr[0])] = str(pr[1])
            finally:
                drill_conn.close()
            evidence = RestoreEvidence(
                database_name=drill_db,
                table_names=tables,
                row_counts=row_counts,
                published_versions=versions or None,
                checked_at=checked_at,
            )
        except Exception as exc:
            primary_error = exc
        finally:
            if created:
                drop_args = ["mysql", f"--host={self._config.mysql_host}",
                             f"--port={self._config.mysql_port}",
                             f"--user={self._config.mysql_user}",
                             "-e", f"DROP DATABASE IF EXISTS `{drill_db}`"]
                drop_result = self._runner.run(drop_args, env)
                if drop_result.returncode != 0:
                    self._safe_mark_restore(backup_id, "cleanup_failed", checked_at)
                    raise RestoreCleanupFailed(drill_db) from primary_error
        if primary_error is not None:
            self._repository.mark_restore(backup_id, "failed", checked_at)
            raise primary_error
        self._repository.mark_restore(backup_id, "succeeded", checked_at)
        return evidence


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass