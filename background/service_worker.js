importScripts("../shared/backgroundState.js");

chrome.runtime.onInstalled.addListener(function () {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(function () {});
});

chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
  if (!msg || !msg.type) return;

  if (msg.type === "OPEN_TARGET_PAGE") {
    BgState.getSyncOptions()
      .then(function (o) {
        var url = (o && o.targetLoginUrl) || BgState.DEFAULT_URL;
        return BgState.openOrFocusPage(url);
      })
      .then(function () {
        sendResponse({ ok: true });
      })
      .catch(function (e) {
        BgState.appendToSession("error", "Tab: " + (e && e.message ? e.message : String(e)))
          .catch(function () {})
          .then(function () {
            sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
          });
      });
    return true;
  }

  if (msg.type === "FILL_STATUS") {
    var line = msg.applied
      ? (msg.submitted
          ? "Login fields filled and submit triggered. Watching for captcha/auth response."
          : "Login fields were filled. Complete verification/sign-in in the tab.")
      : "Saved credentials not applied to the page. Enter your details in the form if needed.";
    if (msg.stage === "captcha") {
      line =
        "Captcha page detected. " +
        (msg.selected ? ("Selected " + msg.selected + (msg.target ? (" tile(s) for " + msg.target) : " tile(s)") + ". ") : "") +
        (msg.submitted ? "Submitted captcha form." : "Continue verification on page.");
    } else if (msg.stage === "rate_limit") {
      line = "Rate-limit page detected (Too Many Requests). " +
        (msg.retryScheduled
          ? ("Auto-retry scheduled in about " + Math.round((msg.retryAfterMs || 0) / 60000) + " minute(s).")
          : "Retry already scheduled; waiting.");
    } else if (msg.stage === "post_login") {
      line = msg.submitted
        ? "Logged in page detected; moving to Book New Appointment."
        : "Logged in page detected.";
    } else if (msg.stage === "booking") {
      line = "Book New Appointment page detected.";
    }
    BgState.appendToSession("info", line)
      .then(function () {
        return BgState.mergeSession({
          captchaOutcome: msg.stage === "captcha"
            ? "captcha page detected"
            : (msg.applied ? "awaiting human verification" : "no fields matched"),
          captchaAt: Date.now(),
        });
      })
      .then(function () {
        sendResponse({ ok: true });
      });
    return true;
  }
});
