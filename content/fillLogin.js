/* Runs on the appointment site. Fills sign-in fields from locally stored (Options) credentials. */
(function () {
  var SITE_HOST = "appointment.thespainvisa.com";

  function inScope() {
    try {
      return location.hostname === SITE_HOST;
    } catch (e) {
      return false;
    }
  }

  function onLoginishPage() {
    var p = (location.pathname || "").toLowerCase();
    if (p.indexOf("login") >= 0) return true;
    if (p.indexOf("signin") >= 0 || p.indexOf("sign-in") >= 0) return true;
    if (/account[\\/].*log/i.test(p)) return true;
    return false;
  }

  function findEmail() {
    var m =
      document.querySelector('input[type="email"]') ||
      document.querySelector('input[autocomplete="email"]') ||
      null;
    if (m) return m;
    var form = document.querySelector("form");
    if (!form) return null;
    var nodes = form.querySelectorAll("input");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.type === "text" && el.name && /user|email|log|mail/i.test(el.name)) return el;
    }
    return form.querySelector('input[type="text"]') || form.querySelector("input:not([type])");
  }

  function findPassword() {
    return document.querySelector('input[type="password"]') || document.querySelector('input[autocomplete="current-password"]');
  }

  function patch(el, val) {
    if (!el || val == null) return;
    el.focus();
    el.value = val;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function run() {
    if (!inScope() || !onLoginishPage()) return;
    chrome.storage.sync.get("userOptions", function (syncRes) {
      if (chrome.runtime.lastError) return;
      var o = syncRes.userOptions;
      if (o && o.loginAssistEnabled === false) return;

      chrome.storage.local.get("extLocalLoginProfile", function (locRes) {
        if (chrome.runtime.lastError) return;
        var p = locRes.extLocalLoginProfile || { email: "", password: "" };
        var email = p.email && String(p.email).trim();
        var pass = p.password && String(p.password);
        if (!email && !pass) return;

        var em = findEmail();
        var pw = findPassword();
        var applied = false;
        if (email && em) {
          patch(em, email);
          applied = true;
        }
        if (pass && pw) {
          patch(pw, pass);
          applied = true;
        }
        if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ type: "FILL_STATUS", applied: applied });
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
