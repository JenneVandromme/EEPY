/* The /v5 page, part 4: the session. Desk check (V5-S2b-*), control panel (V5-S2-Control, with
   its Live HMM view), ending the session, and the wrap up (V5-S3-WrapUp). Everything goes through
   the control panel's own API (EE.panelPost / EE.pstate) and the recorded session (v5api). */
(function () {
  "use strict";

  var EE = window.EE, $ = EE.$, esc = EE.esc, icon = EE.icon;

  function line(id, tone) {
    var l = EE.model && EE.model.lines && EE.model.lines[id];
    return l ? String(l[tone || "hedged"] || l.hedged || "").replace(/\{minutes\}/g, "…") : "";
  }

  // --- the session bar (desk check and control panel) ---------------------------------------------

  var stopSvg = '<svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true"><rect x="1" y="1" width="12" height="12" rx="2" fill="currentColor"></rect></svg>';
  function sessbar(kind) {
    var desk = kind === "desk";
    return '<section class="sessbar' + (desk ? " sessbar--desk" : "") + '" id="sessbar">'
      + '<span id="sb-badge"></span><span id="sb-who" class="' + (desk ? "body-lg" : "headline") + '"></span>'
      + '<span id="sb-time" class="' + (desk ? "body" : "body-lg") + '" style="font-variant-numeric: tabular-nums; color: var(--ink-muted);"></span>'
      + '<span id="sb-more" class="sessbar__more"></span><div style="flex: 1;"></div>'
      + '<button type="button" class="stopbtn" data-act="stop-eepy" title="Stops my moves and voice at once. The session keeps going." aria-label="Stop EEPY now (Esc)">' + stopSvg + "Stop EEPY" + EE.kbd("escape") + "</button>"
      + '<button type="button" class="endred" data-act="end-open" id="sb-end" title="End the session and save. I ask you to confirm first.">End session</button></section>';
  }
  function statusDot(ok, text, warn) {
    return '<span class="caption sdotline"><span class="sdot8" style="background: ' + (ok ? "var(--success)" : warn ? "var(--energy-mid)" : "var(--error)") + ';"></span>' + text + "</span>";
  }
  function updateSessbar(kind) {
    var st = EE.ps(), sess = EE.session();
    EE.setHTML($("#sb-badge"), !sess.active ? EE.badge("neutral", "No session") : kind === "desk" ? EE.badge("neutral", "Getting ready") : EE.badge("error", "Recording"));
    EE.setHTML($("#sb-who"), sess.active ? (kind === "desk" ? "<b>" + esc(sess.participant) + "</b>" : esc(sess.participant)) : "");
    EE.setHTML($("#sb-time"), sess.active ? EE.clockOf(sess.elapsed_s) : "");
    var more = "";
    if (sess.active && kind === "desk") more = '<span class="caption" style="color: var(--ink-muted);">The session is open. We study after the desk check.</span>';
    else if (sess.active && st) {
      var cal = st.calibration || {};
      var desk = { done: "Desk checked", reused: "Last desk check", skipped: "No desk check" }[cal.state] || "No desk check";
      more = EE.badge("neutral", esc(EE.labelOf(EE.MODES, sess.mode || st.mode)))
        + (st.backend === "dry" ? statusDot(true, "No robot") : statusDot(st.connected, "Body"))
        + statusDot(!!cal.vision_connected, "Eyes") + statusDot(cal.state === "done" || cal.state === "reused", desk, true);
    }
    EE.setHTML($("#sb-more"), more);
    var end = $("#sb-end");
    if (end) end.disabled = !sess.active;
  }

  function panelMissing() {
    var up = EE.isUp((EE.parts().panel || {}).state);
    var title = EE.pError === EE.OLD_LAUNCHER ? "Please restart the launcher." : up ? "My control panel isn't answering." : "My control panel isn't running.";
    return '<div class="empty"><img src="img/eepy-builder.svg" alt=""><h1 class="display-md" style="margin: 0;">' + title + "</h1>"
      + '<p class="body-lg" style="color: var(--ink-muted); margin: 0;">' + esc(EE.pError || "") + (EE.pError === EE.OLD_LAUNCHER ? "" : up ? " Give it a second." : " Wake me up first: set up a session.") + "</p>"
      + '<div class="actions">' + EE.linkBtn("primary", "Go to set up", "#/setup") + EE.linkBtn("secondary", "Advanced tools", "#/advanced") + "</div></div>";
  }
  var lastName = "";
  function noSession() {
    var last = EE.lastRun();
    return '<div class="empty"><img src="img/eepy-wave.png" alt=""><h1 class="display-md" style="margin: 0;">No session is running.</h1>'
      + '<p class="body-lg" style="color: var(--ink-muted); margin: 0;">' + (lastName ? "The last one has ended and everything is saved." : "My control panel is awake, but nobody is recording.") + "</p>"
      + '<div class="actions">'
      + (lastName ? EE.linkBtn("secondary", "See how it went", "#/done/" + encodeURIComponent(lastName)) : "")
      + (last ? EE.btn("primary", "Start " + esc(last.participant) + "'s session", 'data-act="begin"') : "")
      + EE.linkBtn(last ? "secondary" : "primary", "Set up a session", "#/setup") + "</div></div>";
  }

  // Shared by the desk check and the control panel.
  function stopEepy(via) {
    EE.panelPost("stop", { via: via }).then(function (r) { answer(r, function () { return "Stopped: no moves, no voice."; }); });
  }
  function commonAct(act, el) {
    if (act === "stop-eepy") { stopEepy("click"); return true; }
    if (act === "end-open") { openEnd(); return true; }
    if (act === "end-confirm") { endSession(el); return true; }
    if (act === "begin") { EE.ask = { busy: false, error: "", other: "" }; EE.beginSession(); return true; }
    return false;
  }

  // --- the desk check (V5-S2b-Calibration and the step screens) -------------------------------------

  var desk = { busy: false, msg: "", seen: "", reason: "", choice: "", back: false };
  var REASONS = [{ value: "", label: "Pick a reason" }, { value: "No time today", label: "No time today" },
                 { value: "The camera isn't working", label: "The camera isn't working" },
                 { value: "They'd rather not", label: "They'd rather not" }, { value: "Just testing", label: "Just testing" }];
  var STAGES = [["choose", "Choose"], ["face", "1 · Face"], ["gaze", "2 · Gaze"], ["baseline", "3 · Normal work"], ["done", "Done"]];

  function stepsBar(current, failed) {
    var idx = STAGES.map(function (s) { return s[0]; }).indexOf(current);
    return '<ol class="desksteps" aria-label="Desk check steps">' + STAGES.map(function (s, i) {
      var cls = i === idx ? (failed ? " is-bad" : " is-now") : i < idx ? " is-done" : "";
      return '<li class="caption' + cls + '">' + (i < idx ? icon("Check", 12) : "") + s[1] + "</li>";
    }).join("") + "</ol>";
  }
  function deskHead(title, text, stage, failed) {
    return '<header class="deskhead"><div class="deskhead__text"><span class="caption eyebrow">Run a session · step 2 of 3 · before we study: your desk</span>'
      + '<h1 class="display-md" style="margin: 0;" aria-live="polite">' + title + '</h1><span class="body-lg" style="color: var(--ink-muted);">' + text + "</span></div>"
      + stepsBar(stage, failed) + "</header>";
  }
  function deskFoot(left, hint, right) {
    return '<footer class="stepbar">' + left + '<div style="flex: 1;"></div>' + (hint ? '<span class="body-sm" style="color: var(--ink-muted); align-self: center;">' + hint + "</span>" : "") + right + "</footer>";
  }
  function saying(text) {
    return text ? '<div class="saying"><img src="img/eepy-mark.svg" alt="" width="32" height="32"><span class="body"><span class="caption" style="display: block; color: var(--ink-muted);">I\'m saying to ' + esc(EE.session().participant || "them") + "</span><i>\"" + esc(text) + "\"</i></span></div>" : "";
  }
  function chk(kind, text, iconName) {
    return '<span class="body-sm chk"><span class="' + kind + '">' + icon(iconName || (kind === "ok" ? "Check" : "CircleAlert"), 20) + "</span>" + text + "</span>";
  }

  function chooseView(cal, sess) {
    var who = esc(sess.participant), eyes = !!cal.vision_connected, reuse = cal.reuse;
    if (!desk.choice) desk.choice = reuse ? "reuse" : "new";
    var setup = (EE.ps() || {}).backend === "real" ? "robot camera" : "laptop camera";
    var age = !reuse ? "" : reuse.age_days == null ? "" : reuse.age_days < 1 ? "today" : Math.round(reuse.age_days) === 1 ? "yesterday" : Math.round(reuse.age_days) + " days ago";
    var known = reuse
      ? '<label class="pick' + (desk.choice === "reuse" ? " is-on" : "") + '" data-act="desk-choice" data-value="reuse"><input type="radio" name="cal"' + (desk.choice === "reuse" ? " checked" : "") + '><span class="ic">' + icon("BookOpen", 20) + "</span>"
        + '<span style="flex: 1; display: flex; flex-direction: column;"><b class="body">My last desk check</b><span class="caption" style="color: var(--ink-muted);">' + esc(age) + " · " + setup + (reuse.late_evening ? " · late evening" : "") + "</span></span>" + EE.badge("success", "Good to reuse") + "</label>"
      : '<div class="pick pick--none"><span class="ic">' + icon("BookOpen", 20) + '</span><span style="flex: 1; display: flex; flex-direction: column;"><b class="body">No desk I can reuse yet</b><span class="caption">I keep a desk check for 14 days, at the same desk and camera.</span></span>' + EE.badge("neutral", "None yet") + "</div>";
    var left = '<section class="card"><div style="display: flex; align-items: baseline; gap: var(--space-sm);"><span class="headline" style="flex: 1;">Desks I know for ' + who + '</span><span class="caption" style="color: var(--ink-muted);">reusable for 14 days</span></div>'
      + '<div role="radiogroup" aria-label="Saved desks" style="display: flex; flex-direction: column; gap: var(--space-xs);">' + known + "</div>"
      + '<div class="ordiv"><span></span><span class="caption">or</span><span></span></div>'
      + '<label class="pick' + (desk.choice === "new" ? " is-on" : "") + '" data-act="desk-choice" data-value="new"><input type="radio" name="cal"' + (desk.choice === "new" ? " checked" : "") + '><span class="ic">' + icon("Plus", 20) + "</span>"
      + '<span style="flex: 1; display: flex; flex-direction: column;"><b class="body">Teach me a new desk</b><span class="caption" style="color: var(--ink-muted);">About 2 minutes, 3 short steps.</span></span>' + icon("ChevronRight", 20) + "</label>"
      + (eyes ? "" : '<p class="body-sm chk" style="margin: 0;"><span class="wait">' + icon("CircleAlert", 20) + "</span>My eyes aren't connected yet, so I can't check the desk. Wait a moment, or skip.</p>")
      + (desk.msg ? '<p class="body-sm chk" role="alert" style="margin: 0;"><span class="bad">' + icon("CircleAlert", 20) + "</span>" + esc(desk.msg) + "</p>" : "")
      + "</section>";
    var right = '<section class="card"><div style="display: flex; align-items: center; gap: var(--space-md);"><img src="img/eepy-scientist.svg" alt="" style="width: 64px; height: 64px; object-fit: contain; flex: none;">'
      + '<div style="display: flex; flex-direction: column; gap: var(--space-xxs);"><span class="caption eyebrow">Why I check the desk</span><span class="headline">So I judge ' + who + " by their own face, not by an average one.</span></div></div>"
      + '<div class="why2"><div><div class="why2__h"><span class="ic">' + icon("Search", 20) + '</span><b class="body">Eyes differ</b></div><span class="body-sm" style="color: var(--ink-muted);">The same half-open eyes can mean two things:</span>'
      + '<div class="why2__rows"><div><span class="caption">For one person</span>' + EE.badge("neutral", "tired") + "</div><div><span class=\"caption\">For " + who + "</span>" + EE.badge("success", "just normal") + "</div></div></div>"
      + '<div><div class="why2__h"><span class="ic">' + icon("BookOpen", 20) + '</span><b class="body">Desks differ</b></div><span class="body-sm" style="color: var(--ink-muted);">I learn where things are, so I know what counts as work:</span>'
      + '<div class="why2__rows"><div><span class="caption">Looking at the screen</span>' + EE.badge("success", "working") + '</div><div><span class="caption">Looking at the notes</span>' + EE.badge("success", "working")
      + '</div><div><span class="caption">Looking to the side</span>' + EE.badge("neutral", "away") + "</div></div></div></div>"
      + '<div style="display: flex; flex-direction: column; gap: var(--space-xs); margin-top: auto;">'
      + chk("ok", "3 short steps, about 2 minutes, I talk " + who + " through them", "AlarmClock")
      + chk("ok", "I keep it for this desk for 14 days, so next time it takes a second") + chk("ok", "No pictures are saved, only numbers about the face") + "</div></section>";
    var title = cal.state === "stopped" ? "The desk check was stopped." : "First, your desk.";
    var text = cal.state === "stopped" ? "Nothing was saved. Pick a desk again, or skip it." : "Pick a desk I already know for " + who + ", or teach me a new one.";
    var primary = desk.choice === "reuse" && reuse ? ["reuse", "Use my last desk check"] : ["calibrate", "Teach me this desk"];
    var foot = deskFoot('<div style="width: 220px;">' + EE.select({ id: "skip-reason", label: "Skipping? Tell me why", options: REASONS, value: desk.reason }) + "</div>"
        + EE.btn("secondary", "Skip for now", 'data-act="cal" data-value="skip"' + (desk.busy ? " disabled" : "")),
      desk.choice === "reuse" ? "Then we start studying." : "About 2 minutes, then we start studying.",
      '<button type="button" class="ds-btn ds-btn--primary glowbtn" data-act="cal" data-value="' + primary[0] + '"' + (desk.busy || !eyes ? " disabled" : "") + '><span class="ds-btn__label">' + primary[1] + "</span>" + icon("ArrowRight", 20) + "</button>");
    return deskHead(title, text, "choose") + '<div class="deskgrid">' + left + right + "</div>" + foot;
  }

  // What the participant sees right now, small (their own screen is full screen, or a small window).
  function participantMini(stage, cal) {
    var who = esc(EE.session().participant || "They");
    var frac = Math.max(0, Math.min(1, Number(cal.progress) || 0));
    if (stage === "face" || stage === "start") {
      return '<div class="mini mini--dark"><svg viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><rect width="1440" height="900" fill="#151515"></rect><ellipse cx="720" cy="430" rx="150" ry="190" fill="#2a2a2a"></ellipse><path d="M520 900 C540 720, 900 720, 920 900 Z" fill="#2a2a2a"></path><ellipse cx="665" cy="405" rx="20" ry="11" fill="#d9d9d9"></ellipse><ellipse cx="775" cy="405" rx="20" ry="11" fill="#d9d9d9"></ellipse><path d="M685 520 Q720 540 755 520" fill="none" stroke="#d9d9d9" stroke-width="5" stroke-linecap="round"></path><rect x="500" y="170" width="440" height="560" rx="24" fill="none" stroke="' + (cal.message ? "#ffb020" : "#2878ff") + '" stroke-width="5"' + (cal.message ? ' stroke-dasharray="18 12"' : "") + "></rect></svg>"
        + '<div class="mini__top"><b>' + esc(cal.instruction || "Sit as you normally work") + "</b><span>Keep your whole face inside the blue box</span></div></div>";
    }
    if (stage === "gaze") {
      return '<div class="mini mini--dark"><span class="mdot" style="left: 50%; top: 50%; background: #3ecf6e;"></span><span class="mdot" style="left: 5%; top: 8%; background: #3ecf6e;"></span><span class="mdot" style="left: 95%; top: 8%;' + (frac > 0.5 ? " background: #3ecf6e;" : " border: 2px solid #4a4a4a;") + '"></span><span class="mdot pulse" style="left: 5%; top: 92%; background: #3d82f0;"></span><span class="mdot" style="left: 95%; top: 92%; border: 2px solid #4a4a4a;"></span>'
        + '<div class="mini__mid"><b>' + esc(cal.instruction || "Look at the blue dot") + "</b><span>until it turns green</span></div></div>";
    }
    return '<div class="mini"><div class="mini__lines"><span style="width: 90%;"></span><span style="width: 75%;"></span><span style="width: 82%;"></span><span style="width: 60%;"></span><span class="caption" style="color: var(--ink-muted); margin-top: 6px; height: auto; background: none;">' + who + "'s own work stays on screen</span></div>"
      + '<div class="mini__win"><span class="mini__wint">EEPY calibration · small window</span><b>' + esc(cal.instruction || "Just work as usual") + '</b><span class="mini__bar"><span style="width: ' + (frac * 100).toFixed(0) + '%;"></span></span></div></div>';
  }

  function runningView(cal, sess) {
    var stage = cal.state === "requested" ? "start" : (cal.stage || "start");
    var titles = { start: "Getting ready…", face: "Your face first.", gaze: "Follow the dots.", baseline: "One minute of normal work." };
    var texts = {
      start: esc(cal.message || "I'm asking my eyes to start the desk check."),
      face: "Step 1 of 3 · I check the face and the light, about 5 seconds",
      gaze: "Step 2 of 3 · dots at the corners of the screen, then the notes and a look to the side",
      baseline: "Step 3 of 3 · 60 seconds, up to 90 · the face and dots are done"
    };
    var lines = { start: line("calibration_intro"), face: line("calibration_face_check"), gaze: line("calibration_look_dot"), baseline: line("calibration_baseline_start") };
    var checks = {
      start: ["My camera is on", "The full-screen window opens on this computer", "I talk " + esc(sess.participant) + " through it"],
      face: ["A face in view", "Inside the box", "Light: not too dark, not too bright"],
      gaze: ["The dots on the screen", "Their notes", "A look to the side, back at the beep"],
      baseline: ["Enough calm working time", "Some reading on the screen", "Normal eye openness and blinks"]
    }[stage] || [];
    var frac = Math.max(0, Math.min(1, Number(cal.progress) || 0));
    var where = stage === "baseline" ? "What " + esc(sess.participant) + " sees: their screen, with my small window in the corner" : "What " + esc(sess.participant) + " sees: my window, full screen";
    var left = '<section class="card" style="padding: var(--space-md);"><div style="display: flex; align-items: center; padding: 0 var(--space-xs);"><span class="caption" style="color: var(--ink-muted); flex: 1;">' + where + '</span><span class="caption" style="color: var(--ink-muted);">' + (cal.state === "running" ? Math.round(frac * 100) + "% of this step" : "starting…") + "</span></div>"
      + participantMini(stage, cal) + "</section>";
    var right = '<section class="card">' + saying(lines[stage]) + '<span class="caption eyebrow">I\'m checking</span><div style="display: flex; flex-direction: column; gap: var(--space-sm);">'
      + checks.map(function (c) { return chk("wait", c, "Search"); }).join("") + "</div>"
      + (cal.message ? '<div class="probnote"><span class="caption" style="color: var(--energy-mid); display: inline-flex; align-items: center; gap: 4px;">' + icon("CircleAlert", 12) + " Right now</span><span class=\"body\"><b>" + esc(cal.message) + "</b></span><span class=\"caption\" style=\"color: var(--ink-muted);\">I say it out loud too, and go on by myself as soon as it's fine.</span></div>" : "")
      + '<div class="dashnote"><span class="caption" style="color: var(--ink-muted);">' + (stage === "baseline" ? "Blinks, yawns and short looks away don't spoil it. If something does, I add up to 30 s, and if it still fails I restart once by myself." : "Q in my window, or Stop calibration here, stops it. A stopped check saves nothing.") + "</span></div>"
      + (desk.msg ? '<p class="body-sm chk" role="alert" style="margin: 0;"><span class="bad">' + icon("CircleAlert", 20) + "</span>" + esc(desk.msg) + "</p>" : "") + "</section>";
    var foot = deskFoot(EE.btn("secondary", "Stop calibration", 'data-act="cal" data-value="stop"' + (desk.busy ? " disabled" : ""), "X", "start"), "I move on by myself when each step is done.", "");
    return deskHead(titles[stage] || "Checking…", texts[stage] || "", stage === "start" ? "face" : stage) + '<div class="deskgrid">' + left + right + "</div>" + foot;
  }

  function doneView(cal, sess) {
    var who = esc(sess.participant), reused = cal.state === "reused";
    var left = '<section class="card" style="align-items: center; justify-content: center; text-align: center;"><img src="img/eepy-scientist.svg" alt="" style="width: 150px; height: 150px; object-fit: contain;">'
      + '<span class="body-lg okpill">' + icon("Check", 20) + "<b>" + (reused ? "Using my last desk check" : "Desk checked") + "</b></span>"
      + '<span class="body" style="color: var(--ink-muted); max-width: 420px;">From now on I compare ' + who + " with their own normal face at this desk.</span></section>";
    var right = '<section class="card">' + (reused ? "" : saying(line("calibration_done")))
      + '<div style="display: flex; flex-direction: column; gap: var(--space-sm);">' + chk("ok", "Face and light") + chk("ok", "Screen and notes") + chk("ok", "Normal eyes, blinks and posture") + "</div>"
      + '<span class="caption" style="margin-top: auto; color: var(--ink-muted);">I keep it for ' + who + " for 14 days, at this desk and camera, so next time it takes a second.</span></section>";
    return deskHead(reused ? "Welcome back! I know this desk." : "Got it! I know this desk now.", reused ? "I'm using the desk check from " + (cal.age_days != null ? Math.round(cal.age_days) + " days ago." : "before.") : "All three steps went well.", "done")
      + '<div class="deskgrid">' + left + right + "</div>"
      + deskFoot(EE.btn("secondary", "Redo it", 'data-act="cal" data-value="calibrate"' + (desk.busy || !cal.vision_connected ? " disabled" : "")), "Then we start studying.",
        '<a class="ds-btn ds-btn--primary glowbtn" href="#/control"><span class="ds-btn__label">Start studying</span>' + icon("ArrowRight", 20) + "</a>");
  }

  function skippedView(cal, sess) {
    var who = esc(sess.participant);
    var left = '<section class="card" style="flex-direction: row; align-items: center; gap: var(--space-xl);"><img src="img/eepy-cookie.svg" alt="" style="width: 150px; height: 150px; object-fit: contain; flex: none;">'
      + '<div style="display: flex; flex-direction: column; gap: var(--space-sm);"><span class="caption" style="color: var(--ink-muted);">You told me why</span><span class="headline">"' + esc(cal.reason || "No reason given") + '"</span>'
      + '<span class="body" style="color: var(--ink-muted);">That\'s fine. I\'ll use my default values instead of ' + who + '\'s own face, and I\'ll mark this session as <b style="color: var(--ink);">not calibrated</b> in the data.</span></div></section>';
    var right = '<section class="card"><span class="headline">What changes without it</span><div style="display: flex; flex-direction: column; gap: var(--space-sm);">'
      + chk("wait", "I may be less sure, so I'll step in a bit less often") + chk("wait", "My guesses about tiredness are rougher") + chk("ok", "Everything else works as usual, and it's all recorded")
      + '</div><span class="caption" style="margin-top: auto; color: var(--ink-muted);">You can calibrate in a later session; this one stays marked.</span></section>';
    return deskHead("Okay, we’ll skip the desk check.", "Here’s what that means for today.", "")
      + '<div class="deskgrid">' + left + right + "</div>"
      + deskFoot(EE.btn("secondary", "Back to desks", 'data-act="desk-back"'), "Then we start studying.",
        '<a class="ds-btn ds-btn--primary glowbtn" href="#/control"><span class="ds-btn__label">Start without it</span>' + icon("ArrowRight", 20) + "</a>");
  }

  function failedView(cal, sess) {
    var stage = cal.stage && cal.stage !== "done" ? cal.stage : "baseline";
    var titles = { face: "The face check didn't work.", gaze: "The gaze dots didn't work.", baseline: "The minute of normal work didn't work.", start: "The desk check didn't start." };
    var tips = {
      face: ["Sit facing the screen, face fully in view", "Turn on a light or face a window", "Then try again"],
      gaze: ["Keep the head still and follow the dots with the eyes", "Keep the notes where they usually are", "Then try again"],
      baseline: ["Close other programs that use the computer a lot", "Keep the laptop still and the camera uncovered", "Then try again"]
    }[stage] || ["Check that my eyes are running (Advanced tools)", "Keep the camera uncovered", "Then try again"];
    var left = '<section class="card" style="box-shadow: var(--shadow-error);"><div style="display: flex; align-items: center; gap: var(--space-md);"><img src="img/eepy-builder.svg" alt="" style="width: 96px; height: 96px; object-fit: contain;">'
      + '<div style="display: flex; flex-direction: column; gap: var(--space-xxs);"><span class="caption" style="color: var(--error); display: inline-flex; align-items: center; gap: 4px;">' + icon("CircleAlert", 12) + " It didn't work, so I stopped</span>"
      + '<span class="headline">"' + esc(cal.message || "Something went wrong.") + '"</span><span class="body-sm" style="color: var(--ink-muted);">What my calibration said, word for word.</span></div></div>'
      + '<span class="caption eyebrow">How to fix it</span><div style="display: flex; flex-direction: column; gap: var(--space-xs);">'
      + tips.map(function (t, i) { return '<span class="body-sm chk fixrow"><b>' + (i + 1) + "</b>" + esc(t) + "</span>"; }).join("") + "</div></section>";
    var right = '<section class="card"><span class="headline">Other reasons I can give</span><div style="display: flex; flex-direction: column; gap: var(--space-xs);">'
      + ["There was not enough calm, ordinary working time", "Your face was out of view too much", "The camera stopped sending pictures for N seconds", "Your eyes could not be measured reliably",
         "Your blinks could not be told apart from open eyes", "Your mouth seemed open most of the time", "Your head was turned far from the camera"].map(function (r) { return '<span class="body-sm">"' + r + '"</span>'; }).join("")
      + '</div><span class="caption" style="margin-top: auto; color: var(--ink-muted);">A failed check saves nothing, so trying again redoes all three steps. Every check and its value goes in the session log.</span></section>';
    return deskHead(titles[stage] || titles.start, "Nothing is lost: here’s what went wrong and what to do.", stage, true)
      + '<div class="deskgrid">' + left + right + "</div>"
      + deskFoot(EE.btn("secondary", "Skip with a reason", 'data-act="desk-back"'), "",
        '<button type="button" class="ds-btn ds-btn--primary glowbtn" data-act="cal" data-value="calibrate"' + (desk.busy || !cal.vision_connected ? " disabled" : "") + '><span class="ds-btn__label">Try again</span>' + icon("ArrowRight", 20) + "</button>");
  }

  function deskCommand(action) {
    var body = { action: action };
    if (action === "skip") {
      if (!desk.reason) { desk.msg = "Pick a reason first, so it's saved with the session."; desk.seen = ""; EE.screens.desk.panelUpdate(); var sel = $("#skip-reason"); if (sel) sel.focus(); return; }
      body.reason = desk.reason;
    }
    desk.busy = true; desk.msg = ""; desk.back = false; desk.seen = "";
    EE.screens.desk.panelUpdate();
    EE.panelPost("calibration", body).then(function (res) {
      desk.busy = false; desk.seen = "";
      if (!res.ok) desk.msg = res.message || "That didn't work.";
      return EE.pollPanel();
    }).then(function () { if (EE.view === "desk") EE.screens.desk.panelUpdate(); });
  }

  EE.screens.desk = {
    render: function () {
      desk.seen = ""; desk.msg = ""; desk.back = false;
      EE.main.innerHTML = '<div class="sessbar-wrap">' + sessbar("desk") + '</div><div id="desk" class="desk"></div>';
      this.panelUpdate();
    },
    panelUpdate: function () {
      var box = $("#desk");
      if (!box) return;
      updateSessbar("desk");
      var st = EE.ps();
      if (!st) { EE.setHTML(box, panelMissing()); desk.seen = ""; return; }
      var sess = st.session || {};
      if (!sess.active) { EE.setHTML(box, noSession()); desk.seen = ""; return; }
      lastName = sess.session || lastName;
      var cal = st.calibration || { state: "none" };
      // What the screen shows depends on these only (never on desk.seen itself, or the key would grow without end).
      var key = JSON.stringify([cal, desk.busy, desk.msg, desk.reason, desk.choice, desk.back, (EE.model || {}).lines ? 1 : 0]);
      if (key === desk.seen) return;                 // nothing new: keep the reason picked and the focus
      desk.seen = key;
      var s = cal.state, html;
      if (desk.back && (s === "skipped" || s === "failed" || s === "stopped" || s === "none" || s === "pending")) html = chooseView(cal, sess);
      else if (s === "requested" || s === "running") html = runningView(cal, sess);
      else if (s === "done" || s === "reused") html = doneView(cal, sess);
      else if (s === "skipped") html = skippedView(cal, sess);
      else if (s === "failed") html = failedView(cal, sess);
      else html = chooseView(cal, sess);
      box.innerHTML = html;
      box.__html = null;
    },
    act: function (act, el) {
      if (commonAct(act, el)) return;
      if (act === "cal") deskCommand(el.getAttribute("data-value"));
      else if (act === "desk-choice") { desk.choice = el.getAttribute("data-value"); desk.seen = ""; this.panelUpdate(); }
      else if (act === "desk-back") { desk.back = true; desk.seen = ""; this.panelUpdate(); }
    },
    change: function (e) { if (e.target.id === "skip-reason") { desk.reason = e.target.value; desk.msg = ""; } },
    key: function (e) {
      if ((e.key === "Escape") && !isTyping(e)) { e.preventDefault(); stopEepy("key"); }
    }
  };

  function isTyping(e) {
    var t = e.target, tag = (t && t.tagName) || "";
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable);
  }

  // --- the control panel (V5-S2-Control) -----------------------------------------------------------

  var ctrl = { tone: "hedged", intensity: null, tab: "controls", version: null, msg: "", msgBad: false, prevProbs: null };
  var buttons = {}, keys = {};
  var volumeInfo = null;
  var whole = null, wholeAt = 0, live = [], liveB = [];   // the recorded session so far, and the live points since

  function loadWhole() {
    var sess = EE.session();
    if (!sess.active || !sess.session) return;
    wholeAt = Date.now();
    EE.getJSON("/api/v5/session?name=" + encodeURIComponent(sess.session)).then(function (d) {
      if (d && d.name === EE.session().session) { whole = d; live = live.filter(function (p) { return p[0] > (d.duration_s || 0); }); liveB = liveB.filter(function (p) { return p[0] > (d.duration_s || 0); }); drawCharts(); }
    }).catch(function () { /* the next poll tries again */ });
  }
  // P(focused) over the session: the recorded curve, then this second's numbers.
  function history() {
    var pts = (whole && whole.curve ? whole.curve.slice() : []);
    return pts.concat(live);
  }
  // The HMM's belief over the session, like the old Live HMM timeline: [t, p x4 (BELIEF order), confidence, state shown, updated].
  var BELIEF = ["focused", "distracted", "drowsy", "very_drowsy"];
  function addLiveBelief(st) {
    var us = st.user_state, sess = st.session || {};
    if (!us || !us.probs || us.fresh === false || BELIEF.some(function (b) { return us.probs[b] == null; })) return;
    var t = sess.elapsed_s || 0;
    if (liveB.length && t - liveB[liveB.length - 1][0] < 1) return;
    liveB.push([t].concat(BELIEF.map(function (b) { return us.probs[b]; }), [us.confidence, us.state, us.updated === false ? 0 : 1]));
    if (liveB.length > 4000) liveB.shift();
  }
  function addLive(st) {
    addLiveBelief(st);
    var us = st.user_state, sess = st.session || {};
    if (!us || !us.probs || us.probs.focused == null || us.fresh === false) return;
    var t = sess.elapsed_s || 0;
    if (live.length && t - live[live.length - 1][0] < 4) return;
    live.push([t, us.probs.focused]);
    if (live.length > 2000) live.shift();
  }
  function curvePoints(pts, from, to, startClock) {
    var span = Math.max(1, to - from);
    return pts.filter(function (p) { return p[0] >= from - 1; }).map(function (p) {
      return [(p[0] - from) / span, p[1], EE.addClock(startClock, p[0])];
    });
  }

  function controlsView(st, panel) {
    var groups = {};
    (panel.groups || []).forEach(function (g) { groups[g.id] = g; });
    var who = esc((st.session || {}).participant || "them");
    var tile = function (b, i, of) {
      var lit = Math.min(5, i + 1);
      return '<button type="button" class="tile tile--ladder" data-act="press" data-id="' + esc(b.id) + '" data-target="' + esc(b.target) + '">'
        + '<span class="tile__top">' + EE.dots(lit, 5) + (b.key ? EE.kbd(b.key) : "") + "</span>"
        + '<span class="body-sm tile__name">' + esc(EE.behaviorName(b.target)) + "</span></button>";
    };
    var ladder = function (g, cols) { return '<div class="ladder" style="grid-template-columns: repeat(' + cols + ', minmax(0, 1fr));">' + g.buttons.map(tile).join("") + "</div>"; };
    var chip = function (b, name) {
      return '<button type="button" class="tile chip" data-act="press" data-id="' + esc(b.id) + '" data-target="' + esc(b.target) + '"><span class="body-sm">' + esc(name) + "</span>" + (b.key ? EE.kbd(b.key) : "") + "</button>";
    };
    var lineRow = function (b) {
      var words = line(b.target, ctrl.tone) || EE.cap(b.label);
      return '<button type="button" class="tile row" data-act="press" data-id="' + esc(b.id) + '" data-target="' + esc(b.target) + '">' + icon("Play", 16)
        + '<span class="body-sm" style="flex: 1; text-align: left;" data-line="' + esc(b.target) + '">' + esc(words) + "</span>" + (b.key ? EE.kbd(b.key) : "") + "</button>";
    };
    var markers = groups.markers ? groups.markers.buttons : [];
    var answers = ["user_accepted", "user_snoozed", "user_dismissed"];
    var did = markers.filter(function (b) { return answers.indexOf(b.target) < 0; });
    var said = answers.map(function (a) { return markers.filter(function (b) { return b.target === a; })[0]; }).filter(Boolean);
    var nod = groups.basics ? groups.basics.buttons.filter(function (b) { return b.kind === "behavior"; }) : [];
    if (ctrl.intensity == null) ctrl.intensity = panel.default_intensity || 0.7;
    return '<div class="cgrid2" id="now-grid"><div id="now-viz"></div><section class="card ccard" id="now-doing"></section></div>'
      + '<div class="sechead" style="margin-top: var(--space-xs);"><span class="sechead__t">Your controls</span><span class="caption" style="color: var(--ink-muted);">Everything with a border is a button. Click it, or press the key in its corner.</span></div>'
      + '<div class="cgrid3">'
      + '<section class="card ccard"><div class="cardhead"><img src="img/eepy-move.svg" alt=""><span style="flex: 1; display: flex; flex-direction: column;"><span class="headline">Move me</span><span class="caption how">Dots show how strong the move is</span></span></div>'
      + (groups.refocus ? '<div class="lbl"><span class="body-sm"><b>Refocus</b></span><span class="caption how">when ' + who + " drifts off</span></div>" + ladder(groups.refocus, groups.refocus.buttons.length) : "")
      + (groups.bedtime ? '<div class="lbl" style="margin-top: var(--space-xxs);"><span class="body-sm"><b>Tiredness</b></span><span class="caption how">when ' + who + " gets sleepy</span></div>" + ladder(groups.bedtime, groups.bedtime.buttons.length) : "")
      + '<div class="movefoot">' + EE.switchEl("idle", "Breathe while idle · " + esc(String(panel.idle_key || "a").toUpperCase()), st.idle_on, 'data-act="idle"')
      + nod.map(function (b) { return chip(b, EE.behaviorName(b.target)); }).join("")
      + '<div class="strength"><span class="caption how" style="white-space: nowrap;">Strength</span><input id="intensity" class="slider" type="range" min="0.2" max="1" step="0.05" value="' + ctrl.intensity + '" aria-label="Strength of the next move"><span class="caption" id="intensity-v" style="font-variant-numeric: tabular-nums;">' + Math.round(ctrl.intensity * 100) + "%</span></div></div></section>"
      + '<section class="card ccard"><div class="cardhead"><img src="img/eepy-talk.svg" alt=""><span style="flex: 1; display: flex; flex-direction: column;"><span class="headline">Talk</span><span class="caption how">Click a line and I say it</span></span></div>'
      + '<div style="display: flex; align-items: center; gap: var(--space-sm);" id="tone-tabs">' + EE.tabs("Tone", [{ value: "hedged", label: "Hedged" }, { value: "direct", label: "Direct" }], ctrl.tone, "tone") + EE.kbd(panel.tone_key || "tab") + "</div>"
      + '<div class="volrow" id="volume"></div>'
      + '<div style="display: flex; flex-direction: column; gap: var(--space-xxs);">' + (groups.lines ? groups.lines.buttons.map(lineRow).join("") : "") + "</div>"
      + '<form class="sayrow" id="say-form" autocomplete="off"><div style="flex: 1;">' + EE.textInput({ id: "say-text", label: "Say something else", placeholder: "Type, then Enter", attrs: 'maxlength="300"' }) + "</div>"
      + '<button type="submit" class="ds-btn ds-btn--secondary" style="background: var(--surface-2);"><span class="ds-btn__label">Say</span></button></form></section>'
      + '<section class="card ccard"><div class="cardhead"><img src="img/eepy-scientist.svg" alt=""><span style="flex: 1; display: flex; flex-direction: column;"><span class="headline">What did you see?</span><span class="caption how">Click the moment you see it</span></span></div>'
      + '<span class="caption how">' + who + " did something</span>"
      + '<div class="chips">' + did.map(function (b) { return chip(b, EE.MARKER[b.target] || EE.cap(b.label)); }).join("") + "</div>"
      + (said.length ? '<span class="caption how" style="margin-top: var(--space-xs);">Their answer to a break offer or "15 more minutes?"</span><div class="chips">' + said.map(function (b) { return chip(b, EE.MARKER[b.target] || EE.cap(b.label)); }).join("") + "</div>" : "")
      + '<span class="caption how" style="margin-top: auto;">Your marks are how I learn what works.</span></section>'
      + "</div>";
  }

  function liveView() {
    return '<div class="lgrid"><section class="card ccard" id="cam"></section><section class="card ccard" aria-label="The HMM graph" id="graph"></section><section class="card ccard" aria-label="How I decide" id="decide"></section></div>'
      + '<section class="card ccard" id="since"></section>';
  }

  function skeleton(st, panel) {
    buttons = {}; keys = {};
    (panel.groups || []).forEach(function (g) {
      g.buttons.forEach(function (b) { buttons[b.id] = b; if (b.key) keys[String(b.key).toLowerCase()] = b; });
    });
    var classic = (EE.parts().panel || {}).url;
    var isLive = ctrl.tab === "hmm";
    return '<div id="banners" class="banners"></div>'
      + '<div class="sechead"><span class="sechead__t">' + (isLive ? "Live HMM" : "Right now") + '</span><span class="caption" style="color: var(--ink-muted);">'
      + (isLive ? "What my camera sees and what I believe, second by second." : "How " + esc((st.session || {}).participant) + " is doing, and what I'm doing about it.") + '</span><div style="flex: 1;"></div>'
      + EE.tabs("View", [{ value: "controls", label: "Controls" }, { value: "hmm", label: "Live HMM" }], ctrl.tab, "tab") + "</div>"
      + (isLive ? liveView() : controlsView(st, panel))
      + '<div class="cfoot body-sm">' + EE.btn("secondary", "Session log", 'data-act="log-open"', "Menu", "start") + EE.btn("secondary", "Add a note", 'data-act="note-open"', "Plus", "start")
      + '<span id="lastthing" style="color: var(--ink-muted);"></span><span id="ctrl-msg" aria-live="polite"></span><div style="flex: 1;"></div>'
      + (classic ? '<a href="' + esc(classic) + '" target="_blank" rel="noopener">Open the classic panel</a>' : "") + "</div>";
  }

  function nowViz(st) {
    var us = st.user_state, who = esc((st.session || {}).participant || "They"), cal = st.calibration || {};
    if (!us) {
      return EE.vizPanel(who + " is", "out of sight", cal.vision_connected ? "I'm looking… my first guess comes in a few seconds." : "My eyes aren't connected, so I can't see how they're doing.",
        '<div class="vizempty caption">' + (st.mode === "wizard" ? "In this session you call the shots: use your controls below." : "Start “My eyes” in Advanced tools, then I'll show it here.") + "</div>");
    }
    var probs = us.probs || {};
    var sure = us.confidence != null ? "I'm " + EE.pct(us.confidence) + " sure" : "My simple rules say so";
    var reason = us.reason ? " · " + esc(us.reason) : "";
    var dur = (st.session || {}).elapsed_s || 0, hist = history();
    var from = Math.max(hist.length ? hist[0][0] : 0, dur - 12 * 60);     // the last 12 minutes, or since I could see
    var pts = curvePoints(hist, from, dur, whole && whole.clock);
    var curve = pts.length > 1 ? EE.focusCurve(pts, { height: 150, label: "Focus" }) : '<div class="vizempty caption">The curve starts after a few seconds of looking.</div>';
    var shown = ["focused", "distracted", "drowsy", "very_drowsy"].filter(function (k) { return probs[k] != null; });
    var legend = '<div class="caption vizlegend">' + shown.map(function (k) {
      return '<span' + (k === us.state ? ' style="color: var(--ink);"' : "") + ">" + EE.STATE_NAME[k] + " " + EE.pct(probs[k]) + "</span>";
    }).join("") + '<a href="#" data-act="tab" data-value="hmm" style="margin-left: auto;">See it in Live HMM</a></div>';
    return EE.vizPanel(who + " looks", esc(EE.STATE_LOOK[us.state] || us.state), sure + reason + (us.fresh === false ? " · not updating" : ""), curve + legend);
  }

  function nowDoing(st) {
    var au = st.autonomy || {}, who = esc((st.session || {}).participant || "them");
    if (st.mode === "wizard" || !au.active && !au.ladder) {
      return '<div class="cardhead"><span class="headline" style="flex: 1;">What I\'m doing</span></div>'
        + '<div class="bubble"><img src="img/eepy-mark.svg" alt="" width="28" height="28"><span class="body-sm">' + (st.mode === "wizard" ? "You call the shots in this session: I only do what you press. I still record everything, and your marks." : esc(EE.cap(au.next || "I'm not deciding anything yet."))) + "</span></div>";
    }
    var head = '<div class="cardhead"><span class="headline" style="flex: 1;">What I\'m doing</span>'
      + EE.btn("secondary", au.paused ? "Let me decide again" : "Pause my decisions", 'data-act="autonomy" data-value="' + (au.paused ? "resume" : "pause") + '" style="background: var(--surface-2);"', au.paused ? "Play" : "Pause", "start") + "</div>";
    var row = "";
    if (au.ladder) {
      var n = au.ladder === "bedtime" ? 6 : 5, level = au.level == null ? -1 : au.level;
      var pills = "";
      for (var i = 0; i < n; i++) {
        var cls = "lpill", txt = String(i);
        if (i === level) { cls += " is-now"; txt = i + " now"; }
        else if (i === level + 1 && !au.paused) { cls += " is-next"; txt = i + (au.next_in != null ? " · " + Math.max(0, Math.round(au.next_in)) + " s" : ""); }
        else if (i < level) cls += " is-past";
        pills += '<span class="caption ' + cls + '">' + txt + "</span>";
      }
      row = '<div class="lrow" style="grid-template-columns: 76px repeat(' + n + ', minmax(0, 1fr));"><span class="caption" style="color: var(--ink-muted);">' + (au.ladder === "bedtime" ? "Tiredness" : "Refocus") + "</span>" + pills + "</div>";
    }
    var ex = au.explanation || {};
    var text = au.paused ? "I'm paused: I won't step in by myself until you let me. Your controls still work."
      : ex.sentence ? esc(ex.sentence) + (au.next ? " Next: " + esc(au.next) + "." : "")
      : au.next ? "Right now: " + esc(au.next) + "." : "I'm watching quietly.";
    var bubble = '<div class="bubble"><img src="img/eepy-mark.svg" alt="" width="28" height="28"><span class="body-sm">' + text + "</span></div>";
    var bandit = "";
    var b = au.bandit;
    if (b && b.arms && b.arms.length) {
      var arms = b.arms.filter(function (a) { return a.arm !== "wait"; }).sort(function (x, y) { return y.mean - x.mean; }).slice(0, 4);
      bandit = '<div style="display: flex; flex-direction: column; gap: var(--space-xs);"><span class="caption" style="color: var(--ink-muted);">Why this one · what I\'ve learned about ' + who + "</span>"
        + arms.map(function (a) {
          var on = a.arm === b.chosen, w = Math.max(3, Math.min(100, (a.mean + 1) / 2 * 100));
          var name = EE.armName(a.arm, a.words);
          return '<div class="caption armrow">' + (on ? "<b>" + esc(name) + "</b>" : "<span>" + esc(name) + "</span>")
            + '<span class="armbar"><span style="width: ' + w.toFixed(0) + "%; background: " + (on ? "linear-gradient(90deg, var(--time-focus-start), var(--time-focus))" : "var(--time-busy)") + ';"></span></span>'
            + (on ? "<b>" : '<span style="color: var(--ink-muted);">') + (a.mean >= 0 ? "+" : "") + Number(a.mean).toFixed(2) + (on ? "</b>" : "</span>") + "</div>";
        }).join("") + "</div>";
    }
    return head + row + bubble + bandit;
  }

  function banners(st) {
    var out = [];
    var b = function (html, button) {
      return '<section role="alert" class="banner"><span class="bad">' + icon("CircleAlert", 20) + '</span><span class="body" style="flex: 1;">' + html + "</span>" + (button || "") + "</section>";
    };
    if (!st.connected && st.backend !== "dry") out.push(b("<b>I lost my body.</b> Keep marking, I'm still recording. Check the cable or Wi-Fi, then reconnect me." + (st.connection_error ? " (" + esc(st.connection_error) + ")" : ""), EE.btn("primary", "Reconnect me", 'data-act="reconnect"')));
    var cal = st.calibration || {};
    if (st.mode !== "wizard" && !cal.vision_connected) out.push(b("<b>I can't see right now.</b> My camera program isn't connected. Carry on by hand; I'll start deciding again when it's back.", EE.linkBtn("primary", "Restart my eyes", "#/advanced")));
    else if (st.user_state && st.user_state.fresh === false) out.push(b("<b>My eyes stopped sending updates.</b> What you see under Right now may be old."));
    if (st.session && st.session.write_error) out.push(b("<b>I can't save the session:</b> " + esc(st.session.write_error)));
    if (st.last_error) out.push(b(esc(st.last_error)));
    return out.join("");
  }

  // --- the Live HMM view ---

  // Colours as on the old Live HMM page (--hmm-* in app.css): green, amber, orange, red; tired is a paler green.
  var NODES = { focused: [60, 62, "var(--hmm-focused)", 0.9], distracted: [240, 62, "var(--hmm-distracted)", 0.9], tired: [55, 196, "var(--hmm-focused)", 0.5],
                drowsy: [150, 196, "var(--hmm-drowsy)", 0.9], very_drowsy: [245, 196, "var(--hmm-very)", 0.9] };
  function hmmGraph(us) {
    var probs = (us && us.probs) || {}, prev = ctrl.prevProbs || {};
    var tr = (EE.model && EE.model.transitions) || {};
    var states = Object.keys(NODES);
    var arrows = "";
    states.forEach(function (a) {
      Object.keys(tr[a] || {}).forEach(function (b2) {
        if (!NODES[b2] || a === b2) return;
        var rate = tr[a][b2];
        if (rate < 0.25) return;                               // the near-zero links would only add clutter
        var A = NODES[a], B = NODES[b2], dx = B[0] - A[0], dy = B[1] - A[1], len = Math.sqrt(dx * dx + dy * dy), ux = dx / len, uy = dy / len;
        var ox = -uy * 9, oy = ux * 9;                         // A->B and B->A side by side
        var x1 = A[0] + ux * 31 + ox, y1 = A[1] + uy * 31 + oy, x2 = B[0] - ux * 34 + ox, y2 = B[1] - uy * 34 + oy;
        var w = 0.6 + Math.log(rate + 1) / Math.log(10) * 0.75;
        arrows += '<line x1="' + x1.toFixed(1) + '" y1="' + y1.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1) + '" style="stroke: var(--ink-muted);" stroke-opacity=".55" stroke-width="' + w.toFixed(2) + '" marker-end="url(#hmah)"><title>'
          + EE.STATE_NAME[a] + " → " + EE.STATE_NAME[b2] + ": about " + (rate >= 1 ? Math.round(rate) : rate) + " times an hour</title></line>";
      });
    });
    var nodes = states.map(function (s, i) {
      var n = NODES[s], p = probs[s], has = p != null;
      var top = has ? n[1] - 29 + 58 * (1 - p) : n[1] + 29;
      var shown = us && us.state === s, star = us && us.most_likely === s;
      var delta = has && prev[s] != null ? Math.round((p - prev[s]) * 100) : 0;
      var below = n[1] > 100;
      return '<clipPath id="hmcp' + i + '"><circle cx="' + n[0] + '" cy="' + n[1] + '" r="29"></circle></clipPath><circle cx="' + n[0] + '" cy="' + n[1] + '" r="29" style="fill: var(--surface-2);"></circle>'
        + '<rect class="fill" x="' + (n[0] - 29) + '" y="' + top.toFixed(1) + '" width="58" height="58" style="fill: ' + n[2] + ';" fill-opacity="' + n[3] + '" clip-path="url(#hmcp' + i + ')"></rect>'
        + '<circle cx="' + n[0] + '" cy="' + n[1] + '" r="29" fill="none" style="stroke: ' + (shown ? "var(--ink)" : "var(--hairline-strong)") + ';" stroke-width="' + (shown ? 4 : 1.5) + '"></circle>'
        + '<text x="' + n[0] + '" y="' + (n[1] + 6) + '" text-anchor="middle" font-size="16" font-weight="700" style="fill: var(--ink);">' + (has ? Math.round(p * 100) + "%" : "–") + "</text>"
        + '<text x="' + n[0] + '" y="' + (below ? n[1] + 46 : n[1] - 37) + '" text-anchor="middle" font-size="12.5" style="fill: var(--ink);">' + EE.STATE_NAME[s] + (star ? " ★" : "") + "</text>"
        + (s === "tired" ? '<text x="' + n[0] + '" y="' + (n[1] + 59) + '" text-anchor="middle" font-size="10.5" style="fill: var(--ink-muted);">shown as focused</text>' : "")
        + (delta ? '<text x="' + (n[0] + 32) + '" y="' + (n[1] + 4) + '" font-size="11" style="fill: ' + (delta > 0 ? "var(--success)" : "var(--error)") + ';">' + (delta > 0 ? "▲" : "▼") + Math.abs(delta) + "</text>" : "");
    }).join("");
    return '<svg viewBox="0 0 300 286" width="100%" role="img" aria-label="What the HMM believes right now" style="display: block; max-height: 330px;"><defs><marker id="hmah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" style="fill: var(--ink-muted);"></path></marker></defs>'
      + arrows + nodes + "</svg>";
  }

  // The live camera picture, like the old launcher's Live HMM page: the vision program keeps the latest
  // one in memory and the launcher passes it through (/api/v5/frame.jpg). One <img> is kept across
  // redraws, so it does not flicker, and it asks for the next picture once the last one has loaded.
  // No picture (vision not running, no camera yet): the face drawn from the numbers stays.
  var cam = { img: null, ok: false, busy: false };
  function camLoad() {
    cam.busy = true;
    cam.img.src = "/api/v5/frame.jpg?t=" + Date.now();
  }
  function camNext(ok, wait) {
    cam.ok = ok;
    var slot = cam.img.parentNode;
    if (slot) slot.classList.toggle("has-pic", ok);
    setTimeout(function () { cam.busy = false; if (document.body.contains(cam.img)) camLoad(); }, wait);
  }
  function camPicture() {
    var slot = $("#cam .campic");
    if (!slot) return;
    if (!cam.img) {
      cam.img = document.createElement("img");
      cam.img.className = "campic__img";
      cam.img.alt = "What my camera sees right now";
      cam.img.onload = function () { camNext(true, 200); };
      cam.img.onerror = function () { camNext(false, 2000); };
    }
    if (cam.img.parentNode !== slot) slot.insertBefore(cam.img, slot.firstChild);
    if (!cam.busy) camLoad();
  }

  function drawLive(st) {
    var us = st.user_state, cal = st.calibration || {}, au = st.autonomy || {};
    var vals = (us && us.values) || {};
    var vision = EE.parts().vision || {};
    var face = vals.face_present_ratio_10s, eyes = vals.perclos_60s, away = vals.gaze_off_ratio_60s;
    var fact = function (k, v) { return '<span class="caption fact2"><span class="how">' + k + "</span><br><b>" + v + "</b></span>"; };
    EE.setHTML($("#cam"), '<div class="cardhead"><span class="headline" style="flex: 1;">What my camera sees</span>' + (us && us.fresh !== false ? EE.badge("success", "live") : EE.badge("neutral", "not live")) + "</div>"
      + '<div class="campic' + (cam.ok ? " has-pic" : "") + '"><svg viewBox="0 0 340 250" width="100%" height="100%" aria-label="Face box, drawn from the numbers (no picture)"><rect x="110" y="34" width="120" height="150" rx="10" fill="none" style="stroke: ' + (face == null ? "var(--hairline-strong)" : face > 0.5 ? "var(--success)" : "var(--error)") + ';" stroke-width="2.5"></rect><ellipse cx="170" cy="110" rx="44" ry="56" style="fill: var(--surface-1);"></ellipse><path d="M120 250 C126 196, 214 196, 220 250 Z" style="fill: var(--surface-1);"></path><path d="M150 104 h14 M176 104 h14" style="stroke: var(--ink);" stroke-width="3" stroke-linecap="round"></path><path d="M160 140 Q170 146 180 140" fill="none" style="stroke: var(--ink);" stroke-width="2" stroke-linecap="round"></path></svg>'
      + (away != null ? '<span class="caption campic__tag">looking away ' + EE.pct(away) + " of the minute</span>" : "") + "</div>"
      + '<div class="facts2">' + fact("Face", face == null ? "–" : face > 0.5 ? "found" : "not found") + fact("Eyes closed (last minute)", eyes == null ? "–" : EE.pct(eyes))
      + fact("Yawns (last minute)", vals.yawns_60s == null ? "–" : vals.yawns_60s) + fact("Calibrated", { done: "this desk", reused: "last desk check", skipped: "no" }[cal.state] || "no") + "</div>"
      + '<span class="caption how" style="margin-top: auto;">' + (cam.ok ? "Live from my camera, with the face box I judge. I never save a picture." : "Drawn from my numbers: no camera picture yet, and I never save one.") + (vision.url && EE.isUp(vision.state) ? ' <a href="' + esc(vision.url) + '" target="_blank" rel="noopener">My live page</a>' : "") + "</span>");
    EE.setHTML($("#graph"), '<div class="cardhead"><span style="flex: 1; display: flex; flex-direction: column;"><span class="headline" style="display: inline-flex; align-items: center; gap: var(--space-xs);"><span class="stepn">2</span>What I believe right now</span><span class="caption how">The HMM\'s 5 states. Each circle fills up with how likely it is.</span></span><span class="caption how">' + EE.hhmm(new Date()) + "</span></div>"
      + '<div class="graphbox">' + (us && us.probs ? hmmGraph(us) : '<span class="caption how">' + (us ? "These are my simple rules: they give no probabilities. The HMM runs in the “I estimate” modes." : "Nothing yet: my eyes aren't sending.") + "</span>") + "</div>"
      + '<div class="caption how graphlegend"><span>★ most likely</span><span>thick ring: the state I show</span><span>▲▼ change this second</span><span>arrows: how often people switch, per hour</span></div>'
      + (us && us.probs ? '<div class="caption how">Uncertainty <b style="color: var(--ink);">' + (us.uncertainty == null ? "–" : Number(us.uncertainty).toFixed(2)) + "</b> (0 sure, 1 no idea) · "
        + (us.updated === false ? "on hold: the belief only drifts (no usable signs)" : "updated with this second's signs") + "</div>" : ""));
    var ev = ((us && us.evidence) || []).slice(0, 4);
    var maxS = ev.reduce(function (m, e) { return Math.max(m, Math.abs(e.strength || 0)); }, 0.0001);
    // 3 and 4 as on the old Live HMM page (v1): the shown state with how sure, and the confidence gate.
    var FAMILY = { focused: "focus", tired: "focus", distracted: "distraction", drowsy: "drowsiness", very_drowsy: "drowsiness" };
    var ladder = us && us.state === "distracted" ? "refocus" : us && (us.state === "drowsy" || us.state === "very_drowsy") ? "bedtime" : null;
    var m = EE.model || {};
    var need = ladder && m.act_confidence ? m.act_confidence[ladder] : null, direct = m.direct_confidence;
    var conf = us ? us.confidence : null, sw = m.switch_probability;
    var enough = need != null && conf != null && conf >= need;
    var shown = '<div class="step2"><span class="caption how"><span class="stepn">3</span> I show</span>'
      + '<span><span class="statechip" style="background: ' + (BELIEF_COL[us && us.state] || "var(--hmm-unknown)") + ';">' + (us ? esc(EE.STATE_NAME[us.state] || us.state) : "–") + "</span></span>"
      + (us && FAMILY[us.state] && conf != null ? '<span class="headline">' + EE.pct(conf) + '</span><span class="caption how">sure it is ' + FAMILY[us.state] + " (the chance of that whole family)</span>" : "")
      + (us && us.pending ? '<span class="caption">→ <b>' + esc(EE.STATE_NAME[us.pending] || us.pending) + "</b> is becoming more likely</span>" : "")
      + (sw != null ? '<span class="caption how">I switch when another family reaches ' + EE.pct(sw) + ", or when this one falls below " + EE.pct(1 - sw) + ".</span>" : "")
      + "</div>";
    var gate;
    if (!ladder) {
      gate = '<span class="headline" style="color: var(--ink-muted);">—</span><span class="caption how">' + (us && us.state === "focused" ? "Focused: nothing to act on." : us && us.state === "absent" ? "Nobody there: the robot waits." : "No usable data: the robot waits.") + "</span>";
    } else if (need == null || conf == null) {
      gate = '<span class="headline" style="color: var(--ink-muted);">No gate</span><span class="caption how">No confidence threshold in config/escalation.yaml.</span>';
    } else {
      gate = '<div class="gate2"><div class="gate2__fill" style="width: ' + (conf * 100).toFixed(1) + "%; background: " + (enough ? "var(--hmm-focused)" : "var(--hmm-warn)") + ';"></div>'
        + '<div class="gate2__tick" style="left: ' + (need * 100).toFixed(1) + '%;"><span>acts ' + EE.pct(need) + "</span></div>"
        + (direct != null ? '<div class="gate2__tick gate2__tick--direct" style="left: ' + (direct * 100).toFixed(1) + '%;"><span>direct ' + EE.pct(direct) + "</span></div>" : "") + "</div>"
        + '<span class="headline" style="color: ' + (enough ? "var(--hmm-focused)" : "var(--hmm-warn)") + ';">' + (enough ? "✓ Sure enough" : "✗ Not sure enough") + "</span>"
        + '<span class="caption how">' + esc(EE.STATE_NAME[us.state]) + " " + EE.pct(conf) + (enough ? " ≥ " : " < ") + EE.pct(need) + " for the " + (ladder === "bedtime" ? "tiredness" : "refocus") + " ladder"
        + (enough && direct != null ? (conf >= direct ? "; the firm tone is allowed" : "; the tone stays gentle (below " + EE.pct(direct) + ")") : "") + ".</span>";
    }
    EE.setHTML($("#decide"), '<div class="cardhead"><span class="headline" style="flex: 1;">How I decide</span></div>'
      + '<div class="step2"><span class="caption how"><span class="stepn">1</span> Signs from the camera</span><div class="caption signs2">'
      + (ev.length ? ev.map(function (e) {
        var focusy = /focus/.test(String(e.supports || ""));
        return "<span>" + esc(e.text || e.feature || "") + '</span><span class="sbar"><span style="width: ' + Math.max(6, Math.abs(e.strength || 0) / maxS * 100).toFixed(0) + "%; background: " + (focusy ? "var(--time-focus)" : "var(--time-sleep)") + ';"></span></span>';
      }).join("") : '<span class="how">No strong signs this second.</span><span></span>') + "</div></div>"
      + shown
      + '<div class="step2"><span class="caption how"><span class="stepn">4</span> Sure enough to act?</span>' + gate + "</div>"
      + '<div class="step2"><span class="caption how"><span class="stepn">5</span> The robot</span><span class="body-sm"><b>' + (st.playing ? esc(EE.behaviorName(st.playing)) : "Resting") + '</b></span><span class="caption how">' + (au.ladder ? (au.ladder === "bedtime" ? "Tiredness" : "Refocus") + " ladder" + (au.level != null ? ", level " + au.level : "") + ". " : "") + (au.next ? "Next: " + esc(au.next) + "." : "") + "</span></div>");
    drawSince(st);
  }

  var BELIEF_COL = { focused: "var(--hmm-focused)", tired: "var(--hmm-focused)", distracted: "var(--hmm-distracted)", drowsy: "var(--hmm-drowsy)",
                     very_drowsy: "var(--hmm-very)", absent: "var(--hmm-absent)", unknown: "var(--hmm-unknown)" };
  function beliefTimeline(rows, dur) {
    var W = 1000, H = 130, t0 = rows[0][0], t1 = Math.max(dur, rows[rows.length - 1][0], t0 + 1);
    var x = function (t) { return ((t - t0) / (t1 - t0) * W).toFixed(1); };
    var y = function (p) { return (H - p * H).toFixed(1); };
    var svg = "";
    for (var k = 3; k >= 0; k--) {                                   // stacked: focused at the bottom
      var d = "M" + x(rows[0][0]) + " " + H;
      rows.forEach(function (r) { var cum = 0; for (var j = 0; j <= k; j++) cum += r[1 + j]; d += " L" + x(r[0]) + " " + y(Math.min(1, cum)); });
      d += " L" + x(rows[rows.length - 1][0]) + " " + H + " Z";
      svg += '<path d="' + d + '" style="fill: ' + BELIEF_COL[BELIEF[k]] + ';" fill-opacity=".85"></path>';
    }
    rows.forEach(function (r, i) {                                   // greyed where the belief only drifted (no usable signs)
      if (!r[7] && i + 1 < rows.length) svg += '<rect x="' + x(r[0]) + '" y="0" width="' + Math.max(0.5, x(rows[i + 1][0]) - x(r[0])).toFixed(1) + '" height="' + H + '" style="fill: var(--surface-1);" fill-opacity=".55"></rect>';
    });
    var acts = EE.model && EE.model.act_confidence ? Object.keys(EE.model.act_confidence).map(function (k2) { return EE.model.act_confidence[k2]; }) : [];
    var act = acts.length ? Math.min.apply(null, acts) : null;
    if (act != null && act <= 1) svg += '<line x1="0" x2="' + W + '" y1="' + y(act) + '" y2="' + y(act) + '" style="stroke: var(--ink);" stroke-opacity=".6" stroke-dasharray="2 3" vector-effect="non-scaling-stroke"></line>';
    var line = "", on = false;                                       // how sure it is of the state it shows (black)
    rows.forEach(function (r) {
      if (r[5] == null || !BELIEF_COL[r[6]] || r[6] === "absent" || r[6] === "unknown") { on = false; return; }
      line += (on ? " L" : " M") + x(r[0]) + " " + y(r[5]); on = true;
    });
    if (line) svg += '<path d="' + line + '" fill="none" style="stroke: var(--ink);" stroke-width="1.6" vector-effect="non-scaling-stroke"></path>';
    var strip = rows.map(function (r, i) {
      var x2 = i + 1 < rows.length ? x(rows[i + 1][0]) : x(t1);
      return '<rect x="' + x(r[0]) + '" y="0" width="' + Math.max(0.6, x2 - x(r[0]) + 0.6).toFixed(1) + '" height="14" style="fill: ' + (BELIEF_COL[r[6]] || "var(--hmm-unknown)") + ';"><title>' + esc(EE.STATE_NAME[r[6]] || r[6]) + "</title></rect>";
    }).join("");
    var mmss = function (t) { t = Math.max(0, Math.round(t)); return String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0"); };
    var stepS = [5, 10, 15, 30, 60, 120, 300, 600, 1200].find(function (v) { return (t1 - t0) / v <= 10; }) || 1800;
    var ticks = "";
    for (var t = Math.ceil(t0 / stepS) * stepS; t <= t1; t += stepS) ticks += '<span style="left: ' + ((t - t0) / (t1 - t0) * 100).toFixed(2) + '%;">' + mmss(t) + "</span>";
    return '<div class="btl">'
      + '<span class="caption how btl__lab">belief</span><svg class="btl__area" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" role="img" aria-label="The HMM\'s belief over the session">' + svg + "</svg>"
      + '<span class="caption how btl__lab">HMM shows</span><svg class="btl__strip" viewBox="0 0 ' + W + ' 14" preserveAspectRatio="none">' + strip + "</svg>"
      + '<span></span><div class="caption how btl__axis">' + ticks + "</div></div>";
  }

  function drawSince(st) {
    var box = $("#since");
    if (!box) return;
    var dur = (st.session || {}).elapsed_s || 0;
    var rows = (whole && whole.belief ? whole.belief : []).concat(liveB);
    var leg = [["focused", "Focused"], ["distracted", "Distracted"], ["drowsy", "Drowsy"], ["very_drowsy", "Very drowsy"], ["absent", "Away"]].map(function (k) {
      return '<span><span class="sq" style="background: ' + BELIEF_COL[k[0]] + ';"></span>' + k[1] + "</span>";
    }).join("") + '<span><span class="ln"></span>how sure I am of what I show</span><span><span class="ln ln--dash"></span>I act from here</span><span><span class="sq" style="background: var(--surface-1); outline: 1px solid var(--hairline-strong);"></span>greyed: no new signs</span>';
    EE.setHTML(box, '<div class="cardhead"><span class="headline" style="flex: 1;">Since the session started</span><span class="caption how">What the HMM believed each second, like the old Live HMM page</span></div>'
      + (rows.length > 1 ? beliefTimeline(rows, dur) : '<div class="vizempty caption">' + (st.user_state && !st.user_state.probs ? "These are my simple rules: they give no probabilities. The HMM runs in the “I estimate” modes." : "The belief starts after a few seconds of looking.") + "</div>")
      + '<div class="caption stripleg">' + leg + "</div>");
  }

  function drawCharts() {
    var st = EE.ps();
    if (!st || EE.view !== "control") return;
    if (ctrl.tab === "hmm") { drawSince(st); return; }
    var viz = $("#now-viz");
    if (viz) { viz.__html = null; EE.setHTML(viz, nowViz(st)); }
  }

  function drawVolume() {
    var box = $("#volume");
    if (!box) return;
    var speaker = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5z"></path><path d="M15.5 8.5a5 5 0 0 1 0 7"></path><path d="M19 5a10 10 0 0 1 0 14"></path></svg>';
    if (volumeInfo && volumeInfo.ok && volumeInfo.volume != null) {
      box.innerHTML = speaker + '<label class="body-sm" for="vol" style="white-space: nowrap;">My volume' + (volumeInfo.simulator ? " · this computer" : "") + "</label>"
        + '<input id="vol" class="slider" type="range" min="0" max="100" step="5" value="' + Number(volumeInfo.volume) + '">'
        + '<span class="body-sm" id="vol-v" style="font-variant-numeric: tabular-nums; width: 38px; text-align: right;">' + Number(volumeInfo.volume) + "%</span>";
    } else {
      box.innerHTML = speaker + '<span class="body-sm" style="color: var(--ink-muted);">My volume: ' + esc((volumeInfo && volumeInfo.message) || "not available right now.") + "</span>";
    }
  }
  function loadVolume() {
    if (!EE.status) { EE.refresh().then(function () { if (EE.status && EE.view === "control") loadVolume(); }); return; }
    if (EE.status.settings.backend === "dry") { volumeInfo = { ok: false, message: "no robot in this setting." }; drawVolume(); return; }
    EE.getJSON("/api/v5/volume").then(function (d) { volumeInfo = d; drawVolume(); }).catch(function () { volumeInfo = null; drawVolume(); });
  }

  function lastThing() {
    var log = (EE.pstate && EE.pstate.log) || [];
    for (var i = log.length - 1; i >= 0; i--) {
      var e = log[i];
      if (e.type === "behavior_started") return "Last thing I did: " + EE.behaviorName(e.behavior).toLowerCase() + " · " + String(e.t_wall || "").slice(11, 16);
      if (e.type === "speech_requested") return 'Last thing I said: "' + (e.text || "") + '" · ' + String(e.t_wall || "").slice(11, 16);
    }
    return "";
  }

  function flash(text, bad) {
    ctrl.msg = text; ctrl.msgBad = !!bad;
    var msg = $("#ctrl-msg");
    if (msg) { msg.textContent = text; msg.style.color = bad ? "var(--error)" : "var(--ink)"; }
    clearTimeout(flash.t);
    flash.t = setTimeout(function () { ctrl.msg = ""; var m = $("#ctrl-msg"); if (m) m.textContent = ""; }, 5000);
  }
  function answer(res, ok) {
    if (res && res.ok) flash(ok(res), false);
    else flash((res && res.message) || "That didn't work.", true);
    EE.pollPanel().then(function () { var s = EE.screens[EE.view]; if (s && s.panelUpdate) s.panelUpdate(); });
  }
  function press(b, via) {
    if (!b) return;
    if (b.kind === "behavior") {
      EE.panelPost("play", { behavior: b.target, intensity: ctrl.intensity, via: via }).then(function (r) { answer(r, function () { return "Playing: " + EE.behaviorName(b.target); }); });
    } else if (b.kind === "line") {
      EE.panelPost("say", { line_id: b.target, tone: ctrl.tone, via: via }).then(function (r) { answer(r, function (x) { return 'Saying: "' + (x.text || EE.cap(b.label)) + '"'; }); });
    } else if (b.kind === "marker") {
      EE.panelPost("annotate", { marker: b.target, via: via }).then(function (r) { answer(r, function () { return "Marked: " + (EE.MARKER[b.target] || EE.cap(b.label)); }); });
    }
  }
  function setTone(t) {
    ctrl.tone = t;
    EE.$$("#tone-tabs .ds-tab").forEach(function (b) {
      var on = b.getAttribute("data-value") === t;
      b.classList.toggle("is-selected", on); b.setAttribute("aria-selected", on ? "true" : "false"); b.tabIndex = on ? 0 : -1;
    });
    EE.$$("[data-line]").forEach(function (s) { var w = line(s.getAttribute("data-line"), t); if (w) s.textContent = w; });
  }

  // The session log, as a drawer over the page (V5-S2-Control, showLog).
  function humanLog(e) {
    var text;
    switch (e.type) {
      case "behavior_started": text = (e.source === "auto" ? "I played " : "I played (you pressed) ") + EE.behaviorName(e.behavior).toLowerCase(); break;
      case "behavior_interrupted": text = "I stopped " + EE.behaviorName(e.behavior).toLowerCase(); break;
      case "speech_requested": text = 'I said "' + (e.text || "") + '"'; break;
      case "annotation": text = "You marked: " + (EE.MARKER[e.marker] || EE.cap(e.label || e.marker)).toLowerCase(); break;
      case "note": text = "Your note: " + (e.text || ""); break;
      case "nudge_decision": text = "I decided to play " + EE.behaviorName(e.behavior).toLowerCase(); break;
      case "nudge_outcome": text = "How it went: " + (EE.OUTCOME[e.outcome] || EE.cap(e.outcome)).toLowerCase(); break;
      case "nudge_reward": text = "How your move went: " + (EE.OUTCOME[e.outcome] || EE.cap(e.outcome)).toLowerCase(); break;
      case "calibration": text = "Desk check: " + EE.cap(e.result).toLowerCase(); break;
      case "escalation": text = EE.cap(e.what); break;
      case "session_start": text = "Recording started for " + (e.participant || ""); break;
      case "session_end": text = "The session ended"; break;
      case "wizard_command": text = "You pressed: " + EE.cap(e.action).toLowerCase() + (e.behavior ? " · " + EE.behaviorName(e.behavior).toLowerCase() : ""); break;
      default: text = EE.cap(e.type);
    }
    return '<span><span style="color: var(--ink-muted);">' + esc(String(e.t_wall || "").slice(11, 16)) + "</span>  " + esc(text) + "</span>";
  }
  function drawerHTML() {
    var rows = ((EE.pstate && EE.pstate.log) || []).slice().reverse();
    return '<div style="display: flex; align-items: center;"><span class="headline" style="flex: 1;">What happened so far</span>' + EE.iconBtn("Close the log", "X", 'data-act="log-close"') + "</div>"
      + '<div class="body-sm drawer__rows">' + (rows.length ? rows.map(humanLog).join("") : '<span style="color: var(--ink-muted);">Nothing yet.</span>') + "</div>"
      + '<span class="caption" style="color: var(--ink-muted); margin-top: auto;">The last 20 things, newest first. Everything is saved in the session folder.</span>';
  }
  function openDrawer() {
    closeDrawer();
    var d = document.createElement("aside");
    d.id = "drawer"; d.className = "drawer"; d.setAttribute("aria-label", "Session log");
    d.innerHTML = drawerHTML();
    document.body.appendChild(d);
  }
  function closeDrawer() { var d = $("#drawer"); if (d) d.remove(); }

  EE.screens.control = {
    render: function () {
      ctrl.version = null;
      live = []; liveB = []; whole = null; wholeAt = 0;
      EE.main.innerHTML = '<div class="control">' + sessbar("control") + '<div id="control" class="control__body"></div></div>';
      this.panelUpdate();
      loadVolume();
    },
    leave: function () { closeDrawer(); },
    panelUpdate: function () {
      var box = $("#control");
      if (!box) return;
      updateSessbar("control");
      var st = EE.ps();
      if (!st) { EE.setHTML(box, panelMissing()); ctrl.version = null; return; }
      var sess = st.session || {};
      if (!sess.active) { EE.setHTML(box, noSession()); ctrl.version = null; closeDrawer(); return; }
      lastName = sess.session || lastName;
      addLive(st);
      if (!whole || Date.now() - wholeAt > 20000) loadWhole();
      var vkey = st.panel_version + "|" + ctrl.tab + "|" + ((EE.model || {}).lines ? 1 : 0);
      if (ctrl.version !== vkey || !$("#banners")) {
        ctrl.version = vkey;
        box.innerHTML = skeleton(st, EE.pstate.panel || {});
        box.__html = null;
        drawVolume();
      }
      EE.setHTML($("#banners"), banners(st));
      if (ctrl.tab === "hmm") { drawLive(st); camPicture(); }
      else {
        EE.setHTML($("#now-viz"), nowViz(st));
        EE.setHTML($("#now-doing"), nowDoing(st));
        EE.$$(".tile").forEach(function (el) { el.classList.toggle("is-playing", !!st.playing && el.getAttribute("data-target") === st.playing); });
        var idle = $("#idle");
        if (idle) idle.setAttribute("aria-checked", st.idle_on ? "true" : "false");
      }
      ctrl.prevProbs = st.user_state && st.user_state.probs ? st.user_state.probs : null;
      EE.setHTML($("#lastthing"), esc(lastThing()));
      var msg = $("#ctrl-msg");
      if (msg && msg.textContent !== ctrl.msg) { msg.textContent = ctrl.msg; msg.style.color = ctrl.msgBad ? "var(--error)" : "var(--ink)"; }
      var d = $("#drawer");
      if (d) EE.setHTML(d, drawerHTML());
    },
    act: function (act, el, e) {
      if (commonAct(act, el)) return;
      if (act === "press") press(buttons[el.getAttribute("data-id")], "click");
      else if (act === "tab") { if (e) e.preventDefault(); ctrl.tab = el.getAttribute("data-value"); ctrl.version = null; this.panelUpdate(); }
      else if (act === "tone") setTone(el.getAttribute("data-value"));
      else if (act === "idle") {
        var on = el.getAttribute("aria-checked") !== "true";
        EE.panelPost("idle", { on: on }).then(function (r) { answer(r, function () { return on ? "I breathe while idle." : "I stay still while idle."; }); });
      } else if (act === "autonomy") {
        var pause = el.getAttribute("data-value") === "pause";
        el.disabled = true;
        EE.panelPost("autonomy", { paused: pause }).then(function (r) { answer(r, function () { return pause ? "Paused: I won't step in by myself." : "I can step in again."; }); });
      } else if (act === "reconnect") {
        el.disabled = true;
        EE.panelPost("reconnect", {}).then(function (r) { el.disabled = false; answer(r, function () { return "Connected to my body again."; }); });
      } else if (act === "log-open") openDrawer();
      else if (act === "log-close") closeDrawer();
      else if (act === "note-open") openNote();
      else if (act === "note-save") saveNote(el);
    },
    input: function (e) {
      if (e.target.id === "intensity") { ctrl.intensity = parseFloat(e.target.value); $("#intensity-v").textContent = Math.round(ctrl.intensity * 100) + "%"; }
      else if (e.target.id === "vol") $("#vol-v").textContent = e.target.value + "%";
    },
    change: function (e) {
      if (e.target.id === "vol") {
        EE.post("/api/v5/volume", { volume: parseInt(e.target.value, 10) }).then(function (r) {
          if (r.ok) { volumeInfo = r; flash("Volume " + r.volume + "%: you should hear a test sound."); }
          else flash(r.message || "The volume didn't change.", true);
        });
      }
    },
    submit: function (e) {
      if (e.target.id !== "say-form") return;
      e.preventDefault();
      var say = $("#say-text"), text = say.value.trim();
      if (!text) return;
      EE.panelPost("say", { text: text, tone: ctrl.tone, via: "click" }).then(function (r) {
        if (r.ok) say.value = "";
        answer(r, function (x) { return 'Saying: "' + (x.text || text) + '"'; });
      });
    },
    // The panel's keys, as in the classic panel: never while typing.
    key: function (e) {
      if (!EE.pstate || e.ctrlKey || e.metaKey || e.altKey || isTyping(e)) return;
      var panel = EE.pstate.panel || {};
      var name = e.key === " " ? "space" : e.key === "Escape" ? "escape" : e.key === "Tab" ? "tab" : e.key.toLowerCase();
      if ($("#drawer") && name === "escape") { closeDrawer(); return; }
      if ((panel.stop_keys || ["escape", "space"]).indexOf(name) >= 0) { e.preventDefault(); stopEepy("key"); return; }
      if (name === (panel.tone_key || "tab")) { e.preventDefault(); setTone(ctrl.tone === "hedged" ? "direct" : "hedged"); flash("Tone: " + (ctrl.tone === "direct" ? "direct" : "hedged")); return; }
      if (name === (panel.idle_key || "a")) {
        e.preventDefault();
        var on = !(EE.ps() && EE.ps().idle_on);
        EE.panelPost("idle", { on: on, via: "key" }).then(function (r) { answer(r, function () { return on ? "I breathe while idle." : "I stay still while idle."; }); });
        return;
      }
      if (keys[name] && !e.repeat) { e.preventDefault(); press(keys[name], "key"); }
    }
  };

  // --- notes and ending the session ---------------------------------------------------------------

  function openNote() {
    EE.openModal(EE.modal("Add a note", "Saved in the session, with the time. Only you see it.",
      '<div class="ds-field"><label class="ds-field__label" for="note-text">What happened?</label><div class="ds-field__control"><textarea id="note-text" class="ds-input ds-textarea" rows="3" maxlength="2000" placeholder="e.g. P05 took a phone call"></textarea></div></div><p class="body-sm" id="note-msg" style="color: var(--error); margin: 0;" hidden></p>',
      EE.btn("secondary", "Cancel", 'data-act="modal-close"') + EE.btn("primary", "Save note", 'data-act="note-save"')));
  }
  function saveNote(btn) {
    var text = (($("#note-text") || {}).value || "").trim();
    if (!text) return;
    btn.disabled = true;
    EE.panelPost("note", { text: text }).then(function (r) {
      btn.disabled = false;
      if (r.ok) { EE.closeModal(); flash("Note saved, with the time."); }
      else { var m = $("#note-msg"); if (m) { m.hidden = false; m.textContent = r.message || "The note wasn't saved."; } }
    });
  }

  function openEnd() {
    var sess = EE.session();
    if (!sess.active) return;
    EE.openModal('<section class="endmodal" role="dialog" aria-modal="true" aria-labelledby="m-title">'
      + '<div style="display: flex; align-items: center; gap: var(--space-md);"><img src="img/eepy-mark.svg" alt="" width="40" height="40"><span class="headline" id="m-title">Shall we stop here with ' + esc(sess.participant) + "?</span></div>"
      + '<p class="body" style="margin: 0; color: var(--ink-muted);">I\'ll stop recording, save everything and relax back to neutral.</p>'
      + '<div class="ds-field"><label class="ds-field__label" for="end-notes">Anything I should remember? (optional)</label><div class="ds-field__control"><textarea id="end-notes" class="ds-input ds-textarea" rows="3" maxlength="2000" placeholder="e.g. ' + esc(sess.participant) + ' was ill today"></textarea></div></div>'
      + '<p class="body-sm" id="end-msg" style="color: var(--error); margin: 0;" hidden></p>'
      + '<div style="display: flex; gap: var(--space-xs); justify-content: flex-end;">' + EE.btn("secondary", "Keep going", 'data-act="modal-close" style="background: var(--surface-2);"')
      + '<button type="button" class="endred" data-act="end-confirm">End the session</button></div></section>');
  }
  function endSession(btn) {
    var sess = EE.session();
    var name = sess.session || lastName;
    var notes = (($("#end-notes") || {}).value || "").trim();
    btn.disabled = true;
    btn.textContent = "Saving…";
    EE.panelPost("session/end", { notes: notes || null }).then(function (res) {
      if (!res.ok) {
        btn.disabled = false; btn.textContent = "End the session";
        var m = $("#end-msg"); if (m) { m.hidden = false; m.textContent = res.message || "The session didn't end."; }
        return;
      }
      EE.closeModal();
      lastName = name;
      location.hash = "#/done/" + encodeURIComponent(name);
    });
  }

  // --- the wrap up (V5-S3-WrapUp) -----------------------------------------------------------------

  EE.screens.done = {
    render: function (name) {
      EE.main.innerHTML = '<p class="body" style="padding: 80px; color: var(--ink-muted);">Putting it together…</p>';
      EE.getJSON("/api/v5/session?name=" + encodeURIComponent(name)).then(function (s) {
        if (EE.view !== "done") return;
        if (!s || !s.name) { EE.main.innerHTML = '<div class="empty"><h1 class="display-md">I can\'t find that session.</h1>' + EE.linkBtn("primary", "See every session", "#/sessions") + "</div>"; return; }
        return EE.getJSON("/api/v5/progress?participant=" + encodeURIComponent(s.participant)).then(function (p) { draw(s, p); }, function () { draw(s, null); });
      }).catch(function () { EE.main.innerHTML = '<div class="empty"><h1 class="display-md">The launcher did not answer.</h1></div>'; });
    },
    act: function (act, el) {
      if (act === "shutdown") {
        el.disabled = true;
        EE.post("/api/stop-all", {}).then(function () { location.hash = "#/"; });
      } else if (act === "copy-path") {
        var path = el.getAttribute("data-path");
        if (navigator.clipboard) navigator.clipboard.writeText(path).then(function () { el.textContent = "Copied"; });
      }
    }
  };

  var DOT = { success: "var(--success)", no_reaction: "var(--time-busy)", dismissed: "var(--time-busy)", interrupted_focus: "var(--time-busy)", snoozed: "var(--time-break)" };
  function draw(s, p) {
    if (EE.view !== "done") return;
    var who = esc(s.participant), n = s.moments.length, good = s.moments.filter(function (m) { return m.outcome === "success"; }).length;
    var E = {
      rest: { tag: "perfect ending", title: "Perfect timing, " + who + "!", text: "I suggested a rest" + (s.suggested_clock ? " at " + s.suggested_clock : "") + " and you took it. That’s exactly how I hope a session ends.", c: "var(--time-focus)" },
      suggested: { tag: "I suggested a rest", title: "Good call to stop there.", text: "You were getting tired, so I suggested a rest" + (s.suggested_clock ? " at " + s.suggested_clock : "") + ". Next time I’ll try a little earlier.", c: "var(--time-sleep)" },
      you: { tag: "you ended it", title: "Nice work, " + who + "!", text: "You ended the session at " + esc(s.end_clock) + ". Everything is saved" + (s.mode === "hmm_bandit" ? ", and I learned a bit more about what helps you." : "."), c: "var(--atmosphere-glow)" }
    }[s.ending] || { tag: "done", title: "Session complete.", text: "Everything is saved.", c: "var(--atmosphere-glow)" };
    var share = s.has_state ? s.focused_share : null;
    var ring = share != null
      ? '<svg width="112" height="112" viewBox="0 0 112 112" aria-hidden="true"><circle cx="56" cy="56" r="48" fill="none" style="stroke: var(--time-track);" stroke-width="12"></circle><circle cx="56" cy="56" r="48" fill="none" style="stroke: var(--time-focus);" stroke-width="12" stroke-linecap="round" stroke-dasharray="' + (share * 302).toFixed(0) + ' 302" transform="rotate(-90 56 56)"></circle></svg><span class="headline ringnum">' + EE.pct(share) + "</span>"
      : '<svg width="112" height="112" viewBox="0 0 112 112" aria-hidden="true"><circle cx="56" cy="56" r="48" fill="none" style="stroke: var(--time-track);" stroke-width="12"></circle></svg><span class="headline ringnum">–</span>';
    var won = p && p.enough
      ? '<span class="display-md" style="font-variant-numeric: tabular-nums;">' + (p.minutes_more >= 0 ? "+" : "") + p.minutes_more + ' min</span><div class="caption wonbars"><span style="color: var(--ink-muted);">First 3</span><span class="wbar"><span style="width: ' + (p.first_share * 100).toFixed(0) + '%; background: var(--time-busy);"></span></span><span>' + EE.pct(p.first_share) + "</span><span>Latest</span>"
        + '<span class="wbar"><span style="width: ' + (p.latest_share * 100).toFixed(0) + '%; background: var(--energy-high);"></span></span><b>' + EE.pct(p.latest_share) + '</b></div><span class="caption" style="color: var(--ink-muted);">' + (p.gain_points >= 0 ? "+" : "") + p.gain_points + " points of focus against their first 3 sessions.</span>"
      : '<span class="display-md">Soon</span><span class="caption" style="color: var(--ink-muted);">After 4 sessions with my eyes on, I compare the latest with the first three' + (p ? " (" + p.sessions + " so far)" : "") + ".</span>";
    var dots = s.moments.map(function (m) {
      return DOT[m.outcome] ? '<span class="dot18" style="background: ' + DOT[m.outcome] + ';"></span>' : '<span class="dot18" style="border: 2px dashed var(--hairline-strong); box-sizing: border-box;"></span>';
    }).join("");
    var counts = { success: 0, none: 0, snoozed: 0, other: 0 };
    s.moments.forEach(function (m) {
      if (m.outcome === "success") counts.success++; else if (m.outcome === "snoozed") counts.snoozed++;
      else if (m.outcome === "no_reaction" || m.outcome === "dismissed" || m.outcome === "interrupted_focus") counts.none++; else counts.other++;
    });
    var span = Math.max(1, s.duration_s);
    var curve = s.curve.length > 1 ? EE.focusCurve(s.curve.map(function (c) { return [c[0] / span, c[1], EE.addClock(s.clock, c[0])]; }), { height: 70, label: "Focus" }) : '<span class="caption" style="color: var(--ink-muted);">My eyes weren\'t on, so there is no curve.</span>';
    var low = s.curve.length ? s.curve.reduce(function (b, c) { return c[1] < b[1] ? c : b; }) : null;
    var ex = s.moments.slice().reverse().filter(function (m) { return m.sentence || m.reason; })[0];
    EE.main.innerHTML = '<div class="done">'
      + '<section class="done__hero"><div class="done__img"><span aria-hidden="true" class="done__halo" style="background: ' + E.c + ';"></span><img class="bob3" src="img/eepy-wave.png" alt="EEPY waving"></div>'
      + '<div class="done__words"><span class="pop body-sm donepill"><span class="donepill__ic">' + icon("Check", 16) + '</span><b>Session complete</b><span style="color: var(--ink-muted);">· ' + EE.minutes(s.duration_s) + " · all saved</span></span>"
      + '<span class="caption eyebrow">Run a session · step 3 of 3 · ' + E.tag + '</span><h1 class="display-lg" style="margin: 0;">' + E.title + '</h1><p class="body-lg" style="margin: 0; color: var(--ink-muted); max-width: 720px;">' + E.text + "</p></div></section>"
      + '<section aria-label="What you achieved" style="display: flex; flex-direction: column; gap: var(--space-sm);"><span class="caption eyebrow">What you achieved today</span><div class="res3">'
      + '<div class="res"><div class="ringbox">' + ring + '</div><div style="display: flex; flex-direction: column; gap: var(--space-xxs);"><span class="caption" style="color: var(--ink-muted);">Focused</span><span class="display-md" style="font-variant-numeric: tabular-nums;">' + (share != null ? EE.minutes(s.focused_s) : "–") + '</span><span class="body-sm" style="color: var(--ink-muted);">' + (share != null ? "out of " + Math.round(s.desk_s / 60) + " minutes at the desk" : "My eyes weren't on in this session.") + "</span></div></div>"
      + '<div class="res res--won"><span aria-hidden="true" class="res__glow"></span><div style="position: relative; display: flex; flex-direction: column; gap: var(--space-xs); flex: 1;"><span class="caption" style="color: var(--ink-muted); display: inline-flex; align-items: center; gap: 4px;"><span style="color: var(--energy-high); display: inline-flex;">' + icon("Zap", 16) + "</span>Time won back</span>" + won + "</div></div>"
      + '<div class="res"><div style="display: flex; flex-direction: column; gap: var(--space-xs); flex: 1;"><span class="caption" style="color: var(--ink-muted);">My help worked</span><span class="display-md" style="font-variant-numeric: tabular-nums;">' + good + ' <span class="headline" style="color: var(--ink-muted);">of ' + n + (n === 1 ? " time" : " times") + '</span></span><div style="display: flex; gap: 6px; flex-wrap: wrap;" aria-hidden="true">' + dots + '</div><span class="caption" style="color: var(--ink-muted);">' + (n ? "worked · no reaction · snoozed · not counted" : "I didn't need to step in.") + "</span></div></div>"
      + "</div></section>"
      + '<a href="#/story/' + encodeURIComponent(s.name) + '" class="golink" aria-label="Open this session in Analyze"><div class="golink__left"><span class="caption eyebrow">Want to look closer?</span><span class="headline">This session, step by step</span><span class="body-sm" style="color: var(--ink-muted);">I\'ll show you minute by minute what I saw and explain each choice I made.</span><span class="openbtn">Open in Analyze ' + icon("ArrowRight", 16) + "</span></div>"
      + '<div class="golink__prev"><div class="prev"><span class="caption" style="color: var(--ink-muted);">How your focus moved</span><div style="pointer-events: none;">' + curve + "</div>"
      + '<span class="body-sm"><b>' + (share != null ? EE.pct(share) + " focused" : "No focus data") + "</b>" + (low ? ' <span style="color: var(--ink-muted);">· lowest at ' + EE.addClock(s.clock, low[0]) + "</span>" : "") + "</span></div>"
      + '<div class="prev"><span class="caption" style="color: var(--ink-muted);">The ' + n + " moment" + (n === 1 ? "" : "s") + ' I stepped in</span><div class="pintrack">' + s.moments.map(function (m) {
        return '<span class="pin' + (DOT[m.outcome] ? "" : " pin--open") + '" style="left: ' + (Math.min(98, Math.max(2, m.t / span * 100))).toFixed(1) + "%;" + (DOT[m.outcome] ? " background: " + DOT[m.outcome] + ";" : "") + '"></span>';
      }).join("") + "</div>"
      + '<div class="caption pinleg"><span><span class="d8" style="background: var(--success);"></span>' + counts.success + ' worked</span><span><span class="d8" style="background: var(--time-busy);"></span>' + counts.none + ' no reaction</span><span><span class="d8" style="background: var(--time-break);"></span>' + counts.snoozed + ' snoozed</span><span style="color: var(--ink-muted);"><span class="d8" style="border: 1px dashed var(--hairline-strong);"></span>' + counts.other + " not counted</span></div></div>"
      + '<div class="prev"><span class="caption" style="color: var(--ink-muted);">Why I chose each move · for example</span>' + (ex ? '<span class="body-sm"><b>' + esc(ex.clock) + " · " + esc(EE.behaviorName(ex.behavior).toLowerCase()) + '</b></span><span class="caption" style="color: var(--ink-muted); line-height: 1.4;">' + esc(ex.sentence || EE.cap(ex.reason)) + "</span>" : '<span class="caption" style="color: var(--ink-muted);">No explanations in this session.</span>') + "</div></div></a>"
      + "</div>"
      + '<footer class="stepbar">' + EE.btn("secondary", "Shut down", 'data-act="shutdown"') + '<span class="caption" style="color: var(--ink-muted);">All saved in data/sessions/' + esc(s.name) + ' · <a href="#" data-act="copy-path" data-path="data/sessions/' + esc(s.name) + '">Copy folder path</a></span>'
      + '<div style="flex: 1;"></div>' + EE.linkBtn("primary", "Next participant", "#/setup", 'style="box-shadow: var(--glow-focus);"') + "</footer>";
  }
})();
