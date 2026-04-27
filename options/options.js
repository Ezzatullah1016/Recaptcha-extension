(function () {
  var form = document.getElementById("optsForm");
  var theme = document.getElementById("theme");
  var logRetention = document.getElementById("logRetention");
  var notifications = document.getElementById("notifications");
  var saveStatus = document.getElementById("saveStatus");
  var targetLoginUrl = document.getElementById("targetLoginUrl");
  var loginAssistEnabled = document.getElementById("loginAssistEnabled");
  var loginEmail = document.getElementById("loginEmail");
  var loginPassword = document.getElementById("loginPassword");

  function fill(opts) {
    theme.value = opts.theme === "dark" ? "dark" : "light";
    logRetention.value = String(opts.logRetentionDays != null ? opts.logRetentionDays : 7);
    notifications.checked = !!opts.notificationsEnabled;
    targetLoginUrl.value =
      opts.targetLoginUrl || (AppState.defaultOptions && AppState.defaultOptions.targetLoginUrl) || "";
    loginAssistEnabled.checked = opts.loginAssistEnabled !== false;
    AppState.applyTheme(opts.theme);
    return AppState.getLocalProfile().then(function (p) {
      loginEmail.value = p.email || "";
      loginPassword.value = "";
    });
  }

  function showSaved() {
    saveStatus.textContent = "Saved.";
    setTimeout(function () {
      saveStatus.textContent = "";
    }, 2000);
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var days = parseInt(logRetention.value, 10);
    if (isNaN(days)) days = 7;
    var url = (targetLoginUrl.value && targetLoginUrl.value.trim()) || AppState.defaultOptions.targetLoginUrl;
    var partial = {
      email: loginEmail.value.trim(),
    };
    if (loginPassword.value) {
      partial.password = loginPassword.value;
    }
    AppState.setOptions({
      theme: theme.value,
      logRetentionDays: days,
      notificationsEnabled: notifications.checked,
      targetLoginUrl: url,
      loginAssistEnabled: loginAssistEnabled.checked,
    })
      .then(function (opts) {
        return AppState.pruneLogsForRetention(opts.logRetentionDays);
      })
      .then(function () {
        return AppState.setLocalProfile(partial);
      })
      .then(function () {
        showSaved();
      });
  });

  AppState.initThemeFromStorage().then(fill);
  AppState.onOptionsChange(function (opts) {
    fill(opts);
  });
})();
