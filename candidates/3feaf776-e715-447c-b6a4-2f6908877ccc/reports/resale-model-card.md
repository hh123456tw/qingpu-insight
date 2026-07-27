# resale 估價模型卡

## 資料期間
- 2018-04-13 至 2026-06-13

## 時間切割
- 訓練集、校準集與測試集依交易日期時間順序切割

## 候選模型
- ridge：MAE = 85,582
- random_forest：MAE = 64,964
- hist_gradient_boosting：MAE = 63,726 ✓
- ridge：MAE = 85,582
- random_forest：MAE = 64,401
- hist_gradient_boosting：MAE = 64,197 ✓
- ridge：MAE = 85,582
- random_forest：MAE = 64,900
- hist_gradient_boosting：MAE = 62,080 ✓

## 近期資料加權
- 半衰期 48 個月，最低權重 0.10；評估指標不加權。

## 特徵實驗與消融
- base：baseline，MAE = 84120.00369299676
- enhanced：hist_gradient_boosting，MAE = 64197.10534612627
- without_transaction_trend：hist_gradient_boosting，MAE = 68054.92660753528
- without_station_building_type：hist_gradient_boosting，MAE = 70770.06577238294
- without_age_band：hist_gradient_boosting，MAE = 64510.99926535224
- without_area_band：hist_gradient_boosting，MAE = 63784.27546216292
- without_floor_band：hist_gradient_boosting，MAE = 62717.60455677917

## 三期時間回測
- 2026-06-30：MAE = 55037.93253810551，通過
- 2025-06-30：MAE = 115624.86348283228，未通過
- 2024-06-30：MAE = 68833.67457571694，通過

## 發布檢查
- overall_mae_improved：通過
- stations_within_limit：通過
- a18_improved：通過
- backtests_passed：通過
- backtest_stations_within_limit：未通過
- candidate_fresh：通過
- recommended：未通過
- 保留原因：backtest_station_regression

## 分群誤差
- overall：MAE = 62080.069688312586，MAPE = 17.63689622297795%，n = 487.0
- station:A19：MAE = 51151.81881806265，MAPE = 12.395776497081451%，n = 189.0
- station:A18：MAE = 72316.66882843155，MAPE = 21.949704391788035%，n = 260.0
- station:A17：MAE = 46393.84963689977，MAPE = 14.19588317834155%，n = 38.0
- building_type:住宅大樓(11層含以上有電梯)：MAE = 49349.48422747419，MAPE = 11.155977322434445%，n = 340.0
- building_type:華廈(10層含以下有電梯)：MAE = 66234.67983533334，MAPE = 33.00603551806095%，n = 125.0

## 區間覆蓋率
- 校準分位數：117,136 元/坪
- 測試集覆蓋率依校準分位數計算

## 限制
- 路段群組重疊：94 筆

## 不適用情境
- 輸入數值超出特徵訓練範圍
- 交易類型與模型類型不符
- 缺乏附近站點近期交易資料

## 近期交易權重
- 近期交易加權半衰期：48 個月

## 版本狀態
- 此版本為未發布候選模型，不會替換網站正式估價模型。
