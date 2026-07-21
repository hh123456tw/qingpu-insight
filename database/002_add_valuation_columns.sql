USE qingpu_insight;

ALTER TABLE market_transactions
  ADD COLUMN floor VARCHAR(80) NULL AFTER source_file,
  ADD COLUMN total_floors VARCHAR(40) NULL AFTER floor,
  ADD COLUMN parking_type VARCHAR(80) NULL AFTER total_floors,
  ADD COLUMN parking_area_sqm DECIMAL(12,4) NULL AFTER parking_type,
  ADD COLUMN parking_price_twd BIGINT UNSIGNED NULL AFTER parking_area_sqm,
  ADD COLUMN analysis_eligible BOOLEAN NOT NULL DEFAULT TRUE AFTER parking_price_twd;
