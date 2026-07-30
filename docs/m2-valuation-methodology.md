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
| 近期中位數基準（Baseline） | 訓練截止日前 48 個月群組 (`station_code`, `building_type`) 中位數；群組少於 20 筆退到站點中位數；再少退到全體中位數 |
| Ridge | alpha=10.0 |
| Random Forest | n_estimators=400, min_samples_leaf=5, max_features=0.8 |
| HistGradientBoosting | learning_rate=0.06, max_iter=350, max_leaf_nodes=31, l2_regularization=1.0 |

## 引導調參設定（Schema v3）

從 v3 版開始，管理者可透過 Web 操作頁選擇調參設定組合（以下簡稱 profile）。提交訓練工作時，
系統會同時訓練多組 profile，在校準集比較後鎖定最佳候選模型，最後以測試集驗證。

### 三組鎖定 Profile

| 名稱 | HGB 學習率 | HGB 迭代次數 | RF 樹數 | 近期權重半衰期 |
|------|------------|-------------|---------|---------------|
| 快速（quick） | 0.08 | 180 | 160 | 48 個月 |
| 平衡（balanced） | 0.06 | 350 | 400 | 48 個月 |
| 精細（thorough） | 0.04 | 600 | 700 | 48 個月 |

三組內建 profile 固定鎖定參數值；若選擇自訂 profile，則使用管理介面送出的半衰期。預售屋不適用近期加權。

### 自訂 Profile

管理者可勾選「使用自訂設定」加入第四組自訂 profile，範圍如下：

| 參數 | 範圍 |
|------|------|
| HGB 學習率 | 0.01 ~ 0.20 |
| HGB 迭代次數 | 100 ~ 1000 |
| RF 決策樹數量 | 100 ~ 1000 |
| 近期權重半衰期（僅中古屋） | 12 ~ 84 個月 |

自訂 profile 僅在當次訓練有效，不會影響下一組鎖定 profile。

## 校準集鎖定與最終測試隔離

1. 所有 profile（含自訂）各自訓練 Ridge、Random Forest、HistGradientBoosting 與 HGB（對數價格）。
2. 在校準集上比較各 profile 內的最佳模型，選出一個候選模型。
3. 鎖定後，**測試集只比較該候選與 Baseline**。
4. 測試結果不得用來改選另一個候選模型或 profile。
5. 若多組 profile 表現相同，以 profile 順序（quick → balanced → thorough → custom）決定。

此設計確保最終測試指標是未被污染的真實泛化評估。

HGB（對數價格）以 `log(y)` 擬合、以 `exp()` 還原預測，所有 MAE、MAPE、RMSE 與畫面價格仍是原始新台幣／坪。它只是一個候選，不會因名稱較複雜而優先。

## Schema v3 證據

系統會維持每一次訓練的完整 manifest（JSON），包含：

- 使用的 profile 組合（含自訂參數值）
- 各 profile 在校準集的完整指標
- 被鎖定的候選模型識別
- 最終測試的各模型（candidate + baseline）分群指標

Schema v2（及更早）的訓練不會有調參快照；頁面上會標示「舊版未保存調參快照」。

## 手動發布要求

模型訓練完成後**不會自動發布**。管理者須：

1. 在訓練紀錄頁檢視結果（含 MAE、MAPE、覆蓋率、baseline delta）。
2. 確認各站點指標沒有異常退化。
3. 點擊「發布」按鈕，讀取並確認系統產生的確認文字。
4. 輸入確認文字提交。

發布會建立一個獨立的模型版本，指向該次訓練的 artifact。正式模型更新後，
新估價請求才會開始使用新版本。

## 特徵工程

- `NUMERIC_FEATURES`：station_distance_m, building_area_ping, bedrooms, living_rooms, bathrooms, building_age_years, floor, total_floors, floor_ratio, parking_area_ping, transaction_year, transaction_month
- `CATEGORICAL_FEATURES`：station_code, building_type, parking_type
- 中位數填補遺漏值 + 標準化（數值特徵）
- 眾數填補 + OneHot encoding（類別特徵）
- 樓層為中文轉數值（如「十層」→ 10、「地下二層」→ -2），再計算 floor_ratio

### 空間特徵契約 v3

中古屋 v3 特徵加入官方 TWD97 `twd97_x`、`twd97_y` 與 `location_known`。座標讓模型表達同一生活圈內的連續位置差異；無精確位置的手動估價仍可由缺值處理，但信心原因會說明只能依生活圈與捷運距離估價。591 詳細頁若有經緯度，會從 EPSG:4326 轉為 EPSG:3826 後使用。

模型不直接採用完整門牌，也不把高基數路名當作正式核心特徵，以降低隱私風險與新路段類別漂移。

## Release Gate

所有候選模型只在校準集比較並鎖定一個候選。鎖定後，測試集只比較該候選與 Baseline；
測試結果不得用來改選另一個候選。候選必須在校準與最終測試兩階段都通過 release gate，
系統判定才可為 recommended。

正式模型須同時滿足：
1. **整體精確度**：時間外 MAE 不超過基準的 98%（至少改善 2%）
2. **各站穩定性**：A17、A18、A19 各站的 MAPE 皆不超過基準對應站的 110%

若無候選模型同時通過兩項門檻，發布基準模型。

## 分群誤差指標

| 指標 | 說明 |
|------|------|
| MAE | 平均絕對誤差（新台幣元/坪）。訓練結果以萬元/坪顯示 |
| MAPE | 平均絕對百分比誤差（百分比）。分母下限 100,000 元/坪 |
| RMSE | 均方根誤差 |
| R² | 決定係數 |
| count | 測試樣本筆數 |
| coverage | 測試覆蓋率（落在估價區間內的樣本比例，目標 90%） |

指標分別輸出「全體」、「各站」（A17/A18/A19）、「主要建物類型」。少於 30 筆的分群不發布個別指標。

正式模型卡顯示的指標來自 **final test**；校準集指標只保留在候選比較。舊 artifact 若未保存來源，頁面會標示「舊版指標（來源未記錄）」。

## 結果閱讀指南

系統會為每個訓練結果提供一份摘要，按此順序閱讀：

1. **發布門檻**：先看是否通過發布門檻。通過表示候選模型在校準和測試兩階段都滿足條件。
2. **MAPE 與 MAE**：確認整體預測誤差在可接受範圍。MAPE 低於 10% 為良好，MAE 對照市場行情判斷。
3. **站點與年度退化檢查**：展開完整指標，確認各站（A17/A18/A19）的 MAPE 沒有比基準模型對應站超過 110%。若某站退化但整體過關，摘要會顯示警告。

### MAE Baseline Delta

系統會計算候選模型與基準模型的 MAE 差異（baseline delta）。負值表示候選模型優於基準（改善），正值表示劣於基準（退步）。改善幅度以萬元／坪顯示。

### 三張指標卡

- **MAPE**（平均絕對百分比誤差）：越低越好。低於 10% 通常表示模型可靠。
- **MAE**（平均絕對誤差，萬元／坪）：反映平均每坪的估價偏差金額。
- **測試覆蓋率**：目標 90%，代表估價區間涵蓋大部分實際成交價。

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
| 高 | 所有數值輸入在訓練 5th–95th 百分位內，且 90% 區間的單側誤差半徑 ≤ 40% 估計單價，且 ≥ 3 筆相似度 ≥ 0.60 |
| 中 | 未達「高」且僅 1 項條件未滿足 |
| 低 | 2+ 項條件未滿足，或任一輸入超出 1st–99th 百分位，或使用降級模型 |

## 對話證據 (Conversation Evidence)

M5 Conversation Assistant 的 Evidence Pack 包含以下來源：

1. **591 詳細頁快照**：標題、總價、單價、坪數、格局、地址、社區、建商、建材、樓層、屋齡、車位、座標
2. **官方成交估價**：M2 模型預測點估計、區間、信心等級（如可用）
3. **相似成交**：最近 36 個月同站點、同類型的前 5 筆相似成交
4. **限制說明**：遺漏座標、估值不可用、樓層不一致、相似成交不足等

### Fact ID 命名規則

- `listing.title`、`listing.price`、`listing.unit_price` — 591 詳細頁資料
- `listing.area`、`listing.layout`、`listing.address` — 物件基本資訊
- `listing.community`、`listing.builder`、`listing.building_type` — 社區與建商
- `listing.floor`、`listing.age`、`listing.parking` — 樓層屋齡車位
- `listing.location` — 座標 (latitude, longitude)
- `valuation.point`、`valuation.low`、`valuation.high` — 模型估值
- `valuation.confidence` — 信心等級
- `comparable.N.price`、`comparable.N.distance`、`comparable.N.date` — 第 N 筆相似成交

### 接地驗證

AI 回答中的 property claim 必須引用至少一個事實 fact ID，且 fact ID 必須存在於
啟用的 Evidence Pack 中。驗證在 `validate_chat_answer()` 完成：
- 拒絕不存在的 fact ID
- 拒絕空的 fact_ids
- 拒絕 claim 內重複的 fact ID
- 拒絕 guidance 中包含數字

兩次驗證失敗時 assistant message 不會被儲存，工作轉為 `validation_failed`。

## 限制與不適用情境

- 僅使用官方成交資料，無法反映社區生活機能、學區、景觀等軟性因素
- 僅涵蓋 A17–A19 三站 2 公里範圍
- 無法可靠辨識社區或建案 ID（路段代理變數僅供洩漏檢測，不是特徵）
- 不含開價資料（開價僅用於與估值比較）
- 不預測未來漲跌
- 不構成專業不動產估價或投資建議
