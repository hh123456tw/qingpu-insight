(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof document !== "undefined") root.QingpuAdmin = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var SECTIONS = [
    "overview", "data", "listings", "models",
    "llm", "backups", "jobs", "diagnostics",
  ];

  function normalizeSection(hash) {
    var section = hash.replace(/^#/, "");
    return SECTIONS.indexOf(section) !== -1 ? section : "overview";
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      var sidebarLinks = document.querySelectorAll(".admin-sidebar a[href^='#']");
      for (var i = 0; i < sidebarLinks.length; i++) {
        sidebarLinks[i].addEventListener("click", function (e) {
          var targetId = "admin-" + normalizeSection(e.currentTarget.getAttribute("href"));
          var target = document.getElementById(targetId);
          if (target) target.scrollIntoView({ behavior: "smooth" });
        });
      }
    });
  }

  return {
    SECTIONS: SECTIONS,
    normalizeSection: normalizeSection,
  };
});
