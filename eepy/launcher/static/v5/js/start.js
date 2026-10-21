/* The /v5 page, part 3: Home, Set up and Waking up (proposal screens V5-Home, V5-S1-Setup,
   V5-S2a-Starting and V5-S2a-Problem). */
(function () {
  "use strict";

  var EE = window.EE, $ = EE.$, esc = EE.esc, icon = EE.icon;
  var PART_NAMES = { sim: "My body", panel: "My control panel", vision: "My eyes" };
  EE.PART_NAMES = PART_NAMES;
  EE.INSTALL = {
    robot: "python3.11 -m venv .venv-robot\n.venv-robot/bin/pip install -r requirements-robot.txt",
    vision: "python3.11 -m venv .venv-vision\n.venv-vision/bin/pip install -r requirements-vision.txt"
  };
  var oneMoment = '<p class="body" style="padding: 80px; color: var(--ink-muted);">One moment…</p>';

  // The way into a session: save the settings, wake everything up, then (on Waking up) open the session.
  EE.startIntent = false;
  EE.ask = { busy: false, error: "", other: "" };
  EE.launch = function (code, settings) {
    var s = EE.status.settings;
    var changed = settings.backend !== s.backend || settings.mode !== s.mode || settings.robot_host !== (s.robot_host || "");
    var anyOurs = Object.keys(EE.parts()).some(function (n) { return EE.parts()[n].pid; });
    var open = EE.session();
    if (changed && anyOurs && open.active) {
      return Promise.reject(new Error(open.participant + "'s session is still recording: end it before changing how I work."));
    }
    return EE.post("/api/settings", settings).then(function (res) {
      if (!res.ok) throw new Error(res.message || "Those settings were not accepted.");
      // Programs I started with the old settings would keep them: restart them with the new ones.
      return changed && anyOurs ? EE.post("/api/stop-all", {}) : null;
    }).then(function () {
      return EE.post("/api/start-all", {});
    }).then(function () {
      EE.store.set("eepy-last", JSON.stringify({ participant: code, backend: settings.backend, mode: settings.mode }));
      EE.startIntent = true;
      EE.ask = { busy: false, error: "", other: "" };
      location.hash = "#/starting";
    });
  };

  // Open the session for the participant of Set up (or Same as last time), once everything is up.
  EE.beginSession = function () {
    var last = EE.lastRun();
    EE.ask = { busy: true, error: "", other: "" };
    EE.pollPanel().then(function () {
      var st = EE.ps();
      if (!st) throw new Error(EE.pError || "My control panel isn't answering yet.");
      var cur = st.session || {};
      if (cur.active) {
        if (last && cur.participant !== last.participant) {
          var e = new Error(cur.participant + "'s session is still open in my control panel.");
          e.other = cur.participant;
          throw e;
        }
        return null;
      }
      if (!last) throw new Error("I don't know who's studying. Go back to set up and type the code.");
      return EE.panelPost("session/start", { participant: last.participant }).then(function (res) {
        if (!res.ok) throw new Error(res.message || "The session didn't start.");
      });
    }).then(function () {
      EE.ask = { busy: false, error: "", other: "" };
      EE.startIntent = false;
      location.hash = "#/desk";
    }).catch(function (err) {
      EE.startIntent = false;
      EE.ask = { busy: false, error: err.message, other: err.other || "" };
      if (EE.view === "starting") EE.screens.starting.update();
    });
  };

  // --- Home (V5-Home) ----------------------------------------------------------------------------

  EE.screens.home = {
    render: function () {
      var now = new Date(), hour = now.getHours();
      var part = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
      var greet = { morning: "Good morning!", afternoon: "Good afternoon!", evening: "Good evening!" }[part];
      var ic = { morning: "Coffee", afternoon: "BookOpen", evening: "BedDouble" }[part];
      var last = EE.lastRun();
      EE.main.innerHTML = '<div class="home">'
        + '<div class="home__hero"><span class="glow" aria-hidden="true"></span><span class="home__shadow" aria-hidden="true"></span>'
        + '<img class="bob" src="img/eepy-wave.png" alt="EEPY waving hello"></div>'
        + '<div class="home__right">'
        + '<section class="rise home__hello" aria-label="EEPY says hello"><span class="home__tail" aria-hidden="true"></span>'
        + '<div class="home__chips"><span class="caption home__time">' + icon(ic, 16) + EE.cap(part) + " · " + EE.hhmm(now) + "</span>"
        + '<span class="caption" style="color: var(--ink-muted);">EEPY, your study buddy</span></div>'
        + '<h1 class="display-lg" style="margin: 0;">' + greet + "</h1>"
        + '<p class="body-lg" style="margin: var(--space-sm) 0 var(--space-lg); color: var(--ink-muted);">Shall we study together? Here\'s how I\'ll help:</p>'
        + '<div class="home__helps">'
        + '<div><span class="home__ic" style="color: var(--time-focus-start);">' + icon("BookOpen", 16) + '</span><span class="body-sm">I keep you company while you work</span></div>'
        + '<div><span class="home__ic" style="color: var(--time-focus-start);">' + icon("Search", 16) + '</span><span class="body-sm">I notice when your focus slips</span></div>'
        + '<div><span class="home__ic" style="color: var(--time-break);">' + icon("Coffee", 16) + '</span><span class="body-sm">I suggest a break at the right time</span></div>'
        + "</div></section>"
        + '<div class="rise2 home__actions">'
        + '<a href="#/setup" class="ds-btn ds-btn--primary home__start"><span class="ds-btn__label">Start a session</span>' + icon("ArrowRight", 16) + "</a>"
        + '<button type="button" class="ds-btn ds-btn--secondary home__same" data-act="same"'
        + (last ? ' title="' + esc(last.participant + " · " + EE.labelOf(EE.BACKENDS, last.backend) + " · " + EE.labelOf(EE.MODES, last.mode)) + '"' : ' disabled title="Nothing to repeat yet: start a session first."')
        + ">" + icon("Play", 16) + '<span class="ds-btn__label">Same as last time</span></button>'
        + "</div></div></div>"
        + '<footer class="home__foot"><span id="camera-note" class="home__note"></span><span class="body-sm" id="home-msg" style="color: var(--error);"></span><div style="flex: 1;"></div>' + EE.appearanceSwitch() + "</footer>";
      this.update();
    },
    update: function () {
      var note = $("#camera-note");
      if (!note || !EE.status) return;
      EE.setHTML(note, EE.missing("vision")
        ? EE.badge("error", "My camera isn't set up") + '<span class="body-sm" style="color: var(--ink-muted);">No worries, I\'ll show you the fix in set up.</span>' : "");
    },
    act: function (act, el) {
      if (act !== "same") return;
      var last = EE.lastRun();
      if (!last || !EE.status) return;
      el.disabled = true;
      EE.launch(last.participant, { backend: last.backend, mode: last.mode, robot_host: EE.status.settings.robot_host || "" }).catch(function (err) {
        el.disabled = false;
        var msg = $("#home-msg"); if (msg) msg.textContent = err.message;
      });
    }
  };

  // --- Set up (V5-S1-Setup) ----------------------------------------------------------------------

  var draft = null, known = [], helpOpen = false, startError = "";

  function selfChecks() {
    var rows = [], help = [];
    var robotMissing = EE.missing("panel") || (draft.backend === "sim" && EE.missing("sim"));
    rows.push(robotMissing ? ["bad", "My robot software is missing"] : ["ok", "My robot software"]);
    if (robotMissing) help.push(EE.INSTALL.robot);
    rows.push(EE.status.other_panel ? ["bad", "Another control panel is already open"] : ["ok", "No other control panel open"]);
    if (EE.missing("vision")) { rows.push(["bad", "My camera software is missing"]); help.push(EE.INSTALL.vision); }
    else rows.push(["ok", "My camera software"]);
    if (draft.backend === "real" && EE.status.robot) {
      rows.push(EE.status.robot.reachable ? ["ok", "I can reach my body at " + EE.status.robot.host] : ["bad", "I can't reach my body at " + EE.status.robot.host]);
    }
    return { rows: rows, help: help };
  }

  function showCodeError(text) {
    var field = $("#code-field"), msg = $("#code-msg");
    if (!field || !msg) return;
    field.classList.toggle("is-error", !!text);
    msg.className = "ds-field__msg" + (text ? " ds-field__msg--error" : "");
    msg.innerHTML = text ? icon("CircleAlert", 14) + "<span>" + esc(text) + "</span>" : "I'll know them by this code only, never by their name.";
    $("#code").setAttribute("aria-invalid", text ? "true" : "false");
  }

  function renderKnown() {
    var box = $("#known");
    if (!box) return;
    if (!known.length) { box.innerHTML = ""; return; }
    box.innerHTML = '<span class="caption" style="color: var(--ink-muted);">Or pick someone I\'ve met:</span>' + known.slice(0, 8).map(function (code) {
      var on = code === draft.participant;
      return '<button type="button" class="chip caption' + (on ? " is-on" : "") + '" aria-pressed="' + on + '" data-act="pick" data-value="' + code + '">' + code + "</button>";
    }).join("");
  }

  EE.screens.setup = {
    needsStatus: true,
    leave: function () { draft = null; },
    render: function () {
      if (!EE.status) { EE.main.innerHTML = oneMoment; return; }
      var s = EE.status.settings;
      if (!draft) {
        var last = EE.lastRun();
        draft = { participant: last ? last.participant : "", backend: s.backend, mode: s.mode, robot_host: s.robot_host || "" };
      }
      startError = "";
      EE.main.innerHTML = '<header class="pagehead"><div class="pagehead__text">'
        + '<span class="caption eyebrow">Run a session · step 1 of 3</span>'
        + '<h1 class="display-md" style="margin: 0;">Let\'s get me ready</h1>'
        + '<span class="body-lg" style="color: var(--ink-muted);">Tell me who\'s studying and how we\'ll work. I\'ll check myself meanwhile.</span></div>'
        + '<ol class="steps" aria-label="Steps"><li class="body-sm" aria-current="step">1 · Set up</li><li class="body-sm">2 · Session</li><li class="body-sm">3 · Wrap up</li></ol></header>'
        + '<div class="setup">'
        + '<section class="card"><div class="cardhead"><span class="ic36">' + icon("GraduationCap", 20) + '</span><span class="headline">Who\'s studying?</span></div>'
        + EE.textInput({ id: "code", fieldId: "code-field", label: "Participant code", value: draft.participant, placeholder: "e.g. P05", hint: "I'll know them by this code only, never by their name." })
        + '<div id="known" class="known"></div></section>'
        + '<section class="card">' + EE.radioGroup({ name: "backend", label: "Which me are we using?", options: EE.BACKENDS, value: draft.backend })
        + '<div id="host-field"' + (draft.backend === "real" ? "" : " hidden") + ">"
        + EE.textInput({ id: "host", label: "My address", value: draft.robot_host, placeholder: EE.status.robot_host || "192.168.50.95", hint: "Same Wi-Fi as me, please. Empty: " + esc(EE.status.robot_host || "the lab robot") + "." })
        + "</div></section>"
        + '<section class="card">' + EE.radioGroup({ name: "mode", label: "Who decides when I step in?", options: EE.MODES, value: draft.mode }) + "</section>"
        + '<section class="card selfcheck" id="selfcheck"></section>'
        + "</div>"
        + '<footer class="stepbar">' + EE.linkBtn("secondary", "Back", "#/")
        + '<div style="flex: 1;"></div><span class="body-sm stepbar__note" id="summary"></span>'
        + '<button type="button" class="ds-btn ds-btn--primary glowbtn" data-act="start" id="start"><span class="ds-btn__label">Start session</span>' + icon("ArrowRight", 18) + "</button></footer>";
      renderKnown();
      this.update();
      EE.getJSON("/api/v5/sessions").then(function (data) {
        var seen = {};
        known = [];
        (data.sessions || []).forEach(function (x) { if (x.participant && !seen[x.participant]) { seen[x.participant] = true; known.push(x.participant); } });
        known.sort();
        renderKnown();
      }).catch(function () { /* no sessions yet */ });
    },
    update: function () {
      var box = $("#selfcheck");
      if (!box || !EE.status || !draft) return;
      var c = selfChecks();
      var advice = [];
      if (EE.missing("vision")) advice.push("Without it I can't see how they're doing. We can still do a session where you call the shots.");
      if (EE.status.other_panel) advice.push("Close the other panel first (Ctrl+C in its terminal), or I would record into its session.");
      if (draft.backend === "real" && EE.status.robot && !EE.status.robot.reachable) advice.push("Check that I'm switched on and on the same Wi-Fi.");
      EE.setHTML(box, '<div style="display: flex; flex-direction: column; gap: var(--space-sm);"><span class="headline">I checked myself</span>'
        + c.rows.map(function (r) {
          return '<span class="body chk"><span class="' + r[0] + '">' + icon(r[0] === "ok" ? "Check" : "CircleAlert", 18) + "</span>" + esc(r[1]) + "</span>";
        }).join("") + "</div>"
        + '<img src="img/eepy-builder.svg" alt="">'
        + '<div style="grid-column: 1 / span 2; display: flex; flex-direction: column; gap: var(--space-sm);">'
        + advice.map(function (a) { return '<span class="body-sm" style="color: var(--ink-muted);">' + esc(a) + "</span>"; }).join("")
        + (c.help.length
          ? (helpOpen
            ? '<div class="caption codebox">' + esc(c.help.join("\n")) + "</div>"
              + '<div style="display: flex; gap: var(--space-xs);">' + EE.btn("secondary", "Copy commands", 'data-act="copy" style="background: var(--surface-2);"')
              + EE.btn("secondary", "Check again", 'data-act="recheck" style="background: var(--surface-2);"') + "</div>"
            : EE.btn("secondary", "How do I install it?", 'data-act="help" style="background: var(--surface-2); align-self: flex-start;"'))
          : "") + "</div>");
      var summary = $("#summary");
      if (summary) {
        EE.setHTML(summary, esc((draft.participant || "No code yet") + " · " + EE.labelOf(EE.BACKENDS, draft.backend) + " · " + EE.labelOf(EE.MODES, draft.mode))
          + '<br><span class="caption">' + (startError ? '<span style="color: var(--error);">' + esc(startError) + "</span>"
            : "Then I wake up, check the desk with " + esc(draft.participant || "them") + " and start recording") + "</span>");
      }
      var start = $("#start");
      if (start) { start.disabled = !!EE.status.busy; start.title = EE.status.busy ? "I'm still busy starting or stopping." : ""; }
    },
    act: function (act, el) {
      if (act === "pick") {
        draft.participant = el.getAttribute("data-value");
        $("#code").value = draft.participant;
        showCodeError(""); renderKnown(); this.update();
      } else if (act === "help") { helpOpen = true; this.update(); }
      else if (act === "copy") {
        if (navigator.clipboard) navigator.clipboard.writeText(selfChecks().help.join("\n")).then(function () { el.querySelector(".ds-btn__label").textContent = "Copied"; });
      } else if (act === "recheck") EE.refresh();
      else if (act === "start") this.start();
    },
    start: function () {
      var code = (draft.participant || "").trim().toUpperCase();
      draft.participant = code;
      if (!EE.PARTICIPANT.test(code)) {
        showCodeError(code ? '"' + code + '" isn\'t a participant code. Use the study\'s code, like P05: never a name.' : "Type the participant code first, like P05.");
        $("#code").focus();
        return;
      }
      showCodeError("");
      var button = $("#start");
      if (button) button.disabled = true;
      var self = this;
      EE.launch(code, { backend: draft.backend, mode: draft.mode, robot_host: draft.robot_host.trim() }).catch(function (err) {
        startError = err.message;
        if (button) button.disabled = false;
        self.update();
      });
    },
    input: function (e) {
      if (!draft) return;
      if (e.target.id === "code") { draft.participant = e.target.value.trim().toUpperCase(); showCodeError(""); renderKnown(); this.update(); }
      else if (e.target.id === "host") draft.robot_host = e.target.value;
    },
    change: function (e) {
      if (!draft || e.target.type !== "radio") return;
      draft[e.target.name] = e.target.value;
      if (e.target.name === "backend") $("#host-field").hidden = draft.backend !== "real";
      this.update();
    },
    key: function (e) {
      if (e.key === "Enter" && e.target && e.target.id === "code") { e.preventDefault(); this.start(); }
    }
  };

  // --- Waking up (V5-S2a-Starting, V5-S2a-Problem) -----------------------------------------------

  var FAIL = /stopped during start-up|did not come up|not started|does not exist|could not start|^Stopped:|already in use/i;
  var logTail = { part: "", lines: [] };

  function wakeState() {
    var list = EE.needed().map(function (n) { return { name: n, state: (EE.parts()[n] || {}).state || "stopped" }; });
    var done = list.filter(function (p) { return EE.isUp(p.state); }).length;
    var failed = !EE.status.busy && done < list.length && FAIL.test(EE.status.message || "");
    var bad = failed ? (list.filter(function (p) { return !EE.isUp(p.state); })[0] || {}).name : "";
    return { list: list, done: done, all: list.length > 0 && done === list.length, failed: failed, bad: bad,
             idle: !EE.status.busy && !failed && done < list.length };
  }

  function fetchLog(part) {
    EE.getJSON("/api/logs?since=0&part=" + encodeURIComponent(part)).then(function (data) {
      if (logTail.part !== part) return;
      // The program's own words, not the command line the launcher printed first ("$ ...").
      logTail.lines = (data.lines || []).map(function (row) { return row[2]; }).filter(function (t) { return t.indexOf("$ ") !== 0; }).slice(-6);
      if (EE.view === "starting") EE.screens.starting.update();
    }).catch(function () { /* keep the dots */ });
  }

  function pills(w) {
    return '<ol class="wake__pills">' + w.list.map(function (p) {
      var label = (EE.parts()[p.name] || {}).label || "";
      var up = EE.isUp(p.state), now = !up && (p.state === "starting" || (EE.status.busy && !!label && (EE.status.step || "").indexOf(label) >= 0));
      var isBad = w.failed && p.name === w.bad;
      var mark = up ? '<span class="ok">' + icon("Check", 16) + "</span>" : isBad ? '<span class="bad">' + icon("X", 16) + "</span>"
        : now ? '<span class="spin-dot" aria-hidden="true"></span>' : '<span class="idle-dot" aria-hidden="true"></span>';
      return '<li class="body-sm' + (now ? " is-now" : "") + (isBad ? " is-bad" : "") + (up || now || isBad ? "" : " is-later") + '">' + mark + PART_NAMES[p.name] + "</li>";
    }).join("") + "</ol>";
  }

  EE.screens.starting = {
    needsStatus: true,
    render: function () {
      EE.main.__html = null;
      if (!EE.status) { EE.main.innerHTML = oneMoment; return; }
      // Built once; only what changed is updated, so the ring keeps spinning smoothly.
      EE.main.innerHTML = '<div class="wake" id="wake">'
        + '<div class="wake__ring" id="wake-ring"><span class="wake__breathe breathe" aria-hidden="true"></span>'
        + '<svg width="260" height="260" viewBox="0 0 260 260" aria-hidden="true" class="wake__svg"><circle cx="130" cy="130" r="120" fill="none" style="stroke: var(--time-track);" stroke-width="6"></circle>'
        + '<circle id="ring-done" class="ring__done" cx="130" cy="130" r="120" fill="none" stroke-width="6" stroke-linecap="round" stroke-dasharray="0 754" transform="rotate(-90 130 130)"></circle></svg>'
        + '<svg id="ring-spin" class="spin wake__svg" width="260" height="260" viewBox="0 0 260 260" aria-hidden="true"><defs><linearGradient id="ringgrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" style="stop-color: var(--time-focus-start);"></stop><stop offset="1" style="stop-color: var(--time-focus);"></stop></linearGradient></defs>'
        + '<circle cx="130" cy="130" r="120" fill="none" stroke="url(#ringgrad)" stroke-width="6" stroke-linecap="round" stroke-dasharray="250 504"></circle></svg>'
        + '<span class="wake__shadow shadowpulse" aria-hidden="true"></span>'
        + '<img id="ring-img" class="bob2" src="img/eepy-builder.svg" decoding="async" alt="EEPY in a builder\'s hat, setting up"></div>'
        + '<div class="wake__text"><span class="caption eyebrow" id="wake-eyebrow">Run a session · step 2 of 3</span>'
        + '<h1 class="display-md" aria-live="polite" id="wake-title" style="margin: 0;"></h1><span class="body-lg" id="wake-text" style="color: var(--ink-muted); max-width: 640px;"></span></div>'
        + '<div id="wake-pills"></div><div id="wake-extra" class="wake__extra"></div><span class="caption" id="wake-hint" style="color: var(--ink-muted);"></span></div>'
        + '<footer class="stepbar" id="wake-foot" hidden></footer>';
      this.update();
    },
    update: function () {
      if (!EE.status) return;
      if (!$("#wake")) { this.render(); return; }
      var w = wakeState(), last = EE.lastRun();
      var title, text, extra = "", foot = "", hint = "";
      if (w.failed) {
        title = { vision: "I couldn't open my eyes.", panel: "My control panel didn't start.", sim: "My body didn't start." }[w.bad] || "Something didn't start.";
        var ready = w.list.filter(function (p) { return EE.isUp(p.state); }).map(function (p) { return PART_NAMES[p.name].replace("My ", "my "); });
        text = { vision: "My camera program did not come up, so I didn't open the session.", panel: "My control panel did not come up, so I stopped there.", sim: "My simulator did not come up, so I stopped there." }[w.bad] || "So I stopped there.";
        if (ready.length) text += " " + EE.cap(ready.join(" and ")) + (ready.length > 1 ? " are" : " is") + " ready.";
        if (logTail.part !== w.bad) { logTail = { part: w.bad, lines: [] }; fetchLog(w.bad); }
        var tips = EE.missing(w.bad) ? ["Its software is missing: install it first (README, step 1)", "Or use the commands in Advanced tools", "Then try again"]
          : w.bad === "vision" ? ["Close other apps that use the camera", "Check the camera choice in Advanced tools", "Try again"]
          : ["Look at what its log says", "Check the settings in Advanced tools", "Try again"];
        extra = '<section class="wakebox"><div style="display: flex; flex-direction: column; gap: var(--space-xs);">'
          + '<span class="caption eyebrow">Try this</span>' + tips.map(function (t, i) { return '<span class="body-sm"><b>' + (i + 1) + "</b> " + esc(t) + "</span>"; }).join("")
          + '</div><div style="display: flex; flex-direction: column; gap: var(--space-xs);"><span class="caption eyebrow">What my log says</span>'
          + '<span class="caption logline">' + esc(logTail.lines.length ? logTail.lines.join("\n") : EE.status.message || "Nothing: it never got to start.") + "</span>"
          + '<a href="#/advanced" class="caption">Open the full log</a></div></section>'
          + (EE.ask.error ? '<p class="body-sm" style="color: var(--error); margin: 0;">' + esc(EE.ask.error) + "</p>" : "");
        // Without my eyes a session still works (you call the shots, nothing is estimated): offer it.
        var noEyes = w.bad === "vision" && EE.isUp((EE.parts().panel || {}).state) && last;
        foot = EE.linkBtn("secondary", "Back to set up", "#/setup")
          + (noEyes ? EE.btn("secondary", "Go on without my eyes", 'data-act="begin"') + '<span class="caption" style="color: var(--ink-muted);">Then only you decide when I step in.</span>' : "")
          + '<div style="flex: 1;"></div><button type="button" class="ds-btn ds-btn--primary glowbtn" data-act="retry"><span class="ds-btn__label">Try again</span></button>';
      } else if (w.all) {
        if (EE.startIntent && !EE.ask.busy && !EE.ask.error) EE.beginSession();
        title = "I'm awake!";
        if (EE.ask.error) {
          title = "I'm awake, but the session didn't open.";
          text = esc(EE.ask.error);
          foot = EE.linkBtn("secondary", "Back to set up", "#/setup") + '<div style="flex: 1;"></div>'
            + (EE.ask.other
              ? EE.linkBtn("secondary", "Go to " + esc(EE.ask.other) + "'s session", "#/control")
                + (last ? EE.btn("primary", "End it and start " + esc(last.participant) + "'s", 'data-act="end-other"') : "")
              : EE.btn("primary", "Try again", 'data-act="begin"'));
        } else if (EE.startIntent || EE.ask.busy) {
          text = "Opening " + esc(last ? last.participant : "the") + "'s session…<br>Then we'll check the desk together and begin.";
        } else {
          text = "My " + w.list.map(function (p) { return PART_NAMES[p.name].replace("My ", ""); }).join(", ") + " are ready.";
          foot = EE.linkBtn("secondary", "Back to Home", "#/") + '<div style="flex: 1;"></div>'
            + (last ? EE.btn("primary", "Start " + esc(last.participant) + "'s session", 'data-act="begin"', "ArrowRight") : EE.linkBtn("primary", "Set up a session", "#/setup"));
        }
      } else if (w.idle) {
        title = "Nothing is starting right now.";
        text = "Set up a session first, and I'll wake up.";
        foot = EE.linkBtn("secondary", "Back to Home", "#/") + '<div style="flex: 1;"></div>' + EE.linkBtn("primary", "Go to set up", "#/setup");
      } else {
        title = "Waking up…";
        text = esc(EE.status.step || "I'm starting everything myself") + (last ? "<br>Then we'll check " + esc(last.participant) + "'s desk together and begin" : "");
        hint = "If something won't start, I'll tell you right here what to do.";
      }
      var frac = w.list.length ? w.done / w.list.length : 0;
      var done = $("#ring-done");
      done.setAttribute("stroke-dasharray", (w.failed ? Math.max(0.5, frac) : frac) * 754 + " 754");
      done.style.stroke = w.failed ? "var(--error)" : "var(--time-focus)";
      $("#ring-spin").style.opacity = EE.status.busy || EE.ask.busy ? "1" : "0";
      $("#wake-ring").classList.toggle("is-failed", w.failed);
      $("#ring-img").classList.toggle("bob2", !w.failed);
      var eyebrow = $("#wake-eyebrow");
      EE.setHTML(eyebrow, w.failed ? icon("CircleAlert", 12) + "Run a session · step 2 of 3 · waking up" : "Run a session · step 2 of 3");
      eyebrow.style.color = w.failed ? "var(--error)" : "";
      EE.setHTML($("#wake-title"), title);
      EE.setHTML($("#wake-text"), text);
      EE.setHTML($("#wake-pills"), pills(w));
      EE.setHTML($("#wake-extra"), extra);
      EE.setHTML($("#wake-hint"), hint);
      var footer = $("#wake-foot");
      footer.hidden = !foot;
      EE.setHTML(footer, foot);
    },
    panelUpdate: function () { /* the session opens through beginSession */ },
    act: function (act, el) {
      if (act === "retry") { EE.startIntent = true; EE.ask = { busy: false, error: "", other: "" }; EE.post("/api/start-all", {}).then(EE.refresh); }
      else if (act === "begin") { EE.ask = { busy: false, error: "", other: "" }; EE.beginSession(); this.update(); }
      else if (act === "end-other") {
        el.disabled = true;
        EE.panelPost("session/end", {}).then(function (res) {
          if (!res.ok) { EE.ask.error = res.message || "That session didn't end."; EE.screens.starting.update(); return; }
          EE.ask = { busy: false, error: "", other: "" };
          EE.beginSession();
        });
      }
    }
  };
})();
