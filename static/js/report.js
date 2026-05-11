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

  new Chart(canvas, {
    type: "bar",
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

document.addEventListener("DOMContentLoaded", function () {
  // Collapsible sections
  document.querySelectorAll(".section-header").forEach(function (hdr) {
    hdr.addEventListener("click", function () {
      this.closest(".section").classList.toggle("collapsed");
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
