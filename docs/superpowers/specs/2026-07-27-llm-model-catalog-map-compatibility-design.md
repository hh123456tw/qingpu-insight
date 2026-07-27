# LLM 模型目錄與首頁地圖相容性設計

日期：2026-07-27

## 1. 目標

本次修改解決三個使用問題：

1. LLM Benchmark 不再要求使用者輸入 provider 與任意模型名稱。
2. 首頁物件對話只使用固定模型下拉選單。
3. 首頁新版群組地圖 API 與舊版執行中後端不一致時，地圖仍能顯示最近
   100 筆交易座標，而不是呈現破圖或空白。

範圍以期中專題與求職作品集為主。保留可說明、可測試的資料流，不加入
自動重啟監督程式、模型下載管理、動態 Gemini 模型探索或離線地圖磚。

## 2. 已確認的地圖根因

實際頁面顯示：

```text
地圖資料載入失敗：map 404
```

同一頁的市場摘要與最近交易可以正常載入，Leaflet 控制與 OpenStreetMap
attribution 也已出現。因此故障不在座標資料、Leaflet 初始化或底圖 CSS。

根因是執行中的 Flask process 仍載入舊版 Python 路由，但 Flask static
直接讀取磁碟上的新版 `app.js`。新版前端呼叫
`/api/market/map-points`，舊 process 沒有該路由而回傳 404，形成
「新前端＋舊後端」版本撕裂。

一般 500、網路中斷或錯誤資料格式不視為版本撕裂，不得以舊資料假裝成功。

## 3. 模型目錄架構

### 3.1 共用定義

後端是模型 ID、provider 與顯示名稱的唯一可信來源。前端不得自行推導或
提交不一致的 provider/model 組合。

Gemini Benchmark 固定只提供：

- `gemini-3.5-flash-lite`
- `gemma-4-31b-it`

首頁對話固定提供：

- `gemini-3.5-flash-lite`
- `gemma-4-31b-it`
- `gemma4:e2b`
- `rule`

### 3.2 Ollama 模型偵測

後端透過已設定的 Ollama base URL 呼叫標準 `GET /api/tags`，讀取目前已
安裝模型。模型清單每次請求重新取得，不要求重啟 Web。

安全限制：

- Ollama base URL 只來自後端設定，不接受瀏覽器提交 URL。
- 回傳內容只保留模型名稱及必要的公開狀態。
- 設定固定連線與讀取 timeout。
- 不回傳檔案路徑、digest、主機資訊或原始上游錯誤。
- 無法連線時回傳安全化 warning，Gemini 固定模型仍正常顯示。

### 3.3 管理端模型目錄 API

新增：

```http
GET /api/admin/llm-models
```

回應：

```json
{
  "items": [
    {
      "id": "ollama:gemma4:e2b",
      "provider": "ollama",
      "model": "gemma4:e2b",
      "label": "Ollama｜gemma4:e2b",
      "ready": true,
      "note": "本機已安裝"
    },
    {
      "id": "gemini:gemini-3.5-flash-lite",
      "provider": "gemini",
      "model": "gemini-3.5-flash-lite",
      "label": "Gemini｜Gemini 3.5 Flash-Lite",
      "ready": false,
      "note": "尚未設定 Gemini API Key"
    }
  ],
  "warnings": []
}
```

`id` 是前端選項值。Benchmark POST 改成只接受目錄內的 `model_id`：

```json
{"model_id": "ollama:gemma4:e2b"}
```

後端重新解析 `model_id`，再建立既有 `BenchmarkRequest`。不得信任前端
額外提交的 provider 或 model 欄位。解析時只以第一個 `:` 分隔 provider
前綴，完整保留 Ollama 模型名稱內的 tag；解析後仍必須存在於當次後端目錄，
不能只依字串格式放行。

## 4. Benchmark 介面

移除：

- `benchmark-provider-select`
- `benchmark-model-input`

新增：

- 單一 `benchmark-model-select`
- `benchmark-model-help`
- `benchmark-model-refresh`

載入流程：

1. 進入管理頁後請求 `/api/admin/llm-models`。
2. 使用 API 回傳順序建立單一下拉選單。
3. Ollama 與 Gemini 以 label 明確區分來源。
4. 選到未就緒模型時停用 Benchmark 按鈕並顯示 `note`。
5. 「重新整理模型清單」再次呼叫 API，安裝或刪除 Ollama 模型後不需重啟。
6. 目錄載入失敗時保留空選單、停用送出並顯示安全錯誤。

任何來自上游的錯誤文字不得以 `innerHTML` 顯示。

## 5. 首頁模型選擇

首頁物件對話維持單一 `assistant-model` 下拉選單，資料來自既有
`GET /api/conversation-models`。該回應增加 `ollama_ready`，由相同的
Ollama discovery 判斷 `gemma4:e2b` 是否存在；不顯示 provider 下拉或
自由輸入欄位。

狀態說明：

- Gemini 無 Key：可建立對話，但提示將自動使用本機模型。
- Ollama `gemma4:e2b` 未就緒：可建立對話，但提示可能改用 Rule。
- Rule：標示不使用 LLM。

後端建立對話時仍由固定目錄解析 provider，reply API 不接受模型覆寫。

## 6. 地圖相容模式

### 6.1 正常路徑

```text
GET /api/market/map-points
→ 全部符合條件的有效座標
→ 依縮放層級聚合
→ 最多 500 個群組
```

地圖狀態標示完整筆數、有座標筆數、未定位筆數與群組數。

### 6.2 404 相容路徑

只有群組 API 回傳 404 時：

```text
GET /api/transactions?...&limit=100
→ 過濾有效 latitude/longitude
→ 轉換成單筆 marker payload
→ 顯示最多最近 100 筆
```

狀態文字固定包含：

```text
相容模式：後端版本較舊，目前顯示最近 100 筆；重新啟動 Web 後可顯示完整群組地圖
```

相容模式沿用目前篩選條件。不得讓最近 100 筆冒充完整市場資料。

### 6.3 非相容錯誤

以下情況不進行 fallback：

- 群組 API 回傳 400、401、403、409、422 或 500。
- 回應不是合法 JSON。
- 必要欄位型別不正確。
- `/api/transactions` fallback 也失敗。

這些情況顯示明確錯誤，不保留可能過期的 marker。

## 7. 元件邊界

- `conversation_models.py`：首頁固定對話模型與 provider 解析。
- 新的 benchmark model catalog 模組：Ollama discovery、Gemini 固定模型、
  公開 DTO 與 `model_id` 解析。
- `provider_ops.py`：使用已解析的 `BenchmarkRequest` 執行 benchmark。
- `admin_web.py`：模型目錄 GET 與只接受 `model_id` 的 Benchmark POST。
- `admin.js`：模型目錄載入、下拉渲染、重新整理與送出。
- `market_map.mjs`：群組資料與 404 fallback 的純資料流。
- `app.js`：Leaflet marker rendering 與狀態呈現。

模型偵測、HTTP 邊界與 DOM rendering 分離，避免介面直接依賴 Ollama。

## 8. 驗證

### Python

- Ollama `/api/tags` 正確轉成目錄項目。
- Ollama 離線時回傳 warning，Gemini 項目仍存在。
- Gemini 清單恆等於核准的兩個模型。
- Gemini ready 狀態跟隨動態 secrets。
- 管理 API 不回傳 API Key、digest、路徑或原始錯誤。
- Benchmark POST 接受合法 `model_id`。
- Benchmark POST 拒絕任意模型及 provider/model 覆寫。

### JavaScript

- Benchmark 使用單一下拉，提交內容只有 `model_id`。
- 重新整理會重新載入模型。
- 未就緒模型無法送出。
- 首頁沒有 provider 選單與自由模型欄位。
- 地圖群組 API 成功時不呼叫 fallback。
- 地圖群組 API 404 時呼叫 `/api/transactions`，limit 固定為 100。
- 500 或格式錯誤不進入相容模式。
- 相容狀態不宣稱顯示完整資料。

### 真實驗收

1. 管理中心列出 `ollama list` 目前可見的模型。
2. 管理中心固定列出兩個 Gemini 模型。
3. 選一個已安裝 Ollama 模型可提交 Benchmark。
4. 首頁顯示固定四模型下拉。
5. 新後端顯示完整群組地圖。
6. 模擬群組 API 404 時顯示最近 100 筆及相容提示。

## 9. 不做事項

- 不由前端直接連線 Ollama。
- 不提供任意模型名稱輸入。
- 不自動下載或刪除 Ollama 模型。
- 不建立會中斷爬蟲、訓練或備份的自動重啟 supervisor。
- 不把 API Key、Ollama 原始錯誤或主機資訊送到前端。
- 不下載或維護離線 OpenStreetMap tile。
