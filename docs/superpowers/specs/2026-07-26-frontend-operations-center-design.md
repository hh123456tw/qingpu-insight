# 青埔智價前端運維中心設計

## 1. 目標

建立只允許本機存取的 `/admin` 運維中心，讓單一操作人員不必記憶或輸入 CLI 指令，即可完成資料更新、591 刊登更新、候選模型訓練、模型發布與回滾、備份與還原、LLM 設定及工作查詢。

本功能是專題、自用工具與求職作品集的精簡運維介面，不建立企業級管理平台。

## 2. 已核准的產品邊界

- 僅允許 `127.0.0.1`／`::1` 與可信 Host 存取，不建立帳號、角色或權限系統。
- 所有可操作功能都提供前端入口，包括正式模型發布／回滾與資料庫正式還原。
- 日常工作提供固定設定的一鍵流程，進階頁面才提供分段重跑與技術詳情。
- 不建立自動排程；所有工作都由操作人員在前端手動啟動。
- 模型訓練只建立候選版本，永遠不得自動發布。
- 中古屋與預售屋模型分開發布、分開回滾。
- MySQL 是 job、工作狀態與稽核摘要的唯一 runtime source of truth。
- MySQL 未連線時 `/admin` 仍可顯示診斷，但所有 mutation 都必須停用。
- 首頁保留市場儀表板、待售情報、AI 估價與智慧購屋報告；資料更新、維運、模型與工作紀錄移至 `/admin`。
- 預設使用白話狀態與建議操作；run ID、SHA256、模型指標、檔案路徑與安全化錯誤放在可展開的技術詳情。
- Gemini 是選配功能，預設仍可使用 Rule 或本機 Ollama。

## 3. 明確不做

- 不建立登入、RBAC、多人協作或遠端管理。
- 不建立 Windows Task Scheduler、自動通知、Email 或 webhook。
- 不建立多 worker、分散式 queue、command bus 或事件平台。
- 不建立獨立 audit 子系統；沿用 job 的建立時間、類型、輸入摘要、階段與結果。
- 不建立複雜取消或人工協作狀態機。
- 不直接從 Web 拼接或執行任意 CLI 指令。
- 不全面重構 `cli.py`；只抽出本次前端流程真正需要共用的 domain service。
- 不加入 XGBoost、超參數搜尋、模型漂移平台或模型自動發布。
- 不將 591 開價資料加入官方成交模型的訓練、校準或測試資料。
- 不透過 Web 提供任意檔案路徑、原始 HTML、Cookie、完整地址或秘密值。

## 4. 資訊架構

公開首頁只保留一個「運維中心」入口。`/admin` 使用固定側欄，分成八類：

### 4.1 總覽

- MySQL、資料、刊登、正式模型、LLM provider 與備份的健康摘要。
- 執行中工作、最近成功／失敗工作。
- 依固定門檻顯示待處理提醒，例如資料過舊、沒有可驗證備份、候選模型待審核。
- 連到各分類主要操作的快速入口。

### 4.2 資料中心

- 「一鍵更新官方資料」固定依序執行：
  1. acquire；
  2. analyse；
  3. market-build；
  4. MySQL publish。
- 任一步驟失敗即停止，不發布不完整資料。
- 成功後顯示資料筆數、期間、SHA256、品質判定與版本。
- 進階操作允許從安全 checkpoint 重跑單一步驟，但參數只能使用後端白名單，不接受任意路徑。
- 資料更新完成後只提示「可訓練新候選模型」，不自動訓練或發布。

### 4.3 刊登爬蟲

- 一鍵更新 `sale`、`newhouse`、`rental`。
- 允許只重跑單一刊登類型。
- 顯示 capture、normalize、locate、validate、publish 等階段。
- Selenium 必須使用可見 Chrome；遇到驗證頁時提示操作人員到 Chrome 處理，不規避 CAPTCHA。
- 每種類型獨立驗證與發布；某一類失敗不得污染其他類型。
- 未完成或驗證失敗的 batch 永遠不能觸發下架或替換正式版本。
- 前端顯示安全化驗證摘要；原始 HTML 與 Cookie 不提供下載。

### 4.4 模型中心

- 顯示中古屋與預售屋目前正式模型、資料期間、候選模型與模型卡。
- 候選訓練沿用既有固定候選：Recent Median Baseline、Ridge、Random Forest、HistGradientBoosting。
- 候選模型使用 calibration 選模，鎖定後才在 final test 與 baseline 比較。
- 發布頁並列候選與正式模型的 MAE、MAPE、RMSE、R²、區間覆蓋率、資料日期與 release gate。
- 中古屋與預售屋各自發布，候選未通過 gate 時不顯示可執行發布按鈕。
- 發布與回滾使用不可變候選／歷史版本，禁止 Web 上傳 artifact 或指定任意檔案路徑。
- 網站推論只讀目前正式 manifest 指向的 artifact。

### 4.5 LLM 與報告

- 顯示 Rule、Ollama、Gemini 是否可用及最近一次測試結果。
- 提供 provider smoke test 與固定匿名案例 benchmark。
- Benchmark 僅接受後端列出的 provider、模型與固定案例集，不接受任意檔案路徑。
- Gemini Key 可新增、替換、刪除及測試連線。
- 智慧購屋報告的產生介面仍留在首頁；運維中心只管理 provider 與 benchmark。

### 4.6 備份與還原

- 建立 MySQL dump，保存 SHA256、大小、時間與驗證結果。
- 提供隔離資料庫還原演練。
- 正式還原只能選擇 repository 已登錄且 SHA256 驗證通過的備份。
- 正式還原前必須先建立並驗證目前資料庫的安全備份；失敗時不得繼續。
- 正式還原後執行 health check 並記錄結果。
- 不提供任意 dump 路徑輸入，也不在本階段提供備份刪除功能。

### 4.7 工作紀錄

- 列出 queued、running、succeeded、failed 與 interrupted 工作。
- 顯示白話階段、耗時、輸入摘要、結果摘要與允許下載的產物。
- 可重試的工作以相同安全參數建立新 run ID；不得修改或重新執行原 run。
- 同類 mutation 同時間只允許一個；UI 停用重複按鈕，後端仍以 active-job check 保護。

### 4.8 設定與診斷

- 檢查 MySQL、Chrome／Selenium、Ollama、Gemini、`mysqldump`／`mysql` 與必要資料目錄。
- 顯示相依工具是否可用及白話修復建議。
- 不提供服務啟動／停止、套件安裝或 Windows 排程控制。
- 技術詳情可顯示安全化版本與路徑，不顯示資料庫密碼、Cookie 或 API Key。

## 5. 架構

### 5.1 共用 service

Web 與 CLI 都只能做輸入轉換與輸出呈現。實際流程由共用 service 負責：

- `OfficialDataUpdateService`
- 既有刊登 pipeline／runner
- 既有 `ModelTrainingService`
- 新的模型發布 repository／service
- 既有 `BackupService`
- 新的正式還原 orchestration
- 既有 LLM smoke／benchmark service
- 新的本機秘密設定 repository

不能以 `subprocess` 將使用者輸入組成 `qingpu-data.exe` 指令。外部程式只允許由既有受控 runner 使用固定 executable 與後端產生的參數。

### 5.2 Job 執行

所有長時間 mutation 都遵循相同路徑：

1. 前端送出受限表單。
2. Admin API 驗證 loopback、Host、CSRF、MySQL readiness 與欄位。
3. Service 建立 MySQL job，API 立即回傳 `202` 與 run ID。
4. 既有單一本機 worker 執行工作並更新 stage、progress、summary 與 artifact metadata。
5. 前端輪詢既有 job API，呈現進度與最終結果。

第一版不加入工作取消。程式重啟後發現無法繼續的 running job，必須標記為 `interrupted`，不得永遠顯示執行中。

### 5.3 產物與 metadata

- 交易資料、刊登 batch、模型 artifact、報告與備份沿用現有檔案目錄。
- Job、正式版本指標、發布／回滾摘要與備份 metadata 存 MySQL。
- 候選模型目錄以 run ID 命名且不可變。
- 正式模型使用小型 manifest 指向已驗證版本，不直接把候選來源改寫成正式檔案。
- 前端只可下載白名單報告；下載 API 以 ID 對 repository 查找實體路徑。

## 6. 一鍵流程

### 6.1 官方資料更新

`acquire → analyse → market-build → MySQL publish`

- 每個 stage 保存輸出摘要。
- `analyse` 為 NO-GO、品質報告失敗或版本驗證失敗時停止。
- MySQL publish 成功後才更新總覽的正式資料版本。

### 6.2 591 刊登更新

`visible capture → normalize／locate → artifact validation → two-phase publish`

- 每種類型獨立成功或失敗。
- 正式版本只在完整 batch 驗證成功後切換。
- 使用者處理驗證頁所花時間包含在 job 中；超過固定 timeout 則安全失敗並保留上一版。

### 6.3 候選模型訓練

`load official M1 data → split → calibration selection → locked final test → release gate → immutable candidate`

- 訓練完成只新增候選。
- 不更動正式 manifest。
- 591 資料不進入任何模型 frame。

## 7. 高風險操作

### 7.1 共通安全閘門

正式模型發布／回滾與資料庫正式還原必須依序完成：

1. 取得後端產生的預覽，顯示來源版本、目標版本與影響範圍。
2. 執行 preflight：MySQL、active-job lock、artifact／backup 完整性及目前版本檢查。
3. 使用者輸入頁面顯示的完整確認文字。
4. 以預覽 ID 建立 job；預覽短時間有效且只能使用一次。
5. 建立並驗證安全備份。
6. 執行受控切換或還原。
7. 讀回正式狀態並執行 smoke／health check。
8. 將結果寫入 job summary。

任何一步失敗即停止。錯誤結果必須明確說明舊正式版本是否仍在使用。

### 7.2 模型發布與回滾

- 發布前再次確認候選屬於指定 market、gate 通過且尚未被修改。
- 備份目前 market 的正式 manifest。
- 以暫存 manifest 完成載入與 smoke，最後使用原子替換切換正式 manifest。
- 發布後讀回版本並完成一次受控估價 smoke。
- 回滾只能選擇 repository 已記錄且可載入的歷史正式版本。
- 中古屋操作不得變更預售屋 manifest，反之亦然。

### 7.3 資料庫正式還原

- 只接受已驗證 backup ID。
- 自動備份目前正式資料庫並完成 SHA256 驗證。
- 使用固定 database URL 與受控 mysql runner，不接受任意 host、database 或 shell 參數。
- 還原結束後執行 schema、health 與必要 row-count 檢查。
- 還原失敗時保留事前備份 ID 與人工復原說明，不自動進行第二次破壞性操作。

## 8. 錯誤處理

所有錯誤畫面回答：

1. 發生什麼；
2. 目前正式資料／模型是否仍安全；
3. 操作人員現在可執行什麼。

錯誤類型：

- readiness error：不建立 job，停用 mutation 並提供重新檢查。
- validation error：在送出表單處指出欄位問題。
- job failure：保存白話摘要與可展開的安全化技術詳情。
- interrupted：程式重啟後將遺留工作標記為中斷，可由安全參數建立新 job。
- publish／restore failure：fail closed，不把未驗證結果設為正式版本。

可重試只代表「用相同白名單參數建立新工作」，不得重放已消耗的確認預覽。

錯誤、log 與 artifact 必須遮蔽 API Key、Cookie、資料庫密碼、Authorization header、完整地址及原始 HTML。

## 9. Gemini Key 精簡方案

- 使用 `instance/secrets.env` 保存 `GEMINI_API_KEY`。
- `instance/secrets.env` 必須被 Git 排除，且不納入備份、報告或下載 API。
- 前端只回傳 `configured: true／false`，不得讀回完整 Key。
- 新增／替換時以暫存檔寫入後原子替換。
- 刪除只移除 Gemini Key，不刪除其他受支援設定。
- 連線測試只回傳 provider、成功狀態、安全化錯誤與檢查時間。
- README 明確標示此方案只適用本機單人專題，不適用公開或多人部署。

## 10. 測試與驗收

### 10.1 自動測試

- Service：一鍵流程順序、失敗停止、輸出 metadata、正式版本不被污染。
- API：loopback／trusted Host、CSRF、MySQL readiness、欄位白名單與安全錯誤。
- Job：active-job check、stage／progress、成功、失敗、interrupted 與安全重試。
- 高風險操作：預覽過期、確認文字錯誤、備份失敗、artifact／SHA256 不符時不得執行 mutation。
- 模型：market 隔離、候選 gate、原子 manifest 發布與回滾。
- 刊登：三類隔離、不完整 batch 不發布、失敗不觸發 delisting。
- Secrets：寫入、替換、刪除、狀態查詢及所有輸出不洩漏 Key。
- 前端 contract：八類導覽、按鈕 readiness、job polling、白話錯誤與技術詳情。

每個行為採小型 TDD cycle，不為測試新增不必要的框架或抽象層。

### 10.2 人工瀏覽器 smoke

每一交付階段在 `http://127.0.0.1:5000/admin` 執行主要流程，確認畫面、進度、錯誤與導覽。

最終真實驗收只有五組：

1. 官方資料一鍵更新；
2. 591 三類更新與至少一次失敗安全保留舊版本；
3. 候選模型訓練、中古屋或預售屋單獨發布及回滾；
4. MySQL 備份、隔離還原演練及正式還原安全閘門；
5. Rule、Ollama、Gemini 狀態與至少一個可用 provider 的 smoke／benchmark。

不新增大型 browser E2E framework 或像素比對。

## 11. 分階段交付

### Phase 1：運維中心骨架

- `/admin` shell、固定側欄、總覽、設定與診斷、工作紀錄。
- MySQL readiness、共用 Admin API 保護與首頁入口。

### Phase 2：資料與刊登

- 官方資料一鍵更新及進階 checkpoint。
- 591 三類一鍵更新、單類型重跑、驗證摘要與失敗保護。

### Phase 3：模型生命週期

- 候選訓練與比較。
- 中古屋／預售屋獨立發布、正式 manifest、歷史版本與回滾。

### Phase 4：備份與 LLM

- 備份、隔離還原演練、正式還原安全閘門。
- Gemini Key、provider 診斷、smoke 與 benchmark。

每個 Phase 都必須能獨立測試、人工驗收與提交；前一 Phase 完成後才開始下一 Phase。

## 12. 成功標準

- 操作人員不需要輸入 CLI 指令即可完成五組最終真實驗收。
- 所有長工作都有可追蹤 run ID、白話進度及最終結果。
- 任何失敗都不會以不完整資料、刊登 batch 或模型取代正式版本。
- 正式模型發布／回滾與資料庫還原都必須通過預覽、確認、備份及驗證。
- 首頁保持一般使用者導向，運維功能集中且分類清楚。
- 專案沒有新增排程、登入、分散式 queue 或企業級秘密管理等非必要複雜度。
