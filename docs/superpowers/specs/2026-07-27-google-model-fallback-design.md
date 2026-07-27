# Google 模型選擇與本機降級設計

日期：2026-07-27
狀態：使用者已核准設計，等待書面 spec 檢閱
範圍：591 物件 AI 對話助理

## 1. 目標

讓使用者在建立對話時，從固定且可理解的模型目錄中選擇分析模型。
雲端模型不可用時，系統自動重試並降級到本機 Ollama，最後以 Rule
摘要維持基本可用性。介面必須清楚呈現原始選擇、實際執行模型及降級原因。

本功能以期中專題與求職作品集為範圍，不實作動態模型探索、用量計費、
多租戶金鑰管理或複雜路由策略。

## 2. 已確認模型

Google Gemini API 使用固定、允許的 model ID：

- Google Gemini 3.5 Flash-Lite：`gemini-3.5-flash-lite`
- Google Gemma 4 31B Instruct：`gemma-4-31b-it`

本機與離線選項：

- Ollama Gemma 4：`gemma4:e2b`
- Rule 摘要模式：`rule`

參考：

- [Gemini 3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)
- [Run Gemma with the Gemini API](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api)

兩個 Google model ID 已於 2026-07-27 使用臨時測試金鑰完成真實
`generateContent` smoke test，皆回傳 HTTP 200。臨時金鑰不寫入本文件、
原始碼或 Git。

## 3. 核心決策

### 3.1 對話固定模型

使用者只在建立新對話時選擇模型。選定後，整段對話的 requested model
固定，不提供逐則訊息切換。這讓歷史紀錄、示範結果與錯誤追蹤保持一致。

### 3.2 固定模型目錄

前端不得送出任意 provider 或 model 字串。後端維護唯一的允許模型目錄，
並驗證建立對話請求。未知、停用或 provider/model 不相符的組合一律拒絕。

不向 Google 動態取得模型清單，避免展示期間因遠端清單、預覽模型或
不相容模型變動而使介面失去可重現性。

### 3.3 雲端優先降級鏈

Google 模型的執行順序固定為：

1. 呼叫使用者選擇的 Google 模型。
2. 第一次失敗後，使用相同模型重試一次。
3. 第二次失敗後，改用本機 Ollama `gemma4:e2b`。
4. Ollama 失敗後，改用 Rule 摘要模式。

直接選擇 Ollama 時，執行順序為 Ollama、Rule。直接選擇 Rule 時不再降級。

成功取得且通過既有 grounded-answer validation 的回答後，立即停止降級，
不做額外 provider 呼叫。

## 4. 後端元件

### 4.1 模型目錄

建立單一後端模型目錄，為每個公開選項保存：

- 公開 model ID
- 顯示名稱
- provider
- 是否為雲端
- 預設模型標記
- 簡短用途說明

前端模型選單與後端驗證皆使用同一份目錄投影，避免 HTML 與 API
各自維護不一致的字串。

預設模型為 `gemini-3.5-flash-lite`。

### 4.2 降級執行器

降級執行器包裝既有 `ConversationProviderRegistry`，但不改變各 provider
產生 `ChatAnswerDraft` 的責任。它負責：

- 根據 requested model 建立執行序列
- 執行 Google 一次重試
- 在 provider 錯誤或 validation 失敗時前往下一層
- 回傳回答及執行 metadata
- 只暴露安全化錯誤碼

Google Gemini 與 Google Gemma 4 共用既有 Gemini API transport；
model ID 決定實際遠端模型。

### 4.3 回答 metadata

每則助理回答必須保存並回傳：

- `requested_provider`
- `requested_model`
- `actual_provider`
- `actual_model`
- `fallback_reason`，未降級時為空

若現有欄位已能代表 actual provider/model，沿用欄位並只新增缺少的
requested/fallback 資訊，不建立重複資料結構。

舊對話缺少新增 metadata 時，以既有 provider/model 顯示，不要求回填。

## 5. API 與資料流

### 5.1 模型目錄 API

提供只讀模型目錄給首頁與對話頁使用。回應只包含公開 metadata 與
`gemini_configured` 布林值，不包含 API Key、環境變數值或內部錯誤。

### 5.2 建立對話

建立對話請求提交固定模型目錄中的 model ID。後端由目錄解析 provider，
不信任前端自行宣告的 provider/model 組合。

對話保存 requested provider/model。後續回覆沿用該選擇；reply API
不接受任意切換模型。

### 5.3 回覆

Conversation service 建立 evidence context 後，交給降級執行器。
執行器回傳通過既有 evidence citation validation 的 `ChatAnswerDraft`
與執行 metadata，repository 再以單一結果保存。

## 6. 前端設計

首頁目前的 Provider 下拉與模型自由輸入欄位，改為單一「分析模型」選單：

1. Google Gemini 3.5 Flash-Lite — 快速、穩定，預設
2. Google Gemma 4 31B — 較大型開放模型
3. 本機 Ollama Gemma 4 — 不使用雲端 API
4. Rule 摘要模式 — 完全離線備援

選擇 Google 模型時顯示：

- 「雲端優先」
- 「失敗時自動改用本機」
- Gemini API Key「已設定」或「未設定」

前端不得取得、顯示或保存 API Key。

對話頁頁首顯示 requested model。每則助理回答顯示 actual model；發生降級時
顯示黃色提示，例如「Gemini 連線逾時，已改用本機 Gemma 4」。Rule
回答必須標示「離線摘要」，不得讓使用者誤認為 LLM 回覆。

## 7. API Key 安全

一般對話頁只讀取 `gemini_configured` 狀態。API Key 維持由管理介面
寫入 `instance/secrets.env`，或由 `QINGPU_GEMINI_API_KEY` 環境變數提供。

金鑰不得出現在：

- HTML
- JSON API
- JavaScript 或 localStorage
- 資料庫
- application log
- 例外訊息
- Git diff

本次聊天提供的臨時測試金鑰只用於 smoke test。正式驗證前，使用者應撤銷
該金鑰並由管理介面輸入新金鑰。

## 8. 錯誤與降級原因

前端只接收以下安全錯誤碼：

- `cloud_timeout`
- `cloud_rate_limited`
- `cloud_unavailable`
- `cloud_auth_failed`
- `cloud_invalid_response`
- `local_unavailable`

Google 逾時、連線錯誤、HTTP 429、HTTP 5xx 或回答未通過 schema/evidence
validation 時，重試一次。依使用者核准的單一規則，API Key 無效、
模型不存在等 HTTP 4xx 也重試一次；第二次失敗後才進入本機降級。

日誌只記 provider、model、耗時、結果與安全錯誤碼。Google 原始錯誤本文、
回應 header 及 API Key 不得寫入 log 或前端。

## 9. TDD 驗收

實作須遵循 Red-Green-Refactor，至少涵蓋：

1. 模型目錄只接受四個公開 model ID。
2. 建立對話拒絕任意 model ID 與不一致 provider/model。
3. Gemini 可恢復錯誤後重試一次。
4. 第二次 Google 失敗後改用 Ollama。
5. Ollama 失敗後改用 Rule。
6. Google 成功時不呼叫其他 provider。
7. Google HTTP 4xx 也依核准規則重試一次，再進入本機降級。
8. requested、actual 與 fallback metadata 正確保存及投影。
9. 舊對話缺少新 metadata 時仍能讀取。
10. 首頁使用固定模型選單，不再提交自由輸入 model。
11. 對話頁顯示 actual model、降級提示及 Rule 離線標籤。
12. API Key 不出現在 HTML、JSON、log 或版本差異。
13. 以使用者新換的測試金鑰，對兩個 Google 模型各做一次真實 smoke test。

真實 API smoke test 不放入一般自動測試套件，以避免 CI 依賴秘密與外網。

## 10. 不在本次範圍

- 逐則訊息切換模型
- 動態探索 Google 模型
- AutoML 或模型品質自動排名
- API 用量、成本結算與配額儀表板
- 多使用者／多租戶金鑰
- 並行競速多個 provider
- 自動保存聊天中提供的臨時金鑰

## 11. 完成條件

- 使用者能在首頁從四個模型選項建立對話。
- Google 兩個模型可透過同一 Gemini API Key 真實回覆。
- 雲端失敗時依核准順序自動降級，且不使對話中斷。
- requested 與 actual model 在資料、API 及 UI 中一致且可追溯。
- API Key 不離開後端安全邊界。
- 相關 Python 與 JavaScript 測試全部通過。
