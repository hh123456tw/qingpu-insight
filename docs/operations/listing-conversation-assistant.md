# 單一物件 AI 對話助理 — 操作手冊

## 概述

本功能讓使用者在首頁貼入一個 591 中古屋或預售屋詳細頁網址，
系統以可見 Chrome 擷取物件，結合官方成交、正式估價模型與相似成交建立
Evidence Pack，再讓使用者針對同一物件持續對話。

## 支援的網址格式

### 接受
- `https://sale.591.com.tw/home/house/detail/{X}/{Y}.html` — 中古屋詳細頁
- `https://newhouse.591.com.tw/{X}/detail` 或 `/{X}/` — 預售屋詳細頁
- `https://591.to/{token}` — 官方分享短網址（2~64 字元 ASCII）

### 拒絕
- HTTP 而非 HTTPS
- 租屋網址（rent.591.com.tw）
- 搜尋結果頁、列表頁
- 嵌入帳號密碼、非標準 port、IP 字面位址
- 不支援的子網域或非 591 網域
- Chat 分享網址（591.com.tw/chat/...）

## 架構

```
使用者貼入網址 → parse_initial_591_url() 驗證格式
                        ↓
              DetailPageBrowser.capture()（可見 Chrome）
                        ↓
              ParsedListingDetail → Snapshot (revision N)
                        ↓
              ConversationEvidenceBuilder → Evidence Pack (revision N)
                        ↓
              activate_evidence() → 雙欄工作台
                        ↓
              使用者提問 → ConversationService._run_reply()
                        ↓
              固定模型 → Google（最多 2 次）→ Ollama → Rule
                        ↓
              ChatAnswerDraft（記錄實際模型與 fallback 原因）
                        ↓
              validate_chat_answer() → 檢驗 fact ID 接地
                        ↓
              儲存 assistant message → 顯示回答
```

### 主要元件

| 元件 | 說明 |
|------|------|
| `conversation_urls.py` | URL 驗證與正規化 |
| `conversation_listing_capture.py` | 可見 Chrome 頁面擷取 |
| `conversation_listing_parser.py` | HTML → ParsedListingDetail |
| `conversation_evidence.py` | Evidence Pack 建立（fact ID、估值、相似成交） |
| `conversation_repository.py` | MySQL 持久層（conversations、listings、snapshots、evidence、messages） |
| `conversation_validation.py` | ChatAnswerDraft fact ID 接地驗證 |
| `conversation_providers.py` | Rule / Ollama / Gemini provider |
| `conversation_models.py` | 固定模型目錄與 model → provider 對應 |
| `conversation_fallback.py` | Google → Ollama → Rule 的安全備援編排 |
| `conversation_service.py` | 流程編排（import、refresh、reply） |
| `conversation_import.py` | 首次匯入與重新擷取 |

## 資料庫 Migration

本功能需要 migration 008 與 009；008 建立對話表格，009 為既有資料庫補上
`fallback_reason`：

- `conversations` — 對話主記錄（status、provider、active evidence revision）
- `conversation_listings` — 對話關聯的物件（支援未來多物件擴充）
- `conversation_listing_snapshots` — 各版本快照（hash 去重）
- `conversation_evidence_packs` — 各版本證據包（facts、valuation、comparables）
- `conversation_messages` — 對話訊息（role、content、evidence revision、citations、
  實際 provider/model、fallback reason）

```sql
-- 簡要結構（完整 SQL 請見 database/008_conversation_assistant_schema.sql）
CREATE TABLE conversations (
    id CHAR(36) PRIMARY KEY,
    title VARCHAR(200) NOT NULL DEFAULT '新的物件分析',
    status ENUM('empty','importing','ready','needs_attention') NOT NULL DEFAULT 'empty',
    default_provider VARCHAR(50) NOT NULL,
    default_model VARCHAR(120) NOT NULL,
    active_listing_id CHAR(36) NULL,
    active_evidence_revision INT NULL,
    rolling_summary TEXT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    deleted_at DATETIME NULL
);
```

## 相依性

- **MySQL 8**：必備，負責對話、快照、證據與訊息持久化
- **Chrome（可見模式）**：用於 591 詳細頁擷取（headless 不支援驗證頁）
- **Ollama**（選用）：預設 `http://127.0.0.1:11434`，可設定
  `QINGPU_OLLAMA_BASE_URL`
- **Gemini**（選用）：在管理中心儲存 API Key，或設定
  `QINGPU_GEMINI_API_KEY`
- **Rule**：不需外部服務，使用內建規則產生摘要與建議

## 隱私邊界

以下資料「不」儲存：
- 原始 HTML、Cookie、Chrome Profile
- 賣方姓名、電話、Email
- API Key、完整 Prompt
- URL query string（儲存前移除）

對話僅儲存在本機 MySQL，直到使用者手動刪除。刪除操作會一併移除關聯的 listing、snapshot、evidence 與 messages。

## 證據版本與引用

每次「重新擷取」會建立：
- 新的 Snapshot（revision +1）
- 新的 Evidence Pack（revision +1）
- 更新 conversation 的 `active_evidence_revision`

舊的 assistant message 仍引用原始的 evidence revision，不會被更新。
新問題使用最新的 `active_evidence_revision`。

## 操作步驟

### 首次使用
1. 確認 MySQL 8 已啟動並套用 migration 008
2. 設定 `QINGPU_DATABASE_URL` 與 `QINGPU_SECRET_KEY`
3. 啟動 Web：`python -m qingpu_insight.web`
4. 開啟 `http://127.0.0.1:5000`
5. 在首頁貼入 591 詳細頁網址，從固定清單選擇回答模型，點擊「分析這個物件」

### 對話
1. 在雙欄工作台右側輸入問題
2. AI 回答會引用 Evidence Pack 中的 fact ID
3. 可持續追問，系統維護 rolling summary

### 重新擷取
1. 在工作台點擊「重新擷取」
2. 系統建立新版 Snapshot 與 Evidence Pack
3. 舊對話歷史保留，仍引用舊版 revision

### 恢復對話
1. 從首頁「最近對話」列表選取
2. 系統載入對話與最後使用的 evidence revision

## 問題排除

| 問題 | 可能原因 | 解決方式 |
|------|----------|----------|
| URL 被拒絕 | 不支援的格式或網域 | 確認使用支援的 591 詳細頁網址 |
| 瀏覽器無回應 | Chrome 未安裝或版本過舊 | 確認 Chrome 為最新穩定版 |
| Verification 頁面 | 591 要求驗證 | 手動操作可見 Chrome 完成驗證後重試 |
| Provider 錯誤 | Ollama/Gemini 設定不完整 | 檢查環境變數或 API Key |
| 回答顯示「已自動切換」 | 雲端逾時、限流、驗證失敗或格式不合格 | 查看回答旁的實際模型；到管理中心執行 LLM Benchmark |
| 回答無引用 | 問題與物件無關或 fact ID 不足 | 重新擷取或重新提問 |

## 未來擴充

目前一次只分析一個物件。架構已預留 `conversation_listings` 的 `position` 欄位，
未來可擴充為多物件對話，但仍需注意 fact ID 的跨物件引用限制。
