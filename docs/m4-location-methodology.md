# M4.1 刊登定位品質控制

本文件定義 591 刊登批次的地址證據、定位、兩公里生活圈與發布控制。它補充而不取代 [M3 刊登資訊方法論](m3-listing-methodology.md)；所有命令以 `qingpu-data listing-build --help` 為準。

## 範圍與發布邊界

591 搜尋結果頁通常沒有可用的精確門牌。它可產生刊登卡片與來源座標，但不能從標題、社區名、行政區、站名或模糊地標推測地址或座標。

`listing-build` 只接受 `manifest.json` 為 `is_complete=true` 的既有 raw batch。預設是離線 build：不開 Chrome、不查外部 API，也不啟動官方門牌 geocoder。來源座標無效或不存在時，列保留為 `unknown`，而不是以站點或區域中心補值。

預售屋 detail enrichment 是明確 opt-in，且僅能在使用者有權瀏覽的、正常可見的 591 detail page 執行。它不呼叫私人端點、不繞過驗證。遇到驗證／阻擋頁、非允許網址或不安全 evidence 位置時，batch 會標為 `is_complete=false`，並以 `detail_enrichment_blocked` 記入 manifest；本次不寫 processed 資料或品質 JSON，不能發布。反之，正常頁面只是沒有經驗證的精確地址時，保留 `detail_address_missing` 診斷並繼續，該列仍是 unknown。

## 證據優先序與信心

同一列只採用可追溯、可驗證的最高優先證據：

1. 來源提供且通過台灣座標範圍檢查的座標：`location_method=source_coordinates`、`location_confidence=high`、`location_reason=valid_source_coordinates`。
2. 正規化地址與桃園官方門牌資料**精確且唯一**相符的結果：`location_method=structured_address`、`location_confidence=medium`；成功解析後生活圈理由會是 `eligible_structured_address`。
3. 人工證據：保留給未來的人工覆核契約（須同時保存可稽核來源、座標、信心與理由）。目前沒有 manual CLI 或輸入流程，不得把它描述成可用功能；資料層保留 `manual` 方法與 `eligible_manual` 理由的相容契約。
4. 其餘情況：`location_method=unknown`、`location_confidence=unknown`，並使用明確理由，例如 `missing_or_invalid_source_coordinates`、`missing_structured_address`、`detail_address_missing`、`address_not_resolved`、`geocoder_unavailable` 或 `invalid_geocoder_coordinates`。

地址 metadata 只在可驗證 detail page 有精確門牌時保存：`structured_address`、`address_source_url`、`address_observed_at`。來源座標是原始 provenance，絕不交給 geocoder 覆寫。

## Detail enrichment 與 manifest 生命週期

detail 功能預設關閉。它需要可見 Chrome（不可 headless）、專用而未被其他 Chrome 程序使用的 profile，以及 timeout；實際 flag 為：

```powershell
qingpu-data listing-build --batch-dir <complete-batch-dir> `
  --detail-enrichment-enabled `
  --profile-dir <dedicated-visible-chrome-profile> `
  --page-timeout 30
```

可與 `--geocoder-enabled` 放在同一次 `listing-build`：先以可見 detail page 補入合格結構化地址，再對尚未有來源座標的列做官方門牌精確解析。detail evidence 寫在該 raw batch 的 `details/<safe-listing-id>.html`；無地址但正常的頁面寫在 `details-diagnostic/<safe-listing-id>.html`。

如果 detail 被阻擋，該 manifest 已被改為不完整，不能直接重跑或發布；重新取得一個獲授權、完整的 input batch 後再 build。這個流程不會假設搜尋頁本身含有精確地址。

## 官方門牌 geocoder 與儲存

`--geocoder-enabled` 是 opt-in，僅使用本機 `data/raw/doorplates.csv` 的官方門牌資料做精確、唯一匹配。它不使用外部 geocoding API，沒有模糊比對、名稱猜測或 fallback 座標。無匹配或歧義保留 unknown。

這項 opt-in 必須設定 `QINGPU_DATABASE_URL`，因為成功的正規化地址結果要寫入 MySQL `geocode_cache` 作持久 cache；沒有 DB 或 DB 設定／連線錯誤時，指令受控地失敗且不發布。URL 只支援 `mysql://` 或 `mysql+pymysql://`，必須含 database 名稱；URL query parameters 和 fragment 都不支援。把密碼只放在本機環境變數，文件中的值一律是 placeholder：

```powershell
$env:QINGPU_DATABASE_URL = "mysql+pymysql://<user>:<password>@127.0.0.1:3306/<database>"
```

不要將真實密碼、Cookie、token 或完整連線字串提交到 Git、寫入命令列或 release evidence。

M4 runtime／jobs 設定 `QINGPU_DATABASE_URL` 時，MySQL listing tables 與 `geocode_cache` 是唯一正式的持久 runtime source of truth。`listing_snapshots.parquet` 只是由 repository 匯出的相容聚合／分析／重現快照，絕不取代 MySQL 的可變 runtime 狀態，也不是網站的 fallback。未設定 `QINGPU_DATABASE_URL` 的預設離線 build 則保留 M3 本機／Parquet 相容流程；它不是 M4 網站或 runtime 的正式 fallback，且不會暗中啟用 geocoder。

## 兩公里生活圈規則

站點由 A17、A18、A19 的官方門牌定位；若 station schema、座標或轉換後位置都無有效站點，定位程式受控地失敗，不以任意站點繼續。對有效刊登座標使用 WGS84 Haversine distance，邊界採**包含式**：`station_distance_m <= 2000` 才是 `location_eligible=true`。

有有效座標但在 2,000 m 外的列仍保存最近 `station_code` 與 `station_distance_m`，理由為 `outside_service_radius`，但 `location_eligible=false`。沒有有效座標的列沒有誤導性的最近站點或距離。只有 `source_coordinates`、`structured_address` 或未來 `manual` 證據在半徑內才可進入生活圈／模型指標。

## 品質 artifact 與資料欄位

成功的 `listing-build` 會寫入 `outputs/reports/listing-quality.json`；下列 processed file artifact 則依選取的 repository adapter 而定：

| 路徑 | 用途 |
|---|---|
| `outputs/reports/listing-quality.json` | 本次成功 build 的定位品質摘要 |
| `data/processed/current.parquet` | Parquet adapter 的最新快照 |
| `data/processed/snapshots/{batch_id}.parquet` | Parquet adapter 的單批次快照 |
| `data/processed/listing_snapshots.parquet` | repository 載入的聚合／相容快照 |

`listing-quality.json` 的 `location` 物件固定只有 `eligible`、`unknown`、`by_method`、`by_reason` 四個欄位。`by_method` 與 `by_reason` 是各自的計數 map。只有 detail enrichment 產生診斷時才有 `detail` 物件；目前的明確 key 是 `detail_address_missing`，值為計數。被 block 的 batch 不產生 processed snapshot 或此 quality artifact。

每個結構化列的 location provenance 欄位為 `structured_address`、`address_source_url`、`address_observed_at`、`location_method`、`location_confidence`、`location_reason`、`geocoded_at`、`geocoder_version`，並另有 `station_code`、`station_distance_m`、`location_eligible`。quality 計數可用來檢查未知或半徑外原因，但不是取得原始 detail HTML 的途徑。

## 隱私、保留與存取

raw detail HTML 可能含公開但像聯絡資料的文字。它只可留在本機、被 Git ignore 的 raw evidence 路徑，依資料保留期限刪除，且只授權必要操作者讀取。結構化 snapshot、quality JSON 與 API 不保存電話、姓名、email、Cookie 或帳密。Chrome profile 是敏感資料：使用專用的本機 profile，放在 repository 外或明確加入本機 ignore 規則，確認不受 Git 追蹤；不得分享或放入 artifact。原始 HTML 不得直接渲染給使用者。

## 失敗矩陣、驗收與 rollback

| 情況 | 行為 | 發布／復原 |
|---|---|---|
| manifest 不完整或缺頁 | 拒絕 build | 不發布；重新 scrape 完整 batch |
| detail verification／安全阻擋 | 將 manifest 標為不完整、記錄 `detail_enrichment_blocked` | 不產 processed／quality；重新取得可授權完整 batch |
| detail 正常但無精確地址 | 計數 `detail_address_missing`，列保留 unknown | 可發布其他合格列；不猜測地址 |
| geocoder 未設定 DB、URL 不合法或 MySQL 失敗 | 受控非零失敗 | 不發布；修正本機環境後用完整 input 重跑 |
| 官方門牌無精確唯一命中 | `address_not_resolved` | 列 unknown；不做 fuzzy／外部 fallback |
| station 無有效座標 | controlled failure | 不發布；修正官方門牌／station input |
| 超過 2 km | 保存最近站與距離、標記不合格 | 不進入生活圈指標；無需改寫座標 |

本 task 的 release evidence 只包含本機 test gate，沒有宣稱已進行真實 591 live acceptance。驗收時保存命令輸出與產生的 artifact 路徑，並以實際批次的 `eligible`、`unknown`、`by_method`、`by_reason` 與（如有）`detail.detail_address_missing` 檢查；不要在文件硬編造資料筆數。若 acceptance 失敗，停止發布、保留上一個已發布 MySQL 版本或既有 Parquet snapshot，修正 input／設定後以新完整 batch 重跑。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_listing_location.py tests/test_listing_repository.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```
