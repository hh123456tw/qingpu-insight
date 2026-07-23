## M4.2 真實驗收結果
**日期：** 2026-07-23
**MySQL:** qingpu_insight@127.0.0.1:3306
**591 類型:** sale
**max_pages:** 10

### 結果
- **status:** succeeded
- **rows:** 31
- **events:** 31
- **batches:** 1
- **output_version:** 20260723T132123423369Z-426a910988cc42738cb532ca8457fdd7

### MySQL 驗證
- listing_current: 31 rows
- listing_events: 31 rows
- published_datasets: listings -> version above

### CLI 命令
.\.venv\Scripts\qingpu-data listing-update --types sale --max-pages 10
