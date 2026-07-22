CREATE TABLE IF NOT EXISTS job_runs (
    run_id VARCHAR(36) NOT NULL PRIMARY KEY,
    job_type VARCHAR(64) NOT NULL,
    `trigger` VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    active_idempotency_key VARCHAR(255)
        GENERATED ALWAYS AS (
            CASE
                WHEN status IN ('pending', 'running', 'retry_wait') THEN idempotency_key
                ELSE NULL
            END
        ) STORED,
    started_at DATETIME(3) NULL,
    finished_at DATETIME(3) NULL,
    attempt INT NOT NULL DEFAULT 1,
    input_version VARCHAR(64) NULL,
    output_version VARCHAR(64) NULL,
    summary JSON NOT NULL,
    error_code VARCHAR(64) NULL,
    error_message TEXT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL
        DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE INDEX uq_job_runs_active_key (active_idempotency_key),
    INDEX idx_idempotency_key (idempotency_key),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP PROCEDURE IF EXISTS upgrade_job_runs_active_key;

DELIMITER //
CREATE PROCEDURE upgrade_job_runs_active_key()
BEGIN
    DECLARE active_column_count INT DEFAULT 0;
    DECLARE active_index_count INT DEFAULT 0;
    DECLARE duplicate_key_count INT DEFAULT 0;

    SELECT COUNT(*)
      INTO active_column_count
      FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'job_runs'
       AND COLUMN_NAME = 'active_idempotency_key';

    IF active_column_count = 0 THEN
        ALTER TABLE job_runs
            ADD COLUMN active_idempotency_key VARCHAR(255)
            GENERATED ALWAYS AS (
                CASE
                    WHEN status IN ('pending', 'running', 'retry_wait') THEN idempotency_key
                    ELSE NULL
                END
            ) STORED;
    END IF;

    SELECT COUNT(*)
      INTO active_index_count
      FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'job_runs'
       AND INDEX_NAME = 'uq_job_runs_active_key';

    IF active_index_count = 0 THEN
        SELECT COUNT(*)
          INTO duplicate_key_count
          FROM (
              SELECT idempotency_key
                FROM job_runs
               WHERE status IN ('pending', 'running', 'retry_wait')
               GROUP BY idempotency_key
              HAVING COUNT(*) > 1
          ) AS duplicate_active_keys;

        IF duplicate_key_count > 0 THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT =
                    'job_runs has duplicate active idempotency keys; history was not modified';
        END IF;

        ALTER TABLE job_runs
            ADD UNIQUE INDEX uq_job_runs_active_key (active_idempotency_key);
    END IF;
END//
DELIMITER ;

CALL upgrade_job_runs_active_key();
DROP PROCEDURE upgrade_job_runs_active_key;

CREATE TABLE IF NOT EXISTS dataset_versions (
    dataset_key VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    status VARCHAR(32) NOT NULL,
    summary JSON NOT NULL,
    artifact_path VARCHAR(1024) NOT NULL,
    artifact_hash CHAR(64) NOT NULL,
    artifact_row_count BIGINT UNSIGNED NOT NULL,
    rows_hash CHAR(64) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (dataset_key, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP PROCEDURE IF EXISTS upgrade_dataset_versions_scope;

DELIMITER //
CREATE PROCEDURE upgrade_dataset_versions_scope()
BEGIN
    DECLARE column_count INT DEFAULT 0;
    DECLARE primary_scope_count INT DEFAULT 0;
    DECLARE incomplete_row_count INT DEFAULT 0;

    SELECT COUNT(*) INTO column_count
      FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'dataset_versions'
       AND COLUMN_NAME = 'artifact_path';
    IF column_count = 0 THEN
        ALTER TABLE dataset_versions
            ADD COLUMN artifact_path VARCHAR(1024) NULL AFTER summary;
    END IF;

    SELECT COUNT(*) INTO column_count
      FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'dataset_versions'
       AND COLUMN_NAME = 'rows_hash';
    IF column_count = 0 THEN
        ALTER TABLE dataset_versions
            ADD COLUMN rows_hash CHAR(64) NULL AFTER artifact_row_count;
    END IF;

    SELECT COUNT(*) INTO incomplete_row_count
      FROM dataset_versions
     WHERE artifact_path IS NULL OR artifact_path = ''
        OR artifact_hash IS NULL OR artifact_hash = ''
        OR artifact_row_count IS NULL
        OR rows_hash IS NULL OR rows_hash = '';

    IF incomplete_row_count > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT =
                'dataset_versions contains legacy rows without immutable artifact metadata; rows were preserved';
    END IF;

    ALTER TABLE dataset_versions
        MODIFY COLUMN artifact_path VARCHAR(1024) NOT NULL,
        MODIFY COLUMN artifact_hash CHAR(64) NOT NULL,
        MODIFY COLUMN artifact_row_count BIGINT UNSIGNED NOT NULL,
        MODIFY COLUMN rows_hash CHAR(64) NOT NULL;

    SELECT COUNT(*) INTO primary_scope_count
      FROM (
          SELECT INDEX_NAME
            FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA = DATABASE()
             AND TABLE_NAME = 'dataset_versions'
             AND INDEX_NAME = 'PRIMARY'
           GROUP BY INDEX_NAME
          HAVING GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) = 'dataset_key,version'
      ) AS scoped_primary;

    IF primary_scope_count = 0 THEN
        ALTER TABLE dataset_versions
            DROP PRIMARY KEY,
            ADD PRIMARY KEY (dataset_key, version);
    END IF;
END//
DELIMITER ;

CALL upgrade_dataset_versions_scope();
DROP PROCEDURE upgrade_dataset_versions_scope;

CREATE TABLE IF NOT EXISTS dataset_version_batches (
    dataset_key VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    batch_id VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    PRIMARY KEY (dataset_key, version, batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dataset_version_rows (
    dataset_key VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    `row_number` BIGINT UNSIGNED NOT NULL,
    payload JSON NOT NULL,
    row_hash CHAR(64) NOT NULL,
    PRIMARY KEY (dataset_key, version, `row_number`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dataset_version_events (
    dataset_key VARCHAR(64) NOT NULL,
    version VARCHAR(64) NOT NULL,
    event_key VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    PRIMARY KEY (dataset_key, version, event_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS dataset_publish_locks (
    dataset_key VARCHAR(64) NOT NULL PRIMARY KEY,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS published_datasets (
    dataset_key VARCHAR(64) NOT NULL PRIMARY KEY,
    version VARCHAR(64) NULL,
    published_at DATETIME(3) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP PROCEDURE IF EXISTS upgrade_published_dataset_pointer;

DELIMITER //
CREATE PROCEDURE upgrade_published_dataset_pointer()
BEGIN
    DECLARE nullable_version_count INT DEFAULT 0;

    SELECT COUNT(*) INTO nullable_version_count
      FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'published_datasets'
       AND COLUMN_NAME = 'version'
       AND IS_NULLABLE = 'YES';

    IF nullable_version_count = 0 THEN
        ALTER TABLE published_datasets
            MODIFY COLUMN version VARCHAR(64) NULL,
            MODIFY COLUMN published_at DATETIME(3) NULL;
    END IF;
END//
DELIMITER ;

CALL upgrade_published_dataset_pointer();
DROP PROCEDURE upgrade_published_dataset_pointer;
