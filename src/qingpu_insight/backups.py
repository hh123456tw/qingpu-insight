from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol


class ProcessResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    def run(self, args: Sequence[str], env: Mapping[str, str]) -> ProcessResult: ...


class RealRunner:
    def run(self, args: Sequence[str], env: Mapping[str, str]) -> ProcessResult:
        result = subprocess.run(
            args, env=env, capture_output=True, text=True, shell=False,
        )
        return ProcessResult(result.returncode, result.stdout, result.stderr)


class RecordingRunner:
    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        *,
        dump_content: bytes = b"-- MySQL dump snapshot\n",
    ) -> None:
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._dump_content = dump_content
        self.args: list[str] = []
        self.env: dict[str, str] = {}

    def run(self, args: Sequence[str], env: Mapping[str, str]) -> ProcessResult:
        self.args = list(args)
        self.env = dict(env)
        if self._dump_content is not None:
            for i, arg in enumerate(args):
                if arg == "--result-file" and i + 1 < len(args):
                    p = Path(args[i + 1])
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(self._dump_content)
                    break
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
    ) -> None:
        self._config = config
        self._runner = runner
        self._repository = repository
        self._backup_dir = backup_dir

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

        sha256 = hashlib.sha256(partial_path.read_bytes()).hexdigest()
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


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass