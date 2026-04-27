(function (global) {
  var SESSION_KEY = "sessionState";
  var OPTIONS_KEY = "userOptions";

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

  var defaultOptions = {
    theme: "light",
    logRetentionDays: 7,
    notificationsEnabled: true,
    targetLoginUrl: "https://appointment.thespainvisa.com/Global/account/login",
    loginAssistEnabled: true,
  };

  var LOCAL_LOGIN_KEY = "extLocalLoginProfile";

  function clampLogs(entries, max) {
    if (!Array.isArray(entries)) return [];
    if (entries.length <= max) return entries;
    return entries.slice(entries.length - max);
  }

  function getSession() {
    return chrome.storage.session.get(SESSION_KEY).then(function (res) {
      var s = res[SESSION_KEY];
      if (!s || typeof s !== "object") return Object.assign({}, defaultSession);
      return Object.assign({}, defaultSession, s);
    });
  }

  function setSession(partial) {
    return getSession().then(function (current) {
      var next = Object.assign({}, current, partial);
      var maxLogs = 200;
      if (next.logEntries) next.logEntries = clampLogs(next.logEntries, maxLogs);
      return chrome.storage.session.set({ [SESSION_KEY]: next }).then(function () {
        chrome.runtime.sendMessage({ type: "SESSION_UPDATED", payload: next }).catch(function () {});
        return next;
      });
    });
  }

  function appendLog(level, message) {
    return getSession().then(function (s) {
      var entries = (s.logEntries || []).concat([
        { ts: Date.now(), level: level || "info", message: String(message) },
      ]);
      return setSession({ logEntries: entries });
    });
  }

  function getOptions() {
    return chrome.storage.sync.get(OPTIONS_KEY).then(function (res) {
      var o = res[OPTIONS_KEY];
      if (!o || typeof o !== "object") return Object.assign({}, defaultOptions);
      return Object.assign({}, defaultOptions, o);
    });
  }

  function getLocalProfile() {
    return chrome.storage.local.get(LOCAL_LOGIN_KEY).then(function (res) {
      var p = res[LOCAL_LOGIN_KEY];
      if (!p || typeof p !== "object")
        return { email: "", password: "" };
      return {
        email: p.email != null ? String(p.email) : "",
        password: p.password != null ? String(p.password) : "",
      };
    });
  }

  function setLocalProfile(partial) {
    return getLocalProfile().then(function (cur) {
      var next = { email: cur.email, password: cur.password };
      if (partial && typeof partial === "object") {
        if (Object.prototype.hasOwnProperty.call(partial, "email")) next.email = partial.email;
        if (Object.prototype.hasOwnProperty.call(partial, "password")) next.password = partial.password;
      }
      return chrome.storage.local.set({ [LOCAL_LOGIN_KEY]: next }).then(function () {
        return next;
      });
    });
  }

  function setOptions(partial) {
    return getOptions().then(function (current) {
      var next = Object.assign({}, current, partial);
      return chrome.storage.sync.set({ [OPTIONS_KEY]: next }).then(function () {
        chrome.runtime.sendMessage({ type: "OPTIONS_UPDATED", payload: next }).catch(function () {});
        return next;
      });
    });
  }

  function applyTheme(theme) {
    var t = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
  }

  function initThemeFromStorage() {
    return getOptions().then(function (opts) {
      applyTheme(opts.theme);
      return opts;
    });
  }

  function onSessionChange(cb) {
    function handler(changes, area) {
      if (area !== "session" || !changes[SESSION_KEY]) return;
      var nv = changes[SESSION_KEY].newValue;
      if (nv) cb(nv);
    }
    chrome.storage.onChanged.addListener(handler);
    return function () {
      chrome.storage.onChanged.removeListener(handler);
    };
  }

  function onOptionsChange(cb) {
    function handler(changes, area) {
      if (area !== "sync" || !changes[OPTIONS_KEY]) return;
      var nv = changes[OPTIONS_KEY].newValue;
      if (nv) cb(nv);
    }
    chrome.storage.onChanged.addListener(handler);
    return function () {
      chrome.storage.onChanged.removeListener(handler);
    };
  }

  function pruneLogsForRetention(days) {
    var d = Math.max(1, Math.min(365, Number(days) || 7));
    return getSession().then(function (s) {
      var cutoff = Date.now() - d * 86400000;
      var entries = (s.logEntries || []).filter(function (e) {
        return e.ts >= cutoff;
      });
      if (entries.length === (s.logEntries || []).length) return s;
      return setSession({ logEntries: entries });
    });
  }

  function startAssistSession(logLabel) {
    return getLocalProfile()
      .then(function (profile) {
        return setSession({
          running: true,
          status: "running",
          sessionEmail: profile && profile.email ? String(profile.email).trim() : "",
          verificationState: "pending",
          networkLabel: "Default route",
          diagnosticsCheckpoint: "assist_start",
        });
      })
      .then(function () {
        return appendLog("info", logLabel);
      })
      .then(function () {
        return new Promise(function (resolve, reject) {
          chrome.runtime.sendMessage({ type: "OPEN_TARGET_PAGE" }, function (res) {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
              return;
            }
            if (res && res.ok) {
              resolve();
            } else {
              reject(new Error((res && res.error) || "Could not open tab"));
            }
          });
        });
      })
      .then(function () {
        return getSession();
      })
      .catch(function (e) {
        return setSession({ running: false, status: "error" })
          .then(function () {
            return appendLog("error", (e && e.message) || String(e));
          })
          .then(getSession);
      });
  }

  function stopAssistSession(logLabel) {
    return setSession({ running: false, status: "idle" })
      .then(function () {
        return appendLog("info", logLabel);
      })
      .then(getSession);
  }

  global.AppState = {
    getSession: getSession,
    setSession: setSession,
    appendLog: appendLog,
    getOptions: getOptions,
    setOptions: setOptions,
    getLocalProfile: getLocalProfile,
    setLocalProfile: setLocalProfile,
    applyTheme: applyTheme,
    initThemeFromStorage: initThemeFromStorage,
    onSessionChange: onSessionChange,
    onOptionsChange: onOptionsChange,
    pruneLogsForRetention: pruneLogsForRetention,
    startAssistSession: startAssistSession,
    stopAssistSession: stopAssistSession,
    defaultSession: defaultSession,
    defaultOptions: defaultOptions,
  };
})(typeof self !== "undefined" ? self : this);
