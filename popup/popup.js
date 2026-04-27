(function () {
  var chip = document.getElementById("statusChip");
  var btnToggle = document.getElementById("btnToggle");
  var btnOpenPanel = document.getElementById("btnOpenPanel");
  var btnOptions = document.getElementById("btnOptions");

  function chipClassForStatus(status) {
    if (status === "running") return "chip chip-running";
    if (status === "error") return "chip chip-error";
    if (status === "success") return "chip chip-success";
    return "chip chip-idle";
  }

  function chipLabelForStatus(status, running) {
    if (running && status === "running") return "Running";
    if (status === "error") return "Error";
    if (status === "success") return "OK";
    if (status === "idle" || !running) return "Idle";
    return "Idle";
  }

  function renderSession(s) {
    var status = s.status || "idle";
    chip.className = chipClassForStatus(status);
    chip.textContent = chipLabelForStatus(status, s.running);

    if (s.running) {
      btnToggle.textContent = "Stop";
      btnToggle.classList.remove("btn-primary");
      btnToggle.classList.add("btn-danger");
    } else {
      btnToggle.textContent = "Start";
      btnToggle.classList.add("btn-primary");
      btnToggle.classList.remove("btn-danger");
    }
  }

  function openSidePanel() {
    return chrome.windows.getCurrent().then(function (win) {
      if (!win || win.id == null) return;
      return chrome.sidePanel.open({ windowId: win.id });
    });
  }

  btnOpenPanel.addEventListener("click", function () {
    openSidePanel().catch(function () {});
  });

  btnOptions.addEventListener("click", function () {
    chrome.runtime.openOptionsPage();
  });

  btnToggle.addEventListener("click", function () {
    AppState.getSession()
      .then(function (s) {
        if (s.running) {
          return AppState.stopAssistSession("Stopped from popup.");
        }
        return AppState.startAssistSession("Started from popup — opening the appointment site tab.");
      })
      .then(renderSession);
  });

  AppState.initThemeFromStorage();
  AppState.getSession().then(renderSession);
  AppState.onSessionChange(renderSession);
  AppState.onOptionsChange(function (opts) {
    AppState.applyTheme(opts.theme);
  });
})();
