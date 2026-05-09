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
