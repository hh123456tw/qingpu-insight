USE qingpu_insight;

-- Track capture-batch metadata
CREATE TABLE IF NOT EXISTS listing_batches (
  batch_id        VARCHAR(64) NOT NULL PRIMARY KEY,
  source          VARCHAR(32) NOT NULL,
  listing_type    ENUM('sale','newhouse','rental') NOT NULL,
  started_at      DATETIME NOT NULL,
  reached_terminal_page BOOLEAN NOT NULL DEFAULT FALSE,
  error_count     SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Per-listing snapshot history (one row per batch inclusion)
CREATE TABLE IF NOT EXISTS listing_snapshots (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  batch_id        VARCHAR(64) NOT NULL,
  source          VARCHAR(32) NOT NULL,
  listing_type    ENUM('sale','newhouse','rental') NOT NULL,
  source_listing_id VARCHAR(64) NOT NULL,
  snapshot_at     DATETIME NOT NULL,
  source_url      VARCHAR(512) NOT NULL,
  title           VARCHAR(256) NOT NULL DEFAULT '',
  asking_price_twd    BIGINT UNSIGNED NULL,
  monthly_rent_twd    BIGINT UNSIGNED NULL,
  building_area_ping  DECIMAL(10,2) NULL,
  building_type       VARCHAR(80) NULL,
  bedrooms        TINYINT UNSIGNED NULL,
  living_rooms    TINYINT UNSIGNED NULL,
  bathrooms       TINYINT UNSIGNED NULL,
  building_age_years  DECIMAL(6,2) NULL,
  floor           TINYINT UNSIGNED NULL,
  total_floors    TINYINT UNSIGNED NULL,
  parking_type    VARCHAR(80) NULL,
  latitude        DECIMAL(10,7) NULL,
  longitude       DECIMAL(10,7) NULL,
  station_code    VARCHAR(16) NULL,
  raw_hash        CHAR(64) NOT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_snapshot (batch_id, source, listing_type, source_listing_id),
  KEY ix_snapshot_type_station_at (listing_type, station_code, snapshot_at),
  CONSTRAINT fk_snapshot_batch FOREIGN KEY (batch_id) REFERENCES listing_batches(batch_id)
) ENGINE=InnoDB;

-- Current state (latest snapshot per listing)
CREATE TABLE IF NOT EXISTS listing_current (
  source          VARCHAR(32) NOT NULL,
  listing_type    ENUM('sale','newhouse','rental') NOT NULL,
  source_listing_id VARCHAR(64) NOT NULL,
  snapshot_at     DATETIME NOT NULL,
  source_url      VARCHAR(512) NOT NULL,
  title           VARCHAR(256) NOT NULL DEFAULT '',
  asking_price_twd    BIGINT UNSIGNED NULL,
  monthly_rent_twd    BIGINT UNSIGNED NULL,
  building_area_ping  DECIMAL(10,2) NULL,
  building_type       VARCHAR(80) NULL,
  bedrooms        TINYINT UNSIGNED NULL,
  living_rooms    TINYINT UNSIGNED NULL,
  bathrooms       TINYINT UNSIGNED NULL,
  building_age_years  DECIMAL(6,2) NULL,
  floor           TINYINT UNSIGNED NULL,
  total_floors    TINYINT UNSIGNED NULL,
  parking_type    VARCHAR(80) NULL,
  latitude        DECIMAL(10,7) NULL,
  longitude       DECIMAL(10,7) NULL,
  station_code    VARCHAR(16) NULL,
  station_distance_m   DECIMAL(10,2) NULL,
  location_eligible    BOOLEAN NOT NULL DEFAULT FALSE,
  raw_hash        CHAR(64) NOT NULL,
  active          BOOLEAN NOT NULL DEFAULT TRUE,
  consecutive_absences  TINYINT UNSIGNED NOT NULL DEFAULT 0,
  last_seen_batch_id   VARCHAR(64) NOT NULL DEFAULT '',
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (source, listing_type, source_listing_id)
) ENGINE=InnoDB;

-- Event stream (price changes, status transitions, etc.)
CREATE TABLE IF NOT EXISTS listing_events (
  event_key       VARCHAR(64) NOT NULL PRIMARY KEY,
  source          VARCHAR(32) NOT NULL,
  listing_type    ENUM('sale','newhouse','rental') NOT NULL,
  source_listing_id VARCHAR(64) NOT NULL,
  event_type      VARCHAR(32) NOT NULL,
  event_data      JSON NULL,
  occurred_at     DATETIME NOT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_event_time (occurred_at),
  KEY ix_event_listing (source, listing_type, source_listing_id, occurred_at)
) ENGINE=InnoDB;

-- Valuation results attached to listings
CREATE TABLE IF NOT EXISTS listing_valuations (
  id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  source          VARCHAR(32) NOT NULL,
  listing_type    ENUM('sale','newhouse','rental') NOT NULL,
  source_listing_id VARCHAR(64) NOT NULL,
  estimated_price_twd   BIGINT UNSIGNED NULL,
  price_lower_twd       BIGINT UNSIGNED NULL,
  price_upper_twd       BIGINT UNSIGNED NULL,
  confidence_score      DECIMAL(5,4) NULL,
  valuation_date  DATE NOT NULL,
  model_version   VARCHAR(32) NULL,
  features_snapshot JSON NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_valuation_listing (source, listing_type, source_listing_id, valuation_date)
) ENGINE=InnoDB;
