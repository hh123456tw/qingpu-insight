CREATE TABLE IF NOT EXISTS model_versions (
    version_id CHAR(36) NOT NULL,
    market VARCHAR(20) NOT NULL,
    source_run_id CHAR(36) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(50) NOT NULL,
    artifact_path VARCHAR(500) NOT NULL,
    artifact_sha256 CHAR(64) NOT NULL,
    metadata JSON,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (market, version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS published_models (
    market VARCHAR(20) NOT NULL,
    PRIMARY KEY (market),
    version_id CHAR(36) NOT NULL,
    job_run_id CHAR(36) NOT NULL,
    action VARCHAR(20) NOT NULL,
    activated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (market, version_id) REFERENCES model_versions(market, version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS model_release_events (
    event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    market VARCHAR(20) NOT NULL,
    version_id CHAR(36) NOT NULL,
    job_run_id CHAR(36) NOT NULL,
    action VARCHAR(20) NOT NULL,
    previous_version_id CHAR(36),
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS operation_previews (
    preview_id CHAR(36) NOT NULL PRIMARY KEY,
    operation VARCHAR(30) NOT NULL,
    payload JSON NOT NULL,
    confirmation_text VARCHAR(500) NOT NULL,
    expires_at TIMESTAMP(3) NOT NULL,
    consumed_at TIMESTAMP(3) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
