# 青埔智價 Qingpu Insight

青埔智價是一個聚焦桃園機場捷運 A17～A19 生活圈的本機房價分析產品。它把官方實價登錄、591 公開售屋刊登、機器學習估價、可追溯的 AI 買方報告，以及資料與模型維運整合在同一個 Flask Web 介面。

這不是全台房仲平台，也不預測未來房價。專案的目標是示範一條可檢查、可重現、可回滾的資料與 AI 產品流程。

> 本工具僅供資料分析、課程展示與購屋研究，不構成正式不動產鑑價、投資建議或未來價格預測。

> **公開產品範圍：只支援中古屋（`resale`）。** 預售屋（`presale`）底層官方資料仍保留作為資料沿革與研究素材，但不進入公開市場指標、估價、模型訓練、候選／正式模型發布或 591 物件入口。既有預售屋正式 artifact 與 MySQL 歷史工作／對話資料仍保留；目前工作區沒有 legacy 本機 presale candidate／report 目錄，因此不宣稱保存不存在的本機目錄。

## 成果與限制

**做什麼**：輸入 A17～A19 生活圈中古屋的站點、坪數、屋齡、樓層、格局與座標，回傳每坪單價與總價估值、90% 區間、可信度與相似成交；也能貼上 591 中古屋網址，讓 LLM 依據已驗證的證據（fact ID）回答問題。

**目前正式模型**：版本 `ad50841d`，同棟價格錨點混合模型（兩個 HistGradientBoosting 在對數空間平均，其中一個以同棟完工前／預售／過去成交價為錨點），以 5,280 筆清理後的中古屋成交訓練。以下為 final test（2025-06-14～2026-06-13，n=624，模型訓練時沒看過的最後 12 個月），用**總價 MAPE** 比較，因為車位目標值定義修正後單價 MAPE 無法跨版本比較：

| 總價 MAPE | 正式模型 | 前一版（對數 HGB） | 近期中位數基準線 |
|------|----------|----------|------------------|
| 全部 | 18.94% | 17.93% | 25.20% |
| 扣除水源南路新建案（n=532） | **9.51%** | 12.56% | 17.57% |
| A17／A18／A19 | 9.6%／24.3%／11.1% | 10.9%／21.3%／13.3% | 27.1%／27.5%／19.8% |

- 模型是依 final test 之前的 6 個半年滾動回測選出：總價 MAPE 11.8%，前一版 12.3%，基準線 19.4%。
- 單價指標（新目標定義）：MAPE 20.65%、R² 0.635；基準線 27.22%、0.395。
- 90% 估價區間在 final test 的實際覆蓋率 77.6%，**低於名目 90%**，主因是水源南路建案幾乎全部落在區間外。

**主要限制**：模型對「以接近預售價轉手的新建案」會高估。水源南路建案 92 筆成交是 final test 誤差的最大來源，新模型在這個建案上比前一版更差（73% vs 49%）；因此屋齡 2 年內、主要依預售價推估的估價會加註說明並把可信度降為中。首頁估價表單沒有座標，用不到同棟錨點。

**資料污染案例**：舊正式模型 `57cf2ba9` 的 MAE 4.24 萬／坪、R² 0.775 來自預售移轉混入中古屋的污染資料，並非真實能力。追查過程、重現方式與修正決策見 [問題紀錄 §18](docs/project-issue-log.md#18-正式模型指標來自污染前資料改用對數目標重訓)（前因見 §16），改用同棟錨點的過程見 §19。

## 專案定位

- **AIPE04 期中專題**：展示資料工程、機器學習、MySQL、Web 與生成式 AI 的整合。
- **求職作品集**：呈現從資料取得、品質控制、模型評估到本機產品化與維運的完整 Python 專案。
- **自用分析工具**：查詢青埔市場、比較刊登開價、進行條件估價並產生單一物件買方報告。

## 可以做什麼

| 功能 | 使用者看到的結果 |
|------|------------------|
| 市場分析 | A17～A19 中古屋成交摘要、價格趨勢、交易量、近期成交與互動地圖 |
| AI 條件估價 | 中古屋估計總價、合理區間、可信度、影響因素與相似成交 |
| 591 物件助理 | 貼入中古屋詳細頁 → 秒回初始摘要 → 可持續對話提問，AI 給出具體看法並附驗證證據 |
| 智慧購屋報告 | 後端保留完整報告產出 API，但首頁不再顯示報告表單 |
| 管理中心 | 查看模型指標，並在前端操作資料更新、模型訓練、發布、回滾、LLM Benchmark 與診斷 |

主要頁面：

- 產品首頁：<http://127.0.0.1:5000/>
- 管理中心：<http://127.0.0.1:5000/admin>

管理端只接受本機 `127.0.0.1`／`localhost` 存取。

## 顯示慣例

首頁由上至下依序為：標題與說明、591 物件助理輸入、市場分析（摘要、地圖、趨勢、近期成交）、AI 條件估價。單一 591 網址輸入即可進入物件工作台，不需要先產生購屋報告。

### 價格格式

| 情境 | 格式 | 範例 |
|------|------|------|
| 總價 | 萬（不含 `$` 與完整數字） | `2,298 萬` |
| 單價 | 萬／坪（不含 `$`） | `58.7 萬／坪` |
| 模型 MAE | 萬元／坪 | `4.50 萬元／坪` |

### 可信度

估值可信度由三項因素綜合決定：

- **區間寬度**：合理價格區間越窄，可信度越高
- **輸入完整度**：車站、坪數、類型、樓層、車位等欄位填寫越完整，可信度越高
- **相似成交品質**：附近可比較的近期成交越多且特徵越接近，可信度越高

可信度標示為「高（綠色）／中（黃色）／低（紅色）」。

### 591 刊登定價

591 刊登價格包含議價空間，不代表最終成交價。系統在估價頁面與對話中會明確提醒此差異。刊登價不會混入官方成交資料，也不會成為模型訓練標籤。

### 收合狀態

- **近期對話**：以藥丸按鈕（pill）展開，不佔版面；點選後恢復對話，按 Escape 或點擊外部關閉
- **近期成交**：預設顯示最近 8 筆，點擊「顯示更多」展開至 100 筆（不需重新請求後端），再次點擊收合回 8 筆

## 系統架構

```mermaid
flowchart LR
    A["內政部實價登錄"] --> B["下載、清理與門牌定位"]
    C["591 公開售屋刊登"] --> D["擷取、正規化與定位"]
    B --> E["市場資料集"]
    B --> P["預售屋底層資料<br/>保留但不供公開產品"]
    E --> F["中古屋模型"]
    D --> H["版本化刊登資料"]
    E --> I["Flask Web"]
    F --> I
    H --> I
    E --> J["Evidence Pack"]
    H --> J
    J --> K["Rule / Ollama / Gemini"]
    K --> I
    L["管理中心"] --> B
    L --> D
    L --> F
```

核心設計：

- 底層資料處理保留中古屋與預售屋分類；公開查詢、估價、訓練與發布只使用中古屋。
- 591 刊登價不會混入官方成交價，也不會成為模型訓練標籤。
- 模型負責數值估價；LLM 只整理已驗證的 Evidence Pack。
- 訓練只建立候選模型，不會自動覆蓋正式模型。
- 資料與模型發布採版本化流程，失敗時保留上一個可用版本。

## 技術棧

| 層級 | 技術 |
|------|------|
| 資料處理 | Python 3.11、Pandas、NumPy、PyArrow |
| 地理處理 | 桃園門牌資料、PyProj、A17～A19 兩公里生活圈 |
| 機器學習 | scikit-learn、Ridge、Random Forest、HistGradientBoosting |
| Web | Flask、原生 JavaScript、Leaflet |
| 資料庫 | MySQL 8、PyMySQL、版本化 staging／publish |
| 爬蟲 | Selenium、Beautiful Soup、可見 Chrome |
| AI 報告 | Rule Provider、Ollama、Gemini、Pydantic schema validation |
| 品質 | Pytest、Ruff、模型發布閘門、備份還原演練 |

## 五分鐘啟動

### 1. 前置需求

- Windows 10／11
- **Python 3.11**
- Chrome；更新 591 刊登或分析 591 詳細頁時需要
- MySQL 8；選用。591 物件助理、模型訓練、管理中心的寫入／發布、備份與完整報告流程需要
- Gemini API Key；選用。只有選擇 Google 模型回答或 Gemini 報告時需要，未設定時可用 Rule 或本機 Ollama

公開儲存庫**不包含**資料集、備份、密鑰、Cookie 或 591 原始 HTML，但**包含目前的正式中古屋模型**（`artifacts/official/resale/current.json` 指向 `versions/ad50841d/`，另保留上一版 `870c95b0` 作為回滾目標）。因此新 clone 不必訓練模型，只要先下載官方資料並建立市場資料集，就能使用市場分析與 AI 條件估價。

| 功能 | 只有 Parquet（不設定 MySQL） | 需要 MySQL | 需要 Gemini |
|------|:---:|:---:|:---:|
| 市場分析、成交地圖、AI 條件估價 | ✓ | | |
| 591 物件助理（對話） | | ✓ | 選用（否則用 Rule／Ollama） |
| 模型訓練（`model-train`）、發布、回滾、管理中心寫入操作 | | ✓（另需強度足夠的 `QINGPU_SECRET_KEY`） | |
| 買方報告、健康檢查、備份 | | ✓ | 選用 |

### 2. 建立環境

```powershell
git clone https://github.com/hh123456tw/qingpu-insight.git
cd qingpu-insight
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env   # 選用：依需要填入 .env，未填的功能會自動停用
```

如果專案已有 `.venv`，不要用另一個 Python 版本直接覆寫。NumPy 顯示 `cp311`／`cp312` 不相容時，請關閉正在使用虛擬環境的程式，再以同一版 Python 乾淨重建。

`web.py` 依賴 `python-dotenv`（提供 `from dotenv import load_dotenv`），已列入 `pyproject.toml` 的 dependencies；`pip install -e ".[dev]"` 會一併安裝。若在乾淨環境看到 `ModuleNotFoundError: No module named 'dotenv'`，代表沒有重新安裝依賴，請重新執行上述安裝指令。

### 3. 建立市場資料（第一次必做）

公開 clone 沒有 `data/`。依序執行以下兩個指令；`run` 的下載階段需要網路（內政部實價登錄與桃園門牌），`market-build` 可離線執行：

```powershell
# 下載 110S3～115S2 官方成交資料與桃園門牌，並完成地址定位（= acquire + analyse）
.\.venv\Scripts\qingpu-data.exe run --start-season 110S3 --end-season 115S2

# 清理成市場資料集 data/processed/market_transactions.parquet（首頁與估價讀取的就是這個檔案）
.\.venv\Scripts\qingpu-data.exe market-build
```

`run` 只做下載與定位，**不會**自動執行 `market-build`；少了第二步，首頁會沒有資料。若 `analyse` 判定 `NO-GO` 會以非零狀態結束，可加上 `--allow-no-go` 單獨重跑 `qingpu-data.exe analyse`。

### 4. 啟動 Web

```powershell
.\.venv\Scripts\qingpu-web.exe
```

開啟 <http://127.0.0.1:5000/>。未設定 `QINGPU_DATABASE_URL` 時，Web 讀取本機 Parquet，並使用 Git 內附的正式模型估價；需要 MySQL 的功能（見上表）會保持停用並說明原因。要啟用完整管理功能，請見「管理中心 → 全新 MySQL 建置順序」。

時間資料在後端與資料庫統一以 UTC 保存；首頁、管理中心與助理介面統一以台北時區 `Asia/Taipei`（UTC+8）顯示。

### 5. 驗證（選用）

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\qingpu-data.exe llm-smoke --provider rule --model rule `
  --output-dir outputs/m44-benchmark
```

Rule smoke 不需要 MySQL、Ollama 或 Gemini。成功時輸出 JSON 內的 `success`、
`schema_success` 應為 `true`，Fact Accuracy 與 Coverage 應為 `1.0`。

若看到 NumPy C-extension 的 `cp311`／`cp312` 不相容訊息，代表 `.venv` 曾被另一個
Python 版本覆寫。確認沒有程式正在使用環境後，刪除並以同一版本乾淨重建：

```powershell
Remove-Item -Recurse -Force .venv
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 系統流程

```text
官方實價登錄 + 桃園門牌
        │
        ▼
下載 → 清理／定位 → 市場資料集 → 中古屋估價模型
                              │
591 公開刊登 → 正規化／定位 ──┼→ MySQL 版本發布 → Flask Web
                              │
                              └→ Evidence Pack → Rule／Ollama／Gemini 報告
```

重要設計：

- 預售屋（`presale`）底層資料保留，但公開市場查詢、估價、訓練、發布與 591 入口只接受中古屋（`resale`）。
- 刊登價不會冒充成交價；租屋資料不會混入買賣實價比較。
- 估價由已評估的模型負責；LLM 只負責依 Evidence Pack 整理說明。
- 報告中的數字必須引用現有 fact ID；無效報告不會被 benchmark/smoke 判為成功。
- 買方報告目前一次只分析 **一個物件**，避免多物件證據交叉引用。

## AI 購屋助理

### 快速開始

1. 打開首頁；可先由 A17～A19 的「591 中古屋」連結挑選物件，再將單一中古屋
   詳細頁網址貼入「貼上 591 物件，開始分析」
2. 從固定清單選擇回答模型：Google Gemini 3.5 Flash-Lite、Google Gemma 4
   31B、本機 Ollama Gemma 4 或 Rule
3. 點擊「分析這個物件」
4. 系統以可見 Chrome 開啟 591 頁面、建立物件快照與估價證據
5. 自動導向雙欄工作台，左側顯示物件資訊與證據，右側可開始對話
6. 輸入問題後按 Enter，AI 以對話語氣回覆具體看法，下方附帶驗證過的物件證據
7. 需要更新資料時按「重新擷取」建立新版快照
8. 舊對話可從首頁「最近對話」恢復

### 進階功能

- **模型固定**：模型在建立對話時選定，後續回覆不能由瀏覽器覆寫 provider
- **Google 模型**：使用管理中心儲存的 `QINGPU_GEMINI_API_KEY`；金鑰更新後
  下一次請求立即生效，不必重啟 Web
- **自動備援**：Google 失敗時依序嘗試本機 `gemma4:e2b` 與 Rule；本機模型失敗
  時使用 Rule。回答會標示實際模型與安全化的切換原因
- **Ollama**：預設連線 `http://127.0.0.1:11434`，可用
  `QINGPU_OLLAMA_BASE_URL` 覆寫；模型目錄、Benchmark 與首頁對話共用此設定
- **Rule 模式**：完全離線，使用證據資料產生固定摘要與建議
- **對話內容**：AI 以「青埔房產顧問」角色回覆，可根據估值差距、格局、屋齡等
  給出具體看法（如開價偏高、適合小家庭），而非單純複述數據；每條物證仍需引用 fact ID
- **初始摘要**：物件分析完成後使用 Rule 秒回摘要，不需等待 Gemini，使用者可立即對話
- **詳細頁擷取**：中古屋支援目前 591 DOM（即使沒有 JSON-LD）；新建案網址會
  顯示此產品已停用該入口。若 591 顯示驗證頁，系統會要求人工處理，不繞過驗證
- **Gemini 回覆**：只採用正式文字 part，略過 API 回傳中的思考 part，再執行
  schema 與 fact ID 驗證

### 技術說明

詳細操作說明請見 `docs/operations/listing-conversation-assistant.md`

### LLM 模型與 Benchmark

- 首頁物件助理固定提供兩個 Gemini 模型、本機 `gemma4:e2b` 與 Rule。
- 管理中心的 Benchmark 模型清單會即時讀取 Ollama `/api/tags`，並固定列出
  `gemini-3.5-flash-lite`、`gemma-4-31b-it`；不接受任意模型名稱。
- 安裝或刪除 Ollama 模型後，按「重新整理模型清單」即可，不必重啟 Web。
- Gemini API Key 由管理中心儲存；不得把 Key 寫進 README、命令列或 Git。

### 地圖相容模式

正式地圖使用 `/api/market/map-points` 顯示完整聚合資料。若頁面顯示
「相容模式」，表示瀏覽器已讀到新版 JavaScript，但執行中的 Flask process
仍是舊版；此時只顯示最近 100 筆有效座標。停止舊 process 並重新啟動
`qingpu-web` 後，即可恢復完整群組地圖。

## 資料來源

- 內政部不動產交易實價登錄：https://data.gov.tw/dataset/77051
- 桃園市門牌資料：https://data.gov.tw/dataset/157689
- 桃園捷運 A17、A18、A19 車站地址

環境設定與原始資料以 `data/raw/` 管理，不提交 Git。

## Windows 環境

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## M0 工作流程

```powershell
# 下載 110 年第 3 季～115 年第 2 季 MOI 歷史與當期資料及門牌
.\.venv\Scripts\qingpu-data acquire --start-season 110S3 --end-season 115S2

# 以本機檔案進行地址定位與可行性分析
.\.venv\Scripts\qingpu-data analyse

# 一鍵完成以上兩步驟
.\.venv\Scripts\qingpu-data run --start-season 110S3 --end-season 115S2
```

輸出成果：

- `data/processed/transactions.parquet`
- `outputs/reports/m0-data-feasibility.md`
- `outputs/reports/m0-station-summary.csv`

`GO` 表示資料量與座標覆蓋率達門檻，可進入模型開發階段；`NO-GO` 表示需調整範圍或定位方法，重複執行 analyse 即可重新評估。

## M1 市場分析工作流程

### 建立市場資料集

```powershell
# 從 M0 定位結果建立市場分析資料集與品質報告
.\.venv\Scripts\qingpu-data market-build
```

輸出成果：

- `data/processed/market_transactions.parquet` — 市場分析用清理後交易資料
- `outputs/reports/m1-market-quality.json` — 品質報告（含 output_records、output_by_type、maximum_date）

### 無資料庫 Portfolio Demo

```powershell
# 直接從 Parquet 啟動網頁伺服器
.\.venv\Scripts\qingpu-web
```

### 選用 MySQL 8 路徑

```powershell
# 設定連線字串
$env:QINGPU_DATABASE_URL = "mysql+pymysql://<user>:<url-encoded-password>@127.0.0.1:3306/qingpu_insight"

# 僅建立 M1 市場表格；完整管理功能的 migration 順序請見「管理中心 → 全新 MySQL 建置順序」
$env:MYSQL_PWD = Read-Host "MySQL password"
Get-Content -Raw -Encoding UTF8 database/001_market_schema.sql |
  mysql -h 127.0.0.1 -u <user> qingpu_insight
Remove-Item Env:MYSQL_PWD

# 載入市場資料到 MySQL
.\.venv\Scripts\qingpu-data.exe mysql-load

# 啟動網頁伺服器（自動使用 MySQL 資料源）
.\.venv\Scripts\qingpu-web.exe
```

### 環境變數

所有設定都是選用的；`qingpu-web` 與 `qingpu-data` 啟動時會讀取專案根目錄的 `.env`（不覆寫已存在的環境變數）。範本與說明見 [`.env.example`](.env.example)。

| 變數 | 用途 | 未設定時 |
|------|------|----------|
| `QINGPU_DATABASE_URL` | MySQL 連線字串，scheme 須為 `mysql+pymysql://` 或 `mysql://` | 市場資料改讀 Parquet；591 物件助理、模型訓練、管理寫入、報告、備份停用 |
| `QINGPU_SECRET_KEY` | Flask session 密鑰；管理中心寫入功能另要求至少 32 字元並通過強度檢查 | 每次啟動隨機產生；管理中心寫入功能停用 |
| `QINGPU_GEMINI_API_KEY` | Google Gemini API Key（也可由管理中心存入 `instance/secrets.env`） | 無法使用 Google 模型（可改選本機 Ollama 或 Rule） |
| `QINGPU_GEMINI_MODEL` | Gemini 報告與 CLI benchmark 使用的模型 ID | 報告不註冊 Gemini provider |
| `QINGPU_OLLAMA_BASE_URL` | 本機 Ollama 位址 | `http://127.0.0.1:11434` |
| `QINGPU_OLLAMA_MODEL` | 報告使用的 Ollama 模型（例如 `gemma4:e2b`） | 報告不註冊 Ollama provider |
| `QINGPU_PORT` | Web 埠號 | `5000` |
| `QINGPU_DEBUG` | 設為 `1` 開啟 Flask debug | 關閉 |

所有變數都可以寫在 `.env`；shell 環境變數優先於 `.env`。

### 生成檔案說明

| 檔案 | 階段 | 說明 |
|------|------|------|
| `data/raw/manifest.json` | acquire | 下載記錄清單 |
| `data/processed/transactions.parquet` | analyse (M0) | 地址定位後完整交易資料 |
| `data/processed/market_transactions.parquet` | market-build (M1) | 清理後市場分析用交易資料 |
| `data/processed/precompletion_transfers.parquet` | market-build (M1) | 完工前移轉紀錄；不進入中古屋目標值，只作為同棟價格錨點 |
| `outputs/reports/m0-data-feasibility.md` | analyse (M0) | 資料可行性報告 |
| `outputs/reports/m0-station-summary.csv` | analyse (M0) | 各站交易量摘要 |
| `outputs/reports/m1-market-quality.json` | market-build (M1) | 市場資料品質報告 |

### 官方資料來源

- 內政部不動產交易實價登錄（含歷史每季 ZIP 與當期 CSV）：https://plvr.land.moi.gov.tw
- 桃園市門牌資料：桃園市政府資料開放平台
- 桃園機場捷運車站地址：桃園捷運公司官方網站

### 更新日期行為

- `acquire` 會重新下載指定季範圍的所有歷史 ZIP 與當期 CSV，覆蓋既有檔案。
- `analyse` 總是重新處理 `data/raw/` 下的所有原始 CSVs，輸出新的 `transactions.parquet`。
- `market-build` 讀取 `data/processed/transactions.parquet`，產出新的 `market_transactions.parquet`。
- 若 MySQL 已載入資料，`qingpu-web` 會自動使用 MySQL 資料源；否則回退到 Parquet。

### 中古屋公開產品與預售屋底層資料

`market-build` 仍保留中古屋與預售屋分類，讓來源資料與歷史品質統計可追溯；公開產品只回傳中古屋。預售屋不會合併進中古屋價格指標，也不會進入估價、訓練、候選／正式模型發布或 591 公開入口。

## M2 AI 估價工作流程

### 模型訓練

```powershell
# 建立已驗證的 M1 特徵來源
.\.venv\Scripts\qingpu-data market-build

# 建立 immutable 中古屋候選；不會自動發布
.\.venv\Scripts\qingpu-data model-train --markets resale
```

輸出成果：

| 檔案 | 階段 | 說明 |
|------|------|------|
| `candidates/<run_id>/resale.joblib` | model-train | immutable 中古屋候選 artifact |
| `candidates/<run_id>/manifest.json` | model-train | 資料快照、來源版本、模型指標與發布建議 |
| `candidates/<run_id>/reports/resale-evaluation.json` | model-train | 中古屋候選模型評估報告 |
| `candidates/<run_id>/reports/resale-model-card.md` | model-train | 中古屋模型卡 |

候選訓練需要可用的 `QINGPU_DATABASE_URL` 記錄管理工作狀態。既有預售屋正式 artifact 與 MySQL 歷史工作／對話資料只作為歷史資料保留，不會被新的訓練流程覆寫或發布；目前工作區沒有 legacy 本機 presale candidate／report 目錄可供保存。

Web 操作頁支援三組固定調參設定（快速／平衡／精細）及選用自訂設定，
自動在校準集比較各 profile 結果、鎖定最佳候選，並在測試集隔離驗證。
訓練結果以摘要卡片（MAE、MAPE、覆蓋率、baseline delta）優先呈現，
完整指標與模型比較收合在可展開的進階區塊。

#### AutoML 自動探索

當引導調參的固定 profile 無法產出通過 A18 發布閘門的候選時，Web 操作頁可選擇 AutoML 模式進行廣域參數探索。

**三種預算：**

| 預算 | 時間 | 最多試驗組數 |
|------|------|-------------|
| 快速探索 | 5 分鐘 | 12 組 |
| 標準探索 | 15 分鐘 | 35 組 |
| 深度探索 | 30 分鐘 | 70 組 |

AutoML 使用 Optuna TPE 取樣器，在 Random Forest 與 HistGradientBoosting 的參數空間中搜尋，每個試驗經過完整的校準集評估與發布閘門檢查。

**排行榜排序規則：** 以校準閘門通過（`calibration_passed`）為第一排序鍵，通過者優先；同通過狀態下以整體 MAE 由低至高排列；MAE 相同時以 MAPE 為第三鍵。

**排名第 1 不一定可發布：** 排行榜僅反映校準集表現。候選仍需通過完整發布閘門（年度回測 ≥ 2/3 通過、各站 MAPE 不倒退、資料新鮮度等），且必須由管理者在管理端手動決定發布。

**合作停止（Cooperative Stop）：** AutoML 搜尋在 Web 背景執行。管理者可發出停止請求，搜尋在當前試驗完成後優雅終止，已完成的排行榜保留在 `outputs/automl/<run_id>/`。

**輸出位置：**
- 執行中的部分結果：`outputs/automl/<run_id>/<market>-trials.json`
- 正式驗證後的候選副本：`<candidate-stage>/automl/<market>-trials.json`
- 完整候選 artifact（含模型參數與 AutoML 證據）存放於 `candidates/`

AutoML 模式與引導調參為互斥選擇。AutoML 不自動發布任何模型，也不保證排行榜第 1 名可通過完整發布閘門。

### 啟用估價產品

```powershell
.\.venv\Scripts\qingpu-web
```

首頁「AI 條件估價」面板只支援中古屋。估價結果包含合理區間、可信度、影響因素、相似成交與開價評估。

- 成交地圖依目前篩選條件涵蓋全部有效座標資料，並依縮放層級聚合為最多
  500 個群組；地圖狀態會揭露總筆數、有座標筆數與未定位筆數。
- 「近期成交」獨立顯示最新 100 筆，不代表地圖或市場摘要的資料上限。

### 方法論文件

請參閱 [docs/m2-valuation-methodology.md](docs/m2-valuation-methodology.md)。

### 中古屋模型證據工作流程

中古屋（resale）估價模型在訓練與發布時，會額外執行完整的證據檢查，確保模型符合最低品質門檻。

**資料狀態**
- 資料範圍：訓練資料的時間區間（min_date～max_date）、各站點（A17/A18/A19）與中古屋可用交易筆數；manifest 仍記錄底層資料快照中的預售屋筆數，但不使用它訓練
- 日期語義：`data_max_date` 是訓練資料最後一筆交易的日期，也是模型「知道」的最後日期

**候選模型家族**
中古屋引導訓練比較 `Ridge`、`RandomForest` 與 `HistGradientBoosting（對數價格）` 三種候選（`RESALE_GUIDED_CANDIDATES`），另以 `RecentMedianBaseline`（最近 12 個月的站點×建物類型中位數）作為基準線；基準線只用來比較，不會被發布。HGB 只保留對數目標版本的原因見[問題紀錄 §18](docs/project-issue-log.md)。AutoML 模式另在 Random Forest 與（原始目標）HistGradientBoosting 的參數空間中搜尋。中古屋 v3 特徵契約加入 TWD97 空間座標。**XGBoost 被刻意排除**：本專案的資料規模（數千至一萬多筆）不需要 XGBoost 的分散式加速優勢，且 scikit-learn HGB 能減少相依套件與部署複雜度。

**衍生特徵**
在中古屋 13 個基本特徵（站點、車站距離、坪數、建物類型、房／廳／衛、屋齡、樓層、總樓層、樓層比、交易年、交易月）之上，新增五個衍生特徵與三個空間特徵（`twd97_x`、`twd97_y`、`location_known`），合計 21 欄。**車位不是模型特徵**：模型只估算房屋本體（扣除車位後）的每坪單價，車位另由同一 artifact 內的車位價格政策（`parking_price_policy`，依車位類型取訓練資料中位價，樣本不足 20 筆時退回全體中位價）計價，發布檢查 `parking_price_consistency` 會確認模型特徵不含車位欄位。五個衍生特徵：
- `transaction_month_index`：交易年月數值，捕獲長期時間趨勢
- `station_building_type`：車站與建物類型交互項
- `building_age_band`：屋齡分群（0–5、5–10、10–20、20+ 年）
- `area_band`：坪數分群（small ≤ 20、standard > 20 且 ≤ 50、large > 50 坪）
- `floor_band`：樓層比三分群（low、middle、high）

中古屋訓練使用所選 profile 的**近期交易半衰期權重**（三組內建 profile 皆為 48 個月，自訂 profile 可設 12～84 個月），愈近期的交易權重愈高，權重下限為 0.10。

**嚴格時間切割**
所有評估使用嚴格的時間順序切割：
- 訓練集：最新日期往前推 18 個月以前的所有交易
- 校準集：訓練集結束後 6 個月
- 測試集：最後 12 個月

三組各自至少 100 筆交易，否則訓練失敗。

**三次年度回溯測試**
中古屋模型自動執行三次年度回溯測試：以最後資料日期所在月份的月底為第一個截止日，再逐年往前推兩次，每次以相同的時間切割重新訓練選定的模型。每次回溯記錄 `passed`（候選整體 MAE 是否低於 12 個月基準線）與 `stations_within_limit`（A17／A18／A19 各站 MAPE 是否都 ≤ 基準線 × 1.10）。

**發布閘門（Release Gate）**
候選模型要獲得 `recommended` 狀態，必須同時通過以下七項檢查（manifest 的 `release_checks`；1–6 由 `model_analysis.evaluate_release_checks` 以 final test 指標計算，7 由 `model_training_service` 補上）：
1. `overall_mae_improved`：final test 整體 MAE ≤ 基準線 MAE × 0.98（至少改善 2%）
2. `stations_within_limit`：A17、A18、A19 各站 MAPE ≤ 基準線對應站 × 1.10（最多退步 10%）
3. `a18_improved`：A18 的 MAPE 必須**嚴格低於**基準線（`<`，而非 `≤`）
4. `backtests_passed`：必須剛好產生三次年度回溯，且至少兩次 `passed = true`
5. `backtest_stations_within_limit`：三次回溯中至少兩次 `stations_within_limit = true`
6. `candidate_fresh`：`data_max_date` 不早於最新官方資料日期前 180 天。訓練當下必然成立，真正的把關在發布預覽：若最新市場資料已比候選資料晚超過 180 天，`preview_publish` 會拒絕發布。
7. `parking_price_consistency`：模型特徵不含車位欄位，且 artifact 帶有有效的車位價格政策（全體中位價 > 0）

七項全部通過，`recommended` 才會是 `true`，管理端才允許發布；未通過時不會發布任何模型（包含基準線），正式模型維持原版本。

**過期降級（Stale Fallback）**
正式模型若超過 **180 天**未更新，`valuate()` 自動切換至降級模式：
- 使用最近 12 個月交易計算 `RecentMedianBaseline` 作為估價主體
- 區間與相似成交仍參考原模型的校準參數
- 估價結果標記為 `degraded = true`、`degraded_reason = "stale_model"`
- 信賴度強制設為 `low`，並附加「正式模型資料過舊」說明

若模型 artifact 完全無法載入，則退而使用最近 24 個月的中位數作為估價。

**五分鐘操作流程**
從資料更新到發布確認的典型操作：

```powershell
# 1. 更新官方交易資料（指定季度範圍）
.\.venv\Scripts\qingpu-data.exe run --start-season 115S1 --end-season 115S2

# 2. 建立市場資料集並訓練中古屋模型
.\.venv\Scripts\qingpu-data.exe market-build
.\.venv\Scripts\qingpu-data.exe model-train --markets resale
.\.venv\Scripts\qingpu-web.exe
```

完成首次資料與模型建立後，日常更新、訓練與發布可以改由管理中心操作。

## 管理中心

管理中心將操作依目的分類，避免使用者背誦 CLI：

| 分類 | 功能 |
|------|------|
| 總覽 | 系統狀態、可用功能、待處理事項 |
| 資料 | 指定季度範圍，一鍵更新官方資料 |
| 刊登 | 更新與發布 591 中古屋 |
| 模型 | 中古屋調參訓練、候選比較、發布預覽、發布與回滾 |
| LLM | Gemini Key、模型清單與固定案例 benchmark |
| 工作 | 背景工作進度、歷史、輸出與安全錯誤摘要 |
| 診斷 | 目前環境、資料及服務狀態 |

### 全新 MySQL 建置順序

公開 clone 的 MySQL 是空的。要啟用完整管理功能，需依序套用以下 migration：

```powershell
$env:MYSQL_PWD = Read-Host "MySQL password"
$mysql = "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"

# 建立資料庫（001 內含 CREATE DATABASE 與 USE，也可先手動建立）
& $mysql -u root -e "CREATE DATABASE IF NOT EXISTS qingpu_insight CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 依序套用 migration
$migrations = @(
  "database/001_market_schema.sql",
  "database/003_listing_intelligence_schema.sql",
  "database/004_listing_range_fields.sql",
  "database/004_m4_jobs_publishing_schema.sql",
  "database/005_m43_health_backup_schema.sql",
  "database/006_m44_reports_schema.sql",
  "database/007_frontend_operations_schema.sql",
  "database/008_conversation_assistant_schema.sql",
  "database/009_conversation_fallback_metadata.sql"
)
foreach ($m in $migrations) {
  Get-Content -Raw -Encoding UTF8 $m | & $mysql -u root qingpu_insight
}
Remove-Item Env:MYSQL_PWD
```

注意事項：

- **跳過 `002_add_valuation_columns.sql`**：全新資料庫由 `001` 建立的 `market_transactions` 已含 `floor`、`total_floors`、`parking_type` 等欄位，再套用 `002` 會報 `Duplicate column`。`002` 只供舊 schema 升級使用。
- **`004_listing_range_fields.sql`** 使用 stored procedure 以 `IF NOT EXISTS` 方式補欄位，可安全重複執行。
- **`008`／`009`** 也會在 Web 啟動時由 `_ensure_conversation_schema` 自動套用；手動套用不會衝突（皆使用 `CREATE TABLE IF NOT EXISTS` 或 `INFORMATION_SCHEMA` 檢查）。
- 套用後執行 `.\.venv\Scripts\qingpu-data.exe mysql-load` 載入市場資料，再啟動 `qingpu-web`。

### 密碼 URL 編碼

`QINGPU_DATABASE_URL` 的密碼含特殊字元（`@`、`:`、`/` 等）時必須做 URL 編碼（例如 `p@ssw0rd` → `p%40ssw0rd`）。程式端（`cli.py`、`market_repository.py`、`web.py`）都會以 `urllib.parse.unquote` 解碼。密碼不含特殊字元時可直接填寫。

### 完整管理功能的必要設定

```powershell
$env:QINGPU_DATABASE_URL = "mysql+pymysql://<user>:<url-encoded-password>@127.0.0.1:3306/<database>"
$env:QINGPU_SECRET_KEY = "<至少 32 字元的本機隨機密鑰>"
$env:QINGPU_DEBUG = "0"
.\.venv\Scripts\qingpu-web.exe
```

`QINGPU_SECRET_KEY` 必須符合強度政策。真實連線字串與密鑰只放在本機環境，不要提交到 Git。

## 資料範圍

### 官方成交資料

- 內政部不動產交易實價登錄
- 桃園市官方門牌資料
- 桃園機場捷運 A17、A18、A19 車站資料
- 只納入通過住宅、價格、面積、日期與兩公里生活圈規則的交易

### 591 公開刊登

Web 主流程只處理：

1. `sale`：中古屋／成屋出售

`newhouse`（預售屋／新成屋）網址會顯示產品入口已停用，不進行分析。租屋資料也不屬於本專題的市場估價與前端維運主線。舊版相容程式可能仍保留 newhouse／rental 的底層契約與本機歷史資料，但管理介面不會抓取、發布或展示這些流程。

系統使用可見 Chrome，不繞過驗證，也不刻意收集帳號、密碼、Cookie 或聯絡欄位。結構化 schema 沒有專用聯絡欄位；但標題等 free text 與本機 raw HTML 仍可能含無法完全辨識的 contact-shaped text，因此發布前會執行偵測／清理 gate，原始 HTML 只保留在本機忽略路徑。

`listing-build` 的離線、門牌定位及詳情補強模式，都需要完整的 raw batch 與官方門牌檔 `data/raw/doorplates.csv`。門牌檔可先由既有 `qingpu-data acquire` 流程取得。要刻意驗證不連 MySQL 的離線路徑時，可在不改動目前 shell 的 child PowerShell 中執行：

```powershell
$batchDir = "data/raw/listings/591/<YYYY-MM-DD>/<complete-batch-id>"
pwsh -NoProfile -Command {
  param($inputBatch)
  $env:QINGPU_DATABASE_URL = $null
  & .\.venv\Scripts\qingpu-data.exe listing-build --batch-dir $inputBatch
} -args $batchDir
```

## 地圖與近期成交不是同一個上限

- 成交地圖使用目前篩選條件下的全部有效座標資料。
- 為避免瀏覽器一次繪製上萬個 marker，後端依縮放層級聚合為最多 500 個地圖群組。
- 地圖狀態會顯示總交易、有座標與未定位筆數。
- 「近期成交」表格獨立顯示最新 100 筆；它不代表地圖或市場統計只有 100 筆。
- 地圖與其他首頁模組分開更新，單一摘要 API 失敗不應讓底圖或成交點一起消失。

## 模型與評估

### 為什麼沒有使用 XGBoost

目前中古屋引導訓練的候選模型為 Ridge、Random Forest 與 HistGradientBoosting（對數價格目標），另以 RecentMedianBaseline 作為比較基準。此專案資料量為數千至一萬多筆，scikit-learn 的 HistGradientBoosting 已能提供合適表現，也能減少額外套件與部署複雜度。模型選擇由實際時間切割結果決定，不因工具熱門程度決定。

### 訓練策略

- 訓練、校準與測試依時間順序切割，不隨機打散未來交易。
- 中古屋使用近期交易權重，內建 profile 的半衰期為 48 個月。
- 中古屋加入交易月份、站點×建物類型、屋齡帶、坪數帶與樓層帶等衍生特徵，以及 TWD97 座標；車位不是模型特徵，另以車位價格政策計價。
- 基準線為訓練期最後 12 個月的站點×建物類型中位數。
- Web 提供快速、平衡、精細三組固定 profile，也可加入一組受範圍限制的自訂 profile。
- 每次訓練產生 immutable candidate，不直接覆蓋正式 artifact。

### 指標怎麼看

| 指標 | 白話說明 | 判讀方向 |
|------|----------|----------|
| MAE | 平均每坪估錯多少元 | 越低越好 |
| MAPE | 平均百分比誤差 | 越低越好，適合跨價位理解 |
| R² | 模型解釋價格差異的程度 | 越高越好，但不能單獨判斷模型 |
| Coverage | 真實價格落在估價區間內的比例 | 越高代表區間較常涵蓋真值 |
| Baseline delta | 候選模型相對近期中位數改善多少 | 正向改善才值得考慮發布 |

指標由 `model_training._compute_metrics` 計算，單位為元／坪。MAPE 的分母取 `max(|實際單價|, 100,000)`，避免極低單價讓百分比失真；由於市場清理（`market_cleaning.PRICE_PER_PING_MIN`）已排除官方單價低於 10 萬／坪的交易，目前 5,280 筆訓練資料的目標單價也都高於 10 萬，這個下限實際上不會生效。

### 發布閘門

中古屋候選模型必須同時通過七項檢查（整體 MAE ≤ 基準線 × 0.98、各站 MAPE ≤ 基準線 × 1.10、A18 MAPE 嚴格低於基準線、三次年度回溯至少兩次勝過基準線、三次回溯至少兩次各站在限制內、資料新鮮度 180 天、車位計價一致性），管理端才會標記為建議發布；詳見上方「M2 → 發布閘門」。發布前可展開查看：

- 訓練資料範圍與筆數
- 使用的 profile 與超參數
- 候選模型和 baseline 指標
- 明確區分校準集與 final test 指標
- 最大誤差前 20 筆與資料品質排除計數（不顯示完整門牌）
- A17／A18／A19 分站結果
- 三次年度回溯測試
- 每一項發布資格及失敗原因

正式模型資料超過 180 天時，估價會降級為近期中位數基準、放寬區間並將可信度設為低，不會假裝舊模型仍可靠。

完整定義請見 [AI 估價方法論](docs/m2-valuation-methodology.md)。

## 可追溯 AI 報告

買方報告一次只分析一個刊登物件。系統先把成交、估價與刊登資訊整理成 Evidence Pack，每個數字都有 fact ID。Rule、Ollama 或 Gemini 只能引用已存在的 fact；schema 或 evidence validation 失敗時，報告不會被判定成功。

- **Rule**：完全離線，不需要 LLM，適合展示 smoke test 與規則式報告。
- **Ollama**：選用的本機模型。
- **Gemini**：選用的外部 Provider；API Key 可由管理介面存入不提交 Git 的 `instance/secrets.env`。

LLM 不是資料清理、刊登發布或模型估價的必要條件。

## 測試與驗證

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
# 執行本機健康檢查（MySQL 連線、資料集、備份等）
.\.venv\Scripts\qingpu-data.exe health-run

# 建立 MySQL dump 備份
.\.venv\Scripts\qingpu-data.exe backup-create

# 在隔離資料庫驗證備份
.\.venv\Scripts\qingpu-data.exe backup-restore-drill --backup-id <uuid>
```

### 環境變數

健康檢查與備份指令必須設定 `QINGPU_DATABASE_URL`；完整環境變數清單見上方「M1 市場分析工作流程 → 環境變數」與 [`.env.example`](.env.example)。

`backup-create` 產出 `.sql` 檔案至 `outputs/backups/`，僅透過 child process 環境傳遞密碼。

### Web 維運端點

| 路由 | 方法 | 說明 |
|------|------|------|
| `/api/ops/health` | GET | 執行健康檢查（限本機） |
| `/api/ops/backups?limit=N` | GET | 列出最近備份記錄（限本機） |

兩個 GET 端點沿用 M4.2 的 loopback + trusted Host 保護。無任何 backup／restore HTTP mutation route。

### M4.4 Reports 工作流程

M4.4 報告以已發布的 MySQL 刊登與市場資料建立 Evidence Pack，目前每次只接受一個
candidate listing ID。這是刻意的產品限制，用來避免多物件的事實與數字交叉引用。

```powershell
# 更新中古屋刊登資料（最少 1 頁）
.\.venv\Scripts\qingpu-data.exe listing-update --types sale --max-pages 1

# 執行健康檢查
.\.venv\Scripts\qingpu-data.exe health-run

# 取得一個有效的刊登 ID
$listingId = mysql -h 127.0.0.1 -u root -p --batch --skip-column-names qingpu_insight -e "SELECT source_listing_id FROM listing_current WHERE active = TRUE LIMIT 1"

# 用 Rule（無 LLM 需求）產生買方報告
.\.venv\Scripts\qingpu-data.exe report-generate --candidate $listingId --provider rule --intended-use self_use

# 啟動網頁（含報告 API /api/reports）
.\.venv\Scripts\qingpu-web.exe
```

Ollama 與 Gemini 為可選的 LLM report provider。未設定時 Web UI 與 CLI 可選 provider `rule`，
以 RuleReportProvider 產生規則式報告。指定 Ollama／Gemini 但缺少必要設定時會明確失敗，
不會以 Rule 結果冒充指定模型成功。設定方式：

```powershell
$env:QINGPU_OLLAMA_MODEL = "gemma4:e2b"
$env:QINGPU_GEMINI_API_KEY = "<your-key>"
$env:QINGPU_GEMINI_MODEL = "<available-model-id>"
```

### Smoke 與 Benchmark

```powershell
# Rule smoke（不需 LLM）
.\.venv\Scripts\qingpu-data.exe llm-smoke --provider rule --model rule --output-dir outputs/m44-benchmark
```

自動測試涵蓋資料契約、模型特徵、時間切割、發布閘門、API、安全限制、前端 JavaScript 契約與失敗回滾。真實 MySQL、可見 Chrome、591 頁面及選用 LLM Provider 仍需在展示電腦進行人工 smoke test。

## 公開儲存庫邊界

以下內容不提交 Git：

- `.env`、`instance/secrets.env` 與任何 API Key
- `data/raw/`、`data/processed/` 與 Parquet 資料集
- `candidates/` 內的訓練候選（實驗輸出，不隨 Git 發佈）
- `artifacts/` 內除了下列兩個正式版本以外的模型
- `outputs/backups/` 與執行輸出
- 591 原始 HTML、Chrome profile、Cookie 與聯絡資訊

**會**提交 Git 的模型只有 `artifacts/official/resale/`：`current.json`（指向目前正式版本）、`versions/ad50841d/`（目前正式模型，同棟錨點混合）與 `versions/870c95b0/`（上一版對數 HGB，保留作為回滾目標）。其他歷史版本只留在本機。

因此，公開 clone 在執行「五分鐘啟動 → 建立市場資料」之前，首頁沒有資料點；這代表本機資料尚未建立，不代表前端故障。

## 詳細文件

| 文件 | 內容 |
|------|------|
| [M1 市場資料方法論](docs/m1-market-methodology.md) | 住宅篩選、生活圈、定位與市場指標 |
| [M2 AI 估價方法論](docs/m2-valuation-methodology.md) | 特徵、調參、時間切割、指標與模型限制 |
| [中古屋估價誤差研究](docs/research/2026-07-29-resale-model-error-analysis.md) | RMSE 根因、資料清理、空間特徵實驗與面試摘要（文中正式模型指標為污染前數字，已加註更正） |
| [M3 刊登方法論](docs/m3-listing-methodology.md) | 591 擷取、批次、事件與隱私邊界 |
| [M4 刊登定位方法論](docs/m4-location-methodology.md) | 地址證據、定位信心與發布控制 |
| [專案問題與決策紀錄](docs/project-issue-log.md) | 歷次工程問題、根因分析與設計決策 |

CLI 的實際參數以程式內說明為準：

```powershell
.\.venv\Scripts\qingpu-data.exe --help
```

## 已知限制

- 僅涵蓋 A17～A19 兩公里生活圈，不代表桃園或全台市場。
- 模型估計目前合理價格，不預測未來漲跌。
- 未納入利率、政策、景觀、裝潢與建商品牌等難以穩定量化的特徵。
- 591 頁面結構或驗證流程變更時，擷取可能需要人工處理或程式更新。
- 座標不足的交易或刊登會保留為未定位，不以標題或地標猜測位置。
- 估值模型缺乏社區特徵，同社區不同座標可能對到不同成交案例；2026-07 已將
  站體推斷從「最近成交案抄入」改為「物件座標直接計算」，大幅改善同社區一致性。
- 本機管理中心不是多使用者 SaaS，也沒有公開雲端部署目標。

## 估價模型車位處理

房屋坪數不含車位。
房屋模型估算本體每坪價格；車位採用同版官方資料的類型中位價。
估計總價 = 房屋本體估值 + 車位估值。

訓練候選（`candidates/`）與 reports 為本機產生，已透過 `.gitignore` 排除；只有目前與上一個正式模型版本納入版本控制（見「公開儲存庫邊界」）。

## 設計決策

### 中古屋限定公開產品

預售屋底層資料保留供研究與資料沿革，但公開市場分析、估價、模型訓練與發布
只使用中古屋（resale）。理由：預售屋的合約價、工期延遲與成屋後交付不確定性
無法與實價登錄的成交價、完工日期直接類比。詳見
[issue-log §16](docs/project-issue-log.md)。

### 時間切割評估

模型評估使用嚴格的時間順序切割（train / calibration / test），不隨機打散。
訓練集、校準集、測試集依日期先後分離，確保模型評估的是「用過去資料預測未來」
的真實能力，而非用未來資訊洩漏假造好成績。

### 訓練與發布分離

每次訓練產出 immutable 候選（candidate），不會自動覆蓋正式模型（official）。
發布需要通過七項檢查（整體 MAE、各站 MAPE、A18 嚴格改善、年度回溯 ≥ 2/3、
回溯各站 ≥ 2/3、資料新鮮度、車位計價一致性），由管理者手動在管理端決定。

### LLM 只引用 Evidence Pack

Rule、Ollama 或 Gemini 只能引用 Evidence Pack 內已存在的 fact ID。
報告中的每個數字必須有對應的 fact 來源；schema 或 evidence validation 失敗時，
報告不會被判定成功。這確保 LLM 不編造價格、坪數等數值。

### 不可變 artifact 與回滾

模型、資料集以 immutable artifact 搭配版本發布流程管理。發布失敗時保留上一個
可用版本；配合 health check 與 backup-restore drill，確保更新不會破壞已上線產品。

### 站體特徵直接計算

估值模型的站體資訊（station_code、station_distance_m）必須從物件座標直接計算，
不能從最近成交案例抄入。同社區兩個物件座標差數百公尺 → 最近成交案不同 →
可能抄到不同捷運站 → 估值差異可達 28%。2026-07 修正後統一使用 Haversine
公式計算，確保同社區物件獲得一致的站體特徵。

### 對話內容兩層架構

AI 回應採用「對話內容 + 證據清單」兩層顯示：上層為 LLM 的自然對話回答
（可根據估值給出具體看法），下層為每條引用 fact ID 的驗證證據。最初基於
安全性考量只顯示證據，後來發現完全隱藏對話內容失去互動意義，改為並陳。

