export function withRecentLimit(baseParams) {
  const params = new URLSearchParams(baseParams);
  params.set("limit", "100");
  return params;
}

export function withMapView(baseParams, view) {
  const params = new URLSearchParams(baseParams);
  params.set("zoom", String(view.zoom));
  params.set("south", String(view.south));
  params.set("west", String(view.west));
  params.set("north", String(view.north));
  params.set("east", String(view.east));
  return params;
}

export function mapStatusText(payload) {
  const number = new Intl.NumberFormat("zh-TW");
  return [
    "符合 " + number.format(payload.total_records || 0) + " 筆",
    "有座標 " + number.format(payload.located_records || 0) + " 筆",
    "未定位 " + number.format(payload.unlocated_records || 0) + " 筆",
    "目前顯示 " + number.format(payload.group_count || 0) + " 個群組",
  ].join("｜");
}

export function markerRadius(recordCount) {
  return Math.min(16, Math.max(5, 4 + Math.log2(Math.max(1, recordCount))));
}

export function createMapLoader({ fetchImpl, render, showError }) {
  let controller = null;
  return async function load(baseParams, view) {
    if (controller !== null) controller.abort();
    controller = new AbortController();
    const params = withMapView(baseParams, view);
    try {
      const response = await fetchImpl(
        "/api/market/map-points?" + params.toString(),
        { signal: controller.signal }
      );
      if (!response.ok) throw new Error("map " + response.status);
      const payload = await response.json();
      render(payload);
      return payload;
    } catch (error) {
      if (error.name === "AbortError") return null;
      showError("地圖資料載入失敗：" + error.message);
      return null;
    }
  };
}
