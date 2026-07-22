# 青埔智價 M4 本機產品化與智慧報告設計

## 1. 目標

M4 將 M0～M3 的官方成交分析、AI 估價及 591 刊登情報整合成可在 Windows
長期使用、可重複驗收、適合作為求職作品集展示的本機產品。第一版以單一使用者為主，
完成資料更新、故障復原、監控、收藏、通知及個人化購屋決策報告。

部署採 Windows 原生模組化方案。Flask、MySQL、資料管線、可見 Chrome 與 Ollama
直接在 Windows 運行；不以 1C1G 免費伺服器或 Cloudflare 作為 M4 的必要條件，
但以 provider、repository 與匯出契約保留未來拆分或雲端展示的界面。

## 2. 成功條件

1. 使用者可在管理頁一鍵完成 591 售屋、新建案及租屋更新，失敗不污染上一版資料。
2. 官方資料、分析、監控及備份可由 Windows 工作排程器執行並留下可稽核紀錄。
3. 網站可保存購屋條件、收藏與比較物件，並在新物件或價格變動時建立去重通知。
4. 個人化報告的價格、樣本數、日期及模型結果全部來自可驗證的 Evidence Pack；LLM
   只負責解讀與表達，不自行計算或改寫事實。
5. 報告可切換 Mock、Ollama 及 Gemini provider；任何外部服務不可用時仍能產生規則式報告。
6. Dashboard 可查看資料新鮮度、工作狀態、資料品質、模型版本、漂移、備份及通知狀態。
7. 在乾淨 Windows 環境可依文件完成安裝、啟動、更新、排程安裝／移除及 smoke test。

## 3. 範圍

### 3.1 M4.1 資料完整性

- 補強新建案地址、座標、站點距離與 `location_eligible` 判定。
- 保存定位方法、可信度、失敗原因及人工確認狀態，不以模糊文字假裝精確座標。
- 建立跨官方成交、刊登快照、估價及報告共用的資料版本與新鮮度定義。

### 3.2 M4.2 自動化管線

- 建立一鍵 orchestrator，串接取得、驗證、正規化、定位、同步、事件、特徵及發布。
- 支援 checkpoint、冪等鍵、有上限的指數退避、互斥鎖與失敗後續跑。
- 官方資料與背景安全工作使用 Windows 工作排程器。
- 591 預設由管理頁或 PowerShell 手動一鍵更新；提供但預設不安裝自動排程。

### 3.3 M4.3 維運監控

- 監控資料新鮮度、批次完整性、接受／拒收筆數、爬蟲成功率及欄位缺失率。
- 監控估價模型版本、效能、輸入分布及資料漂移。
- 監控 MySQL、磁碟、Ollama、Gemini 設定、排程與最近備份。
- 備份必須通過隔離環境的實際還原驗證，不能只檢查檔案存在。

### 3.4 M4.4 智慧購屋報告

- 定義共用 `ReportProvider`，實作 Mock、Ollama、Gemini 與規則式 fallback。
- 建立 Evidence Pack、報告 JSON Schema、輸出驗證、事實核對及 provider fallback。
- 在 RTX 3080 12GB 主機執行候選本機模型 benchmark，保存品質與效能結果。
- Gemini 3.5 Flash 作雲端品質基準與選配，不是系統單點依賴。

### 3.5 M4.5 使用者功能

- 儲存單一使用者的預算、房型、坪數、站點及其他購屋偏好。
- 收藏、取消收藏及並排比較物件。
- 產生並保存有版本的個人化購屋決策報告。
- 建立站內通知與 Windows 系統通知；SMTP Email 填入設定後才啟用。

### 3.6 M4.6 Windows 交付

- 提供 PowerShell 安裝、啟動、停止、環境檢查、排程安裝及排程移除腳本。
- 提供端對端 smoke test、操作手冊、故障排查、資料方法論及作品集說明。
- 提供未來 Cloudflare 靜態展示／API 匯出契約，但不把公開部署列為 Release Gate。

## 4. 非目標

- 公開雲端正式部署、公開註冊、多人租戶、角色權限或付費功能。
- 手機 App、LINE、Telegram、簡訊或自動聯絡房仲／屋主。
- 讓 LLM 上網查價、直接查詢任意資料庫、訓練估價模型或修改 Evidence Pack。
- 以無人值守方式繞過 591 驗證、CAPTCHA、登入要求或頁面限制。
- 將刊登開價加入 M2 官方成交模型訓練資料。

## 5. 整體架構

```text
Windows Task Scheduler                  Local user action
  official data / analysis / backup       Update 591
                 |                           |
                 +------ Job Orchestrator ---+
                               |
          acquire -> validate -> stage -> build -> publish
                               |
          MySQL runtime source of truth + artifact store
                               |
                       Flask Dashboard/API
                    /          |           \
             preferences   favorites    notifications
                               |
                         Evidence Pack
                               |
           Mock / Ollama / Gemini / RuleReportProvider
                               |
                  validated, versioned buyer report
```

MySQL 是結構化 runtime 資料的唯一 source of truth，包含刊登、版本、工作、健康、使用者功能、
報告 metadata 與 geocode cache。Parquet 只作分析／匯出快照，不參與網站正式讀寫或工作狀態。
檔案系統只保存 raw evidence、模型、備份與 benchmark artifact。

來源 adapter 不直接發布資料；工作 orchestrator 不實作來源解析；LLM provider 不讀任意資料庫。
每個邊界使用明確資料契約，讓未來可替換資料庫、模型或部署位置。

## 6. 工作執行與發布

### 6.1 工作狀態

每次工作建立 `job_run`，狀態只允許：

```text
pending -> running -> succeeded
                   -> retry_wait -> running
                   -> skipped
                   -> failed -> needs_attention
```

至少保存 `run_id`、工作類型、觸發方式、冪等鍵、開始／結束時間、輸入版本、
輸出版本、嘗試次數、摘要、錯誤類別及安全處理後的錯誤訊息。

### 6.2 兩階段發布

1. 來源資料先寫入不可變 raw batch 與 staging。
2. 驗證批次完整性、schema、筆數及品質門檻。
3. 先產生可重現、含 checksum 的 versioned Parquet 分析快照，再將相同 rows 載入 MySQL
   隔離的 dataset version 並產生事件、指標及估價結果。
4. Parquet 與 MySQL row-count／hash contract 一致且全部必要步驟成功後，在單一 transaction
   原子切換 `published_version`。
5. 失敗版本保留供診斷，但網站繼續讀取最後成功版本。

相同來源、批次與工作參數產生相同冪等鍵。重跑不得重複事件、通知或報告。

### 6.3 591 一鍵更新

- 管理頁按鈕與 PowerShell 指令呼叫相同 application service。
- 預設依序執行 sale、newhouse、rental，畫面顯示目前類型、頁數、筆數及安全摘要。
- 全域互斥鎖避免兩個可見 Chrome 工作同時執行。
- 驗證頁、DOM 契約失敗或批次不完整時安全停止，不執行下架或正式發布。
- 成功後自動串接正規化、定位、事件、估價、通知比對與發布，使用者只需觸發一次。
- 可選排程只能在使用者已登入且允許可見 Chrome 的工作階段執行，預設關閉。

## 7. 資料完整性與定位

定位結果增加：

| 欄位 | 說明 |
|---|---|
| `location_method` | `source_coordinates`、`structured_address`、`manual` 或 `unknown` |
| `location_confidence` | `high`、`medium`、`low` 或 `unknown` |
| `location_reason` | 可讀的通過或拒絕原因碼 |
| `geocoded_at` | 定位時間；人工或來源座標可為 null |
| `geocoder_version` | 定位規則／服務版本 |

只有可信座標能進入兩公里正式指標。若外部 geocoder 不可用，紀錄待處理狀態並保留資料，
不得用預設站點或區域中心點填補。定位 cache 存在 MySQL，以正規化地址為唯一鍵，避免重複查詢。

## 8. Evidence Pack 與報告

### 8.1 Evidence Pack

報告生成前，由 deterministic service 建立不可變 Evidence Pack，至少包含：

- 匿名購屋條件與篩選規則。
- 資料截止時間、published version、來源及新鮮度。
- 候選物件、排名分數及各分數組成。
- 開價、M2 點估值、合理區間、價差及模型版本。
- 可比成交案例、樣本數、期間、站點與摘要統計。
- 刊登生命週期、價格事件、缺漏欄位及可信度。
- 允許 LLM 引用的 fact ID。

姓名、電話、Email、Cookie、token、591 聯絡資料及未必要的完整地址不得進入 Evidence Pack。

### 8.2 報告契約

報告使用 Pydantic／JSON Schema 驗證，固定包含：

- 購屋條件摘要與市場定位。
- 推薦物件及排序理由。
- 開價、估值區間與價差。
- 可比成交與樣本限制。
- 優點、缺點、風險及資料不足。
- 看屋與議價問題清單。
- 資料版本、估價模型版本、LLM provider／model 及生成時間。
- 每個數值結論對應的 fact ID。

驗證器必須確認數值與 Evidence Pack 一致、fact ID 存在、必要段落完整且不含禁用欄位。
第一次失敗可使用錯誤摘要重試一次；再次失敗就使用規則式報告。

### 8.3 Provider 與 fallback

```text
explicit provider available -> selected provider
Gemini unavailable/rate-limited -> configured local Ollama -> rule provider
Ollama unavailable/invalid output -> rule provider
automated tests -> mock provider
```

所有 provider 使用相同 request／response DTO。金鑰與 model ID 只從環境變數讀取，
例如 `LLM_PROVIDER`、`OLLAMA_BASE_URL`、`OLLAMA_MODEL`、`GEMINI_API_KEY`、
`GEMINI_MODEL`，不得提交 repository。

Gemini 免費服務會將提交內容用於產品改善，且可能經人工檢視，因此免費 provider 只接受已匿名化
Evidence Pack；介面需明確標示此限制。相關條款以
[Gemini API Additional Terms](https://ai.google.dev/gemini-api/terms) 為準。

## 9. 本機模型選擇與 benchmark

不預先把產品綁死單一模型。在 RTX 3080 12GB、32GB RAM 主機使用固定的 20 組青埔案例，
比較候選 Ollama 模型；初始候選至少包含 `gemma4:12b`、`qwen3.5:9b`，並可加入實作時
仍受支援且符合硬體的模型。

品質總分：

| 指標 | 權重 |
|---|---:|
| Evidence Pack 數字與 fact ID 忠實度 | 40% |
| 繁體中文可讀性與用詞 | 20% |
| 購屋建議實用性與風險揭露 | 20% |
| JSON Schema 合規與穩定性 | 20% |

另記錄首 token／總延遲、輸入／輸出 token、RAM、VRAM、失敗率及重試率，不混入品質總分。
Gemini 3.5 Flash 使用相同案例作雲端品質基準；送出前只使用匿名 Evidence Pack。

第二台 64GB RAM、GTX 1050 Ti 4GB 電腦不執行完整 benchmark，只對選定的小型模型
（初始候選 `gemma4:e2b`、`qwen3.5:4b`）做部署 smoke test：模型可載入、可在合理
逾時內產生 schema 合法報告、資源不足時可安全 fallback。正式預設模型由主機 benchmark 與
弱機 smoke test 的實測結果決定，並保留在 benchmark artifact 與設定檔。

模型尺寸與記憶體規劃以實作當日的
[Gemma 4 官方文件](https://ai.google.dev/gemma/docs/core)及
[Ollama 模型庫](https://ollama.com/library/gemma4)為準。

## 10. 使用者資料、收藏與比較

第一版是單一使用者，不建立登入系統。核心資料包含：

- `buyer_profile`：預算、房型、坪數、站點、通勤及其他可選偏好。
- `favorite_listing`：來源物件鍵、收藏時間、備註及收藏時快照版本。
- `comparison_set`：最多一個作用中比較集合及其物件順序。
- `buyer_report`：profile、Evidence Pack、provider、模型、狀態、版本及報告內容。

若收藏物件下架，收藏仍保留並顯示最後成功快照。比較頁不得把缺失值顯示成零。

## 11. 通知

第一版通知事件包含：新符合條件物件、收藏物件降價／漲價／下架／恢復、資料工作失敗、
資料過期、模型或備份健康異常。

- `InAppNotificationProvider` 必做，作為通知真實來源。
- `WindowsNotificationProvider` 必做，僅在互動式使用者工作階段嘗試顯示。
- `SmtpNotificationProvider` 選配，只有完整環境變數存在才啟用。
- LINE、Telegram 與簡訊僅保留 provider 擴充點。

通知去重鍵由使用者範圍、事件類型、實體鍵與事件版本組成；相同事件不重送。
暫時性管道失敗可重試，但不能重建站內通知。每類通知設冷卻時間與可停用設定。

## 12. Dashboard 與管理介面

產品介面增加：

- 系統狀態：published version、資料截止時間、品質與最近成功工作。
- 工作中心：一鍵更新 591、目前進度、歷史、重試及安全錯誤摘要。
- 購屋條件：編輯 profile、預覽匹配數及資料覆蓋限制。
- 收藏與比較：狀態變化、估值差距、缺失資料及來源連結。
- 報告：生成、版本、provider、fallback 狀態、Evidence Pack 日期及下載／列印視圖。
- 通知中心：未讀、已讀、事件來源及偏好設定。
- 維運：資料新鮮度、漂移、備份還原、Ollama／Gemini 健康及排程狀態。

長時間工作採輪詢 job status，不把 Selenium 或 LLM 呼叫阻塞在單一 HTTP request 生命週期中。
第一版只綁定 localhost；任何未來公開部署必須先加入認證與權限設計。

## 13. 監控、備份與漂移

### 13.1 健康與新鮮度

每項資料集定義 warning／critical 時效門檻。狀態由資料本身的成功版本計算，不以排程程序退出碼
代替。Dashboard 顯示最後成功、最後嘗試及目前服務中的版本。

### 13.2 模型監控

- 保存訓練資料版本、程式版本、特徵 schema、評估指標及 artifact hash。
- 比較最近輸入與訓練基準的缺失率、類別分布及數值分布。
- 漂移只觸發警示，不自動重新訓練或發布模型。
- 新模型必須通過既有 M2 release gate 才能切換。

### 13.3 備份與還原

備份包含 MySQL 邏輯備份、模型、必要 artifact 與 raw batch manifest；Parquet 若存在只視為可重建
匯出物，不是必要還原來源。raw HTML 依保留政策處理。備份寫入日期化目錄、產生 checksum，
並定期還原至隔離資料庫／暫存目錄後
執行 row count、manifest、artifact hash 與 smoke query。還原測試不得覆蓋正式資料。

## 14. 安全與隱私

- `.env`、資料庫密碼、API key、SMTP credential、Cookie 及瀏覽器 profile 不提交 Git。
- 日誌與 job error 經過 secret／個資清理，不記錄原始 prompt、完整 Evidence Pack 或 LLM 回應。
- 免費 Gemini 不接收敏感、機密或個人資訊；若未來加入多人或公開服務，必須重新審查條款。
- 原始來源 HTML 不直接渲染；輸出文字 escape，外部 URL 依 M3 allowlist 驗證。
- 管理工作只綁 localhost，並以 CSRF 防護與明確確認避免瀏覽器跨站觸發更新。
- PowerShell 腳本不在參數或畫面輸出密碼；Task Scheduler 設定不嵌入明文 secrets。

## 15. 測試策略

1. 單元測試：狀態轉移、冪等鍵、Evidence Pack、schema、fact 驗證、通知去重及漂移計算。
2. Contract tests：Mock、Ollama、Gemini、rule provider 與 notification provider 共用契約。
3. 整合測試：MySQL staging／publish transaction、失敗保留上一版、報告保存及收藏事件。
4. CLI／PowerShell 測試：環境檢查、工作觸發、排程安裝／移除、非零錯誤碼與無 secrets 輸出。
5. Web 測試：一鍵更新 job、進度、profile、收藏、比較、報告、通知及 localhost 管理保護。
6. E2E：以 fake 來源與 Mock LLM 完成全流程，CI 不連線 591、Ollama 或 Gemini。
7. 授權 smoke：手動執行 591 三類最小批次，驗證 raw、品質、發布及通知。
8. LLM benchmark：只在 RTX 3080 主機執行；結果以 JSON／Markdown artifact 保存。
9. 弱機 smoke：GTX 1050 Ti 主機只驗證選定小模型與 fallback，不作完整比較。
10. Backup drill：還原到隔離目標並執行 smoke query，確認正式資料未變。

## 16. Release Gate

### M4.1

- 新建案與刊登都有明確 location 狀態；無法確認者包含原因且不進正式地理指標。

### M4.2

- 按一次更新可完成三類 591 的取得、驗證、同步與發布。
- 模擬任一步失敗時 published version 不變，重跑不產生重複事件。

### M4.3

- Dashboard 顯示資料新鮮度、工作、錯誤、模型、漂移及備份狀態。
- 至少一次隔離還原演練成功並保存驗證結果。

### M4.4

- Mock 完全離線通過；Ollama／Gemini 可切換；rule fallback 在兩者不可用時成功。
- 所有報告數值可回指 Evidence Pack，竄改或 hallucinated fact 會被拒絕。
- RTX 3080 主機完成固定案例 benchmark 並保存可重現設定與結果。

### M4.5

- 可儲存 profile、收藏、比較及版本化報告。
- 相同事件不重複建立站內通知或重送外部通知。

### M4.6

- 乾淨 Windows 環境依 README 可完成安裝、啟動、更新、排程管理及 E2E smoke。
- GTX 1050 Ti 主機通過選定小模型載入、schema 報告及 fallback smoke。
- `pytest`、Ruff、secret scan 與主要 E2E 全部通過。
- 沒有 LLM、網路或最新爬蟲批次時，網站仍能查詢最後成功資料並產生基本報告。
- 作品集文件說明問題、架構、AIPE04 技術、成果、限制、benchmark 與未來雲端路徑。

## 17. 未來演進

M4 完成後可將匿名、唯讀的 published view 匯出為 JSON／靜態資產並部署到 Cloudflare；
需要即時私人功能時再評估受認證 API 或其他後端。Crawler、Ollama、MySQL 與管理功能仍留在
可信任的 Windows 主機，除非另行完成安全、成本與維運設計。
