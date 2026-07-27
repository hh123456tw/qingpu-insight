const COMPATIBILITY_MESSAGE =
  "相容模式：後端版本較舊，目前顯示最近 100 筆；" +
  "重新啟動 Web 後可顯示完整群組地圖";

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function validateGroupedPayload(payload) {
  if (!payload || typeof payload !== "object"
      || !Array.isArray(payload.items)
      || !Number.isFinite(payload.total_records)
      || !Number.isFinite(payload.located_records)
      || !Number.isFinite(payload.unlocated_records)
      || !Number.isFinite(payload.group_count)) {
    throw new Error("map invalid response");
  }
  return payload;
}

export function transactionItemsToMapPayload(items) {
  if (!Array.isArray(items)) throw new Error("transactions invalid response");
  const located = items.filter(function (item) {
    return item && finiteNumber(item.latitude) && finiteNumber(item.longitude);
  }).map(function (item) {
    return {
      latitude: item.latitude,
      longitude: item.longitude,
      record_count: 1,
      median_total_price: item.total_price ?? null,
      latest_transaction_date: item.transaction_date ?? null,
    };
  });
  return {
    mode: "compatibility",
    total_records: items.length,
    located_records: located.length,
    unlocated_records: items.length - located.length,
    group_count: located.length,
    items: located,
  };
}

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
  if (payload && payload.mode === "compatibility") {
    return COMPATIBILITY_MESSAGE;
  }
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
      const primary = await fetchImpl(
        "/api/market/map-points?" + params.toString(),
        { signal: controller.signal }
      );
      let payload;
      if (primary.status === 404) {
        const recentParams = withRecentLimit(baseParams);
        const fallback = await fetchImpl(
          "/api/transactions?" + recentParams.toString(),
          { signal: controller.signal }
        );
        if (!fallback.ok) throw new Error("transactions " + fallback.status);
        const recentPayload = await fallback.json();
        payload = transactionItemsToMapPayload(recentPayload.items);
      } else {
        if (!primary.ok) throw new Error("map " + primary.status);
        payload = validateGroupedPayload(await primary.json());
      }
      render(payload);
      return payload;
    } catch (error) {
      if (error.name === "AbortError") return null;
      showError("地圖資料載入失敗：" + error.message);
      return null;
    }
  };
}
