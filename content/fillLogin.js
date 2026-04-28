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

  function onPostLoginHome() {
    var p = (location.pathname || "").toLowerCase();
    return /\/global\/home\/index|\/global\/home\/?$/.test(p);
  }

  function onNewAppointmentPage() {
    var p = (location.pathname || "").toLowerCase();
    return p.indexOf("/global/appointment/newappointment") >= 0;
  }

  function findEmail() {
    var m = document.querySelector('input[type="email"]') || document.querySelector('input[autocomplete="email"]') || null;
    if (m && isUsable(m)) return m;
    var form = document.querySelector('form[action*="LoginSubmit" i]') || document.querySelector("form");
    if (!form) return null;
    var nodes = form.querySelectorAll("input");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var key = ((el.name || "") + " " + (el.id || "")).toLowerCase();
      var t = (el.type || "text").toLowerCase();
      if (!isUsable(el)) continue;
      if (t === "password" || /pass/.test(key)) continue;
      if ((t === "text" || t === "email" || t === "tel") && /user|email|log|mail/.test(key)) return el;
    }
    var fallback = form.querySelector('input[type="text"], input[type="email"], input[type="tel"], input:not([type])');
    return isUsable(fallback) ? fallback : null;
  }

  function findPassword() {
    var form = document.querySelector('form[action*="LoginSubmit" i]') || document.querySelector("form");
    if (!form) return null;
    var first = form.querySelector('input[type="password"], input[autocomplete="current-password"], input[name*="pass" i], input[id*="pass" i]');
    return isUsable(first) ? first : null;
  }

  function isUsable(el) {
    if (!el) return false;
    if (el.disabled) return false;
    if (el.classList && el.classList.contains("entry-disabled")) return false;
    var st = window.getComputedStyle(el);
    if (st && (st.display === "none" || st.visibility === "hidden")) return false;
    return true;
  }

  function patch(el, val) {
    if (!el || val == null) return;
    el.focus();
    el.value = val;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function submitLoginIfPossible() {
    if (typeof window.OnSubmitVerify === "function") {
      window.OnSubmitVerify();
      return true;
    }
    var btn = document.querySelector("#btnSubmit, button#btnSubmit, button[type='submit'], input[type='submit']");
    if (btn) {
      btn.click();
      return true;
    }
    var form = document.querySelector('form[action*="LoginSubmit" i]') || document.querySelector("form");
    if (form) {
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
      return true;
    }
    return false;
  }

  function onCaptchaPage() {
    var p = (location.pathname || "").toLowerCase();
    if (p.indexOf("newcaptcha") >= 0 || p.indexOf("logincaptcha") >= 0) return true;
    if (document.querySelector(".captcha-img")) return true;
    return false;
  }

  function parseCaptchaTarget() {
    var t = (document.body && document.body.innerText ? document.body.innerText : "").replace(/\s+/g, " ");
    var m = t.match(/please\s+select\s+all\s+boxes\s+with\s+number\s+(\d+)/i);
    if (m && m[1]) return m[1];
    return null;
  }

  function collectCaptchaTiles() {
    var out = [];
    var candidates = document.querySelectorAll(
      "[id^='captcha'], [id^='img'], [id^='box'], .captcha-item, .captcha-box, .captcha-image-box"
    );
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (!isUsable(el)) continue;
      var txt = (el.innerText || "").replace(/\D/g, "");
      var attr = (el.getAttribute("data-value") || el.getAttribute("title") || el.getAttribute("aria-label") || "");
      var attrDigits = String(attr).replace(/\D/g, "");
      out.push({ el: el, digits: txt || attrDigits || "" });
    }
    return out;
  }

  function solveCaptchaByDomText() {
    var target = parseCaptchaTarget();
    if (!target) return { solved: false, target: "", selected: 0 };
    var tiles = collectCaptchaTiles();
    var selected = 0;
    for (var i = 0; i < tiles.length; i++) {
      var d = tiles[i].digits;
      if (!d) continue;
      if (d === target || d.indexOf(target) >= 0) {
        try {
          tiles[i].el.click();
          selected++;
        } catch (e) {}
      }
    }
    return { solved: selected > 0, target: target, selected: selected };
  }

  function submitCaptchaIfPossible() {
    var btn = document.querySelector("#btnVerify, button#btnVerify, #btnSubmit, button[type='submit'], input[type='submit']");
    if (btn) {
      btn.click();
      return true;
    }
    var form = document.querySelector("#captchaForm, form[action*='CaptchaSubmit' i], form");
    if (form) {
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
      return true;
    }
    return false;
  }

  function goToBookNewAppointment() {
    var link =
      document.querySelector("a[href*='/Global/appointment/newappointment' i]") ||
      document.querySelector("a[href*='appointment/newappointment' i]");
    if (link) {
      link.click();
      return true;
    }
    return false;
  }

  function oncePerPath(key) {
    try {
      var k = "bls_once_" + key + "_" + location.pathname;
      if (sessionStorage.getItem(k) === "1") return false;
      sessionStorage.setItem(k, "1");
      return true;
    } catch (e) {
      return true;
    }
  }

  function isRateLimitPage() {
    var txt = (document.body && document.body.innerText ? document.body.innerText : "").toLowerCase();
    return txt.indexOf("too many requests") >= 0 && txt.indexOf("unusually high traffic") >= 0;
  }

  function submitCooldownMs() {
    // Keep extension from hammering login/captcha endpoints during server throttling.
    return 5 * 60 * 1000;
  }

  function retryAfterRateLimitMs() {
    // Conservative retry window to reduce repeated blocking.
    return 10 * 60 * 1000;
  }

  function canSubmitNow(stageKey) {
    try {
      var k = "bls_submit_ts_" + stageKey;
      var prev = Number(localStorage.getItem(k) || "0");
      if (!prev) return true;
      return Date.now() - prev >= submitCooldownMs();
    } catch (e) {
      return true;
    }
  }

  function markSubmitted(stageKey) {
    try {
      localStorage.setItem("bls_submit_ts_" + stageKey, String(Date.now()));
    } catch (e) {}
  }

  function shouldScheduleRateLimitRetry() {
    try {
      var k = "bls_rate_limit_retry_ts";
      var prev = Number(sessionStorage.getItem(k) || "0");
      if (!prev) return true;
      return Date.now() - prev >= retryAfterRateLimitMs();
    } catch (e) {
      return true;
    }
  }

  function markRateLimitRetryScheduled() {
    try {
      sessionStorage.setItem("bls_rate_limit_retry_ts", String(Date.now()));
    } catch (e) {}
  }

  function run() {
    if (!inScope()) return;
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

        var applied = false;
        var submitted = false;
        var stage = "unknown";

        if (isRateLimitPage()) {
          var scheduled = false;
          if (shouldScheduleRateLimitRetry()) {
            scheduled = true;
            markRateLimitRetryScheduled();
            setTimeout(function () {
              try {
                location.reload();
              } catch (e) {}
            }, retryAfterRateLimitMs());
          }
          if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
            chrome.runtime.sendMessage({
              type: "FILL_STATUS",
              applied: false,
              submitted: false,
              stage: "rate_limit",
              retryScheduled: scheduled,
              retryAfterMs: retryAfterRateLimitMs(),
            });
          }
          return;
        }

        if (onLoginishPage()) {
          stage = "login";
          var em = findEmail();
          var pw = findPassword();
          if (email && em) {
            patch(em, email);
            applied = true;
          }
          if (pass && pw) {
            patch(pw, pass);
            applied = true;
          }
          if (applied && oncePerPath("login_submit") && canSubmitNow("login")) {
            submitted = submitLoginIfPossible();
            if (submitted) markSubmitted("login");
          }
        } else if (onCaptchaPage()) {
          stage = "captcha";
          var cpw = document.querySelector('input[type="password"], input[name*="pass" i], input[id*="pass" i]');
          if (pass && cpw) {
            patch(cpw, pass);
            applied = true;
          }
          var solve = solveCaptchaByDomText();
          if (oncePerPath("captcha_submit") && canSubmitNow("captcha")) {
            submitted = submitCaptchaIfPossible();
            if (submitted) markSubmitted("captcha");
          }
          if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
            chrome.runtime.sendMessage({
              type: "FILL_STATUS",
              applied: applied || solve.solved,
              submitted: submitted,
              stage: "captcha",
              target: solve.target,
              selected: solve.selected,
            });
            return;
          }
        } else if (onPostLoginHome() && oncePerPath("goto_book")) {
          stage = "post_login";
          submitted = goToBookNewAppointment();
        } else if (onNewAppointmentPage()) {
          stage = "booking";
        }

        if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({
            type: "FILL_STATUS",
            applied: applied,
            submitted: submitted,
            stage: stage,
          });
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
