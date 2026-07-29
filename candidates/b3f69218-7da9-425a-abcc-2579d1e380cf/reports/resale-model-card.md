# resale 估價模型卡

## 資料期間
- 2018-04-13 至 2026-06-13

## 車位估值政策
- 政策版本：1
- 房屋模型不包含車位特徵
- 坡道平面：1,800,000 元（樣本數 11829）
- 坡道機械：900,000 元（樣本數 346）
- 一樓平面：1,400,000 元（樣本數 31）
- 市場中位數：1,800,000 元（樣本數 12221）

## 時間切割
- 訓練集、校準集與測試集依交易日期時間順序切割

## 候選模型
- ridge：MAE = 85,709
- random_forest：MAE = 74,596
- hist_gradient_boosting：MAE = 58,868 ✓
- ridge：MAE = 85,709
- random_forest：MAE = 75,152
- hist_gradient_boosting：MAE = 60,222 ✓
- ridge：MAE = 85,709
- random_forest：MAE = 74,538
- hist_gradient_boosting：MAE = 59,687 ✓

## 近期資料加權
- 半衰期 48 個月，最低權重 0.10；評估指標不加權。

## 特徵實驗與消融
- base：baseline，MAE = 84120.00369299676
- enhanced：hist_gradient_boosting，MAE = 60221.974506260056
- without_transaction_trend：hist_gradient_boosting，MAE = 79625.11429680878
- without_station_building_type：hist_gradient_boosting，MAE = 76548.86009367017
- without_age_band：hist_gradient_boosting，MAE = 58469.616128755544
- without_area_band：hist_gradient_boosting，MAE = 59690.163296408806
- without_floor_band：hist_gradient_boosting，MAE = 60024.00494906305

## 三期時間回測
- 2026-06-30：MAE = 54246.17440405474，通過
- 2025-06-30：MAE = 119250.86393917196，未通過
- 2024-06-30：MAE = 68479.97800126218，通過

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
- overall：MAE = 58867.51346632918，MAPE = 15.218966591624891%，n = 487.0
- station:A19：MAE = 52434.32226192354，MAPE = 12.843177843018818%，n = 189.0
- station:A18：MAE = 65477.47969709019，MAPE = 16.990357665412805%，n = 260.0
- station:A17：MAE = 45638.090246192485，MAPE = 14.91534538903779%，n = 38.0
- building_type:住宅大樓(11層含以上有電梯)：MAE = 50265.26159772736，MAPE = 11.382956276859742%，n = 340.0
- building_type:華廈(10層含以下有電梯)：MAE = 51246.28653702779，MAPE = 22.724691592203875%，n = 125.0

## 區間覆蓋率
- 校準分位數：118,923 元/坪
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
