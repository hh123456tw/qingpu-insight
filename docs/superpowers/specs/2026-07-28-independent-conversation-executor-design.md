# 獨立 591 分析工作佇列設計

## 目的

模型訓練與 591 物件分析目前共用一個 `LocalJobExecutor(max_workers=1)`。
當訓練執行時，591 匯入、更新與 AI 回覆只能停留在排隊狀態。

本次調整讓兩類工作可以同時執行，同時維持小型專題需要的資源上限與簡單架構。

## 核准方案

採用兩個獨立的單工 executor：

- 管理 executor：模型訓練、資料更新、模型發布與其他管理工作，`max_workers=1`。
- 對話 executor：591 匯入、證據更新與 AI 回覆，`max_workers=1`。

兩個佇列之間可以並行；每個佇列內仍依序執行。

不採用以下方案：

- 將共用 executor 改成 `max_workers=2`：管理工作與對話工作仍互相搶占，無法表達資源邊界。
- 為每個請求建立 executor：生命週期與關閉行為較複雜，超出專題需求。

## 元件與資料流

`create_app` 建立對話 runtime 時，一律為 production 對話服務建立專用
`LocalJobExecutor`。對話服務不再借用 `AdminServices.executor`。

模型訓練仍由原本的管理 executor 執行。對話的 job 紀錄仍可共用同一個
`JobService`／MySQL `job_runs`，因為 idempotency key 已按工作與對話區分。

流程如下：

1. 使用者啟動模型訓練，管理 executor 執行訓練。
2. 訓練尚未完成時，使用者貼上 591 URL 或送出 AI 問題。
3. 對話 executor 立即執行該工作，不等待管理 executor。
4. 同一對話的既有 busy 與 active reply 防護維持不變。

## 生命週期與錯誤處理

- Flask app 關閉時，管理 executor 與對話 executor 都必須各自 shutdown。
- 對話 runtime 建立失敗時，只關閉已建立的對話 executor，不影響管理中心。
- executor 的例外處理與 job 狀態轉換沿用 `LocalJobExecutor`。
- 不新增前端開關；管理頁與對話頁繼續使用現有 pending／running／failed 狀態。

## 測試與驗收

使用 TDD 驗證：

- 有管理服務時，對話服務取得的 executor 與管理 executor 不是同一個物件。
- 對話 executor 仍為單工，既有同一對話互斥規則不變。
- app shutdown 會同時關閉兩個 executor。
- 端對端並行測試：阻塞管理 executor 的工作時，對話 executor 的工作仍能開始。
- 完整 Python、JavaScript 契約與 Ruff 驗證通過。

## 非目標

- 不允許兩次模型訓練並行。
- 不增加分散式工作佇列、Redis、Celery 或多程序部署。
- 不調整 AutoML 搜尋範圍或模型發布門檻。
- 不改變 591 爬蟲及 LLM provider 的內容。
