document.addEventListener("DOMContentLoaded", async function () {
  const typeSelect = document.getElementById("transaction-type");
  const stationFilter = document.getElementById("station-filter");
  const controls = document.querySelector(".controls");
  const dateFrom = document.getElementById("date-from");
  const dateTo = document.getElementById("date-to");
  const areaPingMin = document.getElementById("area-ping-min");
  const areaPingMax = document.getElementById("area-ping-max");
  const buildingType = document.getElementById("building-type");
  const bedrooms = document.getElementById("bedrooms");
  const statusEl = document.getElementById("status");
  const medianPrice = document.getElementById("median-price");
  const recordCount = document.getElementById("record-count");
  const medianTotal = document.getElementById("median-total");
  const latestDate = document.getElementById("latest-date");
  const mapDiv = document.getElementById("market-map");
  const canvas = document.getElementById("price-trend");
  const transactionsDiv = document.getElementById("recent-transactions");

  const marketMapUi = await import("/static/market_map.mjs");
  var marketResults = typeof QingpuMarketResults !== "undefined" ? QingpuMarketResults : null;

  var recentItems = [];
  var recentExpanded = false;
  const mapStatus = document.createElement("p");
  mapStatus.id = "map-data-status";
  mapStatus.setAttribute("role", "status");
  mapStatus.setAttribute("aria-live", "polite");
  mapDiv.parentNode.insertBefore(mapStatus, mapDiv);

  const money = new Intl.NumberFormat("zh-TW", {
    style: "currency",
    currency: "TWD",
    maximumFractionDigits: 0,
  });

  function formatWan(value) {
    return (
      new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 1 }).format(
        value / 10000
      ) + " 萬"
    );
  }

  var map = null;
  var markerLayer = null;
  var chart = null;
  var lastController = null;
  var mapMoveHandlerRegistered = false;
  function buildParams() {
    var params = new URLSearchParams();
    params.set("transaction_type", typeSelect.value);
    var checks = stationFilter.querySelectorAll(
      'input[type="checkbox"]:checked'
    );
    checks.forEach(function (cb) {
      params.append("station", cb.value);
    });
    if (dateFrom.value) params.set("date_from", dateFrom.value);
    if (dateTo.value) params.set("date_to", dateTo.value);
    if (areaPingMin.value) params.set("area_ping_min", areaPingMin.value);
    if (areaPingMax.value) params.set("area_ping_max", areaPingMax.value);
    if (buildingType.value) params.append("building_type", buildingType.value);
    if (bedrooms.value) params.append("bedrooms", bedrooms.value);
    return params;
  }

  function initMap() {
    if (map === null) {
      map = L.map(mapDiv, { zoomControl: true }).setView([25.01, 121.2], 14);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution:
          '&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>',
        maxZoom: 19,
      }).addTo(map);
      markerLayer = L.layerGroup().addTo(map);
    }
  }



  function initChart() {
    if (chart === null) {
      chart = new Chart(canvas, {
        type: "line",
        data: { labels: [], datasets: [] },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          interaction: { intersect: false, mode: "index" },
          scales: {
            x: { ticks: { maxTicksLimit: 10 } },
          },
          plugins: {
            legend: { position: "bottom" },
          },
        },
      });
    }
  }

  function updateChart(trends) {
    var labels = trends.map(function (t) {
      return t.month.replace("-", "年") + "月";
    });
    var prices = trends.map(function (t) {
      return t.median_unit_price_per_ping_twd / 10000;
    });
    var volumes = trends.map(function (t) {
      return t.record_count;
    });

    chart.data.labels = labels;
    chart.data.datasets = [
      {
        label: "中位單價 (萬/坪)",
        data: prices,
        borderColor: "#147d6f",
        backgroundColor: "rgba(20,125,111,0.1)",
        yAxisID: "y",
        tension: 0.3,
      },
      {
        label: "成交量",
        data: volumes,
        borderColor: "#dd7a45",
        backgroundColor: "rgba(221,122,69,0.1)",
        yAxisID: "y1",
        tension: 0.3,
      },
    ];
    chart.update();
  }

  var display = null;

  function buildTable(items) {
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var headerRow = document.createElement("tr");
    ["日期", "生活圈", "類型", "坪數", "總價", "單價"].forEach(function (
      label
    ) {
      var th = document.createElement("th");
      th.textContent = label;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    items.forEach(function (item) {
      var row = document.createElement("tr");
      var cells = [
        item.transaction_date ? item.transaction_date.slice(0, 10) : "",
        item.station_code || "",
        item.building_type || "",
        item.building_area_ping
          ? item.building_area_ping.toFixed(1) + " 坪"
          : "",
        item.total_price_twd
          ? (display ? display.formatTotalWan(item.total_price_twd) : money.format(item.total_price_twd))
          : "",
        item.unit_price_per_ping_twd
          ? (display ? display.formatUnitWan(item.unit_price_per_ping_twd) : money.format(item.unit_price_per_ping_twd))
          : "",
      ];
      cells.forEach(function (val) {
        var td = document.createElement("td");
        td.textContent = val;
        row.appendChild(td);
      });
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    return table;
  }

  function updateFilterChips() {
    if (!marketResults) return;
    var state = {
      transactionTypeLabel: typeSelect.options[typeSelect.selectedIndex].text,
      stations: Array.from(stationFilter.querySelectorAll('input[type="checkbox"]:checked')).map(function (cb) { return cb.value; }).sort(),
      areaMin: areaPingMin.value,
      areaMax: areaPingMax.value,
    };
    var chips = marketResults.filterSummary(state);
    var container = document.getElementById("active-market-filters");
    var children = chips.map(function (chip) {
      var span = document.createElement("span");
      span.className = "filter-chip";
      span.textContent = chip;
      return span;
    });
    var btn = document.createElement("button");
    btn.id = "modify-filters";
    btn.className = "modify-filters";
    btn.textContent = "修改篩選";
    btn.addEventListener("click", function () {
      document.querySelector(".controls").scrollIntoView({ behavior: "smooth" });
      document.getElementById("transaction-type").focus();
    });
    children.push(btn);
    container.replaceChildren.apply(container, children);
  }

  function updateRecentTable() {
    if (!marketResults) return;
    var items = marketResults.visibleRecent(recentItems, recentExpanded);
    var container = document.getElementById("recent-transactions");
    if (items.length === 0) {
      container.textContent = "目前條件下沒有成交資料";
      return;
    }
    container.replaceChildren(buildTable(items));
    if (recentItems.length > 8 || recentExpanded) {
      var toggle = document.createElement("button");
      toggle.className = "recent-toggle";
      toggle.textContent = marketResults.recentToggleLabel(recentItems.length, recentExpanded);
      toggle.addEventListener("click", function () {
        recentExpanded = !recentExpanded;
        updateRecentTable();
      });
      container.appendChild(toggle);
    }
  }

  function fetchData() {
    if (lastController !== null) lastController.abort();
    lastController = new AbortController();
    var params = buildParams();

    initMap();
    initChart();

    loadMap(params, currentMapView());
    if (map !== null && !mapMoveHandlerRegistered) {
      var mapMoveTimer = null;
      map.on("moveend", function () {
        if (mapMoveTimer !== null) clearTimeout(mapMoveTimer);
        mapMoveTimer = setTimeout(function () {
          loadMap(buildParams(), currentMapView());
        }, 200);
      });
      mapMoveHandlerRegistered = true;
    }

    display = typeof QingpuDisplayFormat !== "undefined" ? QingpuDisplayFormat : null;
    if (!marketResults) return;

    var recentParams = marketMapUi.withRecentLimit(params);

    marketResults.loadSection(
      "/api/market/summary?" + params.toString(),
      window.fetch || fetch,
      function (summary) {
        medianPrice.textContent = summary.median_unit_price_per_ping_twd
          ? (display ? display.formatUnitWan(summary.median_unit_price_per_ping_twd) : formatWan(summary.median_unit_price_per_ping_twd))
          : "—";
        recordCount.textContent = summary.record_count != null ? summary.record_count : "—";
        medianTotal.textContent = summary.median_total_price_twd
          ? (display ? display.formatTotalWan(summary.median_total_price_twd) : money.format(summary.median_total_price_twd))
          : "—";
        latestDate.textContent = summary.latest_transaction_date
          ? summary.latest_transaction_date.slice(0, 10)
          : "—";
        statusEl.textContent = "";
      },
      function (err) {
        statusEl.textContent = "摘要載入失敗：" + err.message;
      }
    );

    marketResults.loadSection(
      "/api/market/trends?" + params.toString(),
      window.fetch || fetch,
      function (trends) {
        updateChart(trends.items || []);
      },
      function (err) {
        console.warn("trends error:", err.message);
      }
    );

    marketResults.loadSection(
      "/api/transactions?" + recentParams.toString(),
      window.fetch || fetch,
      function (transactions) {
        recentItems = transactions.items || [];
        recentExpanded = false;
        updateRecentTable();
        updateFilterChips();
      },
      function (err) {
        var container = document.getElementById("recent-transactions");
        container.textContent = "成交資料載入失敗：" + err.message;
      }
    );
  }

  function renderMap(payload) {
    markerLayer.clearLayers();
    if (!payload.items || !payload.items.length) {
      mapStatus.textContent = marketMapUi.mapStatusText(payload);
      return;
    }
    payload.items.forEach(function (item) {
      var unitPrice = (
        typeof item.median_unit_price_per_ping_twd === "number"
        && Number.isFinite(item.median_unit_price_per_ping_twd)
      ) ? (display ? display.formatUnitWan(item.median_unit_price_per_ping_twd) : formatWan(item.median_unit_price_per_ping_twd)) : "—";
      L.circleMarker([item.latitude, item.longitude], {
        radius: marketMapUi.markerRadius(item.record_count),
        color: "#0b5f55",
        weight: 2,
        fillColor: "#22a896",
        fillOpacity: 0.82,
      })
        .bindPopup(
          "成交 " + item.record_count + " 筆<br>" +
          "中位單價 " + unitPrice + "<br>" +
          "最近成交 " + (item.latest_transaction_date || "—")
        )
        .addTo(markerLayer);
    });
    mapStatus.textContent = marketMapUi.mapStatusText(payload);
  }

  function currentMapView() {
    var bounds = map.getBounds();
    return {
      zoom: map.getZoom(),
      south: Number(bounds.getSouth().toFixed(6)),
      west: Number(bounds.getWest().toFixed(6)),
      north: Number(bounds.getNorth().toFixed(6)),
      east: Number(bounds.getEast().toFixed(6)),
    };
  }

  const loadMap = marketMapUi.createMapLoader({
    fetchImpl: fetch,
    render: renderMap,
    showError: function (message) {
      markerLayer.clearLayers();
      mapStatus.textContent = message;
    },
  });

  fetchData();

  controls.addEventListener("change", fetchData);
});

// --- Valuation UI ---

(function () {
  const form = document.getElementById("valuation-form");
  const typeSelect = document.getElementById("valuation-type");
  const ageInput = document.getElementById("valuation-age");
  const ageLabel = document.getElementById("valuation-age-label");
  const resultSection = document.getElementById("valuation-result");
  const statusEl = document.getElementById("valuation-status");

  function toggleAge() {
    if (typeSelect.value === "presale") {
      ageInput.disabled = true;
      ageInput.value = "";
      ageLabel.style.opacity = "0.4";
    } else {
      ageInput.disabled = false;
      ageLabel.style.opacity = "1";
    }
  }
  typeSelect.addEventListener("change", toggleAge);
  toggleAge();

  var display = typeof QingpuDisplayFormat !== "undefined" ? QingpuDisplayFormat : null;

  function money(val) {
    return new Intl.NumberFormat("zh-TW", {
      style: "currency", currency: "TWD", maximumFractionDigits: 0,
    }).format(val);
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    }
    if (children) {
      (Array.isArray(children) ? children : [children]).forEach(function (c) {
        if (typeof c === "string") { node.appendChild(document.createTextNode(c)); }
        else if (c) { node.appendChild(c); }
      });
    }
    return node;
  }

  function renderPricePosition(low, point, high, asking) {
    if (!display || typeof document === "undefined") return null;
    var state = display.pricePositionState(low, point, high, asking);
    if (!state) return null;
    var posLabel = "";
    if (state.askingPosition === "inside") posLabel = "內";
    else if (state.askingPosition === "below") posLabel = "下";
    else if (state.askingPosition === "above") posLabel = "上";
    else posLabel = "外";
    var ariaLabel = "估值區間：低標 " + display.formatTotalWan(low)
      + " 至高標 " + display.formatTotalWan(high)
      + "，估值點 " + display.formatTotalWan(point);
    if (asking != null) {
      ariaLabel += "，開價 " + display.formatTotalWan(asking)
        + "（位於區間" + posLabel + "）";
    }
    var wrapper = el("div", { "class": "price-position", "role": "img", "aria-label": ariaLabel });
    var track = el("div", { "class": "price-range-track" });
    track.appendChild(el("span", {
      "class": "price-marker price-marker-point",
      "style": "left: " + state.pointPercent + "%",
    }));
    if (state.askingPosition !== "missing") {
      track.appendChild(el("span", {
        "class": "price-marker price-marker-asking",
        "style": "left: " + state.askingPercent + "%",
      }));
    }
    wrapper.appendChild(track);
    wrapper.appendChild(el("div", { "class": "price-labels" }, [
      el("span", {}, ["低標 " + display.formatTotalWan(low)]),
      el("span", {}, ["估值 " + display.formatTotalWan(point)]),
      el("span", {}, ["開價 " + (asking != null ? display.formatTotalWan(asking) : "—")]),
      el("span", {}, ["高標 " + display.formatTotalWan(high)]),
    ]));
    return wrapper;
  }

  function renderValuation(result, askingVal) {
    resultSection.replaceChildren();

    var cards = [];

    // Price interval card
    var priceCard = el("div", { "class": "valuation-card" }, [
      el("h3", {}, ["估價結果"]),
      el("p", { "class": "estimated-price" }, [display ? display.formatTotalWan(result.estimated_total_price_twd) : money(result.estimated_total_price_twd)]),
      el("p", { "class": "price-range" }, [
        "合理區間：" + (display ? display.formatTotalWan(result.interval_total_price_twd[0]) : money(result.interval_total_price_twd[0])) +
        " ~ " + (display ? display.formatTotalWan(result.interval_total_price_twd[1]) : money(result.interval_total_price_twd[1]))
      ]),
    ]);
    cards.push(priceCard);

    // Asking price assessment
    if (result.asking_price_assessment) {
      var askCard = el("div", { "class": "valuation-card" }, []);
      var askLabel = "";
      if (result.asking_price_assessment === "偏低") askLabel = "低於區間";
      else if (result.asking_price_assessment === "合理區間") askLabel = "在區間內";
      else askLabel = "高於區間";
      askCard.appendChild(el("h3", {}, ["開價評估"]));
      askCard.appendChild(el("p", {}, ["開價" + askLabel]));
      var ppEl = renderPricePosition(
        result.interval_total_price_twd[0],
        result.estimated_total_price_twd,
        result.interval_total_price_twd[1],
        askingVal ? parseInt(askingVal) : null
      );
      if (ppEl) askCard.appendChild(ppEl);
      askCard.appendChild(el("p", { "class": "asking-caveat" }, [
        "591 開價僅供參考，實際成交價可能包含議價空間"
      ]));
      cards.push(askCard);
    }

    // Confidence card
    var confText = { high: "高", medium: "中", low: "低" }[result.confidence] || result.confidence;
    var confChildren = [el("h3", {}, ["可信度：" + confText])];
    if (result.confidence_reasons.length) {
      var ul = el("ul", { "class": "reasons" });
      result.confidence_reasons.forEach(function (r) {
        ul.appendChild(el("li", {}, [r]));
      });
      confChildren.push(ul);
    }
    cards.push(el("div", { "class": "valuation-card confidence-" + result.confidence }, confChildren));

    // Factors
    if (result.factors && result.factors.length) {
      var ul = el("ul");
      result.factors.forEach(function (f) {
        var cls = f.direction === "positive" ? "factor-positive" : "factor-negative";
        var sign = f.direction === "positive" ? "+" : "";
        ul.appendChild(el("li", { "class": cls }, [
          f.feature + "：" + sign + f.impact_twd_per_ping + " 元/坪"
        ]));
      });
      cards.push(el("div", { "class": "valuation-card" }, [
        el("h3", {}, ["主要影響因素"]),
        ul,
      ]));
    }

    // Comparables
    if (result.comparables && result.comparables.length) {
      var table = el("table", { "class": "comparable-table" });
      var thead = el("thead");
      var hdr = el("tr");
      ["日期", "生活圈", "坪數", "總價", "單價", "相似度"].forEach(function (label) {
        hdr.appendChild(el("th", {}, [label]));
      });
      thead.appendChild(hdr);
      table.appendChild(thead);
      var tbody = el("tbody");
      result.comparables.forEach(function (c) {
        var tr = el("tr");
         [(c.transaction_date || "").slice(0, 10), c.station_code,
          c.building_area_ping.toFixed(1),
          display ? display.formatTotalWan(c.total_price_twd) : money(c.total_price_twd),
          display ? display.formatUnitWan(c.unit_price_per_ping_twd) : money(c.unit_price_per_ping_twd),
          (c.similarity_score * 100).toFixed(0) + "%"
        ].forEach(function (val) { tr.appendChild(el("td", {}, [val])); });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      cards.push(el("div", { "class": "valuation-card" }, [
        el("h3", {}, ["相似成交"]),
        table,
      ]));
    }

    // Model disclosure
    var discChildren = [
      el("h3", {}, ["模型資訊"]),
      el("p", {}, ["模型：" + result.model.name + "（" + result.model.version + "）"]),
      el("p", {}, ["資料日期：" + result.data_date]),
    ];
    if (result.degraded) {
      discChildren.push(el("p", { "class": "degraded" }, ["⚠ 使用降級模型"]));
    }
    cards.push(el("div", { "class": "valuation-card disclosure" }, discChildren));

    // Limitation
    cards.push(el("div", { "class": "valuation-card limitation" }, [
      el("p", { "class": "limitation-note" }, [
        "本估價僅供參考，不構成投資或購屋建議。結果基於官方實價登錄資料，不含未來價格預測。"
      ]),
    ]));

    cards.forEach(function (card) { resultSection.appendChild(card); });
    resultSection.hidden = false;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!form.reportValidity()) return;
    statusEl.textContent = "估價中…";
    resultSection.hidden = true;

    var payload = {
      transaction_type: document.getElementById("valuation-type").value,
      station_code: document.getElementById("valuation-station").value,
      building_area_ping: parseFloat(document.getElementById("valuation-area").value),
      station_distance_m: parseFloat(document.getElementById("valuation-distance").value),
      building_type: document.getElementById("valuation-building-type").value,
      bedrooms: parseInt(document.getElementById("valuation-bedrooms").value),
      living_rooms: parseInt(document.getElementById("valuation-living-rooms").value),
      bathrooms: parseInt(document.getElementById("valuation-bathrooms").value),
      floor: parseInt(document.getElementById("valuation-floor").value),
      total_floors: parseInt(document.getElementById("valuation-total-floors").value),
      parking_area_ping: parseFloat(document.getElementById("valuation-parking-area").value) || 0,
    };

    var ageVal = document.getElementById("valuation-age").value;
    if (ageVal) payload.building_age_years = parseFloat(ageVal);

    var parkingType = document.getElementById("valuation-parking-type").value;
    if (parkingType) payload.parking_type = parkingType;

    var askingVal = document.getElementById("asking-price").value;
    if (askingVal) payload.asking_total_price_twd = parseInt(askingVal);

    fetch("/api/valuations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (err) {
            throw err;
          });
        }
        return r.json();
      })
      .then(function (result) {
        statusEl.textContent = "";
        renderValuation(result, askingVal);
      })
      .catch(function (err) {
        if (err && err.error && err.error.fields) {
          var fieldMap = {
            building_area_ping: "valuation-area",
            station_distance_m: "valuation-distance",
            building_type: "valuation-building-type",
            bedrooms: "valuation-bedrooms",
            living_rooms: "valuation-living-rooms",
            bathrooms: "valuation-bathrooms",
            floor: "valuation-floor",
            total_floors: "valuation-total-floors",
            parking_area_ping: "valuation-parking-area",
            building_age_years: "valuation-age",
            parking_type: "valuation-parking-type",
            transaction_type: "valuation-type",
            station_code: "valuation-station",
            asking_total_price_twd: "asking-price",
          };
          var controlId = QingpuValuationForm.firstErrorControlId(err.error.fields, fieldMap);
          if (controlId) {
            var controlEl = document.getElementById(controlId);
            if (controlEl) {
              controlEl.focus();
              controlEl.scrollIntoView({ behavior: "smooth", block: "center" });
            }
          }
        }
        statusEl.textContent = (err && err.error && err.error.message) ? err.error.message : "估價失敗";
        resultSection.hidden = true;
      });
  });
})();



