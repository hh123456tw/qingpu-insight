"use strict";

const assert = require("node:assert/strict");
const admin = require("../../src/qingpu_insight/static/admin.js");

assert.deepEqual(admin.SECTIONS, [
  "overview", "data", "listings", "models",
  "llm", "backups", "jobs", "diagnostics",
]);
assert.equal(admin.normalizeSection("#models"), "models");
assert.equal(admin.normalizeSection("#unknown"), "overview");

assert.deepEqual(
  admin.overviewView({
    mutation_ready: false,
    readiness: [{ code: "mysql", status: "blocked", message: "MySQL 無法連線。" }],
    action_items: [{ code: "mysql", message: "先啟動 MySQL。", section: "diagnostics" }],
  }),
  {
    ready: false,
    headline: "運維功能尚未就緒",
    blockedCodes: ["mysql"],
    actionCount: 1,
  }
);

assert.equal(admin.jobStatusLabel("succeeded"), "成功");
assert.equal(admin.jobStatusLabel("failed"), "失敗");
assert.equal(admin.jobStatusLabel("interrupted"), "已中斷");

assert.deepEqual(admin.buildOfficialUpdatePayload("110S3", "115S2", "acquire"), {
  start_season: "110S3",
  end_season: "115S2",
  start_at: "acquire",
});
assert.throws(() => admin.buildOfficialUpdatePayload("115S2", "110S3"), /起始季度/);
assert.equal(admin.stageLabel("publishing_mysql"), "正在發布正式市場資料");

process.stdout.write("admin contract passed\n");
