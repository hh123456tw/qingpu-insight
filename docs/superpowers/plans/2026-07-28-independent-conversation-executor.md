# Independent Conversation Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓模型訓練與 591 匯入／AI 回覆能同時執行，且各自維持單工限制。

**Architecture:** 管理工作保留既有 `LocalJobExecutor(max_workers=1)`；production 對話 runtime 一律建立並持有另一個 `LocalJobExecutor(max_workers=1)`。兩者共用 job repository，但不共用 thread pool，並由 Flask app shutdown hook 各自關閉。

**Tech Stack:** Python 3.12、Flask、`concurrent.futures.ThreadPoolExecutor`、pytest

## Global Constraints

- 不新增 Redis、Celery、程序池或前端設定。
- 不允許兩次模型訓練並行。
- 同一對話的既有 busy 與 active reply 防護維持不變。
- 使用者已明確要求本次不採 TDD；完成後補回歸與真實並行驗證。

---

### Task 1: 分離 production 對話 executor

**Files:**
- Modify: `src/qingpu_insight/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `LocalJobExecutor(job_service: JobService, max_workers: int = 1)`
- Produces: `app.extensions["qingpu_conversation_executor"]`

- [ ] **Step 1: 修改對話 runtime 組裝**

在 `create_app` 的 production 對話組裝區塊中，移除借用
`admin_services.executor` 的分支；一律以對話使用的 `JobService`
建立獨立 `LocalJobExecutor`，並保留在 `conversation_owned_executor`。

- [ ] **Step 2: 暴露可驗證的 executor reference**

將實際對話 executor 存入 `app.extensions["qingpu_conversation_executor"]`；
對話 runtime 不可用時保存 `None`。

- [ ] **Step 3: 驗證 shutdown**

確認 `qingpu_admin_shutdown` 先後關閉管理 executor 與對話 executor，
且既有 `shutdown_complete` 仍避免重複關閉。

- [ ] **Step 4: 補回歸測試**

在 `tests/test_web.py` 使用可記錄 `shutdown` 與 `submit` 的 executor，
驗證管理與對話 executor 身分不同、兩者都會關閉。

- [ ] **Step 5: 執行聚焦測試**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_web.py -k "executor or conversation"
```

Expected: PASS。

### Task 2: 並行驗收與完整驗證

**Files:**
- Modify: `README.md`（只在操作說明需要同步時修改）

**Interfaces:**
- Consumes: 兩個獨立 `LocalJobExecutor`
- Produces: 可重複的並行驗收證據

- [ ] **Step 1: 執行並行整合檢查**

以兩個 `Event` 阻塞管理工作；在管理工作仍為 running 時提交對話工作，
驗證對話工作能進入 running。

- [ ] **Step 2: 執行完整驗證**

Run:

```powershell
$env:PYTHONPATH=(Get-Location).Path
.\.venv\Scripts\python.exe -m pytest -q
Get-ChildItem tests\js -Filter *.cjs | ForEach-Object { node $_.FullName }
.\.venv\Scripts\ruff.exe check .
git diff --check
```

Expected: 全部 exit code 0。

- [ ] **Step 3: 重啟本機網站並驗證**

只停止占用 `127.0.0.1:5000` 的 qingpu-web 程序鏈，重新啟動後確認：

- 首頁回傳 HTTP 200。
- 模型訓練工作執行時，591 對話工作能開始。
- shutdown 不留下額外 qingpu-web listener。

- [ ] **Step 4: 提交並推送**

只加入程式碼、測試與文件；排除 `candidates/`、`.env`、log 與本機輸出。

```powershell
git commit -m "fix(web): isolate conversation background jobs"
git push origin main
```
