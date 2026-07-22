# 青埔智價 M4.2 Integration Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修復 M4.2 的正式組裝、工作交易與兩階段發布，使一次操作真的完成三類 591 capture/build/stage/publish，且任何失敗都保留上一個 published dataset。

**Architecture:** 將 job lifecycle、listing preparation、dataset staging 與 atomic publish 分成明確服務。每個 Web thread/worker transaction 使用獨立 PyMySQL connection；三類 listing 先在記憶體／run-specific Parquet 組成完整候選資料集，再寫入 MySQL versioned staging，最後在單一 transaction 更新 listing runtime tables、事件與 published pointer。

**Tech Stack:** Python 3.11、Flask、PyMySQL、pandas/Parquet、ThreadPoolExecutor、pytest、Ruff

## Global Constraints

- 工作狀態只允許 `pending -> running -> succeeded|retry_wait|skipped|failed -> needs_attention` 的既有合法 edges。
- 相同 active idempotency key 只能存在一筆；所有 `SELECT ... FOR UPDATE` 分支必須 commit 或 rollback。
- Web request 不等待 Selenium，POST 成功只回 `202`；相同 active run 不得重複 enqueue。
- `types` 必須是非空且只含 `sale`、`newhouse`、`rental`；`max_pages` 必須介於 1 與 100。
- 缺少 capture/build/publisher dependency、零筆、schema error、驗證頁、任一類 incomplete、artifact hash／row-count mismatch 都不得 publish。
- Dataset version 使用不可碰撞的 run-derived ID；同一版本 immutable，不得用 duplicate update 改寫 run ownership。
- MySQL runtime rows、events 與 published pointer 必須在同一 transaction 成功或 rollback；Parquet 只是 versioned artifact。
- 錯誤只保存／輸出 stable error code 與 `redact_job_message()` 後的訊息；非 debug 路徑不得 `traceback.print_exc()`。
- Production Web admin enabled 時必須有高 entropy `QINGPU_SECRET_KEY`，只接受 loopback remote address 與可信 Host。
- 所有 production code 變更先新增 regression test，實際看見正確原因的失敗後才實作。

---

### Task 1: Durable Job Repository and Lifecycle Ownership

**Files:**
- Modify: `src/qingpu_insight/jobs.py`
- Modify: `src/qingpu_insight/job_repository.py`
- Modify: `src/qingpu_insight/job_executor.py`
- Create: `database/004_m4_jobs_publishing_schema.sql`
- Modify: `tests/test_jobs.py`
- Modify: `tests/test_job_repository.py`
- Modify: `tests/test_job_executor.py`

**Interfaces:**
- Produces: `JobService.create(...) -> JobSubmission` where `JobSubmission.run` is the run and `created` says whether executor may enqueue it.
- Produces: public `JobService.get(run_id)` and `JobService.list_recent(limit)` methods.
- Produces: `JobService.fail(run_id, error_code, error_message)`, `succeed(run_id, output_version, summary)` and retry transitions that atomically persist metadata/attempt.
- `LocalJobExecutor` owns `pending -> running`; submitted callable receives an already-running job and never starts it again.

- [ ] **Step 1: Write failing lifecycle and persistence tests**

Add tests proving duplicate active creation returns `created=False`, existing-row lookup closes its transaction, concurrent uniqueness is represented by a generated active key/unique index contract, failure metadata is redacted and persisted, retry increments attempt, success persists output version/summary, and list_recent returns newest first.

- [ ] **Step 2: Write failing executor tests**

Use `threading.Event` rather than sleeps. Assert one start, terminal success/failure, sanitized logging, tracked Future completion and clean shutdown.

- [ ] **Step 3: Run RED tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_jobs.py tests/test_job_repository.py tests/test_job_executor.py -q`

Expected: FAIL because submission metadata, public queries and atomic transition fields do not exist.

- [ ] **Step 4: Implement transaction-safe repository and lifecycle**

Accept a connection factory for production. Acquire and close one connection per repository operation. Add a nullable generated `active_idempotency_key` and unique index in runtime schema plus migration SQL. Implement create-or-get with transaction cleanup and duplicate-key recovery. Make transition accept explicit field changes and always rollback on exceptions.

- [ ] **Step 5: Make executor the sole lifecycle owner**

Start before callable execution, succeed only through the application service’s explicit completion call, and on uncaught exceptions call sanitized `fail`. Store Futures by run ID only while active and expose `shutdown(wait=True)`.

- [ ] **Step 6: Run GREEN tests and lint**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_jobs.py tests/test_job_repository.py tests/test_job_executor.py -q`

Run: `..\..\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/jobs.py src/qingpu_insight/job_repository.py src/qingpu_insight/job_executor.py tests/test_jobs.py tests/test_job_repository.py tests/test_job_executor.py`

- [ ] **Step 7: Commit**

Commit: `fix(m4): make job lifecycle durable and atomic`

### Task 2: Immutable Listing Staging and Atomic Publish

**Files:**
- Modify: `src/qingpu_insight/publishing.py`
- Modify: `src/qingpu_insight/listing_repository.py`
- Modify: `database/004_m4_jobs_publishing_schema.sql`
- Modify: `tests/test_publishing.py`
- Modify: `tests/test_listing_repository.py`

**Interfaces:**
- Produces: `DatasetVersion(version, run_id, status, summary, artifact_path, artifact_hash, artifact_row_count)`.
- Produces: `MySQLVersionPublisher.stage(version, batches, rows, events)` that saves immutable metadata and versioned JSON rows/events without changing runtime tables.
- Produces: `MySQLVersionPublisher.publish(version)` that validates the immutable Parquet and staging contract, then atomically inserts batches/snapshots/current/events and changes `published_datasets`.
- Produces: `MySQLVersionPublisher.current()` and `abandon(version, reason)`.

- [ ] **Step 1: Write failing immutable-stage tests**

Assert stage rejects duplicate version ownership, persists batch/row/event payloads with dataset version, records non-null artifact hash/count, and does not modify the published pointer or listing runtime state.

- [ ] **Step 2: Write failing publish rollback tests**

Publish v1, then inject artifact missing, hash mismatch, row-count mismatch, runtime-row failure and pointer failure for v2. Every case must call rollback and leave v1 current. Add a concurrent/expected-current test so an older candidate cannot move the pointer backward.

- [ ] **Step 3: Run RED tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_publishing.py tests/test_listing_repository.py -q`

Expected: FAIL because current publisher only stores metadata and never verifies or publishes rows.

- [ ] **Step 4: Implement immutable staging schema and publisher**

Add `dataset_version_batches`, `dataset_version_rows`, and `dataset_version_events` keyed by `(dataset_key, version, ...)`. Use canonical JSON serialization for staged payload hashes. Keep `(dataset_key, version)` unique and `run_id` immutable. Use UUID/run-derived version IDs.

- [ ] **Step 5: Implement single-transaction runtime publish**

Before pointer change, recompute Parquet SHA-256 and row count, compare staged count/hash, lock the dataset pointer, load all staged payloads, and apply the existing parameterized listing batch/snapshot/current/event SQL on the same connection. On any exception rollback all runtime changes and preserve the old pointer.

- [ ] **Step 6: Run GREEN tests and lint**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_publishing.py tests/test_listing_repository.py -q`

Run: `..\..\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/publishing.py src/qingpu_insight/listing_repository.py tests/test_publishing.py tests/test_listing_repository.py`

- [ ] **Step 7: Commit**

Commit: `fix(m4): publish staged listing datasets atomically`

### Task 3: Real Three-Type Listing Update Application Flow

**Files:**
- Modify: `src/qingpu_insight/listing_update.py`
- Modify: `src/qingpu_insight/cli.py`
- Modify: `src/qingpu_insight/pipeline.py`
- Modify: `tests/test_listing_update.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: validated `ListingUpdateRequest`.
- Produces: `PreparedListingType(batch, rows, events, summary)` and a required `ListingPreparationRunner.prepare(listing_type, max_pages)` protocol.
- `ListingUpdateService.execute_running(run_id, request)` consumes an already-running job, prepares all requested types, writes one run-specific Parquet, stages/publishes exactly once, then persists job output version and summary.
- Production CLI assembles a visible Selenium/M3 preparation runner and connection factories; no dependency may silently default to `None`.

- [ ] **Step 1: Write failing production-composition and validation tests**

Assert the default CLI factory injects a real preparation runner; absence raises before job creation. Assert invalid/empty types and `max_pages` outside 1..100 fail. Assert capture RuntimeError exits 1 rather than already-running exit 2.

- [ ] **Step 2: Write failing all-or-nothing flow tests**

For sale/newhouse/rental, assert exact preparation order, one combined versioned Parquet, non-zero row/hash summary, stage then publish, and final succeeded job. For each type inject zero rows, incomplete batch, schema error and publish failure; assert no pointer switch and sanitized failed job.

- [ ] **Step 3: Write failing lock recovery tests**

Replace empty sentinel locking with a Windows-compatible OS advisory lock or an owner/lease contract. Assert a process-death-safe primitive releases automatically; lock contention does not leave a newly-created pending run forever.

- [ ] **Step 4: Run RED tests**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_listing_update.py tests/test_pipeline.py tests/test_cli.py -q`

Expected: FAIL because the production factory has no capture runner and the service publishes zero rows.

- [ ] **Step 5: Extract reusable M3 preparation without early publication**

Refactor existing `listing_sync` logic so one type can return prepared batch/rows/events while reading current state for event detection, but does not call production repository save methods. Preserve existing `listing-sync` behavior through a compatibility wrapper and its existing tests.

- [ ] **Step 6: Implement complete application flow**

Require the preparation runner, use `PipelineRunner` for named preparation/artifact/stage/publish steps, honor bounded transient retry delays, fail closed on every required step, create artifact under `data/processed/listing_versions/<version>.parquet`, and complete the job only after atomic publish.

- [ ] **Step 7: Run GREEN tests and M3 regression**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_listing_update.py tests/test_pipeline.py tests/test_cli.py tests/test_listing_capture.py tests/test_listing_repository.py tests/test_listing_events.py -q`

Run: `..\..\.venv\Scripts\python.exe -m ruff check src/qingpu_insight/listing_update.py src/qingpu_insight/pipeline.py src/qingpu_insight/cli.py tests/test_listing_update.py tests/test_pipeline.py tests/test_cli.py`

- [ ] **Step 8: Commit**

Commit: `fix(m4): run the complete 591 update pipeline`

### Task 4: Production Web Composition, Job Center Contract, and Release Gate

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Modify: `src/qingpu_insight/static/app.js`
- Modify: `README.md`
- Modify: `tests/test_web.py`
- Create: `tests/test_m42_release_gate.py`

**Interfaces:**
- Produces: `create_app(root=..., admin_services=...)` with a production factory that creates per-operation DB dependencies and one local executor.
- POST `/api/admin/listing-updates` returns 202 only for a newly enqueued valid request; duplicate active request returns the existing run without enqueue.
- GET `/api/jobs/<run_id>` and GET `/api/jobs?limit=N` require local/trusted request and return stable JSON contracts.

- [ ] **Step 1: Write failing production-entry and API contract tests**

Assert the production factory with configured DB/admin secret returns 202 rather than 503. Test Host and loopback rejection, missing admin secret fail-closed, malformed JSON, invalid types/page bounds, duplicate POST dedupe, job detail, real recent history, limit validation and sanitized error output.

- [ ] **Step 2: Write failing real-executor Web integration test**

Use real `LocalJobExecutor`, `threading.Event`, real `ListingUpdateService`, stateful repositories and fake preparation/publisher. POST once, wait conditionally, then assert capture ran and GET reaches `succeeded`; this must catch any duplicate `start()` regression.

- [ ] **Step 3: Write failing frontend polling tests**

Assert polling rejects non-2xx JSON, re-enables the button, never overlaps a pending request, stops on terminal state, and uses bounded backoff/recursive timeout rather than `setInterval`.

- [ ] **Step 4: Implement secure production composition and job APIs**

Build admin dependencies only when `QINGPU_DATABASE_URL` and `QINGPU_SECRET_KEY` are present, validate trusted Host plus loopback, use public JobService APIs, return real recent jobs, and register executor shutdown. Keep the server bound to `127.0.0.1`.

- [ ] **Step 5: Implement resilient polling and documentation**

Use recursive `setTimeout` after each request settles, check `response.ok`, display safe API messages and stop/re-enable on errors. Document commands, environment variables, manual 591 behavior, status/error contracts, stale-job recovery and local-only security.

- [ ] **Step 6: Add M4.2 release-gate test**

The end-to-end fake-source test must publish v1, run a complete three-type v2 update and observe v2, then inject failure into each required stage for v3 and verify v2 remains current with no duplicate events on retry.

- [ ] **Step 7: Run focused and complete verification**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests/test_web.py tests/test_m42_release_gate.py -q`

Run: `..\..\.venv\Scripts\python.exe -m pytest -q`

Run: `..\..\.venv\Scripts\python.exe -m ruff check .`

Run: `git diff --check 12fe7d5..HEAD`

- [ ] **Step 8: Commit**

Commit: `fix(m4): complete the local update job center`

