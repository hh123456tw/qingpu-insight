document.addEventListener("DOMContentLoaded", function () {
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

  function updateMap(items) {
    markerLayer.clearLayers();
    var hasCoords = false;
    items.forEach(function (item) {
      if (item.latitude && item.longitude) {
        hasCoords = true;
        var popup = L.popup();
        var text = (item.record_id || "") + "\n" +
          money.format(item.total_price_twd) + " | " +
          (item.building_area_ping ? item.building_area_ping.toFixed(1) + " 坪" : "");
        popup.setContent(text);
        L.marker([item.latitude, item.longitude])
          .bindPopup(popup)
          .addTo(markerLayer);
      }
    });
    if (hasCoords && map !== null) {
      setTimeout(function () {
        map.invalidateSize();
      }, 200);
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
        item.total_price_twd ? money.format(item.total_price_twd) : "",
        item.unit_price_per_ping_twd ? money.format(item.unit_price_per_ping_twd) : "",
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

  function fetchData() {
    if (lastController !== null) {
      lastController.abort();
    }
    lastController = new AbortController();
    var params = buildParams();
    var signal = lastController.signal;

    initMap();
    initChart();

    Promise.all([
      fetch("/api/market/summary?" + params.toString(), { signal }).then(
        function (r) {
          if (!r.ok) throw new Error("summary " + r.status);
          return r.json();
        }
      ),
      fetch("/api/market/trends?" + params.toString(), { signal }).then(
        function (r) {
          if (!r.ok) throw new Error("trends " + r.status);
          return r.json();
        }
      ),
      fetch("/api/transactions?" + params.toString(), { signal }).then(
        function (r) {
          if (!r.ok) throw new Error("transactions " + r.status);
          return r.json();
        }
      ),
    ])
      .then(function (results) {
        var summary = results[0];
        var trends = results[1];
        var transactions = results[2];

        medianPrice.textContent = summary.median_unit_price_per_ping_twd
          ? formatWan(summary.median_unit_price_per_ping_twd)
          : "—";
        recordCount.textContent =
          summary.record_count != null ? summary.record_count : "—";
        medianTotal.textContent = summary.median_total_price_twd
          ? money.format(summary.median_total_price_twd)
          : "—";
        latestDate.textContent = summary.latest_transaction_date
          ? summary.latest_transaction_date.slice(0, 10)
          : "—";

        statusEl.textContent = "";

        updateMap(transactions.items || []);
        updateChart(trends.items || []);
        transactionsDiv.replaceChildren(
          buildTable(transactions.items || [])
        );
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        statusEl.textContent = "資料載入失敗：" + err.message;
      });
  }

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

  function renderValuation(result) {
    resultSection.replaceChildren();

    var cards = [];

    // Price interval card
    var priceCard = el("div", { "class": "valuation-card" }, [
      el("h3", {}, ["估價結果"]),
      el("p", { "class": "estimated-price" }, [money(result.estimated_total_price_twd)]),
      el("p", { "class": "price-range" }, [
        "合理區間：" + money(result.interval_total_price_twd[0]) +
        " ~ " + money(result.interval_total_price_twd[1])
      ]),
    ]);
    cards.push(priceCard);

    // Asking price assessment
    if (result.asking_price_assessment) {
      var askLabel = "";
      if (result.asking_price_assessment === "偏低") askLabel = "低於區間";
      else if (result.asking_price_assessment === "合理區間") askLabel = "在區間內";
      else askLabel = "高於區間";
      cards.push(el("div", { "class": "valuation-card" }, [
        el("h3", {}, ["開價評估"]),
        el("p", {}, ["開價" + askLabel]),
      ]));
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
         c.building_area_ping.toFixed(1), money(c.total_price_twd),
         money(c.unit_price_per_ping_twd), (c.similarity_score * 100).toFixed(0) + "%"
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
            throw new Error(
              (err.error && err.error.fields
                ? Object.keys(err.error.fields).join("、") + " 欄位錯誤"
                : err.error && err.error.message
                ? err.error.message
                : "估價失敗")
            );
          });
        }
        return r.json();
      })
      .then(function (result) {
        statusEl.textContent = "";
        renderValuation(result);
      })
      .catch(function (err) {
        statusEl.textContent = err.message;
        resultSection.hidden = true;
      });
  });
})();

// --- Listing Intelligence (M3) ---

(function () {
  const typeSelect = document.getElementById("listing-type");
  const stationFilter = document.getElementById("listing-station-filter");
  const statusEl = document.getElementById("listing-status");
  const countEl = document.getElementById("listing-count");
  const medianEl = document.getElementById("listing-median");
  const rangeEl = document.getElementById("listing-range");
  const snapshotEl = document.getElementById("listing-snapshot");
  const listingsDiv = document.getElementById("listing-listings");
  const eventsDiv = document.getElementById("listing-events");
  const controls = document.querySelector(".listing-controls");

  const money = new Intl.NumberFormat("zh-TW", {
    style: "currency", currency: "TWD", maximumFractionDigits: 0,
  });

  function formatWan(value) {
    return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 1 }).format(value / 10000) + " 萬";
  }

  function buildParams() {
    var params = new URLSearchParams();
    params.set("listing_type", typeSelect.value);
    var checks = stationFilter.querySelectorAll('input[type="checkbox"]:checked');
    checks.forEach(function (cb) { params.append("station", cb.value); });
    return params;
  }

  function buildListingTable(items) {
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var hdr = document.createElement("tr");
    ["標題", "生活圈", "坪數", "總價", "更新時間"].forEach(function (label) {
      var th = document.createElement("th");
      th.textContent = label;
      hdr.appendChild(th);
    });
    thead.appendChild(hdr);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    items.forEach(function (item) {
      var row = document.createElement("tr");
      var cells = [
        item.title || "",
        item.station || "",
        item.area ? item.area.toFixed(1) + " 坪" : "",
        item.price ? (item.type === "rental" ? money(item.price) + "/月" : formatWan(item.price)) : "",
        item.snapshot_time ? item.snapshot_time.slice(0, 16).replace("T", " ") : "",
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

  function buildEventList(items) {
    var list = document.createElement("ul");
    list.className = "event-list";
    if (!items.length) {
      var li = document.createElement("li");
      li.textContent = "暫無價格異動";
      list.appendChild(li);
      return list;
    }
    var labels = {
      listed: "新刊登", relisted: "重新刊登", delisted: "已下架",
      price_increase: "漲價", price_decrease: "降價",
    };
    items.forEach(function (evt) {
      var li = document.createElement("li");
      li.className = "event-item event-" + (evt.event_type || "unknown");
      var label = labels[evt.event_type] || evt.event_type;
      var text = label + " — " + evt.occurred_at.slice(0, 10);
      if (evt.event_data && evt.event_data.percentage_change != null) {
        text += "（" + (evt.event_data.percentage_change > 0 ? "+" : "") + evt.event_data.percentage_change + "%）";
      }
      li.textContent = text;
      list.appendChild(li);
    });
    return list;
  }

  function fetchListingData() {
    var params = buildParams();
    statusEl.textContent = "載入中…";

    Promise.all([
      fetch("/api/listings/summary?" + params.toString()).then(function (r) {
        if (!r.ok) throw new Error("summary " + r.status);
        return r.json();
      }),
      fetch("/api/listings?" + params.toString()).then(function (r) {
        if (!r.ok) throw new Error("listings " + r.status);
        return r.json();
      }),
      fetch("/api/listing-events?" + params.toString()).then(function (r) {
        if (!r.ok) throw new Error("events " + r.status);
        return r.json();
      }),
    ])
      .then(function (results) {
        var summary = results[0];
        var listings = results[1];
        var events = results[2];

        statusEl.textContent = "";

        countEl.textContent = summary.active_count != null ? summary.active_count : "—";
        medianEl.textContent = summary.median_price ? formatWan(summary.median_price) : "—";
        rangeEl.textContent = summary.min_price && summary.max_price
          ? formatWan(summary.min_price) + " ~ " + formatWan(summary.max_price)
          : "—";
        snapshotEl.textContent = summary.snapshot_time
          ? summary.snapshot_time.slice(0, 16).replace("T", " ")
          : "—";

        listingsDiv.replaceChildren(buildListingTable(listings.items || []));
        eventsDiv.replaceChildren(buildEventList(events.items || []));
      })
      .catch(function (err) {
        statusEl.textContent = "刊登資料載入失敗：" + err.message;
      });
  }

  fetchListingData();
  controls.addEventListener("change", fetchListingData);
})();

// --- Job Center (M4.2) ---

(function () {
  const submitBtn = document.getElementById("job-submit");
  const typesSelect = document.getElementById("job-types");
  const maxPagesInput = document.getElementById("job-max-pages");
  const statusEl = document.getElementById("job-status");
  var submitting = false;
  var polling = window.QingpuJobPolling;

  function getCSRFToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function releaseButton() {
    submitting = false;
    submitBtn.disabled = false;
  }

  var poller = polling.createPollController({
    fetchJob: function (runId) {
      return fetch("/api/jobs/" + encodeURIComponent(runId));
    },
    schedule: function (callback, delay) { return setTimeout(callback, delay); },
    onUpdate: function (data) {
      statusEl.textContent = "狀態：" + data.status;
    },
    onStop: function (reason, data) {
      if (reason === "terminal" && data.status === "succeeded") {
        var version = data.output_version ? "；版本：" + data.output_version : "";
        var rows = data.summary && data.summary.rows != null
          ? "；筆數：" + data.summary.rows
          : "";
        statusEl.textContent = "更新完成" + version + rows;
      } else if (reason === "terminal" && data.error_message) {
        statusEl.textContent = "狀態：" + data.status + "；" + data.error_message;
      } else if (reason !== "terminal") {
        statusEl.textContent = "輪詢停止，請至工作歷史查詢";
      }
      releaseButton();
    },
    minDelay: 1000,
    maxDelay: 10000,
    maxAttempts: 120,
    maxFailures: 5,
  });

  submitBtn.addEventListener("click", function () {
    if (submitting || submitBtn.disabled) return;
    submitting = true;
    submitBtn.disabled = true;
    statusEl.textContent = "提交中…";

    var types = (typesSelect.value || "sale,newhouse,rental").split(",");
    var maxPages = parseInt(maxPagesInput.value) || 10;

    fetch("/api/admin/listing-updates", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Qingpu-CSRF": getCSRFToken(),
      },
      body: JSON.stringify({ types: types, max_pages: maxPages }),
    })
      .then(polling.parseApiResponse)
      .then(function (data) {
        submitting = false;
        statusEl.textContent = polling.submissionMessage(data, {
          existing: "監看既有工作：",
          created: "已提交，工作 ID: ",
        });
        poller.start(data.run_id);
      })
      .catch(function (err) {
        releaseButton();
        statusEl.textContent = "錯誤：" + err.message;
      });
  });
})();
