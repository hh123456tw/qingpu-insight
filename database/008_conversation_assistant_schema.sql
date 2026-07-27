CREATE TABLE IF NOT EXISTS conversations (
    id CHAR(36) PRIMARY KEY,
    title VARCHAR(160) NOT NULL,
    status VARCHAR(32) NOT NULL,
    default_provider VARCHAR(32) NOT NULL,
    default_model VARCHAR(120) NOT NULL,
    active_listing_id CHAR(36) NULL,
    active_evidence_revision INT NULL,
    rolling_summary TEXT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    deleted_at DATETIME(6) NULL,
    INDEX idx_conversations_updated_id (updated_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS conversation_listings (
    id CHAR(36) PRIMARY KEY,
    conversation_id CHAR(36) NOT NULL,
    position SMALLINT UNSIGNED NOT NULL,
    listing_type VARCHAR(16) NULL,
    source_listing_id VARCHAR(64) NULL,
    canonical_url VARCHAR(2048) NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_conversation_listing_position (conversation_id, position),
    CONSTRAINT fk_conversation_listing_conversation
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS conversation_listing_snapshots (
    id CHAR(36) PRIMARY KEY,
    conversation_listing_id CHAR(36) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    structured_payload JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    UNIQUE KEY uq_conversation_snapshot_revision (conversation_listing_id, revision),
    CONSTRAINT fk_conversation_snapshot_listing
        FOREIGN KEY (conversation_listing_id) REFERENCES conversation_listings(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS conversation_evidence_packs (
    id CHAR(36) PRIMARY KEY,
    conversation_id CHAR(36) NOT NULL,
    conversation_listing_snapshot_id CHAR(36) NOT NULL,
    revision INT UNSIGNED NOT NULL,
    generated_at DATETIME(6) NOT NULL,
    facts JSON NOT NULL,
    valuation JSON NULL,
    comparables JSON NOT NULL,
    limitations JSON NOT NULL,
    UNIQUE KEY uq_conversation_evidence_revision (conversation_id, revision),
    CONSTRAINT fk_conversation_evidence_conversation
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    CONSTRAINT fk_conversation_evidence_snapshot
        FOREIGN KEY (conversation_listing_snapshot_id)
        REFERENCES conversation_listing_snapshots(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS conversation_messages (
    id CHAR(36) PRIMARY KEY,
    conversation_id CHAR(36) NOT NULL,
    sequence_no BIGINT UNSIGNED NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    evidence_revision INT NULL,
    provider VARCHAR(32) NULL,
    model VARCHAR(120) NULL,
    citations JSON NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_conversation_message_sequence (conversation_id, sequence_no),
    INDEX idx_conversation_message_pagination (conversation_id, sequence_no),
    CONSTRAINT fk_conversation_message_conversation
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @conversation_fk_exists = (
    SELECT COUNT(*)
    FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversations'
      AND CONSTRAINT_NAME = 'fk_conversation_active_listing'
);
SET @conversation_fk_sql = IF(
    @conversation_fk_exists = 0,
    'ALTER TABLE conversations ADD CONSTRAINT fk_conversation_active_listing FOREIGN KEY (active_listing_id) REFERENCES conversation_listings(id) ON DELETE SET NULL',
    'SELECT 1'
);
PREPARE conversation_fk_statement FROM @conversation_fk_sql;
EXECUTE conversation_fk_statement;
DEALLOCATE PREPARE conversation_fk_statement;
