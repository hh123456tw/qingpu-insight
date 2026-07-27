(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuValuationForm = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function firstErrorControlId(fields, fieldMap) {
    var keys = Object.keys(fields);
    if (!keys.length) return null;
    var firstKey = keys[0];
    return fieldMap[firstKey] || null;
  }

  return {
    firstErrorControlId: firstErrorControlId,
  };
});
