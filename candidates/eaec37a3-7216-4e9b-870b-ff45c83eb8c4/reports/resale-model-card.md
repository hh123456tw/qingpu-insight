# resale 估價模型卡

## 資料期間
- 2018-04-13 至 2026-06-13

## 車位估值政策
- 政策版本：1
- 房屋模型不包含車位特徵
- 坡道平面：1,800,000 元（樣本數 11663）
- 坡道機械：900,000 元（樣本數 346）
- 一樓平面：1,400,000 元（樣本數 31）
- 市場中位數：1,800,000 元（樣本數 12055）

## 時間切割
- 訓練集、校準集與測試集依交易日期時間順序切割

## 候選模型
- ridge：MAE = 78,554
- random_forest：MAE = 49,544
- hist_gradient_boosting：MAE = 43,445 ✓
- HGB（對數價格）：MAE = 44,883
- ridge：MAE = 78,554
- random_forest：MAE = 49,355
- hist_gradient_boosting：MAE = 43,343 ✓
- HGB（對數價格）：MAE = 43,901
- ridge：MAE = 78,554
- random_forest：MAE = 49,341
- hist_gradient_boosting：MAE = 43,110 ✓
- HGB（對數價格）：MAE = 44,534
- HGB（對數價格）只有在驗證與跨年度回測較佳時才會入選。

## 近期資料加權
- 半衰期 48 個月，最低權重 0.10；評估指標不加權。

## 特徵實驗與消融
- base：baseline，MAE = 79053.44160792422
- enhanced：hist_gradient_boosting，MAE = 43343.37346862021
- without_transaction_trend：hist_gradient_boosting，MAE = 55056.37781974651
- without_station_building_type：hist_gradient_boosting，MAE = 43988.98203724007
- without_age_band：hist_gradient_boosting，MAE = 43655.176587112765
- without_area_band：hist_gradient_boosting，MAE = 43860.32086497423
- without_floor_band：hist_gradient_boosting，MAE = 43068.896301529916

## 三期時間回測
- 2026-06-30：MAE = 41403.15525775493，通過
- 2025-06-30：MAE = 103825.29266244065，未通過
- 2024-06-30：MAE = 57147.276831131785，通過

## 發布檢查
- overall_mae_improved：通過
- stations_within_limit：通過
- a18_improved：通過
- backtests_passed：通過
- backtest_stations_within_limit：通過
- candidate_fresh：通過
- recommended：通過
- parking_price_consistency：通過

## 分群誤差
- overall：MAE = 42355.84819917318，MAPE = 10.998281023281518%，n = 630.0
- station:A18：MAE = 40198.39226983694，MAPE = 10.718602580788348%，n = 379.0
- station:A17：MAE = 41439.28745179155，MAPE = 10.9745399124569%，n = 63.0
- station:A19：MAE = 47012.332902915085，MAPE = 11.57005665991377%，n = 188.0
- building_type:住宅大樓(11層含以上有電梯)：MAE = 44431.176693312875，MAPE = 10.076735242016545%，n = 432.0
- building_type:華廈(10層含以下有電梯)：MAE = 34079.53994059369，MAPE = 11.952189327252949%，n = 173.0

## 區間覆蓋率
- 校準分位數：84,556 元/坪
- 測試集覆蓋率依校準分位數計算

## 限制
- 路段群組重疊：90 筆

## 不適用情境
- 輸入數值超出特徵訓練範圍
- 交易類型與模型類型不符
- 缺乏附近站點近期交易資料

## 近期交易權重
- 近期交易加權半衰期：48 個月

## 版本狀態
- 此版本為未發布候選模型，不會替換網站正式估價模型。
