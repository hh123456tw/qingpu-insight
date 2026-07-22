CREATE TABLE IF NOT EXISTS job_runs (
    run_id VARCHAR(36) NOT NULL PRIMARY KEY,
    job_type VARCHAR(64) NOT NULL,
    trigger VARCHAR(32) NOT NULL,
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
