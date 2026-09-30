"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const labels = require("../../src/qingpu_insight/static/feature_labels.js");

assert.equal(labels.featureLabel("transaction_month_index"), "交易月份");
assert.equal(labels.featureLabel("total_floors"), "總樓層");
assert.equal(labels.featureLabel("common_area_ratio"), "公設比");
assert.equal(labels.featureLabel("some_future_feature"), "some_future_feature");
assert.equal(labels.featureLabel("toString"), "toString");
assert.equal(labels.featureLabel(null), "—");
for (const [name, label] of Object.entries(labels.FEATURE_LABELS)) {
  assert.match(name, /^[a-z0-9_]+$/);
  assert.ok(/[一-鿿]/.test(label), name + " must have a Chinese label");
}

// The valuation result must show labels, never raw feature names.
const app = fs.readFileSync("src/qingpu_insight/static/app.js", "utf8");
assert.ok(app.includes("featureName(f.feature)"));
assert.ok(!app.includes('f.feature + "：'));

process.stdout.write("feature labels contract passed\n");
