# resale 估價模型卡

## 資料期間
- 2019-03-05 至 2026-06-13

## 車位估值政策
- 政策版本：1
- 房屋模型不包含車位特徵
- 坡道平面：1,900,000 元（樣本數 1855）
- 市場中位數：1,900,000 元（樣本數 1894）

## 時間切割
- 訓練集、校準集與測試集依交易日期時間順序切割

## 候選模型
- ridge：MAE = 60,248
- random_forest：MAE = 60,563
- hist_gradient_boosting：MAE = 57,999 ✓
- HGB（對數價格）：MAE = 58,482
- ridge：MAE = 60,248
- random_forest：MAE = 60,479
- hist_gradient_boosting：MAE = 55,880 ✓
- HGB（對數價格）：MAE = 59,302
- ridge：MAE = 60,248
- random_forest：MAE = 60,327
- hist_gradient_boosting：MAE = 56,029 ✓
- HGB（對數價格）：MAE = 58,478
- HGB（對數價格）只有在驗證與跨年度回測較佳時才會入選。

## 近期資料加權
- 半衰期 48 個月，最低權重 0.10；評估指標不加權。

## 特徵實驗與消融
- base：ridge，MAE = 60927.507616983254
- enhanced：hist_gradient_boosting，MAE = 55880.40623801644
- without_transaction_trend：hist_gradient_boosting，MAE = 65821.30743929249
- without_station_building_type：hist_gradient_boosting，MAE = 55895.079725843
- without_age_band：hist_gradient_boosting，MAE = 54566.59135797464
- without_area_band：hist_gradient_boosting，MAE = 55868.92281447631
- without_floor_band：hist_gradient_boosting，MAE = 55417.904994137134

## 三期時間回測
- 2026-06-30：MAE = 65482.26172230086，通過
- 2025-06-30：MAE = 77822.47238447842，通過
- 2024-06-30：MAE = 67542.00547211157，通過

## 發布檢查
- overall_mae_improved：通過
- stations_within_limit：未通過
- a18_improved：未通過
- backtests_passed：通過
- backtest_stations_within_limit：通過
- candidate_fresh：通過
- recommended：未通過
- parking_price_consistency：通過
- 保留原因：station_regression, a18_not_improved

## 分群誤差
- overall：MAE = 65482.26172230086，MAPE = 23.29988304375055%，n = 624.0
- station:A18：MAE = 77548.7696824814，MAPE = 31.110696407817628%，n = 378.0
- station:A17：MAE = 40930.83886379696，MAPE = 10.402571951529696%，n = 63.0
- station:A19：MAE = 49010.12855911779，MAPE = 11.6061297497208%，n = 183.0
- building_type:住宅大樓(11層含以上有電梯)：MAE = 47369.7321573162，MAPE = 10.410566927073276%，n = 426.0
- building_type:華廈(10層含以下有電梯)：MAE = 111106.07078798932，MAPE = 55.76824484322361%，n = 173.0

## 區間覆蓋率
- 校準分位數：100,612 元/坪
- 測試集覆蓋率依校準分位數計算

## 限制
- 路段群組重疊：84 筆

## 不適用情境
- 輸入數值超出特徵訓練範圍
- 交易類型與模型類型不符
- 缺乏附近站點近期交易資料

## 近期交易權重
- 近期交易加權半衰期：48 個月

## 版本狀態
- 此版本為未發布候選模型，不會替換網站正式估價模型。
