# 改善路線圖（2026-10）

依據：專案全面健檢、國際 AVM 做法研究、台灣房價特徵研究（見 [問題紀錄 §18、§19](project-issue-log.md)）。

## 評估規則（所有模型改動都適用）

- 只用 final test 之前的 6 個半年滾動回測選設定；主要指標為**總價 MAPE**，另報中位數誤差（median APE）與誤差在 ±5%／±10%／±20% 內的比例（PPE5／PPE10／PPE20），並依預測類型（同棟錨點／附近成交／無歷史）分開呈現。
- 所有特徵只能使用預測當下之前的資料，且必須是估價時使用者拿得到的輸入。
- 差距小於配對標準誤的 2 倍視為打平，不採用。
- 與時間有關的改動另報「部署落差」回測（模型資料在窗口開始前 3／6 個月就截止，模擬正式模型在資料截止後數個月才估價）與帶正負號的偏誤（總價 MSPE、中位數偏誤）。
- final test 只在最後、依事先登記的清單看一次。

## 第一階段：模型

| # | 項目 | 內容 | 預期 | 狀態（2026-10，見[問題紀錄 §20](project-issue-log.md)） |
|---|---|---|---|---|
| 1.1 | 評估指標 | median APE、PPE5/10/20、依預測類型分組；新增「留建案」測試量新建案真實誤差 | 與 Zillow／IAAO 可比 | 完成 |
| 1.2 | 樓層位置 | 1 樓、頂樓、2–3 樓旗標；錨點看不到同棟內的樓層價差 | 小到中 | 不採用（回測 +0.00 ± 0.06） |
| 1.3 | 公設比 | 由主建物／附屬建物／陽台面積推算（沿用 `codex/resale-common-area-community-features` 分支） | 對無歷史建物有幫助 | 採用為選填輸入（提供時 −2.14 ± 0.26）；需重跑 `analyse` + `market-build`，表單與 591 帶入待第二階段 |
| 1.4 | 市場時間點 | 央行信用管制（2020-12 起七波，2024-09-20 第七波）階梯變數 | 改善跨期外推 | 不採用（與交易月份重複） |
| 1.5 | 錨點時間調整 | 過去成交價先以青埔在地價格指數調整到估價月份 | 中 | 不採用（+0.10 ± 0.16），程式預設關閉 |
| 1.6 | 區間校準 | 依預測類型分組並加重近期殘差，目標覆蓋率由 77.6% 回到約 90% | 覆蓋率 | 改為總價校準（回測總價覆蓋率 85.3% → 90.3%）；分組與近期加權不採用 |
| 1.7 | 上漲期低估（[§22](project-issue-log.md)） | 同棟價格指數扣除時間趨勢、錨點以指數調整到估價月份、資料之後以衰減趨勢外推；線上以估價當天為交易月份 | 偏誤減半 | 採用（`time_trend=True`）：回測資料截止後 0／3／6 個月估價，總價中位數偏誤 −2.3／−5.4／−7.7% → −0.7／−2.7／−3.4%，MAPE −0.12／−0.44／−0.97 個百分點；待以新程式重訓正式模型 |

不做：建商品牌（同棟錨點已吸收，台灣無學術證據）、學區／噪音／淹水／使用分區（青埔區內幾乎無差異）、凶宅名單（法律風險）、591 開價（條款限制且會偷看未來）。

待定：TabPFN v2（需安裝 PyTorch 並確認授權）。

## 第二階段：產品

| # | 項目 | 內容 | 狀態 |
|---|---|---|---|
| 2.1 | 首頁地址定位 | 估價表單可輸入門牌地址，以既有門牌資料轉成座標，讓首頁也能使用同棟錨點 | ✅ 完成：本機門牌索引（`address_location.py`）、API 另接受 TWD97／經緯度，結果顯示定位方式與估價基準（`model.price_anchor`） |
| 2.2 | 模型版本名稱 | 版本字串加入模型種類，避免不同模型同名 | ✅ 完成：新模型為 `{市場}-{模型名稱}-{日期}-{雜湊}`，舊 artifact 照常載入 |
| 2.3 | 公設比輸入 | 首頁表單與 591 助理帶入公設比（說明不含車位的算法，或改收主建物＋附屬建物＋陽台坪數）；API 已支援 `common_area_ratio` | ✅ 完成：表單與 API 收權狀主建物／附屬建物／陽台坪數（或不含車位的公設比，矛盾時拒絕），以 `market_cleaning.common_area_ratio` 換算；591 助理只在定義可對齊時帶入；結果頁標示是否使用 |

## 第三階段：工程

| # | 項目 | 內容 |
|---|---|---|
| 3.1 | 測試加速 | ✅ 完成：全套由約 3.5 分鐘降到 74 秒；訓練測試縮小迭代、移除 DNS 等待與真實 sleep、Baseline 預測向量化 |
| 3.2 | 拆分 `web.py` | ✅ 完成：`web.py` 由 2,295 行降到約 210 行，只負責組裝；路由拆到 `web_routes/`（pages、market、valuation、jobs、reports、ops），服務組裝在 `web_composition.py`，591 助理估價與表單解析移到 `conversation_valuation.py`、`valuation_request.py`。本機＋CSRF 防護統一由 `web_routes/guards.py` 的 before_request 執行（`hmac.compare_digest`），取代 web.py／admin_web.py／conversation_web.py 內約 15 處重複檢查；`tests/test_web_guards.py` 列舉 `app.url_map` 確認所有管理、維運、工作、報告與對話路由都受防護 |
| 3.3 | 合併管理 API | ✅ 完成：`/api/admin/*` 為正式命名空間（新增 `/api/admin/health`、`GET /api/admin/backups`、`/api/admin/restore-previews`、`/api/admin/restores`），`/api/ops/*` 保留為同一實作的別名；管理頁已改用 `/api/admin/*`，說明見 README「Web 管理與維運端點」 |
| 3.4 | 清理預售屋殘留 | ✅ 完成（保守）：移除訓練服務中不可能執行的 presale 分支（`is_resale` 為假時的特徵、基準月數、錨點、診斷與回測）、AutoML 依 `use_recency_weights` 推論 presale 的邏輯與發布 smoke 的 presale 輸入。保留：預售屋成交資料（`build_anchor_table` 的價格錨點）、歷史 presale 候選／AutoML 輸出／報告的讀取、MySQL schema、CLI 與 591 助理的新建案擷取、API 的「僅支援中古屋」檢查；`model_tuning` 的 presale 驗證仍有測試覆蓋，暫不移除 |

程式品質修正（2026-10）：未預期錯誤改回 500 `internal_error`（只有市場／刊登資料讀取失敗仍回 503）；估價依資料版本快取模型資料表，估價紀錄最多保留 5,000 筆；「主要影響因素」改顯示中文特徵名稱（共用 `static/feature_labels.js`）；管理頁備份表改用 `textContent`；AI 助理 API 不再回傳 pydantic 例外文字，改回欄位代碼；`run_tuned_model_experiment` 移除不可能的 baseline 分支並共用 `evaluate_fitted_candidate`；`admin.html` 內嵌的約 1,400 行 JavaScript 移到 `static/admin_page.js`。

注意：joblib 模型檔綁定模組路徑（`qingpu_insight.valuation`、`qingpu_insight.anchor_model`），搬移模組時必須保留原路徑。
