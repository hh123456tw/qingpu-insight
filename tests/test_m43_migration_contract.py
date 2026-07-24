from __future__ import annotations

from pathlib import Path


def test_m43_migration_never_drops_backup_records() -> None:
    sql = Path("database/005_m43_health_backup_schema.sql").read_text("utf-8")
    normalized = " ".join(sql.lower().split())
    assert "drop table if exists backup_records" not in normalized
    assert "create table if not exists backup_records" in normalized
