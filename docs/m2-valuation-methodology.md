# M2 AI 估價方法論

## 資料來源

僅使用內政部不動產交易實價登錄官方成交資料。不爬取售屋網站，不使用任何 591、樂居、永慶房仲網等非官方資料來源。

## 地理範圍

桃園機場捷運 A17（領航站）、A18（高鐵桃園站）、A19（桃園體育園區站）周邊 2 公里範圍。僅納入 `analysis_eligible=True` 的成交記錄。

## 目標變數

預測目標為**不含可拆分車位價值的每坪單價**（新台幣元/坪，TWD/ping）：

- 當交易記錄含有效車位價格與車位面積，且車位面積小於建物面積時：總價減車位價，除以建物面積減車位面積（`parking_split`）
- 無法可靠拆分時：使用官方每平方公尺單價換算值（`official_unit_price`）

## 時間切割

| 分區 | 定義 |
|------|------|
| 訓練集 | 校準集起始日之前 |
| 校準集 | 測試集起始日前 6 個月 |
| 測試集 | 資料最末日往回 12 個月 |

時間順序嚴格保持，**不打散時間序列**。

## 候選模型

所有候選模型的超參數在 M2 中固定，不進行超參數搜尋（hyperparameter tuning）：

| 模型 | 關鍵參數 |
|------|----------|
| 近期中位數基準（Baseline） | 訓練截止日前 24 個月群組 (`station_code`, `building_type`) 中位數；群組少於 20 筆退到站點中位數；再少退到全體中位數 |
| Ridge | alpha=10.0 |
| Random Forest | n_estimators=400, min_samples_leaf=5, max_features=0.8 |
| HistGradientBoosting | learning_rate=0.06, max_iter=350, max_leaf_nodes=31, l2_regularization=1.0 |

## 特徵工程

- `NUMERIC_FEATURES`：station_distance_m, building_area_ping, bedrooms, living_rooms, bathrooms, building_age_years, floor, total_floors, floor_ratio, parking_area_ping, transaction_year, transaction_month
- `CATEGORICAL_FEATURES`：station_code, building_type, parking_type
- 中位數填補遺漏值 + 標準化（數值特徵）
- 眾數填補 + OneHot encoding（類別特徵）
- 樓層為中文轉數值（如「十層」→ 10、「地下二層」→ -2），再計算 floor_ratio

## Release Gate

正式模型須同時滿足：
1. **整體精確度**：時間外 MAE 不超過基準的 98%（至少改善 2%）
2. **各站穩定性**：A17、A18、A19 各站的 MAPE 皆不超過基準對應站的 110%

若無候選模型同時通過兩項門檻，發布基準模型。

## 分群誤差指標

| 指標 | 說明 |
|------|------|
| MAE | 平均絕對誤差 |
| MAPE | 平均絕對百分比誤差（分母下限 100,000 元/坪） |
| RMSE | 均方根誤差 |
| R² | 決定係數 |

指標分別輸出「全體」、「各站」（A17/A18/A19）、「主要建物類型」。少於 30 筆的分群不發布個別指標。

## 估價區間

使用校準集絕對殘差的 90 百分位數作為區間半徑：
- 區間 = [max(0, 預測值 - 半徑), 預測值 + 半徑]
- 目標覆蓋率 90%

## 相似成交搜尋

1. **第一層**：同交易類型、同站點、近 36 個月
2. **排序**：以標準化距離（面積、站距、房數、屋齡、樓層比、時間近距）加權，不同建物類型時 +0.5 懲罰
3. **取前 5 筆**
4. **擴張**：不足 3 筆時擴張至同類型、全站點、近 36 個月

公開欄位：record_id, transaction_type, transaction_date, station_code, building_type, building_area_ping, unit_price_per_ping_twd, total_price_twd, floor_ratio, longitude, latitude, similarity_score。不包含地址、門牌、TWD97 座標。

## 信心等級

| 等級 | 條件 |
|------|------|
| 高 | 所有數值輸入在訓練 5th–95th 百分位內，且區間寬度 ≤ 30% 估計總價，且 ≥ 3 筆相似度 ≥ 0.60 |
| 中 | 未達「高」且僅 1 項條件未滿足 |
| 低 | 2+ 項條件未滿足，或任一輸入超出 1st–99th 百分位，或使用降級模型 |

## 限制與不適用情境

- 僅使用官方成交資料，無法反映社區生活機能、學區、景觀等軟性因素
- 僅涵蓋 A17–A19 三站 2 公里範圍
- 無法可靠辨識社區或建案 ID（路段代理變數僅供洩漏檢測，不是特徵）
- 不含開價資料（開價僅用於與估值比較）
- 不預測未來漲跌
- 不構成專業不動產估價或投資建議
