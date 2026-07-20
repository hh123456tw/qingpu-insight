document.addEventListener("DOMContentLoaded", function () {
  const typeSelect = document.getElementById("transaction-type");
  const stationFilter = document.getElementById("station-filter");
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
        var popup =
          (item.record_id || "") +
          "<br>" +
          money.format(item.total_price_twd) +
          " | " +
          (item.building_area_ping ? item.building_area_ping.toFixed(1) + " 坪" : "");
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
      return t.median_unit_price_per_ping_twd;
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
        transactionsDiv.innerHTML = "";
        transactionsDiv.appendChild(
          buildTable(transactions.items || [])
        );
      })
      .catch(function (err) {
        if (err.name === "AbortError") return;
        statusEl.textContent = "資料載入失敗：" + err.message;
      });
  }

  fetchData();

  typeSelect.addEventListener("change", fetchData);
  stationFilter.addEventListener("change", fetchData);
});
