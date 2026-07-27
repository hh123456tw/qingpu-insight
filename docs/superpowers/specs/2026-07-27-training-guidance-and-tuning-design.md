# 青埔智價訓練導覽、安全調參與 591 範圍收斂設計

## 1. 目標

小幅改善管理中心與模型觀測台的模型訓練體驗，讓尚未學過完整機器學習指標的使用者能完成訓練、理解結果並向老師或面試官說明方法，同時保留完整技術證據。

本次改動增加：

- 白話優先、技術詳情可展開的訓練報告；
- 快速、平衡、精細三組固定設定的一次比較；
- 一組選填且受安全範圍限制的自訂設定；
- 中古屋與預售屋皆可使用的前端訓練入口；
- 可重現的參數快照與設定比較證據。
- 將 Web 端 591 更新範圍收斂為中古屋出售與預售新建案。

這仍是本機單人專題與求職作品，不建立 AutoML 平台。

## 2. 已核准的產品決策

- 管理中心 `/admin` 與模型觀測台使用相同的訓練控制項與結果導覽。
- 兩個頁面都能訓練中古屋、預售屋或兩者。
- 每次工作固定比較 `quick`、`balanced`、`thorough` 三組後端定義的設定。
- 使用者可選擇再加入一組 `custom` 設定。
- 校正集 MAE 負責選出「設定＋模型」勝者。
- 勝者鎖定後，最終測試集只用於最終評估與 Release Gate，不得反過來改選勝者。
- 各站 MAPE、年度回測及既有 Release Gate 繼續保護發布資格。
- 訓練完成只建立不可變候選，永遠不自動發布正式模型。
- 預設畫面以簡潔導覽為主；完整技術證據保留在可展開區域。
- 管理介面與 Web 一鍵更新只處理 `sale`、`newhouse`，不再顯示或排程 `rental`。
- 租屋底層 parser／source、CLI 相容入口及歷史工作紀錄保留，不刪除既有租屋資料。
- 正式實作依 TDD 小步完成。

## 3. 明確不做

- 不加入 XGBoost。
- 不加入 Grid Search、Random Search、Bayesian Optimization 或其他 AutoML。
- 不允許任意 estimator、任意 Python 參數或任意 JSON 設定。
- 不讓測試集參與候選挑選。
- 不自動發布、回滾或取代正式模型。
- 不增加排程、遠端運算、分散式 queue 或工作取消。
- 不重寫 591 capture、normalize、locate、validate 或 publish pipeline。
- 不刪除租屋 parser／source、CLI 相容入口、資料表或既有租屋資料。
- 不將租屋改造成出租收益率、租售比或投資分析功能。
- 不把 591 開價資料加入官方成交模型的訓練、校正或測試。

## 4. 使用者流程

### 4.1 建立訓練工作

1. 使用者選擇市場：中古屋、預售屋或全部。
2. 畫面固定顯示快速、平衡、精細三組將被比較。
3. 使用者可展開「加入自訂設定」並輸入四項受限參數。
4. 畫面顯示送出摘要，例如「2 個市場 × 4 組設定」。
5. 使用者按下「開始訓練與比較」。
6. 前端做即時欄位檢查，後端再做權威驗證。
7. API 建立單一可追蹤工作並回傳 run ID。
8. 畫面輪詢既有 job API，顯示市場、設定與訓練階段。

同一時間仍只允許一個模型訓練工作。

### 4.2 閱讀結果

結果由淺入深依序顯示：

1. 是否可發布；
2. 勝出設定與勝出模型；
3. MAPE、MAE、估價區間 Coverage 三張摘要卡；
4. 與 Baseline 的差距；
5. 快速、平衡、精細及自訂設定比較；
6. A17、A18、A19 與年度回測警示；
7. 可展開的完整技術證據。

白話摘要提供固定說明：

- MAPE：平均差百分之幾，越低越好；
- MAE：平均每坪差多少元，越低越好；
- Coverage：真實價格落入估價區間的比例，目標約 90%；
- RMSE：會加重懲罰離譜錯誤，越低越好；
- R²：解釋價格變化的能力，越接近 1 越好；
- n／Count：計算該指標使用的資料筆數。

進階區域保留：

- 完整參數快照；
- 所有候選模型的 MAE、MAPE、RMSE、R² 與筆數；
- 各站與主要建物類型指標；
- 資料診斷與漂移資訊；
- 特徵消融實驗；
- 年度回測；
- Release Gate 各項檢查；
- JSON／Markdown 報告下載。

## 5. 訓練設定

### 5.1 固定設定

固定設定由後端版本化定義，前端只讀取並顯示，不得自行組合：

| 設定 | HGB learning rate | HGB max iterations | Random Forest trees | 定位 |
|---|---:|---:|---:|---|
| `quick` | 0.08 | 180 | 160 | 快速確認流程 |
| `balanced` | 0.06 | 350 | 400 | 目前預設，兼顧速度與效果 |
| `thorough` | 0.04 | 600 | 700 | 增加訓練量，確認改善是否值得 |

未列出的參數沿用目前受測試保護的固定值：

- HistGradientBoosting：`max_leaf_nodes=31`、`l2_regularization=1.0`；
- Random Forest：`min_samples_leaf=5`、`max_features=0.8`；
- 所有 stochastic estimator 使用既有固定 seed。

三組設定的中古屋近期權重半衰期預設皆為 48 個月。預設設定的差異主要代表模型訓練量，而不是任意改變資料假設。

### 5.2 自訂設定

自訂設定只開放四項：

| 欄位 | 類型 | 合法範圍 | 適用範圍 |
|---|---|---|---|
| `hgb_learning_rate` | float | 0.01～0.20 | 中古屋、預售屋 |
| `hgb_max_iter` | integer | 100～1000 | 中古屋、預售屋 |
| `rf_n_estimators` | integer | 100～1000 | 中古屋、預售屋 |
| `recency_half_life_months` | integer | 12～84 | 只影響中古屋 |

預售屋單獨訓練時，半衰期欄位停用且請求不得傳送該值。選擇「全部」時可以傳送半衰期，但只套用中古屋，預售屋結果明確標示不適用。

前端提供白話提示；後端仍必須拒絕：

- 非數值；
- `NaN`、Infinity 或布林值冒充數字；
- 超出範圍；
- 未列入白名單的欄位；
- 不適用市場的參數；
- 缺少自訂組必要欄位。

## 6. API 與資料契約

訓練 API 在既有 `markets` 之外接受受限 `tuning`：

```json
{
  "markets": ["resale", "presale"],
  "tuning": {
    "mode": "preset_comparison",
    "include_custom": true,
    "custom": {
      "hgb_learning_rate": 0.06,
      "hgb_max_iter": 350,
      "rf_n_estimators": 400,
      "recency_half_life_months": 48
    }
  }
}
```

規則：

- `mode` 第一版只接受 `preset_comparison`。
- 客戶端不能覆寫三個固定設定的內容。
- `include_custom=false` 時不得傳送 `custom`。
- 中古屋或全部市場的自訂組必須包含四個欄位；只選預售屋時必須包含前三個模型欄位且不得包含半衰期。
- 後端把輸入解析成不可變的 `TrainingTuningPlan` value object。
- Job 與 manifest 保存解析後的完整設定，不只保存原始輸入。
- idempotency 仍以單一 active model-training job 為準。

新 manifest 使用下一個 schema 版本，至少新增：

- `tuning_plan_version`；
- `profiles` 與每組解析後參數；
- 每個市場的 `profile_results`；
- `selected_profile`；
- `selected_model`；
- 每組與每個候選的 calibration metrics；
- 鎖定勝者的 final test metrics；
- 實際使用的近期權重參數；
- profile／candidate 失敗摘要。

Schema v1／v2 舊紀錄仍可顯示，畫面標示「舊版未保存調參快照」，不得推測不存在的參數。

## 7. 訓練與選模資料流

每個市場只載入一次資料並建立一次時間切分：

`load official data → time split → resolve profiles → calibration comparison → lock winner → final test → diagnostics/backtests → release gate → immutable candidate`

### 7.1 設定比較

- `quick`、`balanced`、`thorough` 必定執行。
- 使用者勾選自訂時再加入 `custom`。
- 每組設定沿用既有候選模型：Ridge、Random Forest、HistGradientBoosting。
- Recent Median Baseline 是共同參考，不屬於可調設定。
- 實作可以安全地重用完全相同的中間結果，但 manifest 必須如實表示實際執行及重用關係。
- 所有比較使用同一個 train／calibration split，避免設定間資料不同。
- 中古屋 fitting 使用該設定記錄的近期權重；評估指標保持不加權。
- 預售屋不使用近期權重。

### 7.2 勝者與 Release Gate

1. 在 calibration results 中排除執行失敗或指標不完整的候選。
2. 以整體 MAE 最低者選出唯一「設定＋模型」勝者。
3. 固定 tie-breaker：
   1. 較低整體 MAPE；
   2. 較低 RMSE；
   3. 較低運算量的設定順序 `quick → balanced → thorough → custom`；
   4. 穩定模型名稱排序。
4. 鎖定勝者後才計算 final test metrics。
5. 沿用既有整體 MAE、各站 MAPE、年度回測及其他 release checks。
6. 通過只代表可以人工發布，不代表已發布。

測試集結果不得改變第 2～3 步選出的勝者。

## 8. 共用前端元件

管理中心與模型觀測台保留各自頁面，但共用純前端資料轉換與呈現模組。此模組負責：

- 訓練 request 組裝；
- 參數驗證訊息映射；
- preset／custom 標籤；
- 單位格式化；
- 初學者摘要卡；
- 設定比較列；
- 各站警示；
- 回測與 Release Gate 列；
- 舊 schema fallback。

兩個頁面不得各自複製一份指標判讀規則。後端回傳原始數值與狀態，前端共用模組負責白話呈現。

介面要求：

- 預設只看到市場、三組固定設定及開始按鈕；
- 自訂設定使用可展開區域；
- 送出前顯示市場數與設定數；
- 欄位具有 label、單位、合法範圍及簡短說明；
- 結果摘要先於完整表格；
- 進階區域使用原生或可存取的 disclosure；
- 狀態不能只靠顏色表示；
- 執行中停用重複提交，但後端仍保護 active job。

## 9. 參數與報告一致性

參數說明不得再寫死在報告程式。

目前程式的近期權重函式預設為 48 個月，但既有報告文字曾固定顯示 24 個月。新流程必須：

- 由實際解析後的 tuning plan 傳入 fitting；
- 將實際值保存到 manifest；
- JSON、Markdown、管理中心及模型觀測台都讀取同一份快照；
- 缺少快照的舊紀錄只標示未知，不猜測 24 或 48 個月。

模型 artifact 必須保存推論所需的模型與 feature contract；評估報告保存訓練設定與證據。發布前 smoke 繼續驗證 artifact 可載入及可估價。

## 10. 錯誤處理

### 10.1 建立工作前

- 前端在欄位旁顯示白話錯誤。
- 後端以結構化 `invalid_request` 與欄位錯誤拒絕請求。
- 驗證失敗不建立 job。

### 10.2 工作執行中

- progress 顯示目前市場、設定與階段，不顯示不可靠的假百分比。
- 任一請求設定或候選執行異常時保存安全化原因。
- `quick`、`balanced`、`thorough` 任一必要設定無法完成，整體工作失敗。
- 使用者要求的 `custom` 若在 runtime 失敗，整體工作同樣失敗。
- 已完成結果可以保留供診斷，但整個 run 不得產生可發布候選。
- 舊正式模型與 manifest 永遠不受失敗訓練影響。

頁面必須回答：

1. 哪個市場／設定／階段失敗；
2. 已有正式模型是否仍安全；
3. 使用者可修改哪個欄位或重新建立什麼工作。

錯誤不得洩漏完整本機路徑、資料庫秘密或原始例外堆疊。

## 11. TDD 與驗收

### 11.1 後端單元與契約測試

- 三個固定設定的名稱、參數與版本。
- 四個自訂欄位的正常值、邊界值與拒絕值。
- market 與半衰期適用規則。
- request 不允許額外欄位或任意 profile。
- tuning plan 能序列化為穩定快照。
- schema v1／v2／新版 manifest 的相容讀取。
- 報告使用實際半衰期，不再寫死 24 個月。

### 11.2 訓練 service 測試

- 無自訂時執行三組，有自訂時執行四組。
- 中古屋與預售屋都使用同一時間切分比較各設定。
- 中古屋套用設定中的近期權重，預售屋不套用。
- 只使用 calibration metrics 選勝者。
- tie-breaker 完全 deterministic。
- final test 結果不會改選勝者。
- 任一要求設定失敗時 run fail closed，沒有可發布候選。
- 成功 run 保存 selected profile、model、parameters、metrics、backtests 與 checks。

### 11.3 前端 contract 測試

- 管理中心與模型觀測台產生相同 payload。
- 三個預設永遠顯示且不可被覆寫。
- 自訂欄位範圍、錯誤訊息與市場停用行為。
- 送出摘要正確計算市場與設定數。
- MAE、MAPE、Coverage 單位與白話說明。
- 勝出設定、Baseline 差距、站點警示及 gate 狀態。
- 舊紀錄顯示 fallback，不產生虛構參數。

### 11.4 整合與人工驗收

- 使用小型固定 fixture 完成一次三組設定比較。
- 使用一組合法 custom 完成四組比較。
- 使用一組非法 custom 驗證不建立 job。
- 模擬其中一組失敗，確認沒有可發布候選且正式模型不變。
- 在 `/admin` 與模型觀測台開啟同一 run，摘要與技術證據一致。
- 在瀏覽器確認初學者預設畫面簡潔、進階資訊可展開、鍵盤可操作。
- 手動發布仍需原有預覽與確認流程。

不新增大型 E2E framework 或像素比對。

## 12. 591 Web 更新範圍收斂

本專題的估價與待售情報只需要：

- `sale`：中古屋目前開價與供給；
- `newhouse`：預售／新建案目前供給。

`rental` 不參與成交模型、估價、待售比較或本次求職展示，因此不再出現在 Web 操作主線。

### 12.1 管理介面

- 591 更新區只顯示「中古屋出售」與「預售新建案」。
- 一鍵更新固定依序執行 `sale → newhouse`。
- 工作進度、結果摘要與分型狀態只顯示本次要求的兩種類型。
- 前端不得送出 `rental`。
- 舊工作若包含 `rental`，歷史詳細仍照實顯示，不隱藏或改寫舊結果。

### 12.2 Web API 與 service

- Web listing-update request 的預設 `types` 改為 `["sale", "newhouse"]`。
- Web API 白名單只接受 `sale`、`newhouse`，收到 `rental` 時回傳欄位驗證錯誤且不建立 job。
- Web 一鍵 sequencer 的 canonical order 固定為 `sale → newhouse`。
- Service 只執行 request 中經驗證的類型，不暗中補上租屋。
- CLI 為既有相容性可繼續接受明確指定的 `rental`；它不出現在 Web 或文件的日常操作流程。

### 12.3 資料與相容性

- 不刪除、搬移或覆寫既有租屋 batch、正式版本、資料列或工作紀錄。
- 不讓停止更新租屋觸發 rental delisting。
- 公開首頁與估價 API 不新增租屋資料依賴。
- 訓練資料仍只來自官方實價登錄；`sale` 與 `newhouse` 也不進入模型 fitting。

### 12.4 591 收斂測試

- Web request 未傳 `types` 時只建立 `sale`、`newhouse` 工作。
- Web request 明確傳送 `rental` 時拒絕且不建立 job。
- 一鍵 sequencer 只依序呼叫 `sale`、`newhouse`。
- 任一類失敗仍沿用既有 fail-closed publish 規則。
- 管理介面沒有租屋控制項或新租屋狀態列。
- 含租屋的舊工作詳細仍能正常顯示。
- CLI 明確指定 rental 的既有相容測試繼續通過。
- 測試停止 Web 租屋更新不會刪除或下架既有租屋資料。

## 13. 成功標準

- 不懂完整機器學習公式的使用者能說明這次模型是否通過及主要誤差。
- 不輸入 CLI 即可比較三個固定設定與一個選填自訂設定。
- 中古屋與預售屋皆可使用相同操作流程。
- 每個結果都能追溯到實際參數、資料切分、候選模型與 gate。
- 測試集未參與選模，失敗工作不能產生可發布候選。
- 管理中心與模型觀測台不再出現不同的指標說明或狀態。
- 現有舊訓練紀錄仍可閱讀。
- Web 端 591 一鍵更新只執行出售與新建案，不再顯示或接受租屋。
- 舊租屋資料、工作紀錄及明確 CLI 相容入口不受影響。
- 專案沒有膨脹成 AutoML 或企業級 MLOps 平台。
