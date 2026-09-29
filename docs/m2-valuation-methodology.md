# M2 AI 估價方法論

## 資料來源

模型訓練與評估僅使用內政部不動產交易實價登錄官方成交資料，且只使用中古屋（`resale`）；預售屋資料保留在底層資料集，但不進入訓練。591 等刊登資料只在估價後拿來與開價比較，不會成為訓練標籤。

## 地理範圍

桃園機場捷運 A17（領航站）、A18（高鐵桃園站）、A19（桃園體育園區站）周邊 2 公里範圍。僅納入 `analysis_eligible=True` 的成交記錄。

## 目標變數

預測目標為**不含可拆分車位價值的每坪單價**（新台幣元/坪，TWD/ping）：

- 當交易記錄含有效車位價格與車位面積，且車位面積小於建物面積時：總價減車位價，除以建物面積減車位面積（`parking_split`）
- 無法可靠拆分時：使用官方每平方公尺單價換算值（`official_unit_price`）

車位不是模型特徵。估價時，車位價格由同一個 artifact 內的車位價格政策（`parking_price_policy`）另外計算：依車位類型取訓練資料中位價，該類型樣本不足 20 筆時退回全體中位價。估計總價 = 房屋本體估值 + 車位估值。

## 時間切割

| 分區 | 定義 |
|------|------|
| 訓練集 | 校準集起始日之前 |
| 校準集 | 測試集起始日前 6 個月 |
| 測試集 | 資料最末日往回 12 個月 |

時間順序嚴格保持，**不打散時間序列**。三個分區各自至少 100 筆，否則訓練失敗。

## 候選模型

中古屋引導訓練比較三種候選（`model_training_service.RESALE_GUIDED_CANDIDATES`），並與基準線比較：

| 模型 | 關鍵參數 |
|------|----------|
| 近期中位數基準（Baseline） | 訓練集最後 **12 個月**內，群組 (`station_code`, `building_type`) 中位數；群組少於 20 筆退到站點中位數；再少退到全體中位數 |
| Ridge | alpha=10.0 |
| Random Forest | min_samples_leaf=5, max_features=0.8；樹數由 profile 決定 |
| HistGradientBoosting（對數價格） | max_leaf_nodes=31, l2_regularization=1.0；學習率與迭代次數由 profile 決定 |

原始目標的 HistGradientBoosting 仍存在於程式中，但 2026-09 起不再列入中古屋引導訓練（原因見[問題紀錄 §18](project-issue-log.md)）；AutoML 模式仍會在 Random Forest 與原始目標 HGB 的參數空間中搜尋。

超參數有三種調整方式：三組內建 profile（下節）、一組選用的自訂 profile，以及 Optuna AutoML 模式（見「AutoML 自動探索」）。基準線只作比較，**永遠不會被發布**。

## 引導調參設定（Schema v3）

從 v3 版開始，管理者可透過 Web 操作頁選擇調參設定組合（以下簡稱 profile）。提交訓練工作時，
系統會同時訓練多組 profile，在校準集比較後鎖定最佳候選模型，最後以測試集驗證。

### 三組鎖定 Profile

| 名稱 | HGB 學習率 | HGB 迭代次數 | RF 樹數 | 近期權重半衰期 |
|------|------------|-------------|---------|---------------|
| 快速（quick） | 0.08 | 180 | 160 | 48 個月 |
| 平衡（balanced） | 0.06 | 350 | 400 | 48 個月 |
| 精細（thorough） | 0.04 | 600 | 700 | 48 個月 |

三組內建 profile 固定鎖定參數值；若選擇自訂 profile，則使用管理介面送出的半衰期。

### 自訂 Profile

管理者可勾選「使用自訂設定」加入第四組自訂 profile，範圍如下：

| 參數 | 範圍 |
|------|------|
| HGB 學習率 | 0.01 ~ 0.20 |
| HGB 迭代次數 | 100 ~ 1000 |
| RF 決策樹數量 | 100 ~ 1000 |
| 近期權重半衰期 | 12 ~ 84 個月 |

自訂 profile 僅在當次訓練有效，不會影響下一組鎖定 profile。

## 校準集鎖定與最終測試隔離

1. 所有 profile（含自訂）各自訓練 Ridge、Random Forest 與 HGB（對數價格）。
2. 在校準集上比較所有「profile × 模型」組合，依整體 MAE、MAPE、RMSE 由低至高選出一個候選模型。
3. 鎖定後，**測試集只比較該候選與 Baseline**。
4. 測試結果不得用來改選另一個候選模型或 profile。
5. 若多組 profile 表現相同，以 profile 順序（quick → balanced → thorough → custom）決定。

此設計確保測試集不參與模型與 profile 的選擇。但要注意：2026-09 把 HGB 改為只保留對數目標的決策，是在看過 final test 之後才做的（依據是 final test 之前的滾動回測），因此目前正式模型的 final test 指標應視為略偏樂觀，詳見[問題紀錄 §18](project-issue-log.md)。

### AutoML 自動探索

管理中心也可改用 AutoML 模式（與引導調參互斥）：以 Optuna TPE 取樣器在 Random Forest 與 HistGradientBoosting 的參數空間搜尋，預算分為快速（5 分鐘／最多 12 組）、標準（15 分鐘／35 組）、深度（30 分鐘／70 組）。排行榜只反映校準集表現；入選的候選仍需通過下方完整發布閘門，並由管理者手動發布。

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

中古屋 v3 特徵契約共 21 欄（`model_features.FEATURE_COLUMNS`）：

- 數值特徵：station_distance_m, building_area_ping, bedrooms, living_rooms, bathrooms, building_age_years, floor, total_floors, floor_ratio, transaction_year, transaction_month, transaction_month_index, twd97_x, twd97_y
- 類別特徵：station_code, building_type, station_building_type, building_age_band, area_band, floor_band, location_known
- 不含 `parking_type`、`parking_area_ping`：車位由車位價格政策另外計價（見「目標變數」），發布檢查 `parking_price_consistency` 會拒絕把車位欄位當特徵的候選
- 中位數填補遺漏值（附缺值指示欄）+ 標準化（數值特徵）
- 眾數填補 + OneHot encoding（類別特徵）
- 樓層為中文轉數值（如「十層」→ 10、「地下二層」→ -2），再計算 floor_ratio

### 空間特徵契約 v3

中古屋 v3 特徵加入官方 TWD97 `twd97_x`、`twd97_y` 與 `location_known`。座標讓模型表達同一生活圈內的連續位置差異；無精確位置的手動估價仍可由缺值處理，但信心原因會說明只能依生活圈與捷運距離估價。591 詳細頁若有經緯度，會從 EPSG:4326 轉為 EPSG:3826 後使用。

模型不直接採用完整門牌，也不把高基數路名當作正式核心特徵，以降低隱私風險與新路段類別漂移。

## Release Gate

所有候選模型只在校準集比較並鎖定一個候選。鎖定後，測試集只比較該候選與 Baseline（最近 12 個月中位數）；
測試結果不得用來改選另一個候選。發布閘門以 **final test** 指標與三次年度回測計算，
結果寫入 manifest 的 `release_checks`，七項全部為 `true` 時才是 `recommended`：

| 檢查 | 條件 |
|------|------|
| `overall_mae_improved` | final test 整體 MAE ≤ 基準 MAE × 0.98（至少改善 2%） |
| `stations_within_limit` | A17、A18、A19 各站 MAPE ≤ 基準對應站 × 1.10 |
| `a18_improved` | A18 MAPE **嚴格低於**基準 |
| `backtests_passed` | 必須產生三次年度回測，且至少兩次候選整體 MAE 低於基準 |
| `backtest_stations_within_limit` | 三次年度回測中至少兩次各站 MAPE ≤ 基準 × 1.10 |
| `candidate_fresh` | `data_max_date` 不早於最新官方資料日期前 180 天；發布預覽時會再以最新市場資料檢查一次，過期即拒絕發布 |
| `parking_price_consistency` | 模型特徵不含車位欄位，且帶有有效的車位價格政策 |

年度回測以資料最後月份的月底為第一個截止日，再逐年往前推兩次，每次用相同的時間切割重新訓練選定的模型。
1–6 由 `model_analysis.evaluate_release_checks` 計算，7 由 `model_training_service` 補上。

若候選未通過，**不會發布任何模型**（基準線也不會被發布），正式模型維持原版本；管理端只允許發布 `recommended` 的候選。

## 分群誤差指標

| 指標 | 說明 |
|------|------|
| MAE | 平均絕對誤差（新台幣元/坪）。訓練結果以萬元/坪顯示 |
| MAPE | 平均絕對百分比誤差（百分比）。分母為 `max(\|實際單價\|, 100,000)`（`model_training._compute_metrics`）；市場清理已排除官方單價低於 10 萬／坪的交易，因此這個下限實際上不影響結果 |
| RMSE | 均方根誤差 |
| R² | 決定係數 |
| count | 測試樣本筆數 |
| coverage | 測試覆蓋率（落在估價區間內的樣本比例，目標 90%） |

指標分別輸出「全體」、「各站」（A17/A18/A19）、「主要建物類型」。少於 30 筆的分群不發布個別指標。

正式模型卡顯示的指標來自 **final test**；校準集指標只保留在候選比較。舊 artifact 若未保存來源，頁面會標示「舊版指標（來源未記錄）」。

## 結果閱讀指南

系統會為每個訓練結果提供一份摘要，按此順序閱讀：

1. **發布門檻**：先看是否通過發布門檻（上方七項檢查）。
2. **MAPE 與 MAE**：和同一測試期的基準線比較，而不是和固定數字比較。MAPE 的絕對水準高度取決於測試期是否有新建案等結構變化：目前正式模型在一般期間的滾動回測 MAPE 約 10%，但 final test 因一個沒有價格歷史的新建案而為 17.75%（基準線 25.0%）。
3. **站點與年度退化檢查**：展開完整指標，確認各站（A17/A18/A19）的 MAPE 沒有比基準模型對應站超過 110%，並查看三次年度回測的結果。

### MAE Baseline Delta

系統會計算候選模型與基準模型的 MAE 差異（baseline delta）。負值表示候選模型優於基準（改善），正值表示劣於基準（退步）。改善幅度以萬元／坪顯示。

### 三張指標卡

- **MAPE**（平均絕對百分比誤差）：越低越好；請與同期基準線的 MAPE 一起判讀。
- **MAE**（平均絕對誤差，萬元／坪）：反映平均每坪的估價偏差金額。
- **測試覆蓋率**：目標 90%，代表估價區間涵蓋大部分實際成交價。目前正式模型 final test 實際只有 84.5%，區間偏窄。

## 估價區間

使用校準集絕對殘差的 90 百分位數作為區間半徑：
- 區間 = [max(0, 預測值 - 半徑), 預測值 + 半徑]
- 目標覆蓋率 90%；正式模型 `870c95b0` 在 final test 的實際覆蓋率為 84.5%（平均寬度 21.0 萬／坪），遇到訓練資料外的新建案時會低估不確定性

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
