# 青埔智價模型觀測台設計

**日期：** 2026-07-25

**狀態：** 已核准

**範圍：** 第一版模型觀測、固定設定背景訓練、候選模型評估與歷史紀錄

## 1. 目標

建立只供本機使用的模型觀測台，讓操作人員不必輸入 PowerShell 指令，也能：

- 查看目前正式中古屋與預售屋模型；
- 理解模型使用的資料範圍、樣本數與主要評估指標；
- 以固定安全設定啟動中古屋、預售屋或兩者的訓練；
- 查看訓練進度、歷史、候選模型比較、系統建議與具體警告；
- 保存版本化候選模型、評估報告及模型卡；
- 明確確認本次訓練不會發布或替換正式模型。

此功能同時改善模型流程的可觀測性與可重現性，但不在第一版解決模型發布、模型回復、
任意超參數調整、資料更新或 591 維運。

## 2. 已確認的產品決策

1. 第一版採「模型觀測台」，不擴張成完整維運台。
2. 訓練使用固定安全設定；前端只允許選擇中古屋、預售屋或全部。
3. 每次成功訓練保存版本化候選模型與完整評估資料。
4. 畫面同時呈現原始指標與可解釋的系統判定。
5. 候選模型維持 Recent Median Baseline、Ridge、Random Forest 與
   HistGradientBoosting；第一版不加入 XGBoost。
6. Web、CLI 必須呼叫同一個 application service，不維護兩套訓練邏輯。
7. 第一版不存在由 Web 發布或回復正式模型的路徑。
8. 實作完成後補回歸測試，不採 TDD。

## 3. 不在第一版範圍

- 從前端發布、提升或回復正式模型；
- 自動將成功訓練結果接到現有估價 API；
- XGBoost、LightGBM、超參數搜尋或 AutoML；
- 任意 PowerShell、Python、CLI 指令或檔案路徑輸入；
- 實價登錄下載、資料清理、591 更新、健康檢查與備份入口；
- Windows 工作排程或自動週期訓練；
- 模型漂移通知、自動刪除歷史 artifact 或磁碟配額管理；
- 公開網路部署、帳號、角色與多人權限。

## 4. 架構

新增獨立的 `ModelTrainingService`，從目前 `cli.py` 抽出資料載入、特徵建立、時間切分、
候選模型訓練、評估、判定與 artifact 寫入。CLI `model-train` 與 Web 管理 API 都只負責
組裝依賴及呼叫此服務。

Web 端沿用現有 `JobService`、MySQL job repository 與 `LocalJobExecutor`。訓練工作由單一
背景 worker 執行，避免阻塞 HTTP request，也避免兩個模型工作同時競爭記憶體或覆寫檔案。

```text
模型觀測台
  -> 固定管理 API
  -> JobService / MySQL job history
  -> LocalJobExecutor (max_workers=1)
  -> ModelTrainingService
      -> 固定實價登錄資料
      -> 版本化候選 artifact
      -> 評估報告與模型卡

現有估價 API
  -> artifacts/resale.joblib 或 artifacts/presale.joblib
  -> 不讀取候選目錄
```

候選輸出與正式模型必須實體隔離：

```text
artifacts/
├── resale.joblib                    # 現有正式模型
├── presale.joblib                   # 現有正式模型
└── candidates/
    └── <training_run_id>/
        ├── manifest.json
        ├── resale.joblib            # 僅在本次包含中古屋時存在
        ├── presale.joblib           # 僅在本次包含預售屋時存在
        └── reports/
            ├── resale-evaluation.json
            ├── resale-model-card.md
            ├── presale-evaluation.json
            └── presale-model-card.md
```

`ModelRegistry` 的正式模型查找規則不變，且不得搜尋 `artifacts/candidates/`。

## 5. 元件邊界

### 5.1 ModelTrainingService

職責：

- 接受固定市場集合 `resale`、`presale`；
- 從固定設定解析正式資料來源；
- 建立資料快照與品質摘要；
- 分市場建立模型 frame 與時間切分；
- 訓練固定候選模型；
- 產生比較指標與系統建議；
- 以暫存目錄建立全部產物；
- 完成 schema、hash 及必要檔案驗證後，原子移入候選版本目錄；
- 回傳可寫入 job summary 的安全結果。

它不負責 HTTP、HTML、背景 thread、正式模型發布或資料下載。

### 5.2 Model artifact registry

新增只讀的候選模型 registry，負責：

- 依 `training_run_id` 讀取已完成的 `manifest.json`；
- 列出最近候選版本；
- 驗證 run ID 格式並組成固定根目錄下的路徑；
- 拒絕不存在、未完成或超出候選根目錄的內容；
- 提供報告下載所需的固定檔案映射。

它不得修改正式模型，也不得接受任意相對或絕對檔案路徑。

### 5.3 Web 管理 API

Web 只驗證固定 payload、建立 job、交給 executor，並將 service 結果轉為 JSON。既有
loopback、trusted Host、session CSRF、錯誤 redaction 與 job polling 契約繼續適用。

### 5.4 模型觀測台

前端只處理顯示、固定選項送出、輪詢與報告下載。它不計算評估指標、不判斷推薦模型，
也不組裝任何本機檔案路徑。

## 6. 資料版本與品質摘要

每次工作在訓練前記錄：

- 資料檔 SHA-256；
- 原始總筆數；
- 中古屋與預售屋的可用、排除筆數；
- 最早與最晚交易日期；
- A17、A18、A19 各站可用筆數；
- 缺失值、重複資料與異常排除摘要；
- 程式 Git commit；
- 工作目錄是否為 dirty；
- Python、NumPy、pandas 與 scikit-learn 版本。

訓練只使用官方實價登錄處理資料。591 開價資料不得進入 M2 成交模型。

`training_run_id` 使用既有 job run UUID，成為工作紀錄、候選目錄與 manifest 的共同鍵。
同一 run 的候選目錄不可覆寫。

## 7. 訓練與時間評估

中古屋與預售屋分開建立模型及選擇候選，不要求使用相同 estimator。資料必須按交易日期
順序切分，不得隨機拆分。

第一版保留現有 M2 的 train／calibration／test 日期邊界，但修正目前
`evaluate_candidate()` 直接以 test 比較所有候選所造成的選模偏誤：

1. 訓練區段用於 fit Baseline 與所有候選模型；
2. 所有候選只在校準區段比較，以既有 release gate 選定一個候選；
3. 被選候選的 90% 估價區間半徑使用校準區段絕對殘差第 90 百分位；
4. 候選名稱與區間規則鎖定後，才在最新測試區段進行一次最終評估；
5. 測試區段只比較鎖定候選與 Baseline，不能再用來改選其他候選；
6. 測試區段不得回流至這次被評估的 artifact。

因此報告包含兩套清楚標記的指標：

- `selection_metrics`：所有候選在 calibration 的比較結果；
- `final_test_metrics`：鎖定候選與 Baseline 在 test 的最終結果。

若鎖定候選未通過 final test 的既有 release gate，artifact 仍可保存供診斷，但系統判定必須
是 `not_recommended`。不得回頭挑選在 test 表現較好的另一個候選。

版本化候選是「已被這份報告評估的模型」，不是已使用所有最新交易重新 fit 的正式模型。
觀測台必須顯示訓練資料截止日、校準日期範圍、測試日期範圍及報告日期，避免將資料集最新
日期誤認為模型已訓練至該日。

正式發布流程不在本 spec 內。未來若加入發布，必須另行定義重新 fit、區間校準、獨立驗證、
原子發布及回復規則，不能直接把本 spec 的候選目錄複製到正式位置。

## 8. 候選模型與系統判定

固定候選如下：

| 候選 | 用途 |
|---|---|
| Recent Median Baseline | 所有模型必須比較的基準 |
| Ridge | 穩定、可解釋的線性候選 |
| Random Forest | 非線性樹集成候選 |
| HistGradientBoosting | 非線性梯度提升候選 |

不在第一版新增 XGBoost。先取得可比較、版本化的歷史結果，再以獨立後續規格決定是否新增
候選依賴。

每個市場至少輸出：

- 所有候選在 calibration 的全體 MAE、MAPE、RMSE、R²；
- 鎖定候選與 Baseline 在 test 的全體 MAE、MAPE、RMSE、R²；
- calibration 與 test 各自相對 Baseline 的改善幅度；
- test 的 A17、A18、A19 樣本數與 MAPE；
- test 的主要建物類型樣本數與指標；
- 90% 估價區間覆蓋率與平均寬度；
- 各候選模型訓練耗時；
- 被選模型名稱與選擇理由。

樣本少於 30 筆的分群顯示樣本數與「樣本不足」，不發布該分群性能結論。

系統判定只有 `recommended` 或 `not_recommended`，並保存機器可讀的 reason codes 及繁體中文
說明。候選先在 calibration 通過既有 M2 release gate 才能鎖定；鎖定候選還必須在 test
再次通過同一 gate，最終判定才可為 `recommended`。另加入以下警告：

- `insufficient_segment_sample`：任一必要分群測試筆數少於 30；
- `stale_training_data`：模型訓練截止日明顯落後資料集最新日；
- `interval_too_wide`：估價區間平均寬度超過既有 M2 門檻；
- `segment_regression`：整體改善但任一可評估站點明顯退步；
- `dirty_source_tree`：訓練時工作目錄含未提交修改。

警告不等同自動失敗；UI 必須同時顯示原始數字，讓操作人員自行核對。沒有候選通過既有
release gate 時，Baseline 可成為該次選擇結果，但判定為 `not_recommended`，且不得暗示應發布。

## 9. 頁面設計

新增：

```text
GET /admin/models
```

頁面包含四區。

### 9.1 目前正式模型

分別顯示中古屋與預售屋：

- 模型名稱與版本；
- artifact 建立時間；
- 訓練資料截止日；
- 可從正式 artifact 讀到的主要指標；
- artifact 缺失、無法載入或 metadata 不完整警告。

正式模型卡片使用「目前正式模型」標籤；候選版本不得使用相同視覺標籤。

### 9.2 資料狀態

顯示資料 hash 短碼、日期範圍、原始與可用筆數、中古屋／預售屋分布、A17／A18／A19
分布及排除摘要。狀態由後端計算，前端不直接解析 Parquet。

### 9.3 開始訓練

固定市場選項為：

- 中古屋；
- 預售屋；
- 全部。

按鈕旁永久顯示「只建立候選版本，不會替換正式模型」。送出前使用簡短確認對話框。存在
活動中的模型工作時，按鈕停用並顯示該工作。重複送出回傳既有活動工作，不建立第二份。

### 9.4 訓練歷史與詳情

預設列出最近 20 次，包含：

- run ID 短碼；
- 市場；
- queued、running、succeeded、failed 狀態；
- 開始、完成時間與耗時；
- 資料 hash 短碼；
- 各市場被選模型；
- recommended 或 not recommended；
- 警告數量。

詳情顯示資料摘要、時間區段、候選比較表、分站結果、判定理由、警告及安全錯誤訊息。
成功工作提供模型卡與 JSON 評估報告下載，不提供 `.joblib` 瀏覽器下載。

## 10. API 契約

### 10.1 狀態

```text
GET /api/admin/models/status
```

回傳正式模型 metadata、目前資料摘要及活動中的模型工作。此端點只讀，不觸發訓練。

### 10.2 歷史

```text
GET /api/admin/model-training-runs?limit=20
GET /api/admin/model-training-runs/<run_id>
```

`limit` 允許 1 至 100，預設 20。run detail 合併安全 job metadata 與已驗證 manifest。

### 10.3 建立工作

```text
POST /api/admin/model-training-runs
```

唯一合法 payload：

```json
{
  "markets": ["resale", "presale"]
}
```

`markets` 必須是非空、無重複且只包含 `resale`、`presale` 的陣列。後端將其排序為固定順序，
使冪等鍵可重現。未知欄位、模型名稱、超參數、路徑或命令一律回傳 400。

POST 必須通過 loopback、trusted Host 與 session CSRF 檢查。相同市場集合已有活動工作時，
回傳該工作及 200；新工作回傳 202。

### 10.4 報告下載

```text
GET /api/admin/model-training-runs/<run_id>/reports/<report_type>
```

`report_type` 使用固定白名單：

- `resale-evaluation`
- `resale-model-card`
- `presale-evaluation`
- `presale-model-card`
- `manifest`

後端從白名單映射固定檔名，不接受 query string 路徑或原始檔名。

## 11. 工作狀態與進度

沿用既有 job 狀態：

```text
queued -> running -> succeeded
                  -> failed
```

模型工作使用 `job_type=model_training`。進度摘要只允許固定 stage：

- `validating_data`
- `training_resale`
- `evaluating_resale`
- `training_presale`
- `evaluating_presale`
- `writing_artifacts`

每次 stage 更新保存安全摘要與已完成市場；不把 estimator repr、完整資料列、連線字串或
traceback 寫入公開 job message。

單 worker 保證同一 process 內不並行。MySQL 活動工作唯一性提供跨 request 的第二層保護。

## 12. 錯誤與恢復

- 固定資料來源不存在：工作失敗，顯示缺少的資料種類與既有資料更新指令。
- 必要欄位缺失或可用資料不足：工作失敗，不寫候選版本。
- 單一非 Baseline 候選失敗：保存候選錯誤碼並繼續其他候選。
- Baseline 失敗或沒有任何可評估結果：該市場失敗。
- 「全部」工作任一市場失敗：整個 run 標記 failed，不發布部分候選目錄；安全摘要可顯示
  哪個市場失敗。
- 產物先寫入候選根目錄下的同磁碟暫存目錄；全部必要檔案通過 schema、hash 與可載入驗證後，
  以原子 rename 建立 `<training_run_id>`。
- 正式模型目錄在任何失敗路徑都不可寫入。
- Web process 重啟後，MySQL 中遺留的模型 `running` 工作在啟動恢復程序中標示 failed，
  error code 使用 `worker_interrupted`；第一版不自動重跑。
- 所有外顯錯誤經既有 `redact_job_message()` 處理，不回傳 traceback、credential、API key、
  電話或完整資料列。

## 13. 保存與清理

第一版不自動刪除成功或失敗紀錄，也不從 UI 提供刪除。列表只載入最近 20 次，可調至最多
100 次。artifact 清理、保留週期及磁碟配額是後續維運功能。

暫存目錄只可在確認其 resolved path 位於固定候選根目錄下後清理。啟動恢復可移除已確認屬於
中斷工作且未完成的暫存目錄，但不得移除已完成版本或正式模型。

## 14. 驗收與測試

依產品決策採 implementation-first，完成各元件後補回歸測試，不要求先寫失敗測試。

### 14.1 Service 回歸

- 固定市場集合驗證與排序；
- 資料 hash、日期、筆數與環境 metadata；
- 中古屋與預售屋分離訓練；
- 四類固定候選存在；
- 所有候選只以 calibration 選模；
- test 只評估鎖定候選與 Baseline，且不因 test 結果改選；
- 時間切分不洩漏；
- 分群少於 30 筆時只產生樣本不足警告；
- manifest、報告及 job summary schema；
- 暫存寫入失敗不留下完成版本；
- 候選輸出不修改正式 artifact hash。

### 14.2 API 回歸

- 狀態、歷史、詳情及固定報告下載；
- POST 的 loopback、trusted Host、CSRF；
- 接受三種固定市場組合；
- 拒絕未知市場、空集合、重複市場、未知欄位與任意路徑；
- 活動工作重複送出回傳既有 run；
- API 不洩漏 traceback 或本機秘密。

### 14.3 前端契約

- 活動工作時按鈕停用；
- 輪詢 terminal status 後停止；
- succeeded 後重新載入歷史與詳情；
- failed 顯示安全中文摘要；
- 正式與候選標籤不混淆；
- 頁面永久顯示「不會替換正式模型」。

### 14.4 全域驗證

- 執行完整 pytest；
- 執行 Ruff；
- 檢查前端 JavaScript syntax；
- 比對訓練前後正式 `resale.joblib`、`presale.joblib` hash 不變。

### 14.5 人工瀏覽器 smoke

1. 開啟 `/admin/models`，確認正式模型與資料狀態可讀。
2. 啟動一次中古屋訓練。
3. 觀察 queued、running 與固定 stage。
4. 確認完成後可展開模型比較、分站指標與判定理由。
5. 下載模型卡及 JSON 報告。
6. 回到估價頁執行一次估價，確認仍使用原正式模型版本。
7. 重啟網站，確認歷史紀錄仍存在。
8. 比對正式模型 hash，確認未被候選訓練修改。

## 15. 成功標準

- 操作人員可完全從瀏覽器啟動固定模型訓練並查看結果，不需輸入訓練指令；
- 任意一次訓練都能追溯資料 hash、程式版本、環境版本、時間區段、候選指標及產物；
- 系統建議同時提供數字和理由，不以單一「可信度」隱藏資料不足；
- 網頁無法選擇任意命令、路徑、模型或超參數；
- 重複點擊不建立並行訓練；
- 成功、失敗、中斷均保留可理解且不洩密的紀錄；
- 所有候選版本與正式 artifact 隔離；
- 第一版的任何 Web 操作都不能替換正式模型。
