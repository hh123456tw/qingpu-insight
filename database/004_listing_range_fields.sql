USE qingpu_insight;

DROP PROCEDURE IF EXISTS qingpu_add_column_if_missing;

DELIMITER //
CREATE PROCEDURE qingpu_add_column_if_missing(
  IN target_table VARCHAR(64),
  IN target_column VARCHAR(64),
  IN column_definition VARCHAR(255)
)
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = target_table
      AND COLUMN_NAME = target_column
  ) THEN
    SET @ddl = CONCAT(
      'ALTER TABLE `', target_table,
      '` ADD COLUMN `', target_column, '` ', column_definition
    );
    PREPARE migration_statement FROM @ddl;
    EXECUTE migration_statement;
    DEALLOCATE PREPARE migration_statement;
  END IF;
END//
DELIMITER ;

CALL qingpu_add_column_if_missing(
  'listing_snapshots',
  'asking_unit_price_low_twd_per_ping',
  'BIGINT UNSIGNED NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_snapshots',
  'asking_unit_price_high_twd_per_ping',
  'BIGINT UNSIGNED NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_snapshots',
  'building_area_min_ping',
  'DECIMAL(10,2) NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_snapshots',
  'building_area_max_ping',
  'DECIMAL(10,2) NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_snapshots',
  'acquisition_representation',
  'VARCHAR(24) NULL DEFAULT NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_snapshots',
  'acquisition_schema_version',
  'VARCHAR(64) NULL DEFAULT NULL'
);

CALL qingpu_add_column_if_missing(
  'listing_current',
  'asking_unit_price_low_twd_per_ping',
  'BIGINT UNSIGNED NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_current',
  'asking_unit_price_high_twd_per_ping',
  'BIGINT UNSIGNED NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_current',
  'building_area_min_ping',
  'DECIMAL(10,2) NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_current',
  'building_area_max_ping',
  'DECIMAL(10,2) NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_current',
  'acquisition_representation',
  'VARCHAR(24) NULL DEFAULT NULL'
);
CALL qingpu_add_column_if_missing(
  'listing_current',
  'acquisition_schema_version',
  'VARCHAR(64) NULL DEFAULT NULL'
);

UPDATE listing_snapshots
SET acquisition_representation = 'unknown'
WHERE acquisition_representation IS NULL OR acquisition_representation = '';

UPDATE listing_snapshots
SET acquisition_schema_version = 'unknown'
WHERE acquisition_schema_version IS NULL OR acquisition_schema_version = '';

UPDATE listing_current
SET acquisition_representation = 'unknown'
WHERE acquisition_representation IS NULL OR acquisition_representation = '';

UPDATE listing_current
SET acquisition_schema_version = 'unknown'
WHERE acquisition_schema_version IS NULL OR acquisition_schema_version = '';

ALTER TABLE listing_snapshots
  MODIFY COLUMN acquisition_representation VARCHAR(24) NOT NULL DEFAULT 'unknown',
  MODIFY COLUMN acquisition_schema_version VARCHAR(64) NOT NULL DEFAULT 'unknown';

ALTER TABLE listing_current
  MODIFY COLUMN acquisition_representation VARCHAR(24) NOT NULL DEFAULT 'unknown',
  MODIFY COLUMN acquisition_schema_version VARCHAR(64) NOT NULL DEFAULT 'unknown';

DROP PROCEDURE IF EXISTS qingpu_add_column_if_missing;
