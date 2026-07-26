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


RESTORABLE_TABLES: tuple[str, ...] = (
    "data_refreshes", "market_transactions", "listing_batches",
    "listing_snapshots", "listing_current", "listing_events",
    "listing_valuations", "dataset_versions", "dataset_version_batches",
    "dataset_version_rows", "dataset_version_events", "published_datasets",
    "buyer_reports",
)

MUTATION_JOB_TYPES: frozenset[str] = frozenset({
    "official_data_update", "listing_update", "model_release",
    "backup_create", "database_restore",
})


@dataclass(frozen=True)
class ProductionRestoreResult:
    backup_id: str
    safety_backup_id: str
    rollback_database: str
    table_names: list[str]
    row_counts: list[int]


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


class ProductionRestoreService:
    def __init__(
        self,
        config: object,
        runner: ProcessRunner,
        repository: object,
        backup_dir: Path,
        backup_service: BackupService,
        job_service: object,
        preview_service: object,
        pymysql_connect: Callable[..., pymysql.Connection] = pymysql.connect,
        health_service: object | None = None,
    ) -> None:
        self._config = config
        self._runner = runner
        self._repository = repository
        self._backup_dir = backup_dir
        self._backup_service = backup_service
        self._job_service = job_service
        self._preview_service = preview_service
        self._pymysql_connect = pymysql_connect
        self._health_service = health_service

    def preview(self, backup_id: str) -> object:
        record = self._repository.get(backup_id)
        if record is None:
            raise ValueError(f"Backup {backup_id} not found")
        return self._preview_service.create_for(
            operation="database_restore",
            payload={"backup_id": backup_id},
            confirmation_text=f"還原資料庫 {backup_id[:8]}",
        )

    def submit(self, preview_id: str, confirmation_text: str) -> object:
        preview = self._preview_service.consume(preview_id, confirmation_text)
        backup_id = str(preview.payload.get("backup_id", ""))
        return self._job_service.create(
            "database_restore",
            f"database_restore:{backup_id}",
            "manual",
            input_version=backup_id,
        )

    def execute(self, run_id: str, preview: object) -> ProductionRestoreResult:
        backup_id = str(preview.payload.get("backup_id", ""))
        self._job_service.start(run_id)
        try:
            return self._execute_inner(run_id, backup_id)
        except Exception:
            self._job_service.fail(run_id, "restore_failed", "Production restore failed")
            raise

    def _execute_inner(self, run_id: str, backup_id: str) -> ProductionRestoreResult:
        record = self._repository.get(backup_id)
        if record is None:
            raise ValueError(f"Backup {backup_id} not found")

        backup_path = resolve_backup_path(self._backup_dir, record.path, backup_id)
        if not backup_path.exists():
            raise ValueError(f"Backup file {backup_path} not found")

        actual_sha = hash_file(backup_path)
        if actual_sha != record.sha256:
            raise ValueError("Checksum mismatch")

        for jt in MUTATION_JOB_TYPES:
            try:
                active_list = self._job_service.list_active(jt)
            except Exception:
                active_list = []
            for active in active_list:
                if getattr(active, "run_id", None) != run_id:
                    raise RuntimeError(f"Active mutation job ({jt}) exists")

        safety = self._backup_service.create()
        if safety.status != "completed" or not safety.sha256:
            raise RuntimeError("Safety backup not completed")

        stage_id = uuid.uuid4().hex[:16]
        stage_db = f"qingpu_restore_stage_{stage_id}"
        env = {"MYSQL_PWD": self._config.mysql_password}

        create_result = self._runner.run(
            ["mysql", f"--host={self._config.mysql_host}",
             f"--port={self._config.mysql_port}",
             f"--user={self._config.mysql_user}",
             "-e", f"CREATE DATABASE `{stage_db}`"],
            env,
        )
        if create_result.returncode != 0:
            raise RuntimeError("Failed to create stage database")

        import_result = self._runner.run(
            ["mysql", f"--host={self._config.mysql_host}",
             f"--port={self._config.mysql_port}",
             f"--user={self._config.mysql_user}",
             stage_db],
            env, stdin_path=backup_path,
        )
        if import_result.returncode != 0:
            raise RuntimeError("Import to stage failed")

        stage_conn = self._pymysql_connect(
            host=self._config.mysql_host,
            port=self._config.mysql_port,
            user=self._config.mysql_user,
            password=self._config.mysql_password,
            database=stage_db,
            charset="utf8mb4",
        )
        try:
            row_counts: list[int] = []
            table_names = list(RESTORABLE_TABLES)
            with stage_conn.cursor() as cur:
                for table in table_names:
                    cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError(f"Table {table} not found in stage")
                    row_counts.append(int(row[0]))
        finally:
            stage_conn.close()

        rollback_id = uuid.uuid4().hex[:16]
        rollback_db = f"qingpu_restore_rollback_{rollback_id}"

        create_rb_result = self._runner.run(
            ["mysql", f"--host={self._config.mysql_host}",
             f"--port={self._config.mysql_port}",
             f"--user={self._config.mysql_user}",
             "-e", f"CREATE DATABASE `{rollback_db}`"],
            env,
        )
        if create_rb_result.returncode != 0:
            raise RuntimeError("Failed to create rollback database")

        for table in table_names:
            dump_args = [
                "mysqldump",
                f"--host={self._config.mysql_host}",
                f"--port={self._config.mysql_port}",
                f"--user={self._config.mysql_user}",
                self._config.mysql_database,
                table,
            ]
            dump_result = self._runner.run(dump_args, env)
            if dump_result.returncode != 0:
                raise RuntimeError(f"Failed to dump {table}")
            import_rb_args = [
                "mysql",
                f"--host={self._config.mysql_host}",
                f"--port={self._config.mysql_port}",
                f"--user={self._config.mysql_user}",
                rollback_db,
            ]
            import_rb_result = self._runner.run(
                import_rb_args, env, stdin=dump_result.stdout,
            )
            if import_rb_result.returncode != 0:
                raise RuntimeError(f"Failed to import {table} into rollback")

        rename_parts: list[str] = []
        for table in table_names:
            rename_parts.append(
                f"`{self._config.mysql_database}`.`{table}`"
                f" TO `{rollback_db}`.`{table}`"
            )
        for table in table_names:
            rename_parts.append(
                f"`{stage_db}`.`{table}`"
                f" TO `{self._config.mysql_database}`.`{table}`"
            )
        rename_sql = f"RENAME TABLE {', '.join(rename_parts)}"
        rename_result = self._runner.run(
            ["mysql", f"--host={self._config.mysql_host}",
             f"--port={self._config.mysql_port}",
             f"--user={self._config.mysql_user}",
             "-e", rename_sql],
            env,
        )
        if rename_result.returncode != 0:
            raise RuntimeError("RENAME TABLE failed")

        health_ok = True
        if self._health_service is not None:
            try:
                summary = self._health_service.run()
                if getattr(summary, "status", None) == "critical":
                    health_ok = False
            except Exception:
                health_ok = False

        result = ProductionRestoreResult(
            backup_id=backup_id,
            safety_backup_id=safety.backup_id,
            rollback_database=rollback_db,
            table_names=table_names,
            row_counts=row_counts,
        )

        if not health_ok:
            self._job_service.fail(
                run_id, "health_check_failed",
                "Health check failed after restore; rollback DB retained",
            )
            return result

        self._runner.run(
            ["mysql", f"--host={self._config.mysql_host}",
             f"--port={self._config.mysql_port}",
             f"--user={self._config.mysql_user}",
             "-e", f"DROP DATABASE IF EXISTS `{stage_db}`"],
            env,
        )
        self._runner.run(
            ["mysql", f"--host={self._config.mysql_host}",
             f"--port={self._config.mysql_port}",
             f"--user={self._config.mysql_user}",
             "-e", f"DROP DATABASE IF EXISTS `{rollback_db}`"],
            env,
        )

        self._job_service.succeed(run_id, backup_id, {
            "backup_id": backup_id,
            "safety_backup_id": safety.backup_id,
            "table_names": table_names,
            "row_counts": row_counts,
        })
        return result


class BackupJobService:
    def __init__(self, job_service: object, backup_service: BackupService) -> None:
        self._job_service = job_service
        self._backup_service = backup_service

    def submit_create(self):
        return self._job_service.create(
            "backup_create", "backup_create:active", "manual",
        )

    def submit_restore_drill(self, backup_id: str):
        return self._job_service.create(
            "restore_drill", f"restore_drill:{backup_id}", "manual",
        )

    def execute_create(self, run_id: str) -> BackupRecord:
        self._job_service.start(run_id)
        try:
            record = self._backup_service.create()
            if record.status == "completed":
                self._job_service.succeed(
                    run_id, record.sha256, {
                        "backup_id": record.backup_id,
                        "size_bytes": record.size_bytes,
                        "sha256": record.sha256,
                    },
                )
            else:
                self._job_service.fail(
                    run_id, record.status,
                    f"backup status: {record.status}",
                )
            return record
        except Exception:
            _safe_fail(self._job_service, run_id, "backup_create_failed")
            raise

    def execute_restore_drill(self, run_id: str, backup_id: str) -> RestoreEvidence:
        self._job_service.start(run_id)
        try:
            evidence = self._backup_service.restore_drill(backup_id)
            self._job_service.succeed(
                run_id, backup_id, {
                    "database_name": evidence.database_name,
                    "table_names": evidence.table_names,
                    "row_counts": evidence.row_counts,
                },
            )
            return evidence
        except Exception:
            _safe_fail(self._job_service, run_id, "restore_drill_failed")
            raise


def _safe_fail(job_service, run_id: str, error_code: str) -> None:
    run = job_service.get(run_id)
    if run is not None and run.status == "running":
        job_service.fail(run_id, error_code, f"{error_code}")


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass