// 漲跌幅分布柱狀圖
(function () {
  var canvas = document.getElementById("dist-chart");
  if (!canvas || typeof Chart === "undefined") return;

  var labels = JSON.parse(canvas.dataset.labels || "[]");
  var counts = JSON.parse(canvas.dataset.counts || "[]");

  // Colors: negative bins = green (--neg), positive bins = red (--pos)
  var style = getComputedStyle(document.documentElement);
  var posColor = style.getPropertyValue("--pos").trim() || "#ff6b6b";
  var negColor = style.getPropertyValue("--neg").trim() || "#4ade80";

  var colors = labels.map(function (lbl) {
    // bins index 0~5 are negative/zero, 6~11 are positive
    var idx = labels.indexOf(lbl);
    return idx < 6 ? negColor : posColor;
  });

  // 柱頂數值標記（Chart.js 無內建 datalabels，自繪）
  var valueLabels = {
    id: "distValueLabels",
    afterDatasetsDraw: function (chart) {
      var ctx = chart.ctx;
      ctx.save();
      ctx.fillStyle = "#8b9ab0";
      ctx.font = "600 11px 'IBM Plex Mono', monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      chart.getDatasetMeta(0).data.forEach(function (bar, i) {
        if (!counts[i]) return;
        ctx.fillText(counts[i], bar.x, bar.y - 3);
      });
      ctx.restore();
    }
  };

  new Chart(canvas, {
    type: "bar",
    plugins: [valueLabels],
    data: {
      labels: labels,
      datasets: [{
        data: counts,
        backgroundColor: colors,
        borderRadius: 2,
        barPercentage: 0.85,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { top: 14 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function (ctx) { return ctx.parsed.y + " 支"; }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: "#8b9ab0", font: { size: 11 } },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        y: {
          ticks: { color: "#8b9ab0", font: { size: 11 } },
          grid: { color: "rgba(255,255,255,0.08)" },
        }
      }
    }
  });
})();

// 市場趨勢折線圖（MA20 比例 + 淨新高低）
(function () {
  var canvas = document.getElementById("trend-chart");
  if (!canvas || typeof Chart === "undefined") return;

  var dates = JSON.parse(canvas.dataset.dates || "[]");
  var ma20  = JSON.parse(canvas.dataset.ma20 || "[]");
  var net   = JSON.parse(canvas.dataset.net || "[]");

  var style = getComputedStyle(document.documentElement);
  var posColor  = style.getPropertyValue("--pos").trim() || "#ff6b6b";
  var negColor  = style.getPropertyValue("--neg").trim() || "#4ade80";
  var amber     = "#f0b429";

  // 淨新高低柱色：正紅負綠
  var netColors = net.map(function (v) { return v >= 0 ? posColor : negColor; });

  // 右軸以 0 為中心對稱，使「0」對齊左軸的「50%」（兩軸中點重合）
  var maxAbs = net.reduce(function (m, v) { return Math.max(m, Math.abs(v)); }, 0);
  var step = maxAbs <= 20 ? 5 : maxAbs <= 50 ? 10 : maxAbs <= 100 ? 25 : 50;
  var netBound = Math.max(step, Math.ceil(maxAbs / step) * step);

  new Chart(canvas, {
    data: {
      labels: dates,
      datasets: [
        {
          type: "line",
          label: "收盤>20MA %",
          data: ma20,
          yAxisID: "y",
          borderColor: amber,
          backgroundColor: amber,
          borderWidth: 2,
          pointRadius: 2,
          tension: 0.3,
        },
        {
          type: "bar",
          label: "淨新高(52週)",
          data: net,
          yAxisID: "y1",
          backgroundColor: netColors,
          borderRadius: 2,
          barPercentage: 0.6,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: "#8b9ab0", font: { size: 11 }, boxWidth: 12 }
        },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              if (ctx.dataset.yAxisID === "y") return " MA20以上 " + ctx.parsed.y + "%";
              return " 淨新高 " + (ctx.parsed.y > 0 ? "+" : "") + ctx.parsed.y;
            }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: "#8b9ab0", font: { size: 10 }, maxTicksLimit: 12 },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        y: {
          min: 0, max: 100,
          position: "left",
          ticks: {
            color: amber, font: { size: 11 }, stepSize: 25,
            callback: function (v) { return v + "%"; }
          },
          grid: {
            // 50% 中線加亮，其餘格線維持淡色
            color: function (ctx) {
              return ctx.tick.value === 50
                ? "rgba(255,255,255,0.35)"
                : "rgba(255,255,255,0.08)";
            },
            lineWidth: function (ctx) { return ctx.tick.value === 50 ? 1.5 : 1; },
          },
        },
        y1: {
          min: -netBound, max: netBound,
          position: "right",
          ticks: { color: "#8b9ab0", font: { size: 11 }, stepSize: netBound / 2 },
          grid: { drawOnChartArea: false },
        }
      }
    }
  });
})();

// 寬表格的「可左右滑動」提示：只在真的滑得動時顯示。切頁簽、展開／收合區塊、
// 轉向都會改變可視寬度，ResizeObserver（display:none → 可見時也會觸發）一併處理。
(function () {
  function sync(wrap) {
    var hint = wrap.previousElementSibling;
    if (!hint || !hint.classList.contains("si-scroll-hint")) return;
    hint.classList.toggle("show", wrap.scrollWidth > wrap.clientWidth + 1);
  }
  var wraps = Array.prototype.slice.call(document.querySelectorAll(".si-scroll"));
  if (!wraps.length) return;
  if (typeof ResizeObserver === "function") {
    var ro = new ResizeObserver(function (entries) {
      entries.forEach(function (e) { sync(e.target); });
    });
    wraps.forEach(function (w) { ro.observe(w); sync(w); });
  } else {
    wraps.forEach(sync);
    window.addEventListener("resize", function () { wraps.forEach(sync); });
  }
})();

document.addEventListener("DOMContentLoaded", function () {
  // Collapsible sections
  document.querySelectorAll(".section-header").forEach(function (hdr) {
    hdr.addEventListener("click", function () {
      var section = this.closest(".section");
      section.classList.toggle("collapsed");
      // Tables initialized while hidden mis-measure column widths; fix on expand.
      if (!section.classList.contains("collapsed") && typeof $.fn.DataTable !== "undefined") {
        $(section).find(".dt-table").each(function () {
          if ($.fn.DataTable.isDataTable(this)) {
            $(this).DataTable().columns.adjust();
          }
        });
      }
    });
  });

  // DataTables — init every table with class .dt-table
  if (typeof $.fn.DataTable !== "undefined") {
    $(".dt-table").each(function () {
      var order = [];
      var sortCol = $(this).data("sort-col");
      var sortDir = $(this).data("sort-dir") || "desc";
      if (sortCol !== undefined) {
        order = [[parseInt(sortCol), sortDir]];
      }

      $(this).DataTable({
        pageLength: 25,
        order: order,
        language: {
          search:       "搜尋：",
          lengthMenu:   "顯示 _MENU_ 筆",
          info:         "第 _START_ 至 _END_ 筆，共 _TOTAL_ 筆",
          infoEmpty:    "共 0 筆",
          paginate: {
            first:    "首頁",
            last:     "末頁",
            next:     "下一頁",
            previous: "上一頁",
          },
          emptyTable:   "無資料",
        },
        responsive: true,
      });
    });
  }
});
