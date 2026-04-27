(function () {
  var headerChip = document.getElementById("headerChip");
  var headerStartStop = document.getElementById("headerStartStop");
  var headerOptions = document.getElementById("headerOptions");
  var sessionEmail = document.getElementById("sessionEmail");
  var sessionHint = document.getElementById("sessionHint");
  var captchaLine = document.getElementById("captchaLine");
  var captchaMeta = document.getElementById("captchaMeta");
  var netLabel = document.getElementById("netLabel");
  var netHttp = document.getElementById("netHttp");
  var netErr = document.getElementById("netErr");
  var verifyState = document.getElementById("verifyState");
  var btnInbox = document.getElementById("btnInbox");
  var dropZone = document.getElementById("dropZone");
  var fileInput = document.getElementById("fileInput");
  var previewCanvas = document.getElementById("previewCanvas");
  var dimsLabel = document.getElementById("dimsLabel");
  var logBox = document.getElementById("logBox");
  var linkOptions = document.getElementById("linkOptions");
  var timeline = document.querySelectorAll(".timeline-item");
  var diagToggle = document.getElementById("diagToggle");
  var diagPanel = document.getElementById("diagPanel");
  var diagUa = document.getElementById("diagUa");
  var diagVersion = document.getElementById("diagVersion");
  var diagCheckpoint = document.getElementById("diagCheckpoint");

  var ctx = previewCanvas.getContext("2d");

  function chipClassForStatus(status) {
    if (status === "running") return "chip chip-running";
    if (status === "error") return "chip chip-error";
    if (status === "success") return "chip chip-success";
    return "chip chip-idle";
  }

  function chipLabel(status, running) {
    if (running && status === "running") return "Running";
    if (status === "error") return "Error";
    if (status === "success") return "OK";
    return "Idle";
  }

  function renderLog(entries) {
    logBox.innerHTML = "";
    (entries || []).slice(-80).forEach(function (e) {
      var line = document.createElement("p");
      line.className = "log-line";
      var t = new Date(e.ts).toLocaleTimeString();
      line.textContent = "[" + t + "] " + (e.level || "info").toUpperCase() + " " + e.message;
      logBox.appendChild(line);
    });
    logBox.scrollTop = logBox.scrollHeight;
  }

  function setTimeline(s) {
    var st = s.status || "idle";
    var run = !!s.running;
    var states = ["idle", "idle", "idle", "idle", "idle"];
    if (run && st === "running") {
      states = ["running", "running", "idle", "idle", "idle"];
    } else if (st === "success") {
      states = ["done", "done", "done", "done", "done"];
    } else if (st === "error") {
      states = ["error", "error", "error", "error", "error"];
    }
    timeline.forEach(function (li, i) {
      li.setAttribute("data-state", states[i] || "idle");
    });
  }

  function renderHeader(s) {
    headerChip.className = chipClassForStatus(s.status);
    headerChip.textContent = chipLabel(s.status, s.running);
    if (s.running) {
      headerStartStop.textContent = "Stop";
      headerStartStop.classList.remove("btn-primary");
      headerStartStop.classList.add("btn-danger");
    } else {
      headerStartStop.textContent = "Start";
      headerStartStop.classList.add("btn-primary");
      headerStartStop.classList.remove("btn-danger");
    }
  }

  function renderCards(s) {
    sessionEmail.value = s.sessionEmail || "";
    if (s.running && s.status === "running") {
      sessionHint.textContent =
        "Session started — a tab to the site should open. Use Options for email/password you allow this browser to fill.";
      sessionHint.className = "muted placeholder-loading";
    } else if (s.sessionEmail) {
      sessionHint.textContent = "Display only here; stored credentials are set under Options (local to this device).";
      sessionHint.className = "muted";
    } else {
      sessionHint.textContent = "Set email and password under Options to enable form fill, or type on the page yourself.";
      sessionHint.className = "muted placeholder-empty";
    }

    if (s.captchaOutcome) {
      captchaLine.textContent = "Status: " + s.captchaOutcome;
      captchaLine.className = "";
      captchaMeta.textContent = s.captchaAt ? "At " + new Date(s.captchaAt).toLocaleString() : "";
    } else if (s.running && s.status === "running") {
      captchaLine.textContent = "Complete any verification in the page yourself — this extension will not auto-solve it.";
      captchaLine.className = "placeholder-loading";
      captchaMeta.textContent = "";
    } else {
      captchaLine.textContent = "No sign-in page activity yet. Start the workflow, then use the open tab.";
      captchaLine.className = "placeholder-empty";
      captchaMeta.textContent = "";
    }

    netLabel.textContent = s.networkLabel || "—";
    if (s.lastHttpStatus != null) {
      netHttp.textContent = String(s.lastHttpStatus);
      netHttp.className = "mono";
    } else if (s.running && s.status === "running") {
      netHttp.textContent = "—";
      netHttp.className = "mono placeholder-loading";
    } else {
      netHttp.textContent = "—";
      netHttp.className = "mono placeholder-empty";
    }
    if (s.lastNetworkError) {
      netErr.textContent = s.lastNetworkError;
      netErr.hidden = false;
    } else {
      netErr.textContent = "";
      netErr.hidden = true;
    }

    verifyState.textContent = "State: " + (s.verificationState || "idle");
    verifyState.className = s.verificationState === "error" ? "placeholder-error" : "placeholder-empty";

    diagUa.textContent = "UA: " + navigator.userAgent;
    diagVersion.textContent = "Extension v" + (chrome.runtime.getManifest().version || "0");
    diagCheckpoint.textContent = "Checkpoint: " + (s.diagnosticsCheckpoint || "—");
  }

  function setCardStates(s) {
    var panelState = s.status === "error" ? "error" : s.running ? "loading" : "idle";
    document.querySelectorAll("[data-card]").forEach(function (el) {
      el.setAttribute("data-state", panelState);
    });
  }

  function renderAll(s) {
    renderHeader(s);
    setTimeline(s);
    setCardStates(s);
    renderCards(s);
    renderLog(s.logEntries);
  }

  function persistEmail() {
    AppState.setSession({ sessionEmail: sessionEmail.value.trim() });
  }

  sessionEmail.addEventListener("change", persistEmail);
  sessionEmail.addEventListener("blur", persistEmail);

  headerStartStop.addEventListener("click", function () {
    AppState.getSession()
      .then(function (s) {
        if (s.running) {
          return AppState.stopAssistSession("Stopped from dashboard.");
        }
        return AppState.startAssistSession("Started from dashboard — opening the appointment site tab.");
      })
      .then(renderAll);
  });

  headerOptions.addEventListener("click", function () {
    chrome.runtime.openOptionsPage();
  });
  linkOptions.addEventListener("click", function () {
    chrome.runtime.openOptionsPage();
  });

  btnInbox.addEventListener("click", function () {
    window.open("https://mail.google.com/mail/u/0/#inbox", "_blank", "noopener,noreferrer");
  });

  diagToggle.addEventListener("click", function () {
    var wrap = diagToggle.closest(".collapsible");
    var open = diagPanel.hidden;
    diagPanel.hidden = !open;
    diagToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (wrap) wrap.classList.toggle("is-open", open);
  });

  function drawPreview(file) {
    var img = new Image();
    var url = URL.createObjectURL(file);
    img.onload = function () {
      var w = img.naturalWidth;
      var h = img.naturalHeight;
      var max = 120;
      var scale = Math.min(max / w, max / h, 1);
      var tw = Math.round(w * scale);
      var th = Math.round(h * scale);
      previewCanvas.width = tw;
      previewCanvas.height = th;
      ctx.clearRect(0, 0, tw, th);
      ctx.drawImage(img, 0, 0, tw, th);
      dimsLabel.textContent = "Original " + w + "×" + h + " → preview " + tw + "×" + th;
      URL.revokeObjectURL(url);
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      dimsLabel.textContent = "Could not read image.";
    };
    img.src = url;
  }

  dropZone.addEventListener("click", function () {
    fileInput.click();
  });
  dropZone.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  fileInput.addEventListener("change", function () {
    var f = fileInput.files && fileInput.files[0];
    if (f) drawPreview(f);
  });
  dropZone.addEventListener("dragover", function (e) {
    e.preventDefault();
    dropZone.style.borderColor = "var(--accent)";
  });
  dropZone.addEventListener("dragleave", function () {
    dropZone.style.borderColor = "";
  });
  dropZone.addEventListener("drop", function (e) {
    e.preventDefault();
    dropZone.style.borderColor = "";
    var f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f && f.type.indexOf("image/") === 0) drawPreview(f);
  });

  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.type === "SESSION_UPDATED" && msg.payload) {
      renderAll(msg.payload);
    }
  });

  AppState.initThemeFromStorage();
  AppState.getSession().then(renderAll);
  AppState.onSessionChange(renderAll);
  AppState.onOptionsChange(function (opts) {
    AppState.applyTheme(opts.theme);
  });
})();
