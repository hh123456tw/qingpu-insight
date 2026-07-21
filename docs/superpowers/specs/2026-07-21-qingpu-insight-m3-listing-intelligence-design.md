# 青埔智價 M3 售屋情報設計

## 1. 目標

M3 在既有 M0～M2 官方成交分析與 AI 估價之上，加入經授權的 591 刊登資料取得、每日快照、物件狀態變化與開價比較。第一版支援中古屋售屋、新建案及租屋三種來源，其中中古屋售屋是主要產品；三種資料必須分流保存與分析。

本專案擁有者已確認取得 591 對上述三類資料的自動取得授權，授權允許瀏覽器自動化，且未指定請求頻率限制。591 沒有提供本專案可使用的正式 API，因此 M3 只從授權的正常網頁流程讀取渲染後 DOM。專案仍採保守節流，不提交帳號、Cookie、token 或授權文件。

## 2. 成功條件

1. 單一 CLI 流程可取得三類刊登資料並保存不可變的原始快照。
2. 每筆正規化資料都包含來源、來源物件 ID、刊登類型、抓取時間、原始 URL 與價格。
3. 只有可可靠定位在 A17、A18 或 A19 兩公里內的物件進入正式指標。
4. 同一來源物件跨次快照可辨識新增、降價、漲價、恢復刊登與下架。
5. 中古屋售屋可與 M2 中古屋模型比較；新建案可與 M2 預售屋模型比較；租屋不呼叫售價模型。
6. 刊登開價不得進入 M2 官方成交模型的訓練資料。
7. 來源失敗、頁面變更、欄位缺失與不完整批次不得被誤判為大量下架。

## 3. 範圍與非目標

### 3.1 M3 範圍

- 591 中古屋售屋、新建案、租屋資料取得。
- 使用 Selenium 操作授權範圍內的正常網頁流程並讀取渲染後 DOM。
- 原始快照、正規化、品質報告、A17～A19 地理篩選。
- 刊登生命週期、價格事件、開價與 M2 合理區間比較。
- M3 查詢 API 與首頁刊登情報面板。

### 3.2 非目標

- 不使用刊登資料重新訓練 M2 模型。
- 不預測未來成交價或成交機率。
- 不收集聯絡人姓名、電話、訊息內容或其他非分析必要個資。
- 不自動聯絡屋主、房仲或代銷。
- 不在 M3 實作通知排程與雲端部署；這些留給 M4。

## 4. 架構

```text
591 authorized web pages
  -> ListingSource adapter
  -> immutable raw snapshot
  -> normalize + validate
  -> locate to A17/A18/A19
  -> listing snapshot store
  -> lifecycle/price event detector
  -> M2 asking-price comparison
  -> M3 API and dashboard
```

### 4.1 元件邊界

| 元件 | 責任 |
|---|---|
| `listing_sources.py` | 定義來源介面、批次結果及取得錯誤，不含 591 細節 |
| `listing_591.py` | 591 Selenium adapter，操作正常頁面並產生來源原始紀錄 |
| `listing_normalization.py` | 將三類來源欄位轉成固定契約並做型別、價格與 URL 驗證 |
| `listing_location.py` | 使用可靠座標計算最近站點及距離，執行兩公里門檻 |
| `listing_repository.py` | 保存快照、目前狀態與事件，提供 Parquet/MySQL 實作 |
| `listing_events.py` | 比較完整批次，產生新增、價格變動、恢復及下架事件 |
| `listing_valuation.py` | 將足夠完整的售屋條件映射至 M2 輸入並計算價差 |
| `listing_metrics.py` | 聚合刊登量、開價中位數、降價率、在架天數與 AI 價差 |

來源 adapter 不得直接寫資料庫；正規化器不得發送網路請求；事件偵測器只接受已保存且標記為完整的批次。

## 5. 資料取得

### 5.1 取得策略

1. Selenium 使用本機 Chrome 開啟 591 的售屋、新建案與租屋正常搜尋頁面。
2. 以 explicit wait 等待物件卡片或明確的空結果狀態，不使用固定秒數猜測頁面是否完成。
3. 每頁完成解析後隨機等待 2～5 秒；頁面最多重試 3 次並採指數退避。
4. 每個類型與頁碼保存 checkpoint；重跑時可從最後成功頁繼續。
5. 連續空頁、登入／驗證頁、載入 timeout、DOM schema 改變或下一頁失敗時，批次標記為失敗或不完整，不執行下架判定。
6. 不呼叫未公開的內部 JSON endpoint，也不攔截或重放瀏覽器的私有網路請求。

### 5.2 批次完整性

每次來源執行都產生 `batch_id`、`source`、`listing_type`、開始／結束時間、請求頁數、取得筆數、錯誤數與 `is_complete`。只有正常抵達來源末頁且沒有阻斷錯誤的批次才是完整批次。

## 6. 資料契約

正規化刊登至少包含：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `source` | string | 固定為 `591` |
| `source_listing_id` | string | 來源穩定 ID |
| `listing_type` | enum | `sale`、`newhouse`、`rental` |
| `snapshot_at` | datetime | UTC 抓取時間 |
| `source_url` | string | 原始物件 URL |
| `title` | string | 刊登標題 |
| `asking_price_twd` | integer/null | 售屋或新建案總開價 |
| `monthly_rent_twd` | integer/null | 月租金 |
| `building_area_ping` | float/null | 建物坪數 |
| `building_type` | string/null | 對齊 M2 類別時保留對照結果 |
| `bedrooms` | integer/null | 房數 |
| `living_rooms` | integer/null | 廳數 |
| `bathrooms` | integer/null | 衛浴數 |
| `building_age_years` | float/null | 中古屋屋齡 |
| `floor` | integer/null | 所在樓層 |
| `total_floors` | integer/null | 總樓層 |
| `parking_type` | string/null | 車位類型 |
| `latitude` | float/null | WGS84 緯度 |
| `longitude` | float/null | WGS84 經度 |
| `station_code` | string/null | A17、A18 或 A19 |
| `station_distance_m` | float/null | 與最近站點距離 |
| `location_eligible` | boolean | 是否可靠定位且在兩公里內 |
| `raw_hash` | string | 去除非穩定欄位後的內容雜湊 |

每頁渲染後 HTML 與批次 manifest 以 `data/raw/listings/591/<YYYY-MM-DD>/<batch_id>/` 保存；正規化快照輸出至 `data/processed/listing_snapshots.parquet`。原始資料與正式 M1/M2 資料分開。

## 7. 青埔範圍判定

1. 有來源座標時，驗證 WGS84 範圍後計算與 A17、A18、A19 的 Haversine 距離。
2. 重疊生活圈歸屬最近站點。
3. 最近距離大於 2,000 公尺時 `location_eligible=False`。
4. 沒有可靠座標的刊登保留於品質稽核資料，但不得進入正式刊登指標或 M2 價差比較。
5. 不以模糊標題關鍵字假裝精確定位。

## 8. 快照與事件

事件鍵為 `(source, listing_type, source_listing_id)`：

- 首次出現在完整批次：`listed`。
- 開價下降：`price_decreased`，保存前價、後價及百分比。
- 開價上升：`price_increased`。
- 曾下架後再次出現：`relisted`。
- 連續兩個完整批次未出現：`delisted`。

不完整批次不增加缺席次數。相同 `batch_id` 重跑必須冪等，不能重複產生事件。

## 9. M2 價差整合

- `sale` 對應 `transaction_type=resale`。
- `newhouse` 對應 `transaction_type=presale`。
- `rental` 不進行 M2 售價估算。
- 只有 M2 必填欄位齊全且通過 `ValuationInput` 驗證時才估價。
- 結果保存模型版本、估價時間、合理區間與 `asking_gap_pct`。
- 缺少必要條件時回傳 `valuation_eligible=False` 及明確原因，不用中位數補成看似精確的刊登估價。

開價狀態分成 `below_range`、`within_range`、`above_range`；刊登資料只作外部市場訊號，不加入模型訓練、校準或測試資料。

## 10. CLI 與操作流程

```powershell
# 取得並保存授權來源原始批次
qingpu-data listing-scrape --type sale --max-pages 10

# 正規化、定位並更新快照與事件
qingpu-data listing-build --input data/raw/listings/591/<date>/<batch_id>

# 一次完成三類資料同步；任一類失敗不影響其他類已完成批次
qingpu-data listing-sync --types sale newhouse rental --max-pages 10
```

CLI 使用 Selenium 與本機 Chrome。Chrome binary、driver 與可選的瀏覽器 profile 只從環境變數或本機設定讀取；不提供繞過驗證或切換到未公開 endpoint 的模式。

## 11. API 與畫面

新增 API：

- `GET /api/listings`：依類型、站點、價格狀態及最近變動篩選。
- `GET /api/listings/summary`：刊登量、總價／單價中位數、降價率與 AI 價差分布。
- `GET /api/listing-events`：新增、價格變動、恢復與下架事件。

首頁新增「待售情報」區塊，預設顯示中古屋售屋；新建案與租屋使用獨立頁籤。每筆資料顯示來源與抓取時間，AI 價差卡只出現在可估價的售屋／新建案。

## 12. 錯誤處理與安全

- 瀏覽器啟動、導航或頁面載入錯誤保留 checkpoint 並回傳非零 CLI exit code。
- 來源欄位變更造成必要欄位大量缺失時，中止該類批次並輸出品質報告。
- 原始 HTML／JSON 不經前端直接輸出，避免注入來源內容。
- URL 只允許 `https` 且 hostname 必須屬於授權的 591 網域。
- 日誌不得包含 Cookie、token、電話、姓名或完整地址。
- Selenium 不停用 TLS、安全檢查或瀏覽器 sandbox，也不處理 CAPTCHA。

## 13. 測試策略

1. 使用提交到 repository 的匿名渲染後 HTML fixture 測試解析，不在單元測試連線 591。
2. contract tests 驗證 sale、newhouse、rental 正規化欄位。
3. 地理測試涵蓋三站、兩公里邊界、重疊最近站與缺少座標。
4. 事件測試涵蓋首次、降價、漲價、連續兩批缺席、恢復、冪等及不完整批次。
5. M2 整合測試驗證類型隔離、缺欄位不估價及模型版本保存。
6. CLI 測試使用 fake source，驗證 checkpoint、重試、輸出與失敗 exit code。
7. API 測試驗證篩選、空資料、安全欄位及三類隔離。
8. 授權環境的 smoke test 最多抓一頁，作為手動 release gate，不在 CI 執行。

## 14. 驗收門檻

1. `pytest` 與 Ruff 全數通過。
2. 三類一頁 smoke test 各能保存原始批次並產生品質報告。
3. 正規化結果不存在跨類型混用，且正式指標全部符合 A17～A19 兩公里門檻。
4. 同一 fixture 連續同步兩次不重複事件。
5. 模擬不完整批次不會將現有物件標記下架。
6. 至少一筆中古屋與一筆新建案能完成 M2 價差比較；租屋永遠不呼叫售價模型。
7. repository 不包含 Cookie、token、帳號、電話、姓名或授權文件。
