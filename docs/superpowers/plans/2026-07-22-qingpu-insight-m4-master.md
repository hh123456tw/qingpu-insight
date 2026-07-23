# 青埔智價 M4 本機產品化 Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 M4 設計依相依順序落實為六個可獨立 review 的本機產品化里程碑；M4.3、M4.4 採 2026-07-23 核准的作品集精簡範圍。

**Architecture:** 以 Windows 原生 Flask、MySQL runtime source of truth、可見 Chrome 與可替換 LLM provider 為核心；每個里程碑先建立 domain contract，再接 repository、CLI／Web 與驗收。Parquet 只作可重建匯出，raw evidence、模型、備份與 benchmark 才使用檔案系統。六份子計畫共用同一分支但每個 task 都使用獨立提交與 review gate。

**Tech Stack:** Python 3.11、Flask 3.1、pandas、PyArrow、PyMySQL、scikit-learn、Pydantic 2、requests、Selenium、PowerShell、Ollama、Gemini API、pytest、Ruff

## Global Constraints

- 目標平台為 Windows 10 22H2 或更新版本；第一版服務只綁定 `127.0.0.1`。
- 591 售屋、新建案及租屋預設由使用者手動一鍵更新；可見 Chrome 排程為選配且預設關閉。
- 591 驗證頁、CAPTCHA、登入要求或 DOM 契約失敗時安全停止，不做規避。
- 刊登開價不得加入 M2 官方成交模型的訓練、校準或測試資料。
- LLM 只解讀 Evidence Pack，不自行查價或修改數值；無 LLM 時必須有規則式報告。
- `.env`、MySQL 密碼、Gemini key、SMTP credential、Cookie 與瀏覽器 profile 不提交 Git。
- M4 的工作、版本、健康、備份、geocode cache、profile、收藏、通知與報告 metadata 只存 MySQL；不得新增 JSON／SQLite／Parquet runtime repository。
- CI 不連線 591、Ollama 或 Gemini；外部整合只在明確的手動 smoke／benchmark 執行。
- 完整 LLM benchmark 只在 32GB RAM／RTX 3080 12GB 主機執行；64GB RAM／GTX 1050 Ti 4GB 主機只做小模型與 fallback smoke。
- M4.3 Lite 不包含模型漂移平台、通知、Windows 排程或可編輯 threshold。
- M4.4 Core 不包含 profile、收藏、比較或多人功能；Rule provider 必須在無 LLM 時可用。

---

## 執行順序

| 順序 | 計畫 | 主要產出 | 依賴 |
|---:|---|---|---|
| 1 | [M4.1 資料完整性](2026-07-22-qingpu-insight-m4-1-location-data-quality.md) | 定位證據、可信度、cache、品質報告 | M3 |
| 2 | [M4.2 工作與發布](2026-07-22-qingpu-insight-m4-2-jobs-publishing.md) | Job 狀態機、兩階段發布、一鍵更新 | M4.1 |
| 3 | [M4.3 Lite 健康與備份](2026-07-22-qingpu-insight-m4-3-observability-backup.md) | 健康摘要、手動備份、隔離還原 | M4.2 真實驗收 |
| 4 | [M4.4 Core 智慧報告](2026-07-22-qingpu-insight-m4-4-intelligent-reports.md) | Evidence Pack、Rule/Ollama/Gemini、驗證、一次 benchmark | M4.1～M4.3 Lite |
| 5 | [M4.5 使用者功能](2026-07-22-qingpu-insight-m4-5-buyer-workflow.md) | Profile、收藏、比較、通知與 UI | M4.2、M4.4 |
| 6 | [M4.6 Windows 交付](2026-07-22-qingpu-insight-m4-6-windows-delivery.md) | PowerShell、排程、smoke、作品集文件 | 全部 |

## 每階段共同 review gate

- [ ] 只執行該子計畫列出的 task，不提前實作後續子計畫。
- [ ] 每個 task 先跑指定失敗測試，再寫最小實作，再跑聚焦測試與完整測試。
- [ ] 每個 task 完成後做 spec compliance review，再做 code quality review。
- [ ] 每個子計畫完成後執行 `python -m pytest -q` 與 `python -m ruff check .`。
- [ ] `git status --short` 只能包含該 task 預期檔案，提交訊息使用子計畫指定文字。
- [ ] M4.1～M4.6 皆完成後才建立整體 M4 release commit／tag；本計畫不含 push 或公開部署。

## M4.3／M4.4 範圍凍結

核准設計為
[M4.3 Lite／M4.4 Core 設計](../specs/2026-07-23-qingpu-insight-m4-3-lite-m4-4-core-design.md)。
若實作過程出現以下需求，必須另立里程碑，不得直接加入目前計畫：

- 模型 PSI／missingness 漂移平台、排程監控、Email 或 Windows 通知。
- 自動 backup／restore 排程、Web 觸發 restore 或自動事故修復。
- 多使用者、profile、收藏、物件比較與通知。
- 常駐 benchmark 平台、provider 管理後台或 LLM 工具呼叫。
