ALTER TABLE market_transactions
  ADD COLUMN main_building_area_sqm DECIMAL(12,4) NULL AFTER parking_price_twd,
  ADD COLUMN auxiliary_building_area_sqm DECIMAL(12,4) NULL AFTER main_building_area_sqm,
  ADD COLUMN common_area_ratio DECIMAL(8,6) NULL AFTER auxiliary_building_area_sqm,
  ADD COLUMN has_management BOOLEAN NULL AFTER common_area_ratio;
