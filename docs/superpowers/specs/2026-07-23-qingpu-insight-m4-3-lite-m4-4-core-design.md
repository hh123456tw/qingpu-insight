# 青埔智價 M4.3 Lite／M4.4 Core 設計

**日期：** 2026-07-23
**狀態：** 已批准，待實作計畫更新
**目標：** 在不擴張成完整維運平台或多供應商 AI 平台的前提下，完成可維護、可展示的本機健康／備份能力與可驗證智慧購屋報告。

## 1. 設計原則

- 作品集與單人自用優先，功能必須能用一段話解釋並以單一指令驗收。
- MySQL 維持 runtime source of truth；不新增 SQLite、JSON 或 Parquet runtime repository。
- M4.3 只回答「系統現在是否正常」及「資料能否真正還原」。
- M4.4 只回答「根據現有證據，這個物件有哪些價格優勢、風險及議價方向」。
- 沒有 Ollama、Gemini 或網路時，系統仍能顯示最後成功資料並產生規則式報告。
- 不提前實作 M4.5 的 profile、收藏、比較、通知與多人功能。

## 2. 執行順序與邊界

1. 先完成一次 M4.2 真實 MySQL、可見 Selenium 與三類 591 手動驗收。
2. 實作 M4.3 Lite，建立健康摘要、手動備份與隔離還原驗證。
3. 實作 M4.4 Core，建立 Evidence Pack、規則式報告、Ollama、Gemini 選配與固定案例 benchmark。
4. M4.3、M4.4 各自完成 review gate 後停止，不自動進入 M4.5 或 M4.6。

## 3. M4.3 Lite：健康與可還原備份

### 3.1 使用者看到的功能

本機維運頁只顯示一張健康摘要：

- MySQL 是否可連線。
- 官方成交資料最後成功發布時間與筆數。
- 591 `sale`、`newhouse`、`rental` 最後成功發布時間與目前筆數。
- 最近一次 listing update 工作狀態、安全錯誤碼及完成時間。
- 備份檔最後成功建立時間、檔案大小及 checksum 驗證狀態。
- 資料磁碟剩餘空間。

狀態只分為 `healthy`、`warning`、`critical`。M4.3 Lite 不建立可自訂 threshold 管理介面；門檻由程式設定並在 README 說明。

### 3.2 備份與還原

- `backup-create` 以參數陣列呼叫 `mysqldump`，產生 timestamped SQL dump 與 SHA-256。
- 密碼只能經 process environment 傳入，不得出現在 command、log、API 或 metadata。
- `backup-restore-drill` 建立名稱含固定安全前綴的隔離資料庫，還原 dump，檢查核心資料表、published pointer、核心筆數及 smoke query。
- 演練成功或失敗都保存 metadata；結束後刪除隔離資料庫。
- 未通過名稱前綴檢查時拒絕執行 DROP／restore。
- Web 只顯示備份與演練結果，不提供 HTTP 建立、還原或刪除資料庫操作。

### 3.3 明確刪除的原範圍

M4.3 Lite 不做：

- 模型 PSI、特徵分布及 missingness 漂移平台。
- Windows Task Scheduler 健康排程。
- Email、Windows 通知或自動事故處理。
- 可編輯 threshold、長期圖表及完整維運 Dashboard。
- 自動重新訓練、重新爬取或發布。

模型品質與漂移只在 M2/M4.4 benchmark 中離線觀察；模型穩定且有足夠歷史版本後再另立里程碑。

## 4. M4.4 Core：可驗證智慧購屋報告

### 4.1 資料流

```text
MySQL 已發布資料
  -> EvidenceBuilder
  -> 匿名 Evidence Pack
  -> Rule provider（永遠可用）
  -> Ollama 或選配 Gemini
  -> ReportValidator
  -> MySQL 報告紀錄
  -> Web／CLI 顯示
```

`EvidenceBuilder` 只讀已發布的官方成交、591 刊登、估價結果、資料版本與新鮮度。每個可引用事實都有穩定 `fact_id`、單位、來源類型及資料版本。

### 4.2 報告內容

第一版報告固定包含：

- 價格合理性摘要。
- 三項以內的優點。
- 三項以內的風險。
- 議價方向，不產生保證成交價。
- 資料新鮮度與限制。
- 每個數值結論引用的 Evidence Pack `fact_id`。

報告不提供法律、貸款核准、投資報酬保證或未來房價預測。

### 4.3 Provider 策略

- `RuleReportProvider` 是必備保底，沒有任何 LLM 也能產生完整基本報告。
- `OllamaReportProvider` 是本機主要 AI provider，使用 HTTP structured output；模型名稱由設定指定。
- `GeminiReportProvider` 是有 API key 才啟用的選配雲端 provider。
- Mock provider 只存在於自動測試，不作為使用者功能或獨立平台。
- Provider 只收到匿名 Evidence Pack，不可連線 MySQL、呼叫工具、上網查價或讀取原始 591 HTML。
- timeout、429、連線失敗、無效 JSON、schema 錯誤或 fact 驗證失敗時，最多修復一次，之後回退規則式報告。

### 4.4 驗證與保存

- Pydantic schema 限制報告欄位、長度及 claim 結構。
- `ReportValidator` 拒絕不存在的 `fact_id`、Evidence Pack 外的數值、單位錯置及敏感資訊。
- LLM 文字不得覆寫 Evidence Pack 的數值；顯示層以 fact registry 的值為準。
- MySQL 只保存報告內容、provider、model、evidence version、validation 結果、生成時間與 latency。
- 報告失敗不得影響 M4.2 published dataset，也不得改寫成交或刊登資料。

### 4.5 Benchmark 與弱機驗收

- RTX 3080 12GB 主機只執行一次固定案例 benchmark，比較少量候選 Ollama 模型與 Gemini 選配基準。
- 評分只包含 schema 成功率、fact 正確率、規則覆蓋、延遲及記憶體／VRAM 可否執行。
- benchmark 結果保存為 JSON 與 Markdown artifact，不建立常駐 benchmark 服務。
- GTX 1050 Ti 4GB 主機只對選定的小模型執行一個 smoke case；無法載入時驗證 rule fallback 即算通過。
- Gemini 不作為 release gate 的必要條件，CI 也不呼叫 Gemini 或 Ollama。

## 5. 錯誤與安全

- 所有 CLI／API 錯誤使用固定安全 error code，不回傳 API key、資料庫 URL、prompt、原始 HTML 或 provider raw body。
- Evidence Pack 排除姓名、電話、Email、Cookie、token、591 聯絡資訊及不必要的完整地址。
- 備份與報告檔案不提交 Git；`.env`、Gemini key、MySQL 密碼與瀏覽器 profile 繼續由 `.gitignore` 排除。
- Gemini UI 與文件必須提示資料會傳送至外部服務，且只有使用者明確設定 key 後才可使用。

## 6. 測試策略

### M4.3 Lite

- 單元測試健康狀態與資料新鮮度門檻。
- 使用 fake process runner 測試命令參數、環境變數、checksum 與錯誤碼。
- 使用隔離 fake／測試資料庫驗證 restore 流程、核心表檢查及安全資料庫名稱。
- Web 測試只讀 API，不允許透過 HTTP 執行 backup／restore。
- 手動 release gate 必須完成一次真實 MySQL dump 與隔離還原。

### M4.4 Core

- Rule provider、EvidenceBuilder、Pydantic schema 與 fact validator 全部離線測試。
- Ollama／Gemini 使用 HTTP contract fake 測試 timeout、429、invalid JSON、schema repair 與 fallback。
- E2E 使用已發布 fake dataset 產生報告，竄改 fact 或數值時必須被拒絕。
- CI 只跑 Rule／Mock boundary，不呼叫外部 provider。
- RTX 3080 benchmark 與 GTX 1050 Ti smoke 是明確的手動驗收。

## 7. Release Gate

### M4.3 Lite

- 本機健康頁能顯示 MySQL、官方資料、三類 591、最近工作、備份及磁碟摘要。
- 健康檢查失敗只回安全狀態，不洩漏連線資訊。
- 至少一份真實 MySQL 備份通過 SHA-256。
- 至少一次隔離還原成功，核心表、published pointer 與筆數驗證通過，正式資料庫未改變。

### M4.4 Core

- 無 Ollama、無 Gemini、無網路時，Rule provider 仍能產生合法報告。
- Ollama 可在選定模型產生通過 schema 與 fact 驗證的報告。
- Gemini 有設定時可作選配品質基準，沒有設定時不影響功能。
- 所有報告數值可回指 Evidence Pack；不存在或竄改的 fact 必須被拒絕。
- RTX 3080 完成一次固定案例 benchmark 並保存 JSON／Markdown。
- GTX 1050 Ti 完成選定小模型或 rule fallback smoke。

## 8. 完成後的作品集說法

> 系統不只整合實價登錄與 591 刊登，也能顯示資料健康狀態、驗證 MySQL 備份可還原，並以受 Evidence Pack 約束的本機或雲端 LLM 產生可追溯購屋報告；沒有 LLM 時仍有規則式結果。
