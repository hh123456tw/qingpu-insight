(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.QingpuJobPolling = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var terminalStatuses = ["succeeded", "failed", "skipped", "needs_attention"];

  function parseApiResponse(response) {
    return Promise.resolve()
      .then(function () { return response.json(); })
      .catch(function () { return null; })
      .then(function (data) {
        if (!response.ok) {
          var safeMessage = data && data.error && data.error.message;
          throw new Error(safeMessage || "server temporarily unavailable");
        }
        if (!data) throw new Error("invalid response");
        return data;
      });
  }

  function submissionMessage(data, labels) {
    var text = labels || {
      existing: "Monitoring existing job: ",
      created: "Submitted job: ",
    };
    return (data.created === false ? text.existing : text.created) + data.run_id;
  }

  function createPollController(options) {
    var active = false;
    var inFlight = false;
    var runId = null;
    var attempts = 0;
    var failures = 0;
    var delay = options.minDelay;

    function stop(reason, data) {
      if (!active) return;
      active = false;
      options.onStop(reason, data || null);
    }

    function pollOnce() {
      if (!active || inFlight) return;
      inFlight = true;
      attempts += 1;
      var shouldContinue = false;
      var nextDelay = delay;

      Promise.resolve()
        .then(function () { return options.fetchJob(runId); })
        .then(parseApiResponse)
        .then(function (data) {
          failures = 0;
          delay = options.minDelay;
          options.onUpdate(data);
          if (terminalStatuses.indexOf(data.status) !== -1) {
            stop("terminal", data);
            return;
          }
          if (attempts >= options.maxAttempts) {
            stop("attempt_limit", data);
            return;
          }
          shouldContinue = true;
          nextDelay = delay;
        })
        .catch(function (error) {
          failures += 1;
          delay = Math.min(
            options.maxDelay,
            Math.max(options.minDelay, delay * 2)
          );
          if (failures >= options.maxFailures || attempts >= options.maxAttempts) {
            stop(
              failures >= options.maxFailures ? "failure_limit" : "attempt_limit",
              { error: error.message }
            );
            return;
          }
          shouldContinue = true;
          nextDelay = delay;
        })
        .finally(function () {
          inFlight = false;
          if (active && shouldContinue) options.schedule(pollOnce, nextDelay);
        });
    }

    return {
      start: function (nextRunId) {
        if (active) return false;
        active = true;
        runId = nextRunId;
        attempts = 0;
        failures = 0;
        delay = options.minDelay;
        options.schedule(pollOnce, delay);
        return true;
      },
      stop: function () { stop("cancelled", null); },
      isActive: function () { return active; },
      isInFlight: function () { return inFlight; },
    };
  }

  return {
    createPollController: createPollController,
    parseApiResponse: parseApiResponse,
    submissionMessage: submissionMessage,
  };
});
