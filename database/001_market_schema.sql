CREATE DATABASE IF NOT EXISTS qingpu_insight
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE qingpu_insight;

CREATE TABLE IF NOT EXISTS data_refreshes (
  refresh_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  dataset_version VARCHAR(32) NOT NULL,
  source_max_date DATE NOT NULL,
  row_count INT UNSIGNED NOT NULL,
  quality_report JSON NOT NULL,
  loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_data_refresh_version (dataset_version)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS market_transactions (
  transaction_key CHAR(64) PRIMARY KEY,
  transaction_type ENUM('resale','presale') NOT NULL,
  record_id VARCHAR(64) NULL,
  station_code ENUM('A17','A18','A19') NOT NULL,
  transaction_date DATE NOT NULL,
  building_area_sqm DECIMAL(12,4) NOT NULL,
  building_area_ping DECIMAL(12,4) NOT NULL,
  unit_price_sqm_twd DECIMAL(14,2) NOT NULL,
  unit_price_per_ping_twd DECIMAL(14,2) NOT NULL,
  total_price_twd BIGINT UNSIGNED NOT NULL,
  building_type VARCHAR(80) NULL,
  bedrooms TINYINT UNSIGNED NULL,
  living_rooms TINYINT UNSIGNED NULL,
  bathrooms TINYINT UNSIGNED NULL,
  building_age_years DECIMAL(8,2) NULL,
  station_distance_m DECIMAL(10,2) NOT NULL,
  longitude DECIMAL(10,7) NOT NULL,
  latitude DECIMAL(10,7) NOT NULL,
  match_quality ENUM('exact','nearest_number') NOT NULL,
  source_file VARCHAR(160) NOT NULL,
  floor VARCHAR(80) NULL,
  total_floors VARCHAR(40) NULL,
  parking_type VARCHAR(80) NULL,
  parking_area_sqm DECIMAL(12,4) NULL,
  parking_price_twd BIGINT UNSIGNED NULL,
  analysis_eligible BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY ix_market_type_station_date (transaction_type, station_code, transaction_date),
  KEY ix_market_type_date (transaction_type, transaction_date),
  KEY ix_market_filters (transaction_type, building_type, bedrooms, building_area_ping)
) ENGINE=InnoDB;
