# Qingpu Insight 專案問題與工程決策紀錄

## 1. 問題摘要

ML 模型將車位面積（`parking_area_ping`）納入房屋坪數（`building_area_ping`）作為特徵，導致新增車位時模型預測每坪單價下降，可能產生「有車位總價低於無車位」的矛盾估值。

## 2. 使用者如何發現

在 AI 條件估價中輸入相同條件的房屋，分別測試無車位、坡道平面（8 坪）與坡道機械（5 坪），發現有車位版的估計總價低於無車位版。

## 3. 可重現條件與修正前數字

測試條件：A18、中古屋、30 坪、500 公尺、住宅大樓、3 房 2 廳 2 衛、屋齡 5 年、10/20 樓。

- 無車位：1,686 萬
- 坡道平面，0 車位坪：1,333 萬
- 坡道平面，8 車位坪：1,408 萬
- 坡道機械，5 車位坪：1,513 萬
- 中古屋樣本：坡道平面 14,955 筆、無車位 799 筆
- A18 配對 cohort：坡道平面 808 筆、無車位 33 筆

## 4. 根因分析

`building_area_ping` 同時包含房屋與車位面積。模型從訓練資料學到「較大面積 → 較低每坪單價」的相關性（總價不變下，坪數增加稀釋單價）。新增車位增加 `building_area_ping`，觸發此負相關，導致每坪單價下降，即使加上車位價格後總價仍可能低於無車位案例。

## 5. 評估過的方案

1. **車位 dummy 特徵**：加入 `parking_type` one-hot encoding 讓模型學習車位效果 — 仍無法保證加法一致性。
2. **車位單獨 ML 模型**：增加維運複雜度，且小樣本（799 筆無車位）不可靠。
3. **Monotonic 約束**：sklearn HGBT 支援單調約束，但無法套用至分类型特徵交互作用。
4. **分離房屋與車位估價（採用方案）**：ML 模型僅預測房屋本體每坪單價，車位價值由官方資料的類型中位數決定。

## 6. 最終架構決策

- ML pipeline 僅預測 dwelling unit price，排除 `parking_type` 與 `parking_area_ping` 特徵。
- 建立版本化 `ParkingPricePolicy`，從訓練資料中正值的 `parking_price_twd` 計算類型中位價。
- `compose_total_price()` 組合房屋與車位：`總價 = 房屋本體 + 車位`。
- 區間端點同樣加上車位金額，確保一致性。
- `_smoke_test()` 在新候選發布前驗證車位一致性。

## 7. 修正內容

- `parking_valuation.py`：新增 `ParkingPricePolicy`、`ParkingPriceStat`、`ParkingPriceEstimate` frozen dataclass 與建構/查詢函式。
- `model_features.py`：從 `FEATURE_COLUMNS` / `BASE_FEATURE_COLUMNS` 移除車位欄位；`ValuationInput.__post_init__` 正規化車位輸入。
- `valuation.py`：`ValuationBundle` 新增 `parking_price_policy` 欄位；`valuate()` 三條路徑皆使用 `compose_total_price()` 計算明細。
- `model_artifacts.py`：`MarketTrainingResult` 新增 `parking_policy`。
- `model_release.py`：`_smoke_test()` 驗證車位一致性。
- `web.py`：API 輸入驗證車位面積。
- 前端：顯示房屋本體/車位/總價明細。

## 8. 修正後驗證

- 相同房屋條件下，三種車位版本的 `estimated_building_price_twd` 完全一致。
- 坡道平面與坡道機械的 `estimated_total_price_twd` 皆大於無車位。
- 總價 = 房屋本體 + 車位（加法不變性）。
- 全數 pytest 通過（含 6 個新測試）。
- Node.js contract tests 全數通過。
- `_smoke_test()` 在新候選發布前阻擋車位不一致的 artifact。

## 9. 已知限制

- 車位價格取自同版官方資料的類型中位數，不反映個別車位的位置、樓層或市場波動。
- 無車位樣本（799 筆）遠少於有車位樣本（14,955 筆），fallback 中位數由坡道平面主導。
- Legacy artifact 無 `parking_price_policy`，估值回傳 `estimated_parking_price_twd=None`。

## 10. 期中專題報告說法

本專題發現 ML 估價模型將車位面積納入房屋坪數特徵，導致「有車位總價低於無車位」的邏輯矛盾。修正方案將車位從 ML 特徵中分離，改以官方成交資料的類型中位價作為車位估值，確保房屋本體估價不受車位影響，總價 = 房屋 + 車位。此修正展示了將領域知識注入 ML pipeline 的工程思維。

## 11. 求職面試 STAR 說法

- **Situation**：房價 ML 模型同時使用房屋與車位面積作為特徵。
- **Task**：修正「有車位總價可能低於無車位」的矛盾。
- **Action**：將車位從 ML 特徵合約中移除，建立版本化的車位價格政策（ParkingPricePolicy）從官方資料計算類型中位價，並在發布流程中加入一致性檢查閘門。
- **Result**：相同房屋的房屋本體估價完全一致，有車位總價恆大於無車位，通過全數測試，新候選無法繞過車位一致性檢查。

## 12. 延伸改善方向

- 納入時間維度：車位中位價隨市場更新而推移。
- 加入車位類型、樓層、地區等細分。
- 提供車位估值區間而非單一點估計。
