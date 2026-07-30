# 青埔中古屋專注版實作計畫

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. The user explicitly waived TDD for this corrective change; update existing tests after each behavior change and run focused regression checks.

**Goal:** 將公開產品、591 助理與模型生命週期收斂為中古屋，修正未完工移轉污染中古屋資料的問題，重建資料並產生一個可審查的中古屋候選模型。

**Architecture:** 在資料清洗層以完工日與交易日建立中古屋語意門檻；在 Web、管理中心與 service 層採 fail-closed 的 resale-only admission policy；底層 parser、儲存格式、歷史預售屋資料與 artifact 保持不動。所有新入口只處理 resale，既有預售屋歷史內容仍可讀。

**Tech Stack:** Python 3.11、pandas、Flask、scikit-learn、vanilla JavaScript、pytest、Node contract tests、Ruff。

## Global Constraints

- 依 `docs/superpowers/specs/2026-07-30-resale-only-product-scope-design.md` 實作。
- 不刪除預售屋 parquet 原始來源、資料庫歷史、對話、候選或 artifact。
- 不將 A 表未完工移轉自動重分類為預售屋。
- 不硬編碼特定道路、建案或安置住宅名稱。
- 新的估價、訓練、發布、公開行情及 591 分析只允許中古屋。
- 歷史預售屋估價、報告與對話保持可讀。
- 重新訓練只建立候選，不自動發布。
- 使用者已明確要求本次不採 TDD；以既有測試、資料驗收與瀏覽器 QA 驗證。

---

### Task 1: 修正中古屋資料語意與趨勢樣本門檻

**Files:**

- Modify: `src/qingpu_insight/market_cleaning.py`
- Modify: `src/qingpu_insight/model_features.py`
- Modify: `src/qingpu_insight/market_metrics.py`
- Modify: `tests/test_market_cleaning.py`
- Modify: `tests/test_model_features.py`
- Modify: `tests/test_market_metrics.py`

**Interfaces:**

- `build_market_dataset(frame) -> tuple[pd.DataFrame, MarketQuality]`
- `build_model_frame(frame, transaction_type) -> pd.DataFrame`
- `market_trends(frame, filters) -> list[dict[str, Any]]`

- [ ] 在既有住宅、生活圈、價格、面積、日期、交易標的及特殊關係規則之後，對 resale 增加：
  - `completion_date` 缺失：`missing_completion_date`
  - `completion_date > transaction_date`：`future_completion_transfer`
- [ ] 讓兩個原因只計算原本已符合其他市場條件的 resale 案件，避免重複計數。
- [ ] 讓 `analysis_eligible` 排除上述案件；presale 原始資料不套用此門檻。
- [ ] 在 `build_model_frame()` 對 resale 增加 `building_age_years.notna() & building_age_years.ge(0)` 第二層防護。
- [ ] `market_trends()` 完成月聚合後只回傳 `record_count >= 10` 的月份。
- [ ] 更新既有測試，涵蓋等於完工日保留、未來完工與缺完工日排除、presale 不受影響、9 筆月份省略、10 筆月份保留。
- [ ] 執行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_market_cleaning.py tests/test_model_features.py tests/test_market_metrics.py -q
```

---

### Task 2: 首頁與公開 API 固定為中古屋

**Files:**

- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/web.py`
- Modify: `tests/test_web.py`
- Modify: `tests/js/market_results_contract.cjs`

**Interfaces:**

- `parse_filters(args) -> MarketFilters`
- `parse_valuation_payload(payload) -> ValuationInput`
- `POST /api/valuations`
- `GET /api/market/summary`
- `GET /api/market/trends`
- `GET /api/market/map-points`
- `GET /api/transactions`

- [ ] 首頁市場標示改為靜態「中古屋」，移除 `#transaction-type`。
- [ ] 估價市場標示改為靜態「中古屋」，移除 `#valuation-type`。
- [ ] `app.js` 的市場查詢與估價 payload 固定送出 `transaction_type=resale`。
- [ ] 移除預售屋屋齡切換分支與已刪欄位的錯誤 focus mapping。
- [ ] `parse_filters()` 在未帶市場時預設 resale；顯式 presale 回傳 400。
- [ ] `parse_valuation_payload()` 在缺省市場時使用 resale；顯式 presale 以穩定錯誤碼 `presale_valuation_disabled` 回傳 400。
- [ ] 保持 `GET /api/valuations/<id>` 不變，確保歷史預售屋估價可讀。
- [ ] 更新首頁與 API 測試，執行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web.py -q -k "homepage or market or valuation"
node tests/js/market_results_contract.cjs
```

---

### Task 3: 591 公開流程與管理更新只允許 sale

**Files:**

- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/home_assistant.js`
- Modify: `src/qingpu_insight/static/admin.js`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/conversation_web.py`
- Modify: `src/qingpu_insight/conversation_import.py`
- Modify: `tests/test_web.py`
- Modify: `tests/test_conversation_web.py`
- Modify: `tests/test_conversation_import.py`
- Modify: `tests/js/home_assistant_contract.cjs`
- Modify: `tests/js/admin_contract.cjs`

**Interfaces:**

- `POST /api/admin/listing-updates`
- `POST /api/assistant/listings/import`
- `ConversationImportService.import_initial_listing(*, conversation_id: str, raw_url: str, stage_callback: Callable[[str, ImportStage], None] | None = None) -> ListingImportResult`

- [ ] 首頁移除 591 新建案連結與支援文案，只顯示中古屋。
- [ ] 首頁 URL 驗證拒絕 direct newhouse；保留 `591.to` short URL。
- [ ] conversation Web admission 在 enqueue 前拒絕 direct newhouse。
- [ ] import service 在 short URL capture 完成、repository mutation 前確認解析結果為 sale；newhouse fail-closed。
- [ ] 保持底層 `conversation_urls.py`、newhouse parser、evidence schema 及歷史 GET/refresh 相容。
- [ ] 管理中心 listing update 預設與 allowlist 只接受 `sale`。
- [ ] 移除管理頁 newhouse status/retry row；前端 sequencer 只送 sale。
- [ ] 公開 listing API 缺省 sale、顯式 newhouse 回傳 400；底層 repository 保持通用。
- [ ] 更新 Python 與 Node contracts，執行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_conversation_web.py tests/test_conversation_import.py tests/test_web.py -q -k "import or listing or assistant"
node tests/js/home_assistant_contract.cjs
node tests/js/admin_contract.cjs
```

---

### Task 4: 模型訓練、發布與觀測台只允許 resale

**Files:**

- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/model_training_service.py`
- Modify: `src/qingpu_insight/model_observatory.py`
- Modify: `src/qingpu_insight/model_release.py`
- Modify: `src/qingpu_insight/admin_web.py`
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/static/models_admin.js`
- Modify: `src/qingpu_insight/templates/admin.html`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_model_training_service.py`
- Modify: `tests/test_model_observatory.py`
- Modify: `tests/test_model_release.py`
- Modify: `tests/test_web.py`
- Modify: `tests/js/model_admin_contract.cjs`

**Interfaces:**

- `ModelTrainingRequest(markets: tuple[Literal["resale"], ...], trigger: str = "web", tuning_plan: TrainingPlan | None = None)`
- `ModelTrainingService.submit/start_run/execute`
- `ModelObservatory.status/list_runs/get_run`
- `ModelReleaseService.preview_publish/preview_rollback/submit/execute`
- `POST /api/admin/model-training-runs`
- model release preview/execute APIs

- [ ] CLI `model-train` 預設與 choices 只接受 resale。
- [ ] `ModelTrainingRequest.SUPPORTED` 只允許 resale，拒絕 presale 與混合 market。
- [ ] Web training parser 只允許 `["resale"]`。
- [ ] 觀測台 status、候選清單、run detail 與 AutoML fallback 只投影 resale；presale-only 舊工作不顯示。
- [ ] `ModelReleaseService` 在 preview、submit 與 execute 都拒絕 presale，防止舊 preview 繞過入口。
- [ ] admin release API 只接受 resale；修正歷史 release filter 中無條件為真的 market 判斷。
- [ ] 管理頁移除 presale/all 模型選項、階段文字、報告卡與調參提示。
- [ ] 保留 `OfficialModelStore` 通用能力與 `artifacts/official/presale/**` 檔案不動。
- [ ] 更新既有測試與 Node contract，執行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_model_training_service.py tests/test_model_observatory.py tests/test_model_release.py tests/test_web.py -q -k "model or training or release or observatory"
node tests/js/model_admin_contract.cjs
```

---

### Task 5: 重建資料、訓練候選、文件與整體驗證

**Files:**

- Modify: `README.md`
- Generated, do not commit unless already tracked by project policy:
  - `data/processed/market_transactions.parquet`
  - `outputs/reports/m1-market-quality.json`
  - `candidates/<run_id>/**`

- [ ] 更新 README：公開產品只支援中古屋；預售屋底層資料保留但不進入估價、訓練、發布或 591 公開入口。
- [ ] 執行資料重建：

```powershell
.\.venv\Scripts\qingpu-data.exe market-build
```

- [ ] 驗證 parquet：
  - resale 無缺失或負 `building_age_years`
  - 2024-09 resale 中位單價約 43.4 萬／坪
  - 趨勢每個回傳月份 `record_count >= 10`
  - 品質報告包含 `future_completion_transfer` 與 `missing_completion_date`
- [ ] 若 `QINGPU_DATABASE_URL` 可用，透過管理中心官方資料更新流程同步 MySQL；若不可用，明確記錄網站需使用 parquet 或稍後同步。
- [ ] 執行中古屋候選訓練：

```powershell
.\.venv\Scripts\qingpu-data.exe model-train --markets resale
```

- [ ] 保存候選與完整報告，不發布。
- [ ] 執行所有 Node contracts、Ruff 與 Python release gate：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
Get-ChildItem tests\js\*.cjs | ForEach-Object { node $_.FullName }
node tests/js/market_map_contract.mjs
```

- [ ] 啟動網站並以瀏覽器確認：
  - 首頁無預售選項；
  - 趨勢無 18 萬異常低點；
  - 中古屋估價可用；
  - 591 中古屋 URL 可建立分析；
  - newhouse URL 顯示已停用；
  - 管理中心只顯示 sale 更新與中古屋模型；
  - 舊預售屋 artifact、候選與歷史對話仍存在。
- [ ] 檢查 `git diff --check`、secret scan 與工作目錄，保留所有既有未追蹤候選資料。
