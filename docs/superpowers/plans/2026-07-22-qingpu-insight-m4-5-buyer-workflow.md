# 青埔智價 M4.5 購屋工作流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供單一使用者的購屋條件、收藏、比較、版本化報告與不重複通知，形成完整自用流程。

**Architecture:** MySQL 保存 profile、favorites、comparison、reports 與 notifications；domain services 不依賴 Flask。`ListingMatcher` 對 published listings 產生 deterministic score，事件比對器把 M3 listing events 轉成 notification intents，再由站內、Windows 與選配 SMTP providers 發送。

**Tech Stack:** Python 3.11、Pydantic 2、PyMySQL、Flask、PowerShell、Windows toast、SMTP、pytest

## Global Constraints

- 第一版只有單一使用者，不建立登入、密碼或多租戶權限。
- 收藏物件下架後仍保留最後成功快照；缺失欄位顯示未知，不得顯示為零。
- 排名與 notification matching 必須 deterministic，不由 LLM 決定。
- 站內通知是 source of truth；Windows／Email 失敗不得刪除或重建站內通知。
- Email 只有 SMTP 環境變數完整時啟用；LINE、Telegram、簡訊不實作。

## File Map

| File | Responsibility |
|---|---|
| `buyer_models.py` | Profile、favorite、comparison contracts |
| `buyer_repository.py` | MySQL single-user state persistence |
| `listing_matching.py` | Hard filters、deterministic score 與 explanations |
| `listing_comparison.py` | Allowlisted comparison view model |
| `notifications.py` | Event-to-intent mapping、cooldown 與 delivery orchestration |
| `notification_repository.py` | MySQL in-app source of truth 與 dedupe |
| `notification_providers.py` | In-app、Windows、SMTP adapters |
| `ops/show-notification.ps1` | Windows Runtime toast 的薄 wrapper |
| `web.py` / frontend assets | Profile、收藏、比較、報告與通知 UI/API |

---

### Task 1: Buyer profile、收藏與比較 repository

**Files:**
- Create: `src/qingpu_insight/buyer_models.py`
- Create: `src/qingpu_insight/buyer_repository.py`
- Create: `tests/test_buyer_models.py`
- Create: `tests/test_buyer_repository.py`

**Interfaces:**
- Produces: `BuyerProfile`, `FavoriteListing`, `ComparisonSet`、`MySQLBuyerRepository.save_profile/get_profile/add_favorite/remove_favorite/list_favorites/save_comparison`。

- [ ] **Step 1: 寫入 validation、idempotent favorite 與 delisted preservation 測試**

```python
def test_profile_rejects_inverted_budget() -> None:
    with pytest.raises(ValidationError):
        BuyerProfile(profile_id="default", budget_min_twd=20_000_000,
                     budget_max_twd=10_000_000, station_codes=("A18",))


def test_add_favorite_is_idempotent_and_keeps_snapshot(mysql_buyer_repo) -> None:
    mysql_buyer_repo.add_favorite(favorite("591:sale:123", snapshot_version="v1"))
    mysql_buyer_repo.add_favorite(favorite("591:sale:123", snapshot_version="v1"))
    rows = mysql_buyer_repo.list_favorites()
    assert len(rows) == 1
    assert rows[0].snapshot_version == "v1"
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_buyer_models.py tests/test_buyer_repository.py -q`
Expected: FAIL，buyer modules 尚不存在。

- [ ] **Step 3: 實作 Pydantic models 與 MySQL tables**

```python
class BuyerProfile(BaseModel):
    profile_id: str = "default"
    budget_min_twd: int | None = Field(default=None, ge=0)
    budget_max_twd: int | None = Field(default=None, gt=0)
    area_min_ping: float | None = Field(default=None, ge=0)
    area_max_ping: float | None = Field(default=None, gt=0)
    bedrooms: tuple[int, ...] = ()
    station_codes: tuple[Literal["A17", "A18", "A19"], ...] = ()
    max_station_distance_m: int = Field(default=2_000, ge=0, le=2_000)
    transaction_types: tuple[Literal["resale", "presale"], ...] = ("resale", "presale")
```

建立 `buyer_profiles`、`favorite_listings`、`comparison_items`；favorite primary key 為
`(profile_id, listing_key)`，保存 `snapshot_version`、`snapshot_json`、note、created_at。Comparison
每個 profile 最多一組 active set、最多 5 個物件，position unique。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_buyer_models.py tests/test_buyer_repository.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/buyer_models.py src/qingpu_insight/buyer_repository.py tests/test_buyer_models.py tests/test_buyer_repository.py
git commit -m "feat(m4): persist buyer profiles and favorites"
```

### Task 2: Deterministic 匹配、排名與比較 view model

**Files:**
- Create: `src/qingpu_insight/listing_matching.py`
- Create: `src/qingpu_insight/listing_comparison.py`
- Create: `tests/test_listing_matching.py`
- Create: `tests/test_listing_comparison.py`

**Interfaces:**
- Produces: `ListingMatch`, `match_listings(profile, rows)`、`ComparisonRow`, `build_comparison(rows, fields)`。

- [ ] **Step 1: 寫入 hard filter、score explanation 與 missing-value 測試**

```python
def test_matcher_filters_over_budget_and_explains_score(profile, listing_rows) -> None:
    matches = match_listings(profile, listing_rows)
    assert all(item.asking_price_twd <= profile.budget_max_twd for item in matches)
    assert matches == sorted(matches, key=lambda item: (-item.score, item.listing_key))
    assert {part.name for part in matches[0].score_parts} >= {"budget", "station", "valuation"}


def test_comparison_keeps_missing_as_none() -> None:
    view = build_comparison([{"listing_key": "a", "building_age_years": None}])
    assert view.rows[0].values["building_age_years"] is None
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_listing_matching.py tests/test_listing_comparison.py -q`
Expected: FAIL，matching modules 尚不存在。

- [ ] **Step 3: 實作 hard filters 與固定 100 分 score**

先依 location eligible、active、transaction type、budget、area、bedrooms、station distance hard filter；
score 只對仍存在的偏好計算並重新正規化：budget fit 25、station/distance 25、area/layout 20、
valuation gap/confidence 20、listing freshness 10。每個 `ScorePart` 保存 weight、earned、reason_code
與 fact IDs，tie 以 listing key 排序。

Comparison allowlist 固定為 asking price、estimated interval、gap、area、layout、age、floor、station、
distance、parking、status、last event；不得將 raw row 任意欄位輸出。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_listing_matching.py tests/test_listing_comparison.py -q`
Expected: PASS，相同輸入重跑排序一致。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/listing_matching.py src/qingpu_insight/listing_comparison.py tests/test_listing_matching.py tests/test_listing_comparison.py
git commit -m "feat(m4): rank and compare buyer matches"
```

### Task 3: 通知 intent、去重與 providers

**Files:**
- Create: `src/qingpu_insight/notifications.py`
- Create: `src/qingpu_insight/notification_repository.py`
- Create: `src/qingpu_insight/notification_providers.py`
- Create: `ops/show-notification.ps1`
- Create: `tests/test_notifications.py`
- Create: `tests/test_notification_repository.py`
- Create: `tests/test_notification_providers.py`

**Interfaces:**
- Produces: `NotificationIntent`, `NotificationService.process(events, profile)`、`MySQLNotificationRepository`、`InAppNotificationProvider`、`WindowsNotificationProvider`、`SmtpNotificationProvider`。

- [ ] **Step 1: 寫入去重、冷卻與 channel failure 測試**

```python
def test_same_event_creates_one_in_app_notification(service, price_event) -> None:
    service.process([price_event])
    service.process([price_event])
    assert service.repository.count() == 1


def test_windows_failure_does_not_duplicate_in_app(service, price_event) -> None:
    service.windows_provider.raise_error = True
    service.process([price_event])
    saved = service.repository.list_all()
    assert len(saved) == 1
    assert saved[0].delivery_status["windows"] == "failed"
```

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_notifications.py tests/test_notification_repository.py tests/test_notification_providers.py -q`
Expected: FAIL，notification modules 尚不存在。

- [ ] **Step 3: 實作 event mapping、MySQL unique key 與 providers**

```python
@dataclass(frozen=True)
class NotificationIntent:
    dedupe_key: str
    profile_id: str
    event_type: Literal[
        "new_match", "price_decreased", "price_increased", "delisted", "relisted",
        "job_failed", "data_stale", "model_unhealthy", "backup_unhealthy",
    ]
    entity_key: str
    event_version: str
    title: str
    body: str
    created_at: datetime
```

`notifications.dedupe_key` unique；repository 先 insert 站內 row，再逐 channel 更新 delivery status，
不可因 channel exception rollback insert。Windows provider 使用 `subprocess.run()` 以 argument array
呼叫 `ops/show-notification.ps1 -Title <title> -Body <body>`；script 使用 Windows Runtime toast API，
不透過 shell command interpolation。無互動 session 回傳
`skipped`；SMTP settings 缺任一必要值時 factory 不建立 provider。Email subject/body 不含完整地址或
Evidence Pack。

- [ ] **Step 4: 執行測試**

Run: `python -m pytest tests/test_notifications.py tests/test_notification_repository.py tests/test_notification_providers.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/notifications.py src/qingpu_insight/notification_repository.py src/qingpu_insight/notification_providers.py ops/show-notification.ps1 tests/test_notifications.py tests/test_notification_repository.py tests/test_notification_providers.py
git commit -m "feat(m4): deliver deduplicated buyer alerts"
```

### Task 4: Profile、收藏、比較、報告與通知 UI/API

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/templates/index.html`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `src/qingpu_insight/static/app.css`
- Test: `tests/test_web.py`
- Create: `tests/test_buyer_flow.py`

**Interfaces:**
- Produces: profile/favorite/comparison/report/notification REST endpoints 與完整 buyer flow E2E。

- [ ] **Step 1: 寫入 buyer flow API test**

```python
def test_buyer_flow_profile_favorite_compare_report_notification(client) -> None:
    assert client.put("/api/buyer-profile", json=valid_profile()).status_code == 200
    assert client.post("/api/favorites/591:sale:123").status_code == 201
    assert client.put("/api/comparison", json={"listing_keys": ["591:sale:123"]}).status_code == 200
    report = client.post("/api/reports", json={"profile_id": "default"})
    assert report.status_code == 201
    notifications = client.get("/api/notifications").json["items"]
    assert isinstance(notifications, list)
```

另加測試：favorite key 不存在回 404、comparison 超過 5 筆回 400、report 只接受 default profile、
mark-read 冪等、所有 mutation 需要 localhost CSRF。

- [ ] **Step 2: 驗證測試先失敗**

Run: `python -m pytest tests/test_buyer_flow.py tests/test_web.py -q`
Expected: FAIL，routes 尚未接線。

- [ ] **Step 3: 實作 routes 與可用 UI**

Routes：`GET/PUT /api/buyer-profile`、`GET /api/favorites`、
`POST/DELETE /api/favorites/<path:listing_key>`、
`GET/PUT /api/comparison`、`POST /api/reports`、`GET /api/reports/<id>`、
`GET /api/notifications`、`POST /api/notifications/<id>/read`。`create_app()` 注入 buyer、report、
notification services；所有 mutation 使用 M4.2 loopback／CSRF guard。

首頁新增「我的條件」「推薦」「收藏比較」「購屋報告」「通知」區塊；loading、empty、error、fallback、
stale-data 與 unknown value 都有明確繁中狀態。免費 Gemini 產生前顯示 data-use notice，不把 key
或 provider exception 顯示給使用者。

- [ ] **Step 4: 執行 M4.5 gate**

Run: `python -m pytest tests/test_buyer_models.py tests/test_buyer_repository.py tests/test_listing_matching.py tests/test_listing_comparison.py tests/test_notifications.py tests/test_notification_repository.py tests/test_notification_providers.py tests/test_buyer_flow.py tests/test_web.py -q`
Expected: PASS。

Run: `python -m pytest -q && python -m ruff check . && git diff --check`
Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/qingpu_insight/web.py src/qingpu_insight/templates/index.html src/qingpu_insight/static/app.js src/qingpu_insight/static/app.css tests/test_web.py tests/test_buyer_flow.py
git commit -m "feat(m4): complete local buyer workflow"
```
