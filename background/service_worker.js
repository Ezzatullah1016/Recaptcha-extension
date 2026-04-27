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
      ? "Form fields were filled (where found). Complete any human verification and sign in on the page."
      : "Saved credentials not applied to the page. Enter your details in the form if needed.";
    BgState.appendToSession("info", line)
      .then(function () {
        return BgState.mergeSession({
          captchaOutcome: msg.applied ? "awaiting human verification" : "no fields matched",
          captchaAt: Date.now(),
        });
      })
      .then(function () {
        sendResponse({ ok: true });
      });
    return true;
  }
});
