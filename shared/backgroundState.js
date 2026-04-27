/* Shared by the service worker only — no document/window. */
var SESSION_KEY = "sessionState";
var OPTIONS_KEY = "userOptions";
var BgState = (function () {
  var defaultSession = {
    running: false,
    status: "idle",
    sessionEmail: "",
    captchaOutcome: null,
    captchaAt: null,
    networkLabel: "—",
    lastHttpStatus: null,
    lastNetworkError: null,
    verificationState: "idle",
    diagnosticsCheckpoint: "—",
    logEntries: [],
  };

  var MAX_LOG = 200;

  function getSession() {
    return chrome.storage.session.get(SESSION_KEY).then(function (res) {
      var s = res[SESSION_KEY];
      if (!s || typeof s !== "object") return Object.assign({}, defaultSession);
      return Object.assign({}, defaultSession, s);
    });
  }

  function mergeSession(partial) {
    return getSession().then(function (current) {
      var next = Object.assign({}, current, partial);
      if (next.logEntries && next.logEntries.length > MAX_LOG) {
        next.logEntries = next.logEntries.slice(next.logEntries.length - MAX_LOG);
      }
      return chrome.storage.session.set({ [SESSION_KEY]: next }).then(function () {
        chrome.runtime.sendMessage({ type: "SESSION_UPDATED", payload: next }).catch(function () {});
        return next;
      });
    });
  }

  function appendToSession(level, message) {
    return getSession().then(function (s) {
      var entries = (s.logEntries || []).concat([
        { ts: Date.now(), level: level || "info", message: String(message) },
      ]);
      return mergeSession({ logEntries: entries });
    });
  }

  function getSyncOptions() {
    return chrome.storage.sync.get(OPTIONS_KEY).then(function (res) {
      var o = res[OPTIONS_KEY];
      if (!o || typeof o !== "object") return {};
      return o;
    });
  }

  var DEFAULT_URL = "https://appointment.thespainvisa.com/Global/account/login";

  function openOrFocusPage(url) {
    var u = String(url || DEFAULT_URL);
    return chrome.tabs.query({ url: "https://appointment.thespainvisa.com/*" }).then(function (tabs) {
      if (tabs && tabs.length > 0) {
        var t = tabs[0];
        if (t.id == null) return chrome.tabs.create({ url: u });
        return chrome.windows
          .update(t.windowId, { focused: true })
          .then(function () {
            return chrome.tabs.update(t.id, { active: true, url: u });
          });
      }
      return chrome.tabs.create({ url: u });
    });
  }

  return {
    getSession: getSession,
    mergeSession: mergeSession,
    appendToSession: appendToSession,
    getSyncOptions: getSyncOptions,
    openOrFocusPage: openOrFocusPage,
    DEFAULT_URL: DEFAULT_URL,
  };
})();
