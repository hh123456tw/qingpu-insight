CREATE TABLE IF NOT EXISTS buyer_reports (
    report_id VARCHAR(64) NOT NULL PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    dataset_version VARCHAR(64) NOT NULL,
    evidence_pack_id VARCHAR(64) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    model VARCHAR(64) NOT NULL,
    content JSON NOT NULL,
    fallback_reason VARCHAR(64) NULL,
    validation_codes JSON NOT NULL DEFAULT ('[]'),
    latency_ms DOUBLE NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_buyer_reports_request_hash (request_hash),
    INDEX idx_buyer_reports_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
