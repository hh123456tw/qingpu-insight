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
