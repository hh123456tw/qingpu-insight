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

## 開發

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check src tests
```
