# 青埔智價 Qingpu Insight

以桃園機場捷運 A17～A19 生活圈為範圍的本機房價分析產品。專案整合官方實價登錄、
公開房屋刊登、scikit-learn 估價模型、Flask Web、MySQL 維運，以及有 fact ID
證據約束的 AI 買方報告。

本專案同時服務三個目標：

- **自用**：查詢青埔市場、比較刊登開價並產生單一物件買方報告。
- **AIPE04 期中專題**：展示資料工程、機器學習、資料庫、Web 與生成式 AI 整合。
- **求職作品集**：呈現一個從資料取得到本機產品化、測試與維運的完整 Python 專案。

> 本工具僅供資料分析與購屋參考，不構成正式不動產鑑價、投資建議或未來價格預測。

## 目前狀態

截至 2026-07-25：

- M0～M5 主要流程已完成。
- 模型觀測台：`http://127.0.0.1:5000/admin#models`（限本機存取）
  - 僅接受中古屋／預售屋／全部三種選擇
  - 訓練結果寫入 `artifacts/candidates/<run_id>/`
  - 絕不取代 `artifacts/resale.joblib` 或 `artifacts/presale.joblib`
  - 需要 `QINGPU_SECRET_KEY`、MySQL 與本機存取
- 完整測試：**999 tests passed**。
- 靜態檢查：**Ruff passed**。
- Rule Provider 真實 CLI smoke：`success=true`、Fact Accuracy `1.0`、Coverage `1.0`。
- 正式使用方式是 **Windows 本機執行**；公開雲端部署不在目前必要範圍。

## 管理操作中心

瀏覽器開啟 `http://127.0.0.1:5000` 後，首頁連結至 `/admin` 進入管理操作中心。  
所有管理端路由僅接受本機（127.0.0.1／localhost）存取。

```powershell
# 兩種啟動方式
.\.venv\Scripts\qingpu-web.exe
python -m qingpu_insight.web
```

### 八個分類

| 分類 | ID | 說明 |
|------|-----|------|
| 總覽 | `#admin-overview` | 系統就緒狀態、待辦事項 |
| 資料 | `#admin-data` | 一鍵資料更新（指定季度範圍，可選檢查點） |
| 刊登 | `#admin-listings` | 591 一鍵更新（中古屋→預售屋，固定順序執行） |
| 模型 | `#admin-models` | 模型訓練只建立候選，獨立發布／回滾 |
| LLM | `#admin-llm` | Rule／Ollama／Gemini 設定與 smoke test；LLM 固定案例 benchmark |
| 備份 | `#admin-backups` | 備份建立、隔離還原演練、正式資料庫還原（保留 control table） |
| 工作 | `#admin-jobs` | 各類工作歷史與狀態 |
| 診斷 | `#admin-diagnostics` | 診斷資訊 |

### 必要條件

- **MySQL 8** 必須可用才能進行任何資料異動操作（資料更新、刊登更新、模型訓練、備份等）。
- 唯讀功能（市場分析、估價、報告查閱）不需要 MySQL。
- `QINGPU_DATABASE_URL` 與 `QINGPU_SECRET_KEY`（至少 32 字元、三種字元類別）必須同時存在。

### 功能說明

**一鍵資料更新**  
指定季度範圍（如 `110S3`～`115S2`），可選從預設 `acquire`、`analyse`、`market_build` 或 `mysql_publish` 檢查點繼續。背景依序執行：下載官方資料 → 地理編碼 → 建立市場資料集 → 發布至 MySQL。

**591 一鍵更新**  
Web 一鍵更新固定依序執行 591 中古屋出售與預售新建案（sale → newhouse）。租屋不在本專題的 Web 操作主線；既有租屋資料與明確 CLI 相容入口不會被刪除。

**模型訓練**  
選擇 `resale`／`presale` 啟動訓練。可選擇加入自訂調參設定，系統會比較三組預設 profile（快速／平衡／精細）與選用的自訂 profile。結果寫入 `artifacts/candidates/<run_id>/`，**不會自動發布**。需透過發布預覽流程（輸入確認文字）手動發布，或選擇版本回滾。

**備份與還原**  
- 建立備份：建立 MySQL dump 至 `outputs/backups/`
- 隔離還原演練：在隔離資料庫驗證備份完整性
- 正式資料庫還原：需先取得預覽、輸入確認文字後提交；保留 `schema_migrations`、`health_log`、`backup_records` 等 control table 不被覆寫

**LLM 設定與測試**  
- Rule：離線規則式報告，不需 LLM
- Ollama：需設定 `QINGPU_OLLAMA_MODEL` 環境變數
- Gemini：可在頁面上設定／刪除 API Key（存入 `instance/secrets.env`），或透過 `QINGPU_GEMINI_API_KEY` 環境變數
- Smoke test：指定 provider 執行單次測試
- Benchmark：指定 provider 與模型名稱，執行 `benchmarks/m44_cases.json` 固定案例，產出 `outputs/m44-benchmark/<run_id>/`

### 安全限制

| 機制 | 說明 |
|------|------|
| 本機存取 | 所有管理端路由驗證 `REMOTE_ADDR` 與 `Host` header |
| CSRF | 所有資料異動 POST 需要 `X-Qingpu-CSRF` header 匹配 session token |
| 白名單參數 | API 只接受預先定義的欄位，拒絕未知或路徑參數 |
| Secret 強度 | `QINGPU_SECRET_KEY` 必須符合強度政策 |
| 錯誤遮罩 | 內部錯誤不洩漏 DB URL、SQL、traceback、HTML、電話 |

### 路徑

| 用途 | 路徑 |
|------|------|
| 官方候選模型 | `artifacts/candidates/<run_id>/` |
| 正式版本模型 | `artifacts/resale.joblib`、`artifacts/presale.joblib` |
| 備份 | `outputs/backups/` |
| Benchmark | `outputs/m44-benchmark/<run_id>/` |
| Secret | `instance/secrets.env`（不提交 Git） |
| 官方資料品質報告 | `outputs/admin/official-data/<run_id>/quality.json` |

## 五分鐘啟動

### 前置需求

- Windows 10/11
- Python 3.11 以上
- Chrome（需要更新 591 刊登時）
- MySQL 8（需要刊登發布、工作中心、維運與買方報告時）

### 1. 安裝（只在第一次建立環境時執行）

若專案已經有 `.venv`，先不要再次執行 `python -m venv .venv`。直接使用既有環境，
或確認 Python 版本後乾淨重建；不要用不同 Python 版本覆寫既有 `.venv`，否則可能留下
不相容的 NumPy／Pandas binary。

```powershell
cd C:\path\to\qingpu-insight
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 2. 啟動既有本機成果

若 `data/processed/` 已有處理後資料，且 `artifacts/` 已有估價模型：

```powershell
.\.venv\Scripts\qingpu-web.exe
```

開啟 <http://127.0.0.1:5000>。未設定 MySQL 時可展示市場分析與 Parquet 相容功能；
刊登發布、工作中心、維運與 M4.4 買方報告需要 MySQL。

### 3. 驗證安裝

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
下載 → 清理／定位 → 市場資料集 → 中古屋／預售屋估價模型
                              │
591 公開刊登 → 正規化／定位 ──┼→ MySQL 版本發布 → Flask Web
                              │
                              └→ Evidence Pack → Rule／Ollama／Gemini 報告
```

重要設計：

- 中古屋（`resale`）與預售屋（`presale`）始終分開分析與建模。
- 刊登價不會冒充成交價；租屋資料不會混入買賣實價比較。
- 估價由已評估的模型負責；LLM 只負責依 Evidence Pack 整理說明。
- 報告中的數字必須引用現有 fact ID；無效報告不會被 benchmark/smoke 判為成功。
- 買方報告目前一次只分析 **一個物件**，避免多物件證據交叉引用。

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

# 僅建立 M1 市場表格；完整 M4 migration 請見後文
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

| 變數 | 用途 | 預設值 |
|------|------|--------|
| `QINGPU_DATABASE_URL` | MySQL 連線字串（mysql+pymysql://user:pass@host:port/db） | —（無，未設定時使用 Parquet） |

### 生成檔案說明

| 檔案 | 階段 | 說明 |
|------|------|------|
| `data/raw/manifest.json` | acquire | 下載記錄清單 |
| `data/processed/transactions.parquet` | analyse (M0) | 地址定位後完整交易資料 |
| `data/processed/market_transactions.parquet` | market-build (M1) | 清理後市場分析用交易資料 |
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

### 中古屋（resale）與預售屋（presale）分離

中古屋與預售屋因價格形成機制、單價計算方式與市場行為不同，在本專案中**始終分開分析**，不會合併為單一價格指標。

## M2 AI 估價工作流程

### 模型訓練

```powershell
# 建立已驗證的 M1 特徵來源
.\.venv\Scripts\qingpu-data market-build

# 訓練並產出中古屋與預售屋各自獨立的 artifact
.\.venv\Scripts\qingpu-data model-train
```

輸出成果：

| 檔案 | 階段 | 說明 |
|------|------|------|
| `artifacts/resale.joblib` | model-train | 中古屋估價 artifact（不提交 Git） |
| `artifacts/presale.joblib` | model-train | 預售屋估價 artifact（不提交 Git） |
| `outputs/reports/resale-evaluation.json` | model-train | 中古屋候選模型評估報告 |
| `outputs/reports/presale-evaluation.json` | model-train | 預售屋候選模型評估報告 |
| `outputs/reports/resale-model-card.md` | model-train | 中古屋模型卡 |
| `outputs/reports/presale-model-card.md` | model-train | 預售屋模型卡 |

Web 操作頁支援三組固定調參設定（快速／平衡／精細）及選用自訂設定，
自動在校準集比較各 profile 結果、鎖定最佳候選，並在測試集隔離驗證。
訓練結果以摘要卡片（MAE、MAPE、覆蓋率、baseline delta）優先呈現，
完整指標與模型比較收合在可展開的進階區塊。

### 啟用估價產品

```powershell
.\.venv\Scripts\qingpu-web
```

首頁新增「AI 條件估價」面板，支援中古屋與預售屋估價。估價結果包含合理區間、可信度、影響因素、相似成交與開價評估。

### 方法論文件

請參閱 [docs/m2-valuation-methodology.md](docs/m2-valuation-methodology.md)。

### 中古屋模型證據工作流程

中古屋（resale）估價模型在訓練與發布時，會額外執行完整的證據檢查，確保模型符合最低品質門檻。

**資料狀態**
- 資料範圍：訓練資料的時間區間（min_date～max_date）、各站點（A17/A18/A19）與類型（resale/presale）的可用交易筆數
- 日期語義：`data_max_date` 是訓練資料最後一筆交易的日期，也是模型「知道」的最後日期

**候選模型家族**
現有候選模型為 `Ridge`、`RandomForest`、`HistGradientBoosting` 及 `RecentMedianBaseline`（僅作為基準線，不作為正式模型發布）。**XGBoost 被刻意排除**：本專案的資料規模（數千筆）不需要 XGBoost 的分散式加速優勢，且 `HistGradientBoostingRegressor` 在相同資料上已達到可比或更好的表現，同時減少相依套件數量。

**衍生特徵**
在中古屋基本特徵（車站距離、坪數、類型、樓層、車位等 15 個欄位）之上，新增五個衍生特徵：
- `transaction_month_index`：交易年月數值，捕獲長期時間趨勢
- `station_building_type`：車站與建物類型交互項
- `building_age_band`：屋齡分群（0–5、5–10、10–20、20+ 年）
- `area_band`：坪數分群（small ≤ 20、standard > 20 且 ≤ 50、large > 50 坪）
- `floor_band`：樓層比三分群（low、middle、high）

中古屋訓練使用所選 profile 的**近期交易半衰期權重**（內建預設為 48 個月），愈近期的交易權重愈高，權重下限為 0.10；預售屋維持原本不加權流程。

**嚴格時間切割**
所有評估使用嚴格的時間順序切割：
- 訓練集：最新日期往前推 18 個月以前的所有交易
- 校準集：訓練集結束後 6 個月
- 測試集：最後 12 個月

三組各自至少 100 筆交易，否則訓練失敗。

**三次年度回溯測試**
中古屋模型自動執行三次年度回溯測試：以最後資料日期為基準，逐年往過去推移三個不同的截止日期，每次重新訓練並驗證候選模型是否能通過發布閘門。每次回溯記錄 `passed`（候選是否勝過基準線）與 `stations_within_limit`（各站 MAPE 是否在基準線 110% 以內）。

**發布閘門（Release Gate）**
候選模型要獲得 `recommended` 狀態，必須同時滿足以下六項條件：
1. **MAE 改善 ≥ 2%**：整體 MAE ≤ 基準線 MAE × 0.98
2. **各站 MAPE < 10% 倒退**：所有已發布車站的 MAPE ≤ 基準線 × 1.10
3. **A18 嚴格不倒退**：A18 車站的 MAPE 必須嚴格低於基準線（`<`，而非 `≤`）
4. **回溯測試通過 ≥ 2/3**：三次年度回溯中至少兩次 `passed = true`
5. **回溯各站皆在限制內**：所有回溯的 `stations_within_limit` 皆為 `true`
6. **資料新鮮度**：`data_max_date` 距最新官方資料日期不超過 180 天

以上六項全部通過，`recommended` 設為 `true`，管理端才允許發布。

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

# 3. 開啟觀測台檢查 A18 MAPE 與回溯測試
# 瀏覽器開啟 http://127.0.0.1:5000/admin#models
# 找到最新候選 run，展開檢視 release_checks 與 backtests

# 4. 若 recommended = true，取得發布預覽
# 在管理端「模型」頁面點擊「發布預覽」，確認變更內容

# 5. 輸入確認文字提交發布
# 系統執行 smoke test → 複製 artifact → 更新 current pointer → 寫入版本記錄
# 發布後觀測台顯示新版本已啟用

# 僅在所有 release_checks 通過且 recommended = true 時才能發布；
# 任何一項不滿足，管理端不會釋出發布選項。
```

**已知限制**
- 本模型估價的是**當前的合理價格**，不具備未來價格預測能力
- 模型未納入總體經濟指標（利率、通膨、政策變化）
- 預售屋（presale）模型不執行回溯測試與衍生特徵實驗，僅使用基本特徵與標準時間切割

## M3 刊登資訊工作流程

### 前提

- Chrome 瀏覽器（最新穩定版）
- 預設開啟可見 Chrome；`--headless` 僅為 best-effort，591 頁面或驗證流程可能不支援
- `--profile-dir <path>` 會把本機 Chrome user-data 目錄傳給 Selenium；請使用專用且未被其他 Chrome 程序占用的目錄
- 系統不刻意抽取 591 帳號、密碼、Cookie 或專用聯絡欄位；不要把密碼放在命令列。build/sync 會阻擋 title 中可辨識的電話或 email 形狀，但無法辨識所有人名或混淆文字；raw HTML 必須留在本機忽略路徑並依資料保留政策刪除

三種公開桃園路由分別為：

- sale：`https://sale.591.com.tw/?shType=list&regionid=6`
- newhouse：`https://newhouse.591.com.tw/housing-list.html?regionid=6`（目前會導向 `/list?regionid=6`）
- rental：`https://rent.591.com.tw/list?region=6`

### 三種 CLI 模式

```powershell
# 僅擷取原始 HTML 頁面（存入 data/raw/listings/591/）
.\.venv\Scripts\qingpu-data.exe listing-scrape --types sale --max-pages 10

# 使用既有的本機 Chrome user-data 目錄；仍維持可見瀏覽器
.\.venv\Scripts\qingpu-data.exe listing-scrape --types rental --max-pages 3 --profile-dir C:\path\to\dedicated-profile

# headless 是 best-effort 選項
.\.venv\Scripts\qingpu-data.exe listing-scrape --types sale --max-pages 1 --headless

# 離線正規化與定位最新原始批次
.\.venv\Scripts\qingpu-data.exe listing-build

# 一鍵同步：擷取 → 正規化 → 定位 → 事件偵測
.\.venv\Scripts\qingpu-data.exe listing-sync --types sale newhouse rental --max-pages 10
```

`--max-pages` 是擷取上限；尚未抵達末頁的批次會標記為不完整，且不參與下架判定。每次重跑都會建立新的隔離批次目錄；`checkpoint.json` 只記錄診斷用的頁面進度，不提供自動續跑，也不會重用舊批次。設定 `QINGPU_DATABASE_URL` 可改用 MySQL；密碼中的 `@` 等保留字元需先做 URL encoding。

2026-07-22 的可見 Chrome 一頁驗收命令為：

```powershell
python -m qingpu_insight.cli listing-scrape --types sale newhouse rental --max-pages 1 --page-timeout 45 --delay-min 2 --delay-max 4
```

原始證據（皆維持 `is_complete=false`）位於：

- `data/raw/listings/591/2026-07-21/591-sale-20260721T175130Z`：31 accepted、0 rejected、DOM
- `data/raw/listings/591/2026-07-21/591-newhouse-20260721T175137Z`：7 accepted、7 rejected、JSON-LD
- `data/raw/listings/591/2026-07-21/591-rental-20260721T175142Z`：30 accepted、0 rejected、DOM

newhouse 的 JSON-LD 提供每坪單價 `lowPrice` / `highPrice` 與坪數範圍，而非單一總價；無可靠座標的建案保留 `location_eligible=False`，不進入 A17–A19 指標或估價，不以標題猜測位置。

### 事件類型

| 事件 | 說明 |
|------|------|
| `listed` | 新刊登出現 |
| `relisted` | 曾下架後重新刊登 |
| `delisted` | 連續兩次完整批次未出現 |
| `price_increase` | 開價上漲 |
| `price_decrease` | 開價下跌 |

### 資料儲存

| 檔案 | 階段 | 說明 |
|------|------|------|
| `data/raw/listings/591/{date}/{batch}/` | scrape | 原始 HTML 批次（不提交 Git） |
| `data/processed/current.parquet` | build / sync | 最新快照 |
| `data/processed/snapshots/{batch}.parquet` | build / sync | 歷史批次快照 |
| `data/processed/listing_snapshots.parquet` | build / sync | 聚合歷史快照 |
| `data/processed/events.parquet` | sync | 事件記錄（SHA-256 去重） |

### API 路由

| 路由 | 說明 |
|------|------|
| `/api/listings/summary` | 刊登摘要（數量、價格區間） |
| `/api/listings` | 刊登列表 |
| `/api/listing-events` | 事件記錄 |

### 隱私邊界

- 原始 HTML **不提交 Git**（`data/raw/listings/` 已排除）
- 公開 API 回傳已過濾欄位，不包含 `raw_hash`、`batch_id` 等內部欄位
- 座標經四捨五入至小數四位
- 結構化 schema 沒有專用聯絡欄位，也不刻意抽取聯絡資訊；build/sync 會阻擋 title 中可辨識的電話或 email 形狀，但 title 與本機 raw HTML 仍須抽樣稽核，不能宣稱已涵蓋所有人名或刻意混淆的 contact-shaped text
- 不儲存帳號密碼；Chrome profile 與 raw HTML 視為敏感本機資料

### 方法論文件

請參閱 [docs/m3-listing-methodology.md](docs/m3-listing-methodology.md)。

## M4.2 Windows 本機更新工作中心

M4.2 的正式路徑是 Windows 本機 Flask → 單一背景 worker → 可見 Selenium/591 →
run-specific Parquet → MySQL versioned staging → 單一 transaction 發布。Web request 只負責驗證、建立
job 與 handoff，成功時立即回 `202`，不等待 Chrome。Chrome 刻意維持可見，讓操作人員可以處理授權範圍內的
人工驗證或辨識驗證頁；M4.2 不以 headless 模式規避網站控制，也不需要 Ollama 或 Gemini。LLM 是後續智慧層，
不是三類 ingestion、驗證或 atomic publish 的必要條件。

### 必要設定與資料庫 migration

管理端只在 `QINGPU_DATABASE_URL` 與符合格式政策、至少 32 個字元的 `QINGPU_SECRET_KEY` 同時存在時啟用。
Secret 必須涵蓋至少三種字元類別且有足夠字元多樣性；由安全亂數產生器建立的 64–128 位 hex token 亦可。
週期／重複模式、常見連續字串、`change-me`、placeholder 與 `dev-secret-key` 都會 fail closed。此政策只拒絕可明確辨識的弱格式，不宣稱估算 secret entropy。請勿使用真實密碼範例或把 secret 提交到 Git。正式工作中心固定只綁
`127.0.0.1`；`QINGPU_PORT` 必須是可用的本機 TCP port。工作中心啟用時保持
`QINGPU_DEBUG=0`，避免 Flask debug reloader 建立第二個 app/executor。以下全部是 placeholder：

```powershell
$env:QINGPU_DATABASE_URL = "mysql+pymysql://<user>:<url-encoded-password>@127.0.0.1:3306/<database>"
$env:QINGPU_SECRET_KEY = "<at-least-32-cryptographically-random-characters>"
$env:QINGPU_PORT = "5000"
$env:QINGPU_DEBUG = "0"
```

MySQL 8 migration 必須依序套用。PowerShell 不支援 Linux 式的 `< file.sql`；
以下把密碼暫存在目前 process 的環境變數，執行完成後立即移除，不把密碼寫進文件或 Git：

```powershell
$env:MYSQL_PWD = Read-Host "MySQL password"
Get-Content -Raw -Encoding UTF8 database/001_market_schema.sql |
  mysql -h 127.0.0.1 -u <user> <database>
Get-Content -Raw -Encoding UTF8 database/002_add_valuation_columns.sql |
  mysql -h 127.0.0.1 -u <user> <database>
Get-Content -Raw -Encoding UTF8 database/003_listing_intelligence_schema.sql |
  mysql -h 127.0.0.1 -u <user> <database>
Get-Content -Raw -Encoding UTF8 database/004_listing_range_fields.sql |
  mysql -h 127.0.0.1 -u <user> <database>
Get-Content -Raw -Encoding UTF8 database/004_m4_jobs_publishing_schema.sql |
  mysql -h 127.0.0.1 -u <user> <database>
Get-Content -Raw -Encoding UTF8 database/005_m43_health_backup_schema.sql |
  mysql -h 127.0.0.1 -u <user> <database>
Get-Content -Raw -Encoding UTF8 database/006_m44_reports_schema.sql |
  mysql -h 127.0.0.1 -u <user> <database>
Remove-Item Env:MYSQL_PWD
```

正式 artifact 寫入 `data/processed/listing_versions/<version>.parquet`；advisory lock 位於
`data/locks/listing_update.lock`。591 原始證據仍只留在 `data/raw/listings/591/`，報告留在
`outputs/reports/`。這些本機資料／artifact 目錄不是 secret store，也不應公開。

### 啟動、手動更新與查詢

前景 CLI 與 Web 共用同一套 Task-3 組裝；兩者都建立每次操作獨立的 MySQL connection，並要求真實、可見的
Selenium preparation runner：

```powershell
# 前景執行完整三類更新
.\.venv\Scripts\qingpu-data.exe listing-update --types sale newhouse rental --max-pages 10

# 查詢單一 job
.\.venv\Scripts\qingpu-data.exe job-status --run-id <uuid>

# 啟動本機工作中心（只監聽 127.0.0.1）
.\.venv\Scripts\qingpu-web.exe
```

首頁的「刊登更新」按鈕會送出帶 session CSRF token 的 JSON POST。也可在同一個瀏覽器 session 內用開發工具
手動送出；不要把 CSRF token 複製到文件：

```text
POST /api/admin/listing-updates
Content-Type: application/json
X-Qingpu-CSRF: <current-session-token>

{"types":["sale","newhouse"],"max_pages":1,"trigger":"manual"}
```

查詢 API 為 `GET /api/jobs/<uuid>` 與 `GET /api/jobs?limit=<1..100>`。回應只包含
run/job type/status/trigger/attempt/timestamps/input-output version/summary，以及經過遮罩的 stable error
code/message；不包含 DB URL、SQL、traceback、HTML、電話或內部 repository 欄位。狀態生命週期為
`pending → running → succeeded|retry_wait|skipped|failed`，必要時再進入 `needs_attention`。

### 發布、重試與復原語義

- `sale` 與 `newhouse` 先全部 preparation 成功且每類至少一筆，才產生一個 immutable artifact；
  stage 及 publish 各一次。artifact hash/count/schema、任一類完整性、runtime row/event 或 pointer update
  失敗，都 rollback 並保留上一個 published dataset。
- exact active request 使用同一 idempotency key：回既有 run 並監看，不重複 enqueue、capture 或 publish。
  terminal failure 可安全重送；version ownership 不覆寫，事件以 stable event key `INSERT IGNORE` 去重。
- startup/handoff 失敗會用安全的 `startup_failed` 類型 terminalize 可觀察的 job，不讓該次新 run 永久卡在
  `pending`；process-owned advisory lock 會在 handle close/process exit 自動釋放。若 process 在 durable
  `pending/running` 寫入後直接崩潰，目前不做猜測式、按時間自動成功或重跑：操作人員須先從 job history 確認為
  stale，再用受控維護流程依法轉成 `running → failed → needs_attention`，之後才重送；絕不能直接移動 pointer。
- Web polling 每次 fetch settle 後才用 bounded `setTimeout` 排下一次，不會重疊；terminal 或超過 bounded
  failure/attempt policy 一律停止並重新啟用按鈕。
- 安全邊界同時驗證 socket remote address 是 loopback、`Host` 是 `localhost`/`127.0.0.1`/`[::1]`
  （可含設定 port），且 POST 的 CSRF header 必須精確符合 session。系統不信任 forwarded headers。
- executor 由 app/main process 擁有；正常離開會走 idempotent shutdown 並等待 worker，測試也必須明確 shutdown，
  不遺留 thread。

## M4.3 維運 CLI 與唯讀 Web 摘要

### CLI 命令

```powershell
# 執行本機健康檢查（MySQL 連線、資料集、備份等）
.\.venv\Scripts\qingpu-data.exe health-run

# 建立 MySQL dump 備份
.\.venv\Scripts\qingpu-data.exe backup-create

# 在隔離資料庫驗證備份
.\.venv\Scripts\qingpu-data.exe backup-restore-drill --backup-id <uuid>
```

### 環境變數

| 變數 | 用途 | 預設值 |
|------|------|--------|
| `QINGPU_DATABASE_URL` | MySQL 連線字串（mysql+pymysql://user:pass@host:port/db） | —（必要） |

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
# 更新刊登資料（最少 1 頁）
.\.venv\Scripts\qingpu-data.exe listing-update --types sale newhouse rental --max-pages 1

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
$env:QINGPU_OLLAMA_MODEL = "gemma3:4b"
$env:QINGPU_GEMINI_API_KEY = "<your-key>"
$env:QINGPU_GEMINI_MODEL = "<available-model-id>"
```

### Smoke 與 Benchmark

```powershell
# Rule smoke（不需 LLM）
.\.venv\Scripts\qingpu-data.exe llm-smoke --provider rule --model rule --output-dir outputs/m44-benchmark

# Ollama benchmark（需要已安裝的模型）
$env:M44_BENCHMARK_MODELS = ((ollama list | Select-Object -Skip 1 -First 1) -split '\s+')[0]
.\.venv\Scripts\qingpu-data.exe llm-benchmark --cases benchmarks/m44_cases.json --models-env M44_BENCHMARK_MODELS --provider ollama --output-dir outputs/m44-benchmark

# Gemini benchmark；每個 --models 值都會真的建立對應模型 provider
.\.venv\Scripts\qingpu-data.exe llm-benchmark `
  --cases benchmarks/m44_cases.json `
  --models "<available-model-id>" `
  --provider gemini `
  --output-dir outputs/m44-benchmark
```

Benchmark artifact 位於：

- `outputs/m44-benchmark/benchmark_results.json`
- `outputs/m44-benchmark/benchmark_results.md`
- `outputs/m44-benchmark/smoke_result.json`

每筆結果同時保留 requested 與 actual provider/model。只有 schema 與 evidence validation
都通過才算成功；所有案例失敗或沒有結果時，CLI 以非零 exit code 結束。

## 驗收

### 自動驗收

```powershell
$env:PYTHONPATH = (Resolve-Path "src").Path
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

2026-07-25 最近一次結果：

- pytest：999 tests passed
- Ruff：All checks passed
- `git diff --check`：passed
- Rule CLI smoke：passed

### 展示前人工驗收

自動測試不取代真實本機環境，期中展示或作品集錄影前仍需完成：

1. 在真實 MySQL 套用 001～006 migrations（兩個 004 都要執行）。
2. 以可見 Chrome 跑一次 `listing-update --types sale newhouse rental --max-pages 1`。
3. 執行 `health-run`，確認目前 published dataset 與備份狀態。
4. 建立一次真實備份，並以 `backup-restore-drill --backup-id <uuid>` 驗證。
5. 實際操作市場分析、估價與一筆 Rule 買方報告。
6. 若要展示 Ollama／Gemini，先在同一台展示電腦跑 smoke 或 benchmark。

## 期中報告與求職作品集方向

### 期中報告主線

建議以這句話開場：

> 我把分散的實價登錄與房屋刊登資料，整理成一個能查市場、估合理價格，
> 並產生可追溯買方報告的青埔購屋決策工具。

8～10 頁簡報可依序說明：

1. 購屋者面對的資料分散與價格判讀問題。
2. A17～A19 範圍、資料來源與授權邊界。
3. 下載、清理、定位、版本發布的資料流程。
4. 中古屋／預售屋分流與時間切分。
5. 候選模型、release gate 與真實 MAE／MAPE／R²。
6. Web 市場查詢與估價 Demo。
7. 刊登更新與單一物件買方報告 Demo。
8. Evidence Pack／fact ID 如何限制 LLM 幻覺。
9. 999 個測試、隱私措施與已知限制。
10. 結論與下一步。

簡報中的資料筆數、最新日期與模型指標必須來自 `outputs/reports/`、
model card 或 benchmark artifact，不自行補數字。

### 求職作品集定位

建議定位為「Python 資料／AI 應用工程師作品」，不要描述成已上線的全台房產平台。

履歷範例：

> 建置青埔 A17～A19 房價分析產品，串接官方實價登錄與公開刊登資料，
> 完成 Pandas ETL、MySQL 資料版本、scikit-learn 估價模型訓練與比較、
> Flask Web，以及具 fact ID 驗證的 LLM 買方報告。

公開作品集建議包含：

- 一張系統架構圖。
- 3～5 張去識別化畫面。
- 2～3 分鐘本機 Demo 影片。
- 真實模型評估、模型卡與限制。
- 可重現的安裝、測試與 smoke 指令。
- 不提交密碼、Cookie、聯絡資訊、備份、原始 HTML、資料集與模型 artifact。

面試時最值得深入說明的四個決策：

1. 為何中古屋與預售屋必須分開。
2. 為何模型評估使用時間切分。
3. 為何 LLM 使用 Evidence Pack／fact ID。
4. 如何用 immutable artifact、published version、health check 與 restore drill
   降低資料更新風險。

## M4.2 acceptance

Deterministic release gate 不連真實 591、Selenium 或 MySQL，仍會用真實 Task-1/2/3 services、executor 與
transactional state fakes 驗證 v1 → v2、active dedupe、事件重試去重，以及 preparation/artifact/stage/runtime/
pointer 五個 v3 failure boundary 都保留 v2：

```powershell
$env:PYTHONPATH = (Resolve-Path "src").Path
.\.venv\Scripts\python.exe -m pytest tests/test_web.py tests/test_m42_release_gate.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

這個 deterministic gate 不能取代有授權的 live 591/visible-Chrome smoke 或真正 MySQL migration rehearsal；
live 驗收需由操作人員在本機可見瀏覽器觀察三類完整 capture，再以 job detail 與 published version 核對結果。

## M4.1 刊登定位品質 smoke

`listing-build` 的所有模式（包含預設離線、geocoder 與 detail）都需要已完成（`is_complete=true`）的 raw batch **及** `data/raw/doorplates.csv`。目前沒有單獨下載門牌的 CLI；請先執行既有 [M0 工作流程](#m0-工作流程) 的 `qingpu-data acquire`，它會從桃園官方資料來源建立此 CSV（也會下載 M0 的交易輸入）。檔案必須保留官方欄位／schema，讓既有門牌 ingest 建立區域、正規化地址與 TWD97 座標。

`listing-build` 預設保留 M3 的本機／Parquet 相容流程：不開 Chrome、不啟用 geocoder。下列 `$batchDir` 請改成實際完整批次；不要把真實密碼放進指令、文件或 Git。

```powershell
$batchDir = "data/raw/listings/591/<YYYY-MM-DD>/<complete-batch-id>"

# 預設離線 build：只在 child pwsh 清除 DB env，不改變目前 shell
pwsh -NoProfile -Command {
  param($inputBatch)
  $env:QINGPU_DATABASE_URL = $null
  & .\.venv\Scripts\qingpu-data.exe listing-build --batch-dir $inputBatch
} -args $batchDir

# 選用：官方門牌精確 geocoder；需 MySQL 作 persistent cache
$env:QINGPU_DATABASE_URL = "mysql+pymysql://<user>:<password>@127.0.0.1:3306/<database>"
.\.venv\Scripts\qingpu-data.exe listing-build --batch-dir $batchDir --geocoder-enabled

# 選用：授權的可見 Chrome 預售屋 detail enrichment；可與 geocoder 同次執行
.\.venv\Scripts\qingpu-data.exe listing-build --batch-dir $batchDir `
  --detail-enrichment-enabled `
  --profile-dir "C:\path\to\dedicated-visible-profile" `
  --page-timeout 30 `
  --geocoder-enabled
```

detail enrichment 被 591 驗證頁阻擋時會把 input manifest 標為不完整，本次不會產生 processed／quality 輸出；請先取得新的完整且獲授權 batch 再重試。正常 detail page 沒有精確地址只會留下診斷，不能猜測地址或座標。

在 M4 runtime／jobs 設定 `QINGPU_DATABASE_URL` 時，MySQL 是正式的 runtime source of truth；`listing_snapshots.parquet` 是匯出／重現相容快照，不是 runtime fallback。無 DB 的預設離線命令僅用於保留的本機 M3／Parquet 相容流程。

### Release evidence（本機 gate）

本 task 沒有宣稱已執行真實 591 live acceptance。提交前執行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_listing_location.py tests/test_listing_repository.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

成功 build 的 evidence 為 `outputs/reports/listing-quality.json` 及對應的 processed snapshot；以實際輸出的 `location.eligible`、`location.unknown`、`location.by_method`、`location.by_reason` 和（如有）`detail.detail_address_missing` 驗收，不硬編造資料筆數。完整規則與 rollback 見 [docs/m4-location-methodology.md](docs/m4-location-methodology.md)。

## 開發

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
```
