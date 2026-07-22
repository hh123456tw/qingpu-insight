"use strict";

const assert = require("node:assert/strict");
const {
  createPollController,
  parseApiResponse,
  submissionMessage,
} = require("../../src/qingpu_insight/static/job_polling.js");

class FakeTimers {
  constructor() {
    this.queue = [];
  }

  schedule(callback, delay) {
    this.queue.push({ callback, delay });
    return this.queue.length;
  }

  runNext() {
    assert.ok(this.queue.length, "expected a scheduled callback");
    const item = this.queue.shift();
    item.callback();
    return item.delay;
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function response(ok, payload, malformed = false) {
  return {
    ok,
    json: () => malformed
      ? Promise.reject(new SyntaxError("malformed body"))
      : Promise.resolve(payload),
  };
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve));
}

async function testResponseOkAndMalformedBodies() {
  await assert.rejects(
    parseApiResponse(response(false, { status: "succeeded", error: { message: "safe" } })),
    /safe/
  );
  await assert.rejects(
    parseApiResponse(response(false, null, true)),
    /temporarily unavailable/
  );
  await assert.rejects(
    parseApiResponse(response(true, null, true)),
    /invalid response/
  );
}

async function testNoOverlapAndDuplicateMonitoring() {
  const timers = new FakeTimers();
  const pending = deferred();
  let fetchCalls = 0;
  const controller = createPollController({
    fetchJob: () => {
      fetchCalls += 1;
      return pending.promise;
    },
    schedule: timers.schedule.bind(timers),
    onUpdate: () => {},
    onStop: () => {},
    minDelay: 1,
    maxDelay: 4,
    maxAttempts: 4,
    maxFailures: 2,
  });

  assert.equal(controller.start("run-existing"), true);
  assert.equal(timers.runNext(), 1);
  await settle();
  assert.equal(fetchCalls, 1);
  assert.equal(controller.start("run-existing"), false);
  assert.equal(fetchCalls, 1);
  assert.equal(timers.queue.length, 0);
  assert.match(
    submissionMessage({ run_id: "run-existing", created: false }),
    /run-existing/
  );
  assert.match(
    submissionMessage({ run_id: "run-existing", created: false }),
    /existing/i
  );

  pending.resolve(response(true, { status: "pending" }));
  await settle();
  assert.equal(timers.queue.length, 1);
}

async function testBoundedBackoffAndFailureStopReenablesButton() {
  const timers = new FakeTimers();
  const outcomes = [
    response(false, { error: { message: "safe failure" } }),
    response(false, null, true),
  ];
  const delays = [];
  const button = { disabled: true };
  let stopped;
  const controller = createPollController({
    fetchJob: () => Promise.resolve(outcomes.shift()),
    schedule: (callback, delay) => {
      delays.push(delay);
      return timers.schedule(callback, delay);
    },
    onUpdate: () => {},
    onStop: (reason) => {
      stopped = reason;
      button.disabled = false;
    },
    minDelay: 1,
    maxDelay: 2,
    maxAttempts: 10,
    maxFailures: 2,
  });

  controller.start("run-failing");
  timers.runNext();
  await settle();
  timers.runNext();
  await settle();

  assert.deepEqual(delays, [1, 2]);
  assert.equal(stopped, "failure_limit");
  assert.equal(button.disabled, false);
  assert.equal(timers.queue.length, 0);
}

async function testTerminalAndAttemptLimitStop() {
  const terminalTimers = new FakeTimers();
  const terminalUpdates = [];
  const terminalStops = [];
  const terminal = createPollController({
    fetchJob: () => Promise.resolve(
      response(true, { status: "succeeded", output_version: "v2", summary: { rows: 3 } })
    ),
    schedule: terminalTimers.schedule.bind(terminalTimers),
    onUpdate: (data) => terminalUpdates.push(data),
    onStop: (reason, data) => terminalStops.push({ reason, data }),
    minDelay: 1,
    maxDelay: 4,
    maxAttempts: 3,
    maxFailures: 2,
  });
  terminal.start("run-success");
  terminalTimers.runNext();
  await settle();
  assert.equal(terminalUpdates.length, 1);
  assert.equal(terminalStops[0].reason, "terminal");
  assert.equal(terminalStops[0].data.output_version, "v2");
  assert.equal(terminalTimers.queue.length, 0);

  const attemptTimers = new FakeTimers();
  let attemptStop;
  const bounded = createPollController({
    fetchJob: () => Promise.resolve(response(true, { status: "running" })),
    schedule: attemptTimers.schedule.bind(attemptTimers),
    onUpdate: () => {},
    onStop: (reason) => { attemptStop = reason; },
    minDelay: 1,
    maxDelay: 4,
    maxAttempts: 2,
    maxFailures: 2,
  });
  bounded.start("run-bounded");
  attemptTimers.runNext();
  await settle();
  attemptTimers.runNext();
  await settle();
  assert.equal(attemptStop, "attempt_limit");
  assert.equal(attemptTimers.queue.length, 0);
}

async function main() {
  await testResponseOkAndMalformedBodies();
  await testNoOverlapAndDuplicateMonitoring();
  await testBoundedBackoffAndFailureStopReenablesButton();
  await testTerminalAndAttemptLimitStop();
  process.stdout.write("job polling executable contract passed\n");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
