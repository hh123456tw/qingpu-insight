# 青埔智價 Qingpu Insight

以桃園機場捷運 A17～A19 生活圈為範圍的房價分析與 AI 估價專案。本倉庫實作 M0 資料可行性工作流程。

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
$env:QINGPU_DATABASE_URL = "mysql+pymysql://qingpu:password@127.0.0.1:3306/qingpu_insight"

# 建立資料庫表格
mysql -u root -p < database/001_market_schema.sql

# 載入市場資料到 MySQL
.\.venv\Scripts\qingpu-data mysql-load

# 啟動網頁伺服器（自動使用 MySQL 資料源）
.\.venv\Scripts\qingpu-web
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

### 啟用估價產品

```powershell
.\.venv\Scripts\qingpu-web
```

首頁新增「AI 條件估價」面板，支援中古屋與預售屋估價。估價結果包含合理區間、可信度、影響因素、相似成交與開價評估。

### 方法論文件

請參閱 [docs/m2-valuation-methodology.md](docs/m2-valuation-methodology.md)。

## M3 刊登資訊工作流程

### 前提

- Chrome 瀏覽器（最新穩定版）
- 預設開啟可見 Chrome；`--headless` 僅為 best-effort，591 頁面或驗證流程可能不支援
- `--profile-dir <path>` 會把本機 Chrome user-data 目錄傳給 Selenium；請使用專用且未被其他 Chrome 程序占用的目錄
- 系統不把 591 帳號、密碼、Cookie 或聯絡欄位寫入 manifest、結構化快照或 API；不要把密碼放在命令列。原始 HTML 可能重現公開頁面文字，必須留在本機忽略路徑並依資料保留政策刪除

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
- manifest、結構化快照與 API 不保留聯絡欄位；本機 raw HTML 仍可能包含公開頁面文字
- 不儲存帳號密碼

### 方法論文件

請參閱 [docs/m3-listing-methodology.md](docs/m3-listing-methodology.md)。

## M4.1 刊登定位品質 smoke

`listing-build` 預設保留 M3 的本機／Parquet 相容流程：不開 Chrome、不啟用 geocoder，且需要一個已完成（`is_complete=true`）的 raw batch。下列 `$batchDir` 請改成實際完整批次；不要把真實密碼放進指令、文件或 Git。

```powershell
$batchDir = "data/raw/listings/591/<YYYY-MM-DD>/<complete-batch-id>"

# 預設離線 build（未設定 QINGPU_DATABASE_URL）
Remove-Item Env:QINGPU_DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\qingpu-data.exe listing-build --batch-dir $batchDir

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
