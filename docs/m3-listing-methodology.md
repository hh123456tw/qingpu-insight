# M3 刊登資訊方法論

## 1. 概述

M3 刊登資訊（Listing Intelligence）模組為青埔智價新增即時房源監控功能，涵蓋三大類型：

- **sale**（中古屋/成屋出售）
- **newhouse**（預售屋/新成屋出售）
- **rental**（租賃）

資料來源為 **591 房屋交易網**。M3 不涉及實價登錄或模型估價，僅專注於 591 公開刊登資訊的擷取、正規化、定位與變動偵測。

## 2. 環境需求

### 必要軟體

- **Chrome 瀏覽器**（最新穩定版）
- ChromeDriver（由 Selenium Manager 管理）
- Python >= 3.11

### 授權假設

M3 只瀏覽使用者獲准存取的 591 公開頁面，不呼叫私人端點，也不繞過驗證。可見 Chrome 是預設值；`--headless` 僅為 best-effort。若獲准頁面需要既有瀏覽器狀態，可用 `--profile-dir <path>` 將本機 Chrome user-data 目錄交給 Selenium；請使用專用且未被其他 Chrome 程序占用的目錄。帳號、密碼、Cookie 或聯絡欄位不進入 manifest、結構化快照或 API，密碼也不得出現在命令列；本機 raw HTML 可能重現公開頁面文字，必須留在忽略路徑並依資料保留政策刪除。

公開桃園路由：

| 類型 | 請求路由 |
|------|----------|
| sale | `https://sale.591.com.tw/?shType=list&regionid=6` |
| newhouse | `https://newhouse.591.com.tw/housing-list.html?regionid=6`（目前導向 `/list?regionid=6`） |
| rental | `https://rent.591.com.tw/list?region=6` |

### 環境變數

| 變數 | 用途 | 預設值 |
|------|------|--------|
| `QINGPU_DATABASE_URL` | MySQL 連線字串（mysql+pymysql://user:pass@host:port/db） | —（無，未設定時使用 Parquet） |

密碼若包含 `@`、`:`、`/` 等保留字元，必須先做 URL encoding；例如 `@` 寫成 `%40`。連線字串只放在本機環境變數，不要寫入 `.env`、文件或 Git。

## 3. CLI 工作流程

M3 提供三條 CLI 指令，可組合使用或一鍵完成。

### 3.1 listing-scrape（僅擷取原始頁面）

將 591 搜尋結果頁面原始 HTML 存入 `data/raw/listings/591/` 暫存區，**不進行正規化或定位**。

每頁只有在瀏覽器最終 URL 仍符合該類型的 591 host 與桃園參數（sale/newhouse 為 `regionid=6`、rental 為 `region=6`）時才會成為證據。翻頁後必須觀察到正規頁碼變更或穩定 listing ID 集合改變；只有其他 URL 字段變更而卡片未更新時，批次會以 `navigation_failed` 保留為不完整。

```powershell
# 擷取中古屋出售前 10 頁
.\.venv\Scripts\qingpu-data.exe listing-scrape --types sale --max-pages 10

# 同時擷取三種類型（各 5 頁）
.\.venv\Scripts\qingpu-data.exe listing-scrape --types sale newhouse rental --max-pages 5

# 使用已有的專用 Chrome user-data 目錄（仍為可見 Chrome）
.\.venv\Scripts\qingpu-data.exe listing-scrape --types rental --max-pages 3 --profile-dir C:\path\to\dedicated-profile

# headless 是 best-effort，不是預設值
.\.venv\Scripts\qingpu-data.exe listing-scrape --types sale --max-pages 1 --headless
```

### 3.2 listing-build（離線正規化與定位）

從 `data/raw/listings/591/` 中最新的原始批次讀取 HTML，解析、正規化、定位後存入 `data/processed/`，並更新 `listing_snapshots.parquet` 聚合快照。只有 `is_complete=true` 的 manifest 可進入 build；若擷取因 `--max-pages` 上限停止，必須保留原始 manifest 的 `false`，不得為了 build 改寫原始證據。

```powershell
# 處理最新批次
.\.venv\Scripts\qingpu-data.exe listing-build

# 指定目錄批次
.\.venv\Scripts\qingpu-data.exe listing-build --batch-dir data/raw/listings/591/2025-01-15/591-20250115T120000Z
```

### 3.3 listing-sync（一鍵同步：擷取 + 正規化 + 定位 + 事件偵測）

```powershell
# 一鍵完成全部流程
.\.venv\Scripts\qingpu-data.exe listing-sync --types sale newhouse rental --max-pages 10
```

`listing-sync` 自動執行：
1. 以 Selenium 擷取原始 HTML
2. 正規化為結構化欄位
3. 門牌定位（assign life circle）
4. 寫入 snapshot 與 current 資料表
5. 與前次快照比對，偵測事件
6. 追加事件記錄
7. 產出完整的 `listing_snapshots.parquet`

`--max-pages` 是安全上限，不代表已抵達搜尋結果末頁。因頁數上限而停止的批次會保留為不完整批次，且不會觸發下架判定；`listing-build` 也會拒絕處理不完整 manifest。

每次執行以 UTC 微秒建立並獨占新的 `{batch_id}` 目錄；若時間戳仍碰撞，使用序號後綴，不會讓兩個批次共用路徑。

## 4. 資料路徑

### 原始路徑（不提交 Git）

```
data/raw/listings/591/{date}/{batch_id}/
├── manifest.json          # 批次中繼資料
├── checkpoint.json        # 診斷用頁面進度（不提供自動續跑）
├── page-0001.html         # 原始 HTML
├── page-0002.html
└── ...
```

### 處理後路徑（本機產物，不提交 Git）

```
data/processed/
├── current.parquet        # 最新快照（upsert 語意）
├── listing_snapshots.parquet # 聚合歷史快照
├── events.parquet         # 事件記錄（event_key 唯一）
└── snapshots/
    ├── {batch_id}.parquet # 各批次歷史快照
    └── ...
```

### 表示法與 newhouse 範圍語意

- sale 與 rental 目前從渲染後 DOM 卡片解析，manifest 記錄 `representation=dom` 與對應 schema version。
- newhouse 優先從公開頁面的 `ItemList` / `Product` JSON-LD 解析；只有至少一筆通過驗證時才記錄 `representation=jsonld`。若 ItemList 全部被拒絕，系統保留 JSON-LD 拒絕診斷並嘗試 DOM fallback。
- JSON-LD `AggregateOffer.lowPrice` / `highPrice` 是每坪開價範圍；描述中的 `坪數規劃 low~high 坪` 是面積範圍。系統保留 low/high，不以中點冒充精確值，`asking_price_twd` 與 `building_area_ping` 維持空值。
- 公開 newhouse JSON-LD 未提供可靠座標時，`location_eligible=False`；這些列不進入 A17–A19 兩公里指標或 M2 開價比對，也不從建案名稱或標題猜測座標。

## 5. MySQL 遷移路徑

若設定 `QINGPU_DATABASE_URL`，可將 M3 資料存入 MySQL：

```sql
-- 所需表格由 MySQLListingRepository 自動建立
-- listing_batches, listing_snapshots, listing_current, listing_events
```

遷移順序：

1. 確保 M1 市場資料已完成 `mysql-load`
2. 設定 `QINGPU_DATABASE_URL`
3. 執行 `listing-sync`，系統自動建立 M3 表格並寫入

```powershell
$env:QINGPU_DATABASE_URL = "mysql+pymysql://user:URL_ENCODED_PASSWORD@127.0.0.1:3306/qingpu_insight"
```

## 6. API 路由

M3 在 `qingpu-web` 中新增以下 API：

| 路由 | 方法 | 說明 |
|------|------|------|
| `/api/listings/summary` | GET | 指定類型的刊登摘要（數量、價格區間） |
| `/api/listings` | GET | 指定類型的刊登列表（公開欄位） |
| `/api/listing-events` | GET | 指定類型的事件記錄 |

查詢參數：

- `listing_type`（必填）— `sale` / `newhouse` / `rental`
- `station`（可選，可重複）— `A17` / `A18` / `A19`
- `limit`（可選，預設 100，上限 100）

## 7. 事件定義

| 事件類型 | 觸發條件 |
|----------|----------|
| `listed` | 新的 listing 首次出現 |
| `relisted` | 曾離線的 listing 重新出現 |
| `delisted` | 連續兩次完整批次未出現（需批次為 `is_complete`） |
| `price_increase` | 同一 listing 價格上漲（含變化百分比） |
| `price_decrease` | 同一 listing 價格下跌（含變化百分比） |

事件以 `SHA-256(event_key)` 去重，確保重複執行不產生重複記錄。

## 8. 與 M2 的區隔

M3 與 M2（AI 估價）完全分離：

| 面向 | M2 估價 | M3 刊登 |
|------|---------|---------|
| 資料源 | 內政部實價登錄 | 591 公開房源 |
| 估價能力 | 合理區間、模型版本、可信度 | 僅比對開價 vs 模型（若可用） |
| 隱私 | 門牌座標去識別化 | 591 公開資料 |
| 輸出 | 估價 artifact、模型卡 | 快照、事件 |

Listing valuation（`listing_valuation.py`）在 M2 模型可用時，可為 sale/newhouse 房源附加估價比對。Rental 類型不支援估價。

## 9. 隱私規範

1. **不儲存** 591 帳號、密碼、Cookie、Session Token
2. manifest、結構化快照與 API **不保留** 聯絡人姓名、電話、Email 欄位；本機 raw HTML 仍可能包含公開頁面文字
3. **不儲存** 原始 HTML 在 Git 追蹤路徑內（`data/raw/listings/` 已排除）
4. **公開 API** 只回傳 `public_listings()` 定義的欄位，不包含內部 `raw_hash`、`batch_id` 等
5. `listing_metrics.py` 中的 `public_listings()` 已過濾掉所有非公開欄位
6. 座標經 `_round_coord()` 四捨五入至小數四位

## 10. 批次不完整與診斷檢查點

### 情境

Selenium 在擷取過程中斷（網路不穩、Chrome 崩潰、手動中斷），導致只有部分頁面的 HTML 被儲存。

### 處理機制

1. `RawBatchWriter.write_checkpoint()` 在每頁成功後寫入 `checkpoint.json`，作為診斷進度證據
2. `Selenium591Source` 不讀取既有 checkpoint，也不提供自動續跑；重新執行會從第一頁開始並建立新的隔離批次
3. 舊批次的 manifest、checkpoint 與已寫入頁面保持原樣；只有完整批次可由 `listing-build --batch-dir` 處理

### 事件一致性

- 不完整批次（`is_complete = False`）**不會**觸發 `delisted` 事件
- 遺漏的 listing 在非完整批次中被標記為 `active = True`，保留之前的 `consecutive_absences`
- 僅連續兩次完整批次未出現才視為下架

## 11. 2026-07-22 可見瀏覽器驗收證據

從隔離 worktree 執行下列無密碼命令：

```powershell
$env:PYTHONPATH = "<worktree>\src"
<python> -m qingpu_insight.cli listing-scrape --types sale newhouse rental --max-pages 1 --page-timeout 45 --delay-min 2 --delay-max 4
```

| 類型 | accepted / rejected | 表示法 | 原始 batch（`is_complete=false`） |
|------|---------------------|--------|----------------------------------|
| sale | 31 / 0 | DOM | `data/raw/listings/591/2026-07-21/591-sale-20260721T175130Z` |
| newhouse | 7 / 7 | JSON-LD | `data/raw/listings/591/2026-07-21/591-newhouse-20260721T175137Z` |
| rental | 30 / 0 | DOM | `data/raw/listings/591/2026-07-21/591-rental-20260721T175142Z` |

newhouse 的七筆拒絕包含四個非 `ItemList` JSON-LD 文件及三個沒有正值開價範圍的 Product；拒絕不會被當成成功資料。三份原始 manifest 都保留 `reached_terminal_page=false`、`is_complete=false`，raw batch 位於 `.gitignore` 排除路徑。

為驗證離線解析與 Parquet 持久化，將三個 batch 複製到 `data/raw/task-6-acceptance/`；頁面 SHA-256 與原始檔相同，只在**複製的** manifest 將 `reached_terminal_page`、`is_complete` 設為 `true`，再逐一執行：

```powershell
<python> -m qingpu_insight.cli listing-build --batch-dir data/raw/task-6-acceptance/591-sale-20260721T175130Z
<python> -m qingpu_insight.cli listing-build --batch-dir data/raw/task-6-acceptance/591-newhouse-20260721T175137Z
<python> -m qingpu_insight.cli listing-build --batch-dir data/raw/task-6-acceptance/591-rental-20260721T175142Z
```

三次 build 均為 exit 0。`data/processed/listing_snapshots.parquet` 共 68 列（31 sale、7 newhouse、30 rental），具有穩定 source ID、canonical HTTPS 591 URL、SHA-256 `raw_hash`、batch/snapshot/representation/schema 中繼資料，且無聯絡或憑證欄位。sale 保留 31 筆精確總價與面積，rental 保留 30 筆月租與面積；newhouse 保留七筆單價與面積範圍、沒有虛構精確值，七筆皆為 `location_eligible=False`。

## 12. 排程執行（M4）

定期自動擷取（如每日排程）屬於 **M4** 範疇，不在本版本實作範圍內。
