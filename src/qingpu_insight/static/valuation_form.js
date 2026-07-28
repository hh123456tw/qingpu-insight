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

  function parkingState(parkingType, parkingArea) {
    var disabled = !parkingType;
    var normalizedArea = disabled ? 0 : parkingArea;
    var valid = disabled ? true : normalizedArea > 0;
    var message = valid ? "" : "車位面積必須大於 0";
    return { disabled: disabled, normalizedArea: normalizedArea, valid: valid, message: message };
  }

  return {
    firstErrorControlId: firstErrorControlId,
    parkingState: parkingState,
  };
});
