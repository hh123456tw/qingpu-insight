"use strict";

const assert = require("node:assert/strict");
const admin = require("../../src/qingpu_insight/static/admin.js");

assert.deepEqual(admin.SECTIONS, [
  "overview", "data", "listings", "models",
  "llm", "jobs", "diagnostics",
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
assert.match(
  admin.formatTaipeiDatetime("2026-07-28T00:00:00Z"),
  /2026\/7\/28.*8:00:00/
);
assert.equal(admin.formatTaipeiDatetime(null), "—");

assert.deepEqual(admin.buildOfficialUpdatePayload("110S3", "115S2", "acquire"), {
  start_season: "110S3",
  end_season: "115S2",
  start_at: "acquire",
});
assert.throws(() => admin.buildOfficialUpdatePayload("115S2", "110S3"), /起始季度/);
assert.equal(admin.stageLabel("publishing_mysql"), "正在發布正式市場資料");
assert.equal(typeof admin.runListingSequence, "function");

assert.deepEqual(admin.buildReleasePreviewPayload("publish", "resale", "run-123"), {
  action: "publish",
  market: "resale",
  run_id: "run-123",
});
assert.deepEqual(admin.buildReleasePreviewPayload("rollback", "presale", "v1"), {
  action: "rollback",
  market: "presale",
  version_id: "v1",
});
assert.equal(admin.canConfirmDangerousAction("abc", "abc"), true);
assert.equal(admin.canConfirmDangerousAction("abc", "def"), false);

assert.deepEqual(admin.DEFAULT_LISTING_TYPES, ["sale"]);

assert.deepEqual(
  admin.buildBenchmarkPayload("ollama:gemma4:e2b"),
  { model_id: "ollama:gemma4:e2b" }
);
assert.equal(
  admin.benchmarkModelHelp({
    provider: "ollama",
    ready: true,
    note: "本機已安裝",
  }),
  "本機已安裝"
);
assert.equal(
  admin.benchmarkModelHelp(
    { provider: "gemini", ready: true, note: "可使用" },
    "無法連線本機 Ollama；Gemini 模型仍可使用。"
  ),
  "無法連線本機 Ollama；Gemini 模型仍可使用。 可使用"
);
assert.equal(admin.canRunBenchmark({ ready: true }, true), true);
assert.equal(admin.canRunBenchmark({ ready: true }, false), false);
assert.equal(admin.canRunBenchmark({ ready: false }, true), false);
assert.equal(admin.canRunBenchmark(null, true), false);
assert.equal(
  admin.benchmarkProgressLabel("Gemini｜Gemma 4 31B", 12),
  "正在測試 Gemini｜Gemma 4 31B｜已等待 12 秒（通常約 5～45 秒）"
);

async function testListingSequenceUsesOnlyWebListingTypes() {
  var submitted = [];
  var result = await admin.runListingSequence({
    types: admin.DEFAULT_LISTING_TYPES,
    maxPages: 10,
    submit: async function (type) {
      submitted.push(type);
      return { run_id: "run-" + type, created: true };
    },
    waitForTerminal: async function (runId) {
      return { status: "succeeded", output_version: "v-" + runId };
    },
    onTypeStart: function () {},
    onTypeDone: function () {},
  });
  assert.deepEqual(submitted, ["sale"]);
  assert.equal(result.sale.status, "succeeded");
  assert.equal(Object.hasOwn(result, "newhouse"), false);
  assert.equal(Object.hasOwn(result, "rental"), false);
}

testListingSequenceUsesOnlyWebListingTypes().then(function () {
  process.stdout.write("admin contract passed\n");
}).catch(function (err) {
  console.error(err);
  process.exit(1);
});
