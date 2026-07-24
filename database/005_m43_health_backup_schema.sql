CREATE TABLE IF NOT EXISTS health_runs (
    run_id VARCHAR(36) NOT NULL PRIMARY KEY,
    status VARCHAR(16) NOT NULL,
    checked_at DATETIME(3) NOT NULL,
    summary JSON NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS health_items (
    run_id VARCHAR(36) NOT NULL,
    code VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    observed_at DATETIME(3) NOT NULL,
    summary VARCHAR(255) NOT NULL DEFAULT '',
    value DOUBLE NULL,
    unit VARCHAR(32) NULL,
    PRIMARY KEY (run_id, code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Forward-compatible backup_records: never DROP, only CREATE IF NOT EXISTS + ALTER for upgrades
CREATE TABLE IF NOT EXISTS backup_records (
    backup_id VARCHAR(36) NOT NULL PRIMARY KEY,
    status VARCHAR(32) NOT NULL,
    path VARCHAR(1024) NOT NULL,
    sha256 CHAR(64) NOT NULL DEFAULT '',
    size_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    restore_status VARCHAR(32) NULL,
    restore_checked_at DATETIME(3) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Migrate from old schema: add sha256 if missing, then backfill from checksum
SET @has_sha256 = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'backup_records' AND COLUMN_NAME = 'sha256');
SET @add_sql = IF(@has_sha256 = 0,
    'ALTER TABLE backup_records ADD COLUMN sha256 CHAR(64) NOT NULL DEFAULT \'\' AFTER path',
    'SELECT 1');
PREPARE stmt FROM @add_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @has_checksum = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'backup_records' AND COLUMN_NAME = 'checksum');
SET @migrate_sql = IF(@has_checksum > 0,
    'UPDATE backup_records SET sha256 = checksum WHERE sha256 = \'\'',
    'SELECT 1');
PREPARE stmt FROM @migrate_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
