/* The /v5 page, part 2: state, polling, the top bar, routes and the wiring.
   It only talks to the launcher (eepy/launcher/__main__.py): its JSON API (GET /api/status every
   second, /api/settings, /api/start-all, /api/logs, /api/bandit/run) and the /v5 page's own calls in
   v5api.py: the control panel's API passed through (/api/v5/panel/...), the robot's volume, the
   recorded sessions (read only) and the configured model. The old page at / is not touched.
   Routes: #/ (Home), #/setup, #/starting, #/desk, #/control, #/done/<session>, #/sessions,
   #/story/<session>, #/learned, #/advanced. Each screen is EE.screens[name] = {render, update}. */
(function () {
  "use strict";

  var EE = window.EE, $ = EE.$;
  var nav = $("#nav"), main = $("#view");
  EE.main = main;
  EE.screens = {};
  EE.status = null;           // the last /api/status
  EE.pstate = null;           // the last /api/v5/panel/state: {status, panel, log, seq}
  EE.pError = "";
  EE.view = "";
  EE.arg = "";                // the session name of #/done/<name> and #/story/<name>
  EE.OLD_LAUNCHER = "The launcher running now is an older version. Stop it (Ctrl+C in its terminal) and start python3 launch.py again.";

  EE.parts = function () { return (EE.status && EE.status.parts) || {}; };
  EE.needed = function () { return (EE.status && EE.status.needed) || []; };
  EE.isUp = function (state) { return state === "running" || state === "external"; };
  EE.missing = function (name) { var p = EE.parts()[name]; return !!(p && p.missing && p.missing.length); };
  EE.ps = function () { return EE.pstate && EE.pstate.status; };
  EE.session = function () { return (EE.ps() && EE.ps().session) || {}; };
  EE.lastRun = function () {
    try { var v = JSON.parse(EE.store.get("eepy-last") || "null"); return v && EE.PARTICIPANT.test(v.participant) ? v : null; } catch (e) { return null; }
  };

  EE.pollPanel = function () {
    return EE.getCoded("/api/v5/panel/state").then(function (res) {
      if (res.data && res.data.status) { EE.pstate = res.data; EE.pError = ""; }
      else if (res.code === 404 && res.data && res.data.error === "not found") { EE.pstate = null; EE.pError = EE.OLD_LAUNCHER; }
      else { EE.pstate = null; EE.pError = (res.data && res.data.message) || "My control panel isn't answering."; }
    }).catch(function () { EE.pstate = null; EE.pError = "The launcher did not answer."; });
  };
  EE.panelPost = function (path, body) {
    return EE.post("/api/v5/panel/" + path, body).then(function (res) {
      return res && res.error === "not found" ? { ok: false, message: EE.OLD_LAUNCHER } : res;
    });
  };

  // --- day / night: Auto follows the computer (prefers-color-scheme in tokens.css) ------------

  EE.appearance = function () {
    var v = EE.store.get("eepy-appearance");
    return v === "light" || v === "dark" ? v : "auto";
  };
  EE.applyTheme = function () {
    var v = EE.appearance();
    if (v === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", v);
  };
  EE.setAppearance = function (v) {
    EE.store.set("eepy-appearance", v === "auto" ? null : v);
    EE.applyTheme();
    var box = $("#appear");
    if (box) box.outerHTML = EE.appearanceSwitch();
    var sel = $("#appearance");
    if (sel) sel.value = EE.appearance();
  };
  window.addEventListener("storage", function (e) {
    if (e.key === "eepy-appearance" || e.key === null) { EE.applyTheme(); var box = $("#appear"); if (box) box.outerHTML = EE.appearanceSwitch(); }
  });
  EE.appearanceSwitch = function () {
    var cur = EE.appearance();
    var opt = function (v, ic, text, title) {
      return '<button type="button" role="radio" class="appear__opt' + (cur === v ? " is-on" : "") + '" aria-checked="' + (cur === v)
        + '" data-act="appearance" data-value="' + v + '"' + (title ? ' title="' + title + '"' : "") + ">" + ic + text + "</button>";
    };
    var monitor = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2"></rect><path d="M8 20h8M12 16v4"></path></svg>';
    var sun = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path></svg>';
    var moon = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path></svg>';
    return '<div class="appear" id="appear" role="radiogroup" aria-label="Appearance">'
      + '<span class="caption" style="color: var(--ink-muted); text-align: right;">Appearance<br><span class="micro">' + (cur === "auto" ? "follows your computer" : "saved for every screen") + "</span></span>"
      + '<div class="appear__seg">' + opt("auto", monitor, "Auto", "Follow my computer's day/night setting") + opt("light", sun, "Light") + opt("dark", moon, "Dark") + "</div></div>";
  };

  // --- the top bar (the design system's TopNav) ----------------------------------------------------

  var GROUP = { home: "home", setup: "run", starting: "run", desk: "run", control: "run", done: "run",
                sessions: "analyze", story: "analyze", learned: "analyze", advanced: "advanced" };
  function renderNav() {
    var g = GROUP[EE.view] || "home";
    var link = function (href, text, key) {
      return '<a href="' + href + '" class="ds-nav__link"' + (g === key ? ' aria-current="page"' : "") + ">" + text + "</a>";
    };
    nav.innerHTML = '<div class="ds-nav__bar"><div class="ds-nav__brand"><a href="#/" aria-label="EEPY, back to Home" class="brandlink"><img src="img/eepy-mark.svg" alt="" width="26" height="26">EEPY</a></div>'
      + '<nav class="ds-nav__links" aria-label="Main">' + link("#/", "Home", "home") + link("#/setup", "Run a session", "run")
      + link("#/sessions", "Analyze", "analyze") + link("#/advanced", "Advanced tools", "advanced") + "</nav>"
      + '<div class="ds-nav__actions"><span id="nav-session"></span><a class="nav-old" href="/v1">Old launcher (v1)</a></div></div>';
    updateNav();
  }
  // A session is recording: a way back to it from every screen.
  function updateNav() {
    var sess = EE.session();
    var here = EE.view === "control" || EE.view === "desk";
    EE.setHTML($("#nav-session"), sess.active && !here
      ? '<a class="nav-live" href="#/control"><span class="nav-live__dot" aria-hidden="true"></span>' + EE.esc(sess.participant) + " is recording · back to the session</a>" : "");
  }
  EE.updateNav = updateNav;

  // --- routes ------------------------------------------------------------------------------------

  var SCREENS = ["setup", "starting", "desk", "control", "sessions", "learned", "advanced"];
  function route() {
    var h = location.hash.replace(/^#\/?/, "");
    var m = /^(done|story)\/(.+)$/.exec(h);
    if (m) {
      try { EE.arg = decodeURIComponent(m[2]); } catch (e) { EE.arg = m[2]; }
      return m[1];
    }
    EE.arg = "";
    return SCREENS.indexOf(h) >= 0 ? h : "home";
  }

  function show() {
    var next = route();
    var leaving = EE.screens[EE.view];
    if (leaving && leaving.leave) leaving.leave(next);
    EE.view = next;
    EE.closeModal();
    EE.curves = {};
    renderNav();
    main.className = "scr scr-" + next;
    main.__html = null;
    var screen = EE.screens[next];
    if (screen) screen.render(EE.arg);
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
  EE.rerender = function () { var s = EE.screens[EE.view]; if (s) s.render(EE.arg); };

  EE.refresh = function () {
    return EE.getJSON("/api/status").then(function (data) {
      var first = !EE.status;
      EE.status = data;
      var screen = EE.screens[EE.view];
      if (screen) { if (first && screen.needsStatus) screen.render(EE.arg); else if (screen.update) screen.update(); }
      // The session screens follow the control panel; the others only need to know if one is recording.
      var live = EE.view === "desk" || EE.view === "control" || EE.view === "starting";
      if (!live && !EE.isUp((EE.parts().panel || {}).state) && !data.other_panel) { EE.pstate = null; updateNav(); return; }
      return EE.pollPanel().then(function () {
        var s = EE.screens[EE.view];
        if (s && s.panelUpdate) s.panelUpdate();
        updateNav();
      });
    }).catch(function () { /* the launcher is restarting: try again next second */ });
  };

  // --- wiring: every screen's buttons carry data-act; a screen handles its own in act() ----------

  main.addEventListener("click", function (e) {
    var el = e.target.closest("[data-act]");
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return;
    var act = el.getAttribute("data-act");
    if (el.tagName === "A" && el.getAttribute("href") === "#") e.preventDefault();     // a link that only acts
    if (act === "appearance") { EE.setAppearance(el.getAttribute("data-value")); return; }
    var screen = EE.screens[EE.view];
    if (screen && screen.act) screen.act(act, el, e);
  });
  ["input", "change", "submit"].forEach(function (type) {
    main.addEventListener(type, function (e) {
      var screen = EE.screens[EE.view];
      if (screen && screen[type]) screen[type](e);
    });
  });
  // The curves' hover: the time and the focus under the pointer (as the design system's FocusCurve).
  main.addEventListener("mousemove", function (e) {
    var plot = e.target.closest && e.target.closest(".ds-curve__plot");
    EE.$$(".ds-curve__plot").forEach(function (p) { if (p !== plot) EE.curveLeave(p); });
    if (plot) EE.curveHover(plot, e.clientX);
  });
  main.addEventListener("mouseleave", function () { EE.$$(".ds-curve__plot").forEach(EE.curveLeave); });

  // The modal and the drawer live outside <main>: their own clicks.
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("#modal [data-act], #drawer [data-act]");
    if (!el || el.disabled) return;
    var act = el.getAttribute("data-act");
    if (act === "modal-close") { EE.closeModal(); return; }
    var screen = EE.screens[EE.view];
    if (screen && screen.act) screen.act(act, el, e);
  });
  document.addEventListener("keydown", function (e) {
    if ($("#modal")) { if (e.key === "Escape") EE.closeModal(); return; }
    var screen = EE.screens[EE.view];
    if (screen && screen.key) screen.key(e);
  });

  EE.start = function () {
    EE.applyTheme();
    window.addEventListener("hashchange", show);
    show();
    EE.refresh();
    setInterval(EE.refresh, 1000);
  };
})();
