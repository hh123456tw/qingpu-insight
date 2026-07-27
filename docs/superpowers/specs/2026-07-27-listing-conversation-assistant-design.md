# 591 單一物件 AI 對話助理設計

日期：2026-07-27
狀態：使用者已核准設計，等待 spec 檢閱
範圍：期中專題與求職作品集適用的本機功能

## 1. 目標

把目前「產生一次性買方報告」擴充成可持續追問的 AI 購屋助理。使用者在首頁貼入一個 591 中古屋或預售屋詳細頁網址，系統以可見 Chrome 擷取物件，結合官方成交、正式估價模型與相似成交建立 Evidence Pack，再讓使用者針對同一物件持續對話。

所有物件數字與物件事實必須引用 Evidence Pack 的 fact ID。LLM 可以提供一般購屋知識，但必須標示為一般建議，不得冒充該物件的已知事實。

本功能必須：

- 在首頁提供明確的 AI-first 起點。
- 支援回復上次對話。
- 保存每則回答使用的 Provider、模型、物件快照、Evidence Pack 與 fact ID。
- 允許重新擷取 591 頁面，但不得改寫舊回答的證據。
- 與正式 `listing_current` 及版本化刊登發布流程隔離。
- 沿用目前的 loopback、trusted Host、CSRF、錯誤遮罩與背景工作機制。
- 在資料結構上保留未來多物件比較能力，但第一版 API 強制一次只分析一個物件。

## 2. 非目標

第一版不包含：

- 多物件比較對話。
- 租屋網址、591 搜尋結果頁或非 591 網址。
- LLM 自由決定何時開啟 Chrome 或呼叫任意工具。
- 任意網頁瀏覽、通用 Web Agent 或搜尋引擎。
- 回答 token 串流。
- 跨對話或跨物件的向量搜尋。
- 將單頁擷取結果直接加入正式 `listing_current`。
- 公開雲端、多使用者帳號或遠端存取。
- 未來房價預測。

## 3. 已核准的產品決策

| 主題 | 決策 |
|------|------|
| 第一版範圍 | 一個對話綁定一個 591 物件 |
| 未來方向 | 資料模型預留一個對話關聯多個物件 |
| 擷取時機 | 貼網址後按「分析這個物件」才啟動 |
| Provider | Ollama／Gemini 可選；Rule 只提供固定摘要與建議問題 |
| 回答邊界 | 數字與物件事實需 fact ID；一般知識需標示「一般建議」 |
| 保存 | 對話保存在本機 MySQL，直到使用者手動刪除 |
| 快照 | 對話專屬 immutable snapshot，不修改正式刊登資料集 |
| 歷史更新 | 舊訊息綁定舊 Evidence Pack；重新擷取建立新版本 |
| 回答呈現 | 驗證完成後整段顯示，不串流未驗證草稿 |
| 網址 | 只接受 sale／newhouse 單一詳細頁及通過驗證的官方分享重新導向 |
| 對話版型 | 左側物件與證據、右側對話的雙欄工作台 |
| 首頁 | AI-first，貼 591 網址為主動作；市場功能保留在第二層 |

## 4. 使用者流程

### 4.1 建立新對話

1. 使用者在首頁貼入 591 網址，選擇 Ollama 或 Gemini。
2. 使用者按「分析這個物件」。
3. Web 驗證基本 URL 後建立 conversation 與 `conversation_import` 背景工作，回傳 `202`。
4. 前端顯示五個階段：
   - 驗證網址
   - 開啟 Chrome
   - 擷取物件
   - 建立估價
   - 準備證據
5. 後端以可見 Chrome 取得最終網址及頁面，重新驗證網域與路由。
6. 系統解析並驗證物件欄位，建立對話專屬 snapshot。
7. 系統執行估價、相似成交查詢並建立 Evidence Pack。
8. 成功後前端導向 `/assistant/<conversation_id>`。
9. 助理先顯示 Rule 固定摘要與建議問題；這不是 LLM 回答。

### 4.2 持續對話

1. 使用者輸入問題。
2. Web 建立 `conversation_reply` 背景工作並回傳 `202`。
3. 前端顯示：
   - 整理證據
   - 呼叫模型
   - 驗證引用
4. ConversationService 組合目前 Evidence Pack、滾動摘要及最近 12 則訊息。
5. Ollama 或 Gemini 回傳結構化回答草稿。
6. ChatResponseValidator 驗證 schema、fact ID、數字引用與一般建議標籤。
7. 驗證失敗時允許同一 Provider 修復一次。
8. 驗證成功才儲存正式 assistant message 並整段顯示。

### 4.3 回復舊對話

1. 首頁顯示最近對話，包括物件標題、最後活動時間、Provider 與資料時間。
2. 使用者開啟舊對話。
3. 系統載入完整訊息與目前 active evidence revision，不自動開 Chrome。
4. 使用者可直接依原快照繼續追問。

### 4.4 重新擷取

1. 使用者在左欄按「重新擷取」。
2. 系統建立 `conversation_refresh` 工作。
3. 成功後新增 listing snapshot 與 Evidence Pack revision。
4. Conversation 的 active revision 指向新版。
5. UI 加入「資料已更新」系統分隔線，顯示舊版與新版時間。
6. 更新前訊息仍引用原本的 evidence pack；更新後問題使用新版。

### 4.5 刪除

1. 使用者在最近對話或工作台選擇刪除。
2. UI 顯示明確確認，說明會刪除訊息、對話快照與 Evidence Pack。
3. 確認後刪除 conversation 專屬資料。
4. 不刪除官方成交、正式模型、正式刊登版本或共用 job audit 欄位。
5. Job audit 中不得保留完整問題、完整回答、URL query、HTML 或其他可重建私人對話的內容。

## 5. 架構

```text
AI-first Home
    │ POST /api/conversations
    ▼
URL Guard ──reject──> Safe API Error
    │
    ▼
conversation_import Job
    │
    ▼
SingleListingImportService
    │
    ├── Visible Chrome / Selenium
    ├── Final URL validation
    ├── Listing field validation
    └── ConversationListingSnapshotRepository
              │
              ▼
       Valuation + Comparables
              │
              ▼
    ConversationEvidenceBuilder
              │
              ▼
       Evidence Pack Revision

User Question
    │ POST /api/conversations/<id>/messages
    ▼
conversation_reply Job
    │
    ▼
ConversationService
    ├── active Evidence Pack
    ├── rolling summary
    ├── last 12 messages
    └── selected Provider
              │
              ▼
       Ollama / Gemini
              │
              ▼
    ChatResponseValidator
       │ success         │ failure after repair
       ▼                 ▼
Saved message       Safe retry state
```

### 5.1 元件責任

#### URL Guard

- 只接受 HTTPS。
- 拒絕 userinfo、非標準 port、fragment 及非允許 host。
- 中古屋 final URL 只允許 host `sale.591.com.tw`，path 必須符合
  `^/home/house/detail/[1-9][0-9]*/[1-9][0-9]*\.html$`。
- 預售屋 final URL 只允許 host `newhouse.591.com.tw`，path 必須符合
  `^/[1-9][0-9]*(?:/detail)?/?$`；`housingArticle` 等文章頁不屬於物件頁。
- 官方分享短網址只允許 initial host `591.to`，path 必須是單一、長度 2～64 的
  ASCII token；最多跟隨三次 HTTPS redirect，且 final URL 必須符合上述
  sale 或 newhouse 契約。
- 詳細頁 query 可供首次請求使用，但不得包含 userinfo 或另一個 URL；保存 snapshot
  前丟棄 query 與 fragment，只保留 canonical scheme、host、path。
- Chrome 導航後以最終 URL 再驗證一次。
- 不接受 IP literal、localhost、私有網段或非 HTTP(S) scheme。

#### SingleListingImportService

- 組裝單一頁面擷取流程，不重用搜尋結果批次語義冒充單頁成功。
- 使用可見 Chrome 與專用本機 profile。
- 檢測驗證頁、錯誤頁與 route provenance。
- 呼叫既有正規化、PII 檢查、地址證據及估價服務。
- 不寫入 `listing_current`、正式 listing artifact 或 published pointer。

#### ConversationEvidenceBuilder

- 由 conversation listing snapshot、估價結果、相似成交與市場摘要建立證據。
- fact ID 必須包含 evidence revision 或其穩定 source version。
- 明確記錄缺少地址、估價不可用、相似成交不足與資料過舊等限制。
- 不允許 591 開價成為官方成交或模型預測值。

#### ConversationService

- 只接受已完成且 active 的 Evidence Pack。
- 組合最近 12 則訊息、滾動摘要與目前證據。
- 不讓 Provider 取得 Chrome、資料庫或任意 HTTP 工具。
- 記錄 requested provider/model 與 actual provider/model。
- Provider 失敗時不以 Rule 冒充成功的聊天回答。

#### ChatResponseValidator

- 驗證結構化回答 schema。
- 所有物件事實 claim 至少引用一個存在的 fact ID。
- 所有數字 claim 的 numeric fact ID 必須包含在 fact IDs 中。
- 回答中出現的物件數字必須可由引用 fact 支持。
- 一般購屋知識必須標記為 general guidance，且不得包含無證據的物件數值。
- 一次修復後仍無效即拒絕顯示草稿。

## 6. 資料模型

實際 migration 使用 MySQL 8。以下欄位是設計契約；實作可依既有 repository 慣例加入 surrogate key、索引及 control 欄位，但不得改變版本與刪除語義。

### 6.1 `conversations`

| 欄位 | 用途 |
|------|------|
| `conversation_id` | UUID 主鍵 |
| `title` | 使用者可修改的對話標題 |
| `status` | importing、ready、replying、needs_attention、failed、archived |
| `default_provider` | ollama 或 gemini |
| `default_model` | 本次預設模型名稱 |
| `active_evidence_pack_id` | 目前新問題使用的 Evidence Pack |
| `rolling_summary` | 已壓縮的較早對話摘要 |
| `summary_through_message_id` | 摘要包含到哪一則訊息 |
| `created_at` | 建立時間 |
| `updated_at` | 最後活動時間 |

### 6.2 `conversation_listings`

| 欄位 | 用途 |
|------|------|
| `conversation_id` | 對話 |
| `conversation_listing_id` | 對話內物件 ID |
| `ordinal` | 未來多物件穩定排序 |
| `active_snapshot_id` | 此物件目前快照 |
| `created_at` | 建立時間 |

第一版對每個 conversation 加入唯一性或 service invariant，確保最多一個 active conversation listing。未來多物件版本移除 service limit，不變更主關聯模型。

### 6.3 `conversation_listing_snapshots`

| 欄位 | 用途 |
|------|------|
| `snapshot_id` | immutable snapshot UUID |
| `conversation_listing_id` | 所屬對話物件 |
| `revision` | 從 1 開始遞增 |
| `source_url` | 經正規化與安全遮罩的最終網址 |
| `source_listing_id` | 591 公開物件 ID |
| `listing_type` | sale 或 newhouse |
| `source_hash` | 正規化來源內容 hash |
| `normalized_listing` | 通過白名單的 JSON 欄位 |
| `capture_status` | succeeded 或 rejected |
| `captured_at` | 擷取時間 |

不儲存 raw HTML、Cookie、電話、email、Chrome profile 或任意 free-form contact data。

### 6.4 `conversation_evidence_packs`

| 欄位 | 用途 |
|------|------|
| `evidence_pack_id` | UUID |
| `conversation_id` | 所屬對話 |
| `snapshot_id` | 物件快照 |
| `revision` | 對話證據版本 |
| `market_dataset_version` | 官方市場資料版本 |
| `valuation_model_version` | 正式估價模型版本 |
| `facts` | 通過 schema 的 JSON facts |
| `limitations` | 通過 schema 的 JSON limitations |
| `generated_at` | 建立時間 |

### 6.5 `conversation_messages`

| 欄位 | 用途 |
|------|------|
| `message_id` | UUID |
| `conversation_id` | 所屬對話 |
| `role` | user、assistant 或 system |
| `status` | pending、succeeded、failed 或 deleted |
| `content` | 使用者訊息或已驗證回答 JSON |
| `evidence_pack_id` | assistant／system 訊息的證據版本 |
| `fact_ids` | 回答引用的 fact ID JSON |
| `requested_provider` | 使用者選擇 |
| `requested_model` | 使用者選擇 |
| `actual_provider` | 實際執行 Provider |
| `actual_model` | 實際模型 |
| `validation_codes` | 驗證結果 |
| `latency_ms` | 處理時間 |
| `created_at` | 建立時間 |

## 7. LLM 回答契約

回答不是任意 Markdown 字串，而是結構化資料：

```json
{
  "answer": {
    "text": "這間物件的開價高於模型合理價。",
    "fact_ids": ["F-PRICE", "F-VALUATION"],
    "numeric_fact_ids": ["F-PRICE", "F-VALUATION"]
  },
  "general_guidance": [
    {
      "text": "議價前可先確認屋況與車位權利範圍。",
      "label": "一般建議"
    }
  ],
  "suggested_questions": [
    "附近相似成交有哪些？",
    "我可以怎麼安排議價順序？"
  ]
}
```

限制：

- `answer` 必須至少引用一個 fact。
- `general_guidance` 不得引用不存在的 fact，也不得包含物件專屬數值。
- `suggested_questions` 最多三題，不得承諾系統沒有的資料。
- Provider prompt 明確區分 Evidence Facts、Conversation Summary、Recent Messages 與 User Question。
- Prompt 中的 591 文字與使用者訊息視為不可信資料，不得覆寫 system rules。

## 8. UI 設計

### 8.1 AI-first 首頁

首頁首屏包含：

- 「貼上 591 物件，從合理價格開始聊」主標題。
- sale／newhouse 詳細頁 URL 輸入框。
- Provider 與模型選擇。
- 「分析這個物件」主按鈕。
- 三個示例問題：價格合理嗎、有哪些風險、如何議價。
- 最近對話摘要。
- 官方資料與正式模型新鮮度。

原有市場摘要、地圖、趨勢、近期成交、刊登、報告與條件估價不刪除。它們移到首屏下方，並由「市場資料」導覽入口存取。

### 8.2 匯入進度

進度卡片顯示目前 stage、可理解說明、開始時間及安全錯誤。遇到 `verification_required` 時：

- 狀態顯示 `needs_attention`。
- 指示使用者在可見 Chrome 完成正常驗證。
- 提供「重新嘗試」。
- 不自動無限重試。

### 8.3 雙欄工作台

左欄固定顯示：

- 物件標題與 listing type。
- 開價、坪數、格局、樓層、屋齡、車位。
- 地址證據與距車站資訊。
- AI 合理價、區間、可信度與限制。
- 相似成交摘要。
- 擷取時間、Evidence Pack revision、模型版本。
- 「重新擷取」。

右欄顯示：

- 完整對話。
- system 分隔線，例如「資料已更新：revision 1 → 2」。
- 每則 assistant message 的 Provider、模型及資料時間。
- 可展開的「查看依據」，列出 fact IDs 與來源。
- 一般建議標籤。
- 建議追問。
- 問題輸入框與送出按鈕。

窄螢幕改為單欄，左側物件摘要成為可收合區塊。未通過驗證的草稿不渲染。

## 9. API

### 9.1 `POST /api/conversations`

Request：

```json
{
  "source_url": "https://sale.591.com.tw/...",
  "provider": "ollama",
  "model": "gemma3:4b"
}
```

Response：`202`

```json
{
  "conversation_id": "uuid",
  "run_id": "uuid",
  "status": "importing"
}
```

### 9.2 `GET /api/conversations`

回傳最近對話的安全摘要。支援 bounded limit 與 cursor，不回傳完整訊息或 Evidence Pack。

### 9.3 `GET /api/conversations/<conversation_id>`

回傳：

- conversation metadata
- active listing snapshot
- active Evidence Pack 摘要
- messages
- evidence revision timeline
- allowed actions

訊息使用 bounded page size 與 cursor，預設回傳最新 50 則；前端向上捲動時載入更早訊息。
滾動摘要不取代或刪除原始訊息。

不得回傳 raw prompt、raw HTML、Cookie、DB URL 或內部 traceback。

### 9.4 `POST /api/conversations/<conversation_id>/messages`

Request：

```json
{
  "content": "這間房的開價合理嗎？",
  "provider": "gemini",
  "model": "configured-model"
}
```

Response：`202`，包含 user `message_id`、`run_id` 與 `replying` 狀態。只允許一個 active reply job；相同 idempotency key 回傳原工作。

### 9.5 `POST /api/conversations/<conversation_id>/refreshes`

建立新版 snapshot 與 Evidence Pack 的背景工作，回傳 `202`。不接受任意新 URL；若要分析另一個 URL，第一版建立新對話。

### 9.6 `DELETE /api/conversations/<conversation_id>`

需要 CSRF 與明確 confirmation token。刪除 conversation 專屬資料並回傳 `204`。正在執行的 import、refresh 或 reply job 不可刪除；使用者須等待 terminal 狀態。

### 9.7 Job API

沿用 `GET /api/jobs/<run_id>`。新增安全 job type 與 stage：

- `conversation_import`
- `conversation_refresh`
- `conversation_reply`

Job summary 只保存 stage、版本、計數與 stable error code，不保存完整對話內容。

## 10. Rule、Ollama 與 Gemini 行為

### Rule

- 產生固定物件摘要、證據限制與建議問題。
- 不聲稱完成自由對話。
- Ollama／Gemini 不可用時，UI 顯示「聊天模型目前不可用」及 Rule 摘要。
- Rule 摘要不保存成對使用者問題的 AI 回答。

### Ollama／Gemini

- 共用 ChatProvider contract。
- requested 與 actual provider/model 都須記錄。
- Provider 不可使用任意工具。
- Provider error、timeout、schema failure 與 validation failure 使用 stable code。
- Gemini API Key 沿用 `instance/secrets.env` 或環境變數。

## 11. 安全與隱私

- 所有路由限 loopback 與 trusted Host。
- 所有 mutation 要求 session CSRF。
- URL Guard 必須在開 Chrome前與重新導向後執行。
- 不信任 forwarded headers。
- 不將使用者貼入的網址直接傳給 shell。
- 不保存 raw HTML、Cookie、Chrome profile 或聯絡資訊。
- normalized listing 使用欄位白名單與長度限制。
- 使用者問題與 591 文字視為 prompt injection 來源，只能位於 data section。
- API 錯誤不洩漏 SQL、DB URL、prompt、traceback、HTML 或密鑰。
- 管理診斷只顯示 Provider 是否設定，不顯示 Key。
- 刪除後保留的 job audit 不得包含可重建對話的內容。

## 12. 錯誤與狀態

| 情況 | 行為 |
|------|------|
| URL 格式錯誤 | `400 invalid_source_url`，不建立 job |
| 非允許網址／路由 | `400 unsupported_591_url`，不開 Chrome |
| 重新導向離開白名單 | import failed，無 snapshot |
| 591 驗證頁 | `needs_attention / verification_required` |
| 必要欄位不足 | `failed / listing_fields_incomplete` |
| 有物件但無法估價 | 建立受限 Evidence Pack，禁止精確價回答 |
| 相似成交不足 | 建立 Evidence Pack，加入 limitation |
| Provider 未設定 | Rule 摘要 + `provider_unavailable` 提示 |
| Provider timeout | reply failed，可重試或切換 Provider |
| 第一次回答無效 | 同 Provider 修復一次 |
| 修復仍無效 | 不顯示草稿，`validation_failed` |
| 重複送出訊息 | idempotency key 回傳原 job |
| 同對話已有 reply | `409 conversation_busy` |
| 刪除執行中對話 | `409 conversation_busy` |

## 13. 測試與驗收

實作計畫採 TDD，因為這是計畫內的新功能。

### 13.1 單元測試

- URL scheme、host、port、userinfo、fragment 與路由白名單。
- 分享重新導向及最終 URL 重驗證。
- 驗證頁、route provenance 與必要欄位 gate。
- snapshot immutable revision。
- conversation 第一版單一物件 invariant。
- Evidence Pack fact ID 包含正確 source version。
- 舊訊息維持舊 evidence pack。
- 重新擷取建立新版，不更新舊 message。
- 最近 12 則訊息與滾動摘要邊界。
- 一般建議與物件事實分類。
- numeric fact 引用驗證。
- 一次 repair 與 repair failure。
- Rule 不冒充聊天回答。
- conversation 刪除 cascade 與 job audit 去識別。

### 13.2 Repository 與 service 測試

- MySQL migration forward path。
- conversation、listing、snapshot、evidence、message repository。
- active reply idempotency 與 concurrency。
- 正式 `listing_current`、artifact、published pointer 完全不變。
- import、refresh、reply 的 terminal state。
- process restart 後 durable job 與 conversation 狀態可觀察。

### 13.3 API 與安全測試

- loopback、Host、CSRF。
- unknown fields 拒絕。
- bounded length、limit 與 cursor。
- stable errors 與 sensitive text masking。
- conversation ownership 僅限本機單使用者，不建立假帳號模型。
- delete confirmation 與 busy conflict。

### 13.4 前端 JavaScript 契約

- 首頁 URL validation 與 button state。
- import stage polling 不重疊。
- terminal state 停止 polling。
- 最近對話載入與恢復。
- evidence revision 分隔線。
- 未驗證草稿不顯示。
- Provider unavailable 與 Rule 摘要。
- 窄螢幕摘要收合。

### 13.5 決定性整合測試

以 fake 591 source、fake valuation、fake market repository 與 fake Provider 執行：

1. 建立對話。
2. 匯入單一物件。
3. 建立 Evidence Pack revision 1。
4. 詢問並產生有效回答。
5. 重新擷取 revision 2。
6. 驗證舊訊息仍引用 revision 1。
7. 新訊息引用 revision 2。
8. 刪除對話專屬資料。

自動測試不連真實 591、Ollama、Gemini 或外部網路。

### 13.6 人工 smoke

展示前在授權的本機環境：

1. 貼一個允許的 591 sale 詳細頁。
2. 觀察可見 Chrome 與五階段進度。
3. 確認物件摘要、估價與相似成交。
4. 使用 Ollama 或 Gemini 問價格與一般議價問題。
5. 展開 fact ID，核對來源。
6. 重新擷取並確認 revision 分隔線。
7. 關閉頁面後從最近對話恢復。
8. 刪除對話並確認專屬資料消失。

## 14. 未來升級為多物件比較

第一版不得偷偷實作多物件，但需維持以下擴充點：

- `conversation_listings` 已是關聯表，不是 conversation 單欄位。
- Evidence fact namespace 可加入 `conversation_listing_id`。
- 每則回答保存完整 evidence pack ID。
- 左欄可由單一摘要升級成物件切換器。
- 未來 API 可新增「加入比較物件」，第一版不接受 refresh 時更換 URL。
- 多物件版必須重新設計跨物件 claim validation，不能只放寬數量限制。

## 15. 驗收條件

功能完成必須同時滿足：

1. 首頁首屏以 591 URL 分析為主要動作。
2. 只接受允許的 sale／newhouse 單一詳細頁。
3. LLM 永遠不直接控制 Chrome。
4. 單頁擷取不修改正式刊登資料集。
5. 使用者可針對同一物件持續對話並恢復歷史。
6. 每則正式 AI 回答可追溯到 Evidence Pack 與 fact IDs。
7. 一般知識清楚標示為一般建議。
8. 未驗證草稿不顯示。
9. 重新擷取不改寫舊訊息證據。
10. Ollama／Gemini 不可用時不以 Rule 冒充對話成功。
11. 使用者可刪除對話專屬資料。
12. 所有 mutation 通過 loopback、trusted Host 與 CSRF。
13. 自動測試不依賴真實 591 或外部 Provider。
14. 真實展示前完成一次可見 Chrome 人工 smoke。
15. 既有市場、估價、刊登、報告、管理中心與模型發布流程維持可用。
