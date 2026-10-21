/* The /v5 page, part 6: Advanced tools (V5-Advanced). The programs, the settings, the fix-it buttons
   (the panel's own reload / reconnect), everything at once, the live output and the terminal
   commands. The proposal's "Saved desks" needs a change in EEPY (desks by name), so it is not here. */
(function () {
  "use strict";

  var EE = window.EE, $ = EE.$, esc = EE.esc, icon = EE.icon;
  var STATE_TEXT = { stopped: "Stopped", starting: "Starting…", running: "Running", stopping: "Stopping…", crashed: "Crashed", external: "Running in a terminal" };
  var STATE_TONE = { running: "success", crashed: "error" };
  var KIND = { sim: "simulator", panel: "", vision: "camera" };
  var CAMERAS = [
    { value: "", label: "Default" }, { value: "auto", label: "Auto" }, { value: "robot", label: "The robot's camera" },
    { value: "laptop", label: "Laptop camera" }, { value: "fake", label: "No camera (fake)" }
  ];
  var LOG_PARTS = [{ value: "", label: "Everything" }, { value: "launcher", label: "Launcher" }, { value: "sim", label: "My body" },
                   { value: "panel", label: "My control panel" }, { value: "vision", label: "My eyes" }];
  var dirty = false, msg = "", pending = {};
  var logs = [], lastLog = 0, logFilter = "", follow = true, timer = null;

  function sec(ic, title, hint) {
    return '<div class="sec"><span class="sec__ic">' + icon(ic, 20) + '</span><span style="display: flex; flex-direction: column;"><span class="headline">' + title + "</span>"
      + (hint ? '<span class="caption" style="color: var(--ink-muted);">' + hint + "</span>" : "") + "</span></div>";
  }
  function grey(label, attrs) { return EE.btn("secondary", label, (attrs || "") + ' style="background: var(--surface-2);"'); }

  function renderLog() {
    var box = $("#log");
    if (!box) return;
    var rows = logs.filter(function (r) { return !logFilter || r[1] === logFilter; }).slice(-800);
    var names = { launcher: "launcher", sim: "body", panel: "panel", vision: "eyes" };
    box.innerHTML = rows.map(function (r) {
      var bad = /error|traceback|exited with code [1-9]|does not exist|missing/i.test(r[2]);
      return '<span style="color: ' + (bad ? "var(--error)" : "var(--ink)") + ';">' + esc(names[r[1]] || r[1]) + "</span>  " + esc(r[2]);
    }).join("<br>");
    if (follow) box.scrollTop = box.scrollHeight;
  }
  function pollLogs() {
    if (EE.view !== "advanced") return;
    EE.getJSON("/api/logs?since=" + lastLog).then(function (data) {
      if (data.lines && data.lines.length) { logs = logs.concat(data.lines).slice(-4000); renderLog(); }
      lastLog = data.last || lastLog;
    }).catch(function () { /* next second */ });
  }

  EE.screens.advanced = {
    needsStatus: true,
    leave: function () { clearInterval(timer); timer = null; },
    render: function () {
      if (!EE.status) { EE.main.innerHTML = '<p class="body" style="padding: 80px; color: var(--ink-muted);">One moment…</p>'; return; }
      var s = EE.status.settings;
      dirty = false;
      EE.main.innerHTML = '<div class="adv"><header class="advhead"><img src="img/eepy-builder.svg" alt="">'
        + '<div style="flex: 1; display: flex; flex-direction: column; gap: var(--space-xs);"><span class="caption eyebrow">Advanced tools · for developers</span><h1 class="display-md" style="margin: 0;">Under my hood</h1>'
        + '<span class="body-lg" style="color: var(--ink-muted);">See what\'s running, change my settings, fix me when I\'m stuck. All of it also works from the terminal.</span></div><span id="classic"></span></header>'
        + '<section style="display: flex; flex-direction: column; gap: var(--space-sm);"><div class="sec"><span class="sec__ic">' + icon("Play", 20) + '</span><span class="headline">My programs</span></div><div class="adv3" id="programs"></div></section>'
        + '<div class="adv3b">'
        + '<section class="card">' + sec("Menu", "Settings", "Where to find my camera and robot.")
        + '<form id="settings" class="advform" autocomplete="off">'
        + EE.select({ id: "appearance", label: "Appearance", value: EE.appearance(), hint: "Saved for every screen. The Home switch does the same.",
            options: [{ value: "auto", label: "Follow my computer (day/night)" }, { value: "light", label: "Always light" }, { value: "dark", label: "Always dark" }] })
        + EE.select({ id: "camera", label: "Camera", value: s.camera, options: CAMERAS })
        + EE.textInput({ id: "robot_host", label: "Real robot address", value: s.robot_host, placeholder: EE.status.robot_host || "192.168.50.95" })
        + '<div class="pair">' + EE.textInput({ id: "panel_port", label: "Panel port", value: s.panel_port, type: "number" }) + EE.textInput({ id: "live_port", label: "Live page port", value: s.live_port, type: "number" }) + "</div>"
        + '<div style="display: flex; align-items: center; gap: var(--space-sm); margin-top: auto;"><button type="submit" class="ds-btn ds-btn--primary"><span class="ds-btn__label">Save settings</span></button>'
        + '<span class="caption" style="color: var(--ink-muted);" id="save-msg">I\'ll tell you what to restart.</span></div></form></section>'
        + '<section class="card">' + sec("Zap", "Fix-it buttons", "Try these first when I act strange.")
        + '<div style="display: flex; flex-direction: column; gap: var(--space-xs);">'
        + '<div class="fixit"><span class="body-sm" style="flex: 1;"><b>Reload my behaviour files</b><br><span style="color: var(--ink-muted);">After you edit lines or moves</span></span>' + EE.iconBtn("Reload behaviour files", "ArrowRight", 'data-act="fix" data-value="reload" id="fix-reload"') + "</div>"
        + '<div class="fixit"><span class="body-sm" style="flex: 1;"><b>Reconnect my body</b><br><span style="color: var(--ink-muted);">If I stopped moving</span></span>' + EE.iconBtn("Reconnect body", "ArrowRight", 'data-act="fix" data-value="reconnect" id="fix-reconnect"') + "</div>"
        + '<span class="caption" id="fix-msg" style="color: var(--ink-muted);">They need my control panel running.</span></div>'
        + EE.textInput({ id: "panel_extra", label: "Extra arguments · panel", value: s.panel_extra, placeholder: "e.g. --session-prior prior.json", attrs: 'form="settings"' })
        + "</section>"
        + '<section class="card">' + sec("Play", "Everything at once", "In order: body, control panel, eyes. Stop goes the other way.")
        + '<div style="display: flex; gap: var(--space-xs); flex-wrap: wrap;">' + grey("Start everything", 'data-act="start-all"') + grey("Stop everything", 'data-act="stop-all"') + "</div>"
        + '<p class="body-sm" id="adv-message" style="margin: 0;"></p><span class="caption" style="color: var(--ink-muted);">Stopping my control panel ends the session and puts me back to neutral.</span>'
        + EE.textInput({ id: "sim_extra", label: "Extra arguments · body", value: s.sim_extra, placeholder: "e.g. --headless", attrs: 'form="settings"' })
        + EE.textInput({ id: "vision_extra", label: "Extra arguments · eyes", value: s.vision_extra, placeholder: "e.g. --no-aus", attrs: 'form="settings"' })
        + "</section></div>"
        + '<div class="adv2">'
        + '<section class="card"><div style="display: flex; align-items: flex-end; gap: var(--space-md); flex-wrap: wrap;"><div style="flex: 1; min-width: 220px;">' + sec("Search", "Everything I printed", "Live messages from the launcher and my three programs.") + "</div>"
        + '<div style="width: 180px;">' + EE.select({ id: "log-filter", label: "Show", value: logFilter, options: LOG_PARTS }) + "</div>"
        + EE.switchEl("follow", "Follow", follow, 'data-act="follow"') + grey("Clear view", 'data-act="clear-log"') + "</div>"
        + '<div class="mono logbox" id="log" role="log" aria-live="off"></div></section>'
        + '<section class="card">' + sec("ChevronRight", "Same thing, in terminals", "One line per program, in the EEPY folder.")
        + '<div class="mono" id="commands" style="flex: 1; white-space: pre-wrap; word-break: break-all;"></div>' + EE.btn("secondary", "Copy all", 'data-act="copy-commands" style="background: var(--surface-2); align-self: flex-start;"') + "</section>"
        + "</div></div>";
      this.update();
      renderLog();
      clearInterval(timer);
      timer = setInterval(pollLogs, 1000);
      pollLogs();
    },
    update: function () {
      if (!EE.status || !$("#programs")) return;
      var s = EE.status.settings, inUse = EE.needed(), parts = EE.parts();
      EE.setHTML($("#programs"), ["sim", "panel", "vision"].map(function (name) {
        var p = parts[name] || {}, st = p.state || "stopped", busy = pending[name];
        var miss = p.missing && p.missing.length, used = inUse.indexOf(name) >= 0;
        var tone = miss ? "error" : STATE_TONE[st] || "neutral";
        var dot = EE.isUp(st) ? "var(--success)" : st === "crashed" || miss ? "var(--error)" : st === "starting" || st === "stopping" ? "var(--energy-mid)" : "var(--ink-disabled)";
        var actions;
        if (st === "external") actions = '<span class="caption" style="color: var(--ink-muted);">Started in a terminal: I leave it alone.</span>';
        else if (p.pid) actions = grey("Restart", 'data-act="part-restart" data-part="' + name + '"' + (busy ? " disabled" : "")) + grey(busy === "stop" ? "Stopping…" : "Stop", 'data-act="part-stop" data-part="' + name + '"' + (busy ? " disabled" : ""));
        else if (miss) actions = grey("Copy install commands", 'data-act="copy-install" data-part="' + name + '"');
        else actions = grey(busy === "start" ? "Starting…" : "Start", 'data-act="part-start" data-part="' + name + '"' + (busy ? " disabled" : ""));
        var facts = [];
        if (p.port) facts.push("port " + p.port);
        if (p.uptime != null) facts.push("up " + Math.max(1, Math.round(p.uptime / 60)) + " min");
        if (name === "panel" && EE.session().active) facts.push(esc(EE.session().participant) + " recording");
        if (miss) facts.push(esc(p.missing[0]) + " is missing");
        if (!used) facts.push("not used with " + EE.labelOf(EE.BACKENDS, s.backend).toLowerCase());
        return '<div class="card"' + (st === "crashed" || miss ? ' style="box-shadow: var(--shadow-error);"' : "") + '><div style="display: flex; align-items: center; gap: var(--space-sm);">'
          + '<span class="progdot" style="background: ' + dot + ";" + (EE.isUp(st) ? " box-shadow: 0 0 10px " + dot + ";" : "") + '"></span>'
          + '<b class="body" style="flex: 1;">' + EE.PART_NAMES[name] + (KIND[name] ? ' <span style="color: var(--ink-muted); font-weight: 400;">· ' + KIND[name] + "</span>" : "") + "</b>"
          + EE.badge(tone, miss ? "Not installed" : STATE_TEXT[st]) + "</div>"
          + '<span class="caption" style="color: var(--ink-muted);">' + facts.join(" · ") + '</span><div style="display: flex; gap: var(--space-xs); flex-wrap: wrap;">' + actions + "</div></div>";
      }).join(""));
      $("#commands").textContent = inUse.map(function (n) { return (parts[n] || {}).command || ""; }).join("\n\n");
      EE.setHTML($("#adv-message"), esc(msg || EE.status.step || EE.status.message || ""));
      var panel = parts.panel || {}, up = EE.isUp(panel.state);
      EE.setHTML($("#classic"), panel.url && up ? '<a href="' + esc(panel.url) + '" target="_blank" rel="noopener" class="body-sm" style="display: inline-flex; align-items: center; gap: var(--space-xxs);">Open the classic panel ' + icon("ArrowRight", 16) + "</a>" : "");
      ["fix-reload", "fix-reconnect"].forEach(function (id) { var b = $("#" + id); if (b) b.disabled = !up; });
      if (!dirty) {
        ["robot_host", "panel_port", "live_port", "sim_extra", "panel_extra", "vision_extra"].forEach(function (k) {
          var el = $("#" + k); if (el && document.activeElement !== el) el.value = s[k];
        });
        var cam = $("#camera"); if (cam && document.activeElement !== cam) cam.value = s.camera;
      }
    },
    act: function (act, el) {
      var self = this;
      if (act === "part-start" || act === "part-stop" || act === "part-restart") {
        var name = el.getAttribute("data-part");
        pending[name] = act === "part-stop" ? "stop" : "start";
        this.update();
        EE.post({ "part-start": "/api/start", "part-stop": "/api/stop", "part-restart": "/api/restart" }[act], { part: name }).then(function (res) {
          msg = res.message || ""; delete pending[name]; EE.refresh();
          setTimeout(function () { msg = ""; }, 6000);
        });
      } else if (act === "start-all" || act === "stop-all") {
        el.disabled = true;
        EE.post("/api/" + act, {}).then(function (res) { msg = res.message || ""; el.disabled = false; EE.refresh(); setTimeout(function () { msg = ""; }, 6000); });
      } else if (act === "fix") {
        var what = el.getAttribute("data-value"), out = $("#fix-msg");
        el.disabled = true;
        if (out) out.textContent = what === "reload" ? "Reloading…" : "Reconnecting…";
        EE.panelPost(what, {}).then(function (res) {
          el.disabled = false;
          if (out) {
            out.textContent = res.ok === false ? (res.message || "That didn't work.") : what === "reload" ? (res.message || "Reloaded.") : (res.message || "Connected.");
            out.style.color = res.ok === false ? "var(--error)" : "var(--ink-muted)";
          }
        });
      } else if (act === "copy-commands" || act === "copy-install") {
        var text = act === "copy-install" ? (el.getAttribute("data-part") === "vision" ? EE.INSTALL.vision : EE.INSTALL.robot) : $("#commands").textContent;
        if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { el.querySelector(".ds-btn__label").textContent = "Copied"; });
      } else if (act === "clear-log") { logs = []; renderLog(); }
      else if (act === "follow") { follow = el.getAttribute("aria-checked") !== "true"; el.setAttribute("aria-checked", follow ? "true" : "false"); renderLog(); }
      void self;
    },
    submit: function (e) {
      if (e.target.id !== "settings") return;
      e.preventDefault();
      var num = function (id) { return parseInt($("#" + id).value, 10); };
      var body = { camera: $("#camera").value, robot_host: $("#robot_host").value.trim(), panel_port: num("panel_port"), live_port: num("live_port"),
                   sim_extra: $("#sim_extra").value, panel_extra: $("#panel_extra").value, vision_extra: $("#vision_extra").value };
      EE.post("/api/settings", body).then(function (res) {
        var m = $("#save-msg");
        if (m) { m.textContent = res.message || ""; m.style.color = res.ok ? "var(--ink-muted)" : "var(--error)"; }
        if (res.ok) { dirty = false; EE.refresh(); }
      });
    },
    input: function (e) { if (e.target.closest && (e.target.closest("#settings") || e.target.getAttribute("form") === "settings")) dirty = true; },
    change: function (e) {
      if (e.target.id === "appearance") EE.setAppearance(e.target.value);
      else if (e.target.id === "camera") dirty = true;
      else if (e.target.id === "log-filter") { logFilter = e.target.value; renderLog(); }
    }
  };
})();
