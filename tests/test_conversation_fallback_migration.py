from pathlib import Path


def test_fallback_migration_is_idempotent_and_adds_safe_column():
    sql = Path("database/009_conversation_fallback_metadata.sql").read_text("utf-8")

    assert "INFORMATION_SCHEMA.COLUMNS" in sql
    assert "fallback_reason" in sql
    assert "VARCHAR(64)" in sql
    assert "PREPARE" in sql
