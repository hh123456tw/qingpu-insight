SET @fallback_reason_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'conversation_messages'
      AND COLUMN_NAME = 'fallback_reason'
);

SET @fallback_reason_sql = IF(
    @fallback_reason_exists = 0,
    'ALTER TABLE conversation_messages ADD COLUMN fallback_reason VARCHAR(64) NULL AFTER model',
    'SELECT 1'
);

PREPARE fallback_reason_stmt FROM @fallback_reason_sql;
EXECUTE fallback_reason_stmt;
DEALLOCATE PREPARE fallback_reason_stmt;
