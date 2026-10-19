/* The /v5 page, part 5: Analyze. Sessions (V5-A1-Sessions), Session story (V5-A2-Story) and What I
   learned (V5-A3-Learned). Read only: the recorded sessions through v5api, the bandit through
   /api/bandit/run (list / show; reset only after a confirm). */
(function () {
  "use strict";

  var EE = window.EE, $ = EE.$, esc = EE.esc, icon = EE.icon;
  var A = { who: "", list: null, story: null, open: -1, pop: -1, learned: null, bucket: "", codes: [], msg: "" };
  EE.analyze = A;

  function tabs(on) {
    var t = function (href, ic, text, key) {
      return '<a href="' + href + '" class="atab' + (on === key ? " is-on" : "") + '"' + (on === key ? ' aria-current="page"' : "") + ">" + icon(ic, 16) + text + "</a>";
    };
    return '<nav class="atabs" aria-label="Analyze">' + t("#/sessions", "AlarmClock", "Sessions", "sessions")
      + t(A.story ? "#/story/" + encodeURIComponent(A.story.name) : "#/sessions", "Search", "Session story", "story")
      + t("#/learned", "BookOpen", "What I learned", "learned") + "</nav>";
  }
  function top(on, picker) { return '<div class="atop">' + tabs(on) + '<div style="flex: 1;"></div>' + (picker || "") + "</div>"; }
  function whoPicker(codes, value, width) {
    return '<div style="width: ' + (width || 170) + 'px;">' + EE.select({ id: "who", label: "Participant", value: value, options: codes.map(function (c) { return { value: c, label: c }; }) }) + "</div>";
  }
  function dayWord(s) {
    try {
      var d = new Date(s.start), today = new Date();
      var same = function (a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); };
      var y = new Date(today); y.setDate(today.getDate() - 1);
      if (same(d, today)) return "Today";
      if (same(d, y)) return "Yesterday";
      return d.toLocaleDateString("en-GB", { month: "short", day: "numeric" }).replace(/(\d+) (\w+)/, "$2 $1");
    } catch (e) { return s.date; }
  }
  function partOfDay(clock) {
    var h = parseInt(String(clock || "12").slice(0, 2), 10);
    return h < 12 ? ["Morning", "Coffee"] : h < 18 ? ["Afternoon", "BookOpen"] : ["Evening", "BedDouble"];
  }
  function toMin(clock) { var m = /^(\d{1,2}):(\d{2})$/.exec(clock || ""); return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : 0; }
  function fmt(m) { m = ((m % 1440) + 1440) % 1440; return Math.floor(m / 60) + ":" + String(m % 60).padStart(2, "0"); }
  var SEG = { focused: "focus", tired: "focus", distracted: "busy", drowsy: "busy", very_drowsy: "busy", absent: "break" };

  // One session as the design system's small DayTimeline: states as segments, nudges as pins.
  function sessionTimeline(s) {
    var start = toMin(s.clock), a = Math.floor(start / 15) * 15, b = Math.ceil((start + Math.max(60, s.duration_s) / 60) / 15) * 15;
    if (b - a < 30) b = a + 30;
    var span = b - a, at = function (sec) { return (start - a + sec / 60) / span * 100; };
    var ticks = [];
    for (var m = Math.ceil(a / 60) * 60; m <= b; m += 60) ticks.push((m - a) / span * 100);
    return EE.dayTimeline({
      segments: (s.strip || []).filter(function (r) { return SEG[r.state]; }).map(function (r) {
        return { a: at(r.from), b: at(r.to), type: SEG[r.state], label: EE.STATE_NAME[r.state] + ", " + fmt(start + Math.round(r.from / 60)) + "–" + fmt(start + Math.round(r.to / 60)) };
      }),
      pins: (s.marks_at || []).map(function (t) { return { at: at(t), label: "I stepped in at " + fmt(start + Math.round(t / 60)), icon: "Play" }; }),
      ticks: ticks, start: fmt(a), end: fmt(b), aria: "From " + s.clock + " to " + s.end_clock
    });
  }

  function loadList() {
    return EE.getJSON("/api/v5/sessions").then(function (data) {
      A.list = data.sessions || [];
      var codes = [];
      A.list.forEach(function (s) { if (s.participant && codes.indexOf(s.participant) < 0) codes.push(s.participant); });
      codes.sort();
      A.sessionCodes = codes;
      if (!A.who || codes.indexOf(A.who) < 0) {
        var last = EE.lastRun();
        A.who = last && codes.indexOf(last.participant) >= 0 ? last.participant : (A.list[0] || {}).participant || "";
      }
    });
  }

  // --- Sessions (V5-A1-Sessions) -------------------------------------------------------------------

  EE.screens.sessions = {
    render: function () {
      EE.main.innerHTML = top("sessions") + '<p class="body" style="padding: var(--space-lg) 80px; color: var(--ink-muted);">Reading the sessions…</p>';
      loadList().then(drawSessions).catch(function () { EE.main.innerHTML = top("sessions") + '<p class="body" style="padding: 0 80px; color: var(--error);">The launcher did not answer.</p>'; });
    },
    change: function (e) { if (e.target.id === "who") { A.who = e.target.value; drawSessions(); } }
  };

  function drawSessions() {
    if (EE.view !== "sessions") return;
    if (!A.list.length) {
      EE.main.innerHTML = top("sessions") + '<div class="empty"><img src="img/eepy-scientist.svg" alt=""><h1 class="display-md" style="margin: 0;">No sessions yet.</h1><p class="body-lg" style="color: var(--ink-muted); margin: 0;">Once you run a session, I\'ll tell you its story here.</p>' + EE.linkBtn("primary", "Run a session", "#/setup") + "</div>";
      return;
    }
    var who = esc(A.who);
    var rows = A.list.filter(function (s) { return s.participant === A.who; });
    var withState = rows.filter(function (s) { return s.focused_share != null; });
    var firstThree = withState.slice(-3).map(function (s) { return s.name; });
    var cards = rows.map(function (s, i) {
      var pod = partOfDay(s.clock), n = s.nudges + s.by_hand;
      return '<a href="#/story/' + encodeURIComponent(s.name) + '" class="sess' + (i === 0 ? " is-today" : "") + '"><div class="sess__head">'
        + '<span class="sess__ic">' + icon(pod[1], 16) + '</span><span style="flex: 1; display: flex; flex-direction: column;"><span class="body"><b>' + esc(dayWord(s)) + "</b> · " + pod[0]
        + (firstThree.indexOf(s.name) >= 0 && withState.length >= 4 ? ' <span class="caption" style="color: var(--ink-muted);">· one of the first 3</span>' : "") + "</span>"
        + '<span class="caption" style="color: var(--ink-muted);">' + esc(s.clock) + " – " + esc(s.end_clock) + " · " + EE.minutes(s.duration_s) + " · " + n + (n === 1 ? " nudge" : " nudges") + (s.ended ? "" : " · not ended") + "</span></span>"
        + (s.focused_share != null ? '<span class="body-sm" style="color: var(--time-break); font-weight: 700;">' + EE.pct(s.focused_share) + " focused</span>" : '<span class="caption" style="color: var(--ink-muted);">no eye data</span>')
        + '<span class="open">See story' + icon("ChevronRight", 16) + "</span></div>" + sessionTimeline(s) + "</a>";
    }).join("");
    EE.main.innerHTML = top("sessions", whoPicker(A.sessionCodes, A.who))
      + '<header class="ahead"><h1 class="display-md" style="margin: 0;">How did it go?</h1><span class="body-lg" style="color: var(--ink-muted);">Every session with ' + who + ", newest first. Click one and I'll tell you its story.</span></header>"
      + '<div class="a1grid"><section class="a1list"><div style="display: flex; align-items: baseline; gap: var(--space-sm);"><span class="headline" style="flex: 1;">' + who + '\'s sessions <span style="color: var(--time-break);">' + rows.length + " so far</span>"
      + '<span class="caption" style="display: block; color: var(--ink-muted); font-weight: 500; letter-spacing: 0; margin-top: 2px;">Each card opens that session\'s story.</span></span>'
      + '<span class="caption" style="color: var(--ink-muted);">bright = focused · grey = drifting or tired · warm = away · pins = I stepped in</span></div>'
      + '<div class="a1scrollwrap"><div class="scroll a1scroll">' + cards + '</div><span aria-hidden="true" class="a1fade"></span></div></section>'
      + '<aside style="display: flex; flex-direction: column; gap: var(--space-md);"><section class="woncard" id="won"><span class="caption" style="color: var(--ink-muted);">Working it out…</span></section>'
      + '<a href="#/learned" class="golink golink--small"><span class="golink__ic">' + icon("BookOpen", 20) + '</span><span style="flex: 1; display: flex; flex-direction: column; gap: 2px;"><b class="body">What works for ' + who + '</b><span class="caption" style="color: var(--ink-muted);">The moves that helped them most, in each kind of moment</span></span>' + icon("ChevronRight", 20) + "</a></aside></div>";
    EE.getJSON("/api/v5/progress?participant=" + encodeURIComponent(A.who)).then(function (p) {
      var box = $("#won");
      if (!box) return;
      box.innerHTML = '<span aria-hidden="true" class="res__glow"></span><div class="woncard__head"><span class="woncard__ic">' + icon("Zap", 20) + '</span><span class="headline">Time won back</span></div>'
        + (p.enough
          ? '<span class="display-lg" style="position: relative; font-variant-numeric: tabular-nums; line-height: 1;">' + (p.gain_points >= 0 ? "+" : "") + p.gain_points + "%</span>"
            + '<span class="body-sm" style="position: relative; color: var(--ink-muted);">focused in the latest session, compared with ' + who + "'s first 3. About " + Math.abs(p.minutes_more) + " minutes " + (p.minutes_more >= 0 ? "more" : "less") + " per session.</span>"
            + '<div style="position: relative; display: flex; flex-direction: column; gap: var(--space-xs); margin-top: var(--space-xs);"><div class="caption wonrow"><span style="color: var(--ink-muted);">First 3</span><span class="wbar"><span style="width: ' + (p.first_share * 100).toFixed(0) + '%; background: var(--time-busy);"></span></span><span>' + EE.pct(p.first_share) + "</span></div>"
            + '<div class="caption wonrow"><span>Latest</span><span class="wbar"><span style="width: ' + (p.latest_share * 100).toFixed(0) + '%; background: linear-gradient(90deg, var(--time-focus-start), var(--time-focus));"></span></span><b>' + EE.pct(p.latest_share) + "</b></div></div>"
            + '<span class="caption" style="position: relative; color: var(--ink-muted);">A before-and-after from my own recordings, not a proper experiment.</span>'
          : '<span class="body" style="position: relative;">After 4 sessions with my eyes on, I compare the latest one with ' + who + "'s first three.</span>"
            + '<span class="caption" style="position: relative; color: var(--ink-muted);">' + p.sessions + " so far. A before-and-after from my own recordings, not a proper experiment.</span>");
    });
  }

  // --- Session story (V5-A2-Story) -----------------------------------------------------------------

  var STRIP = { distracted: "var(--energy-low)", drowsy: "color-mix(in srgb, var(--time-sleep) 60%, transparent)", very_drowsy: "var(--time-sleep)",
                absent: "repeating-linear-gradient(45deg, var(--hairline-strong) 0 2px, transparent 2px 6px)" };
  var CIRCLE = { success: ["var(--success)", "var(--atmosphere-deep)"], snoozed: ["var(--time-break)", "var(--atmosphere-deep)"] };

  EE.screens.story = {
    render: function (name) {
      A.open = -1; A.pop = -1;
      EE.main.innerHTML = top("story") + '<p class="body" style="padding: var(--space-lg) 80px; color: var(--ink-muted);">Reading the session…</p>';
      Promise.all([EE.getJSON("/api/v5/session?name=" + encodeURIComponent(name)), A.list ? null : loadList()]).then(function (r) {
        var s = r[0];
        if (EE.view !== "story") return;
        if (!s || !s.name) { EE.main.innerHTML = top("story") + '<div class="empty"><h1 class="display-md" style="margin: 0;">I can\'t find that session.</h1>' + EE.linkBtn("primary", "See every session", "#/sessions") + "</div>"; return; }
        A.story = s;
        A.who = s.participant;
        A.open = s.moments.length ? s.moments.length - 1 : -1;
        drawStory();
      }).catch(function () { EE.main.innerHTML = top("story") + '<p class="body" style="padding: 0 80px; color: var(--error);">The launcher did not answer.</p>'; });
    },
    act: function (act, el) {
      if (act === "moment") { A.open = parseInt(el.getAttribute("data-index"), 10); A.pop = -1; drawStory(); var w = $("#why"); if (w) w.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
      else if (act === "moment-prev" && A.open > 0) { A.open--; drawStory(); }
      else if (act === "moment-next" && A.story && A.open < A.story.moments.length - 1) { A.open++; drawStory(); }
      else if (act === "seg") { A.pop = parseInt(el.getAttribute("data-index"), 10); drawStory(); }
      else if (act === "pop-close") { A.pop = -1; drawStory(); }
    },
    change: function (e) { if (e.target.id === "story-pick") location.hash = "#/story/" + encodeURIComponent(e.target.value); }
  };

  function storyTitle(s) {
    if (!s.has_state || s.focused_share == null) return "A session without my eyes.";
    return s.focused_share >= 0.7 ? "A good session, mostly." : s.focused_share >= 0.5 ? "An okay session." : "A hard session.";
  }
  function storySentence(s) {
    var who = esc(s.participant), parts = [];
    if (s.has_state && s.focused_share != null) {
      var f = s.focused_share;
      parts.push(who + " was focused for " + (f >= 0.75 ? "most of it" : f >= 0.5 ? "more than half of it" : "less than half of it") + " (" + EE.pct(f) + " of the time at the desk).");
      var states = {}; (s.strip || []).forEach(function (r) { states[r.state] = (states[r.state] || 0) + (r.to - r.from); });
      if (states.distracted > 60) parts.push("They drifted for about " + Math.round(states.distracted / 60) + " min.");
      if ((states.drowsy || 0) + (states.very_drowsy || 0) > 60) parts.push("They got tired for about " + Math.round(((states.drowsy || 0) + (states.very_drowsy || 0)) / 60) + " min.");
      if (states.absent > 60) parts.push("They were away for about " + Math.round(states.absent / 60) + " min.");
    }
    if (s.ending === "rest") parts.push("I suggested a rest and they took it.");
    else if (s.ending === "suggested") parts.push("I suggested a rest.");
    var n = s.moments.length, good = s.moments.filter(function (m) { return m.outcome === "success"; }).length;
    parts.push(n ? "I stepped in " + n + (n === 1 ? " time" : " times") + (s.by_hand ? " (" + s.by_hand + " by you)" : "") + "; " + good + " worked." : "Nobody stepped in.");
    return parts.join(" ");
  }

  function drawStory() {
    var s = A.story;
    if (!s || EE.view !== "story") return;
    var span = Math.max(1, s.duration_s), n = s.moments.length, good = s.moments.filter(function (m) { return m.outcome === "success"; }).length;
    var sessions = (A.list || []).filter(function (x) { return x.participant === s.participant; });
    var picker = '<div style="width: 240px;">' + EE.select({ id: "story-pick", label: "Session", value: s.name, options: sessions.map(function (x) {
      return { value: x.name, label: dayWord(x) + ", " + x.clock + " · " + EE.minutes(x.duration_s) };
    }) }) + "</div>";
    var stat = function (k, v, unit) { return '<div class="stat2"><span class="caption" style="color: var(--ink-muted);">' + k + '</span><span class="display-md" style="font-variant-numeric: tabular-nums;">' + v + (unit ? '<span class="headline">' + unit + "</span>" : "") + "</span></div>"; };
    var curve = s.curve.length > 1 ? EE.focusCurve(s.curve.map(function (c) { return [c[0] / span, c[1], EE.addClock(s.clock, c[0])]; }), { height: 220, label: "Focus" })
      : '<div class="vizempty caption">My eyes weren\'t on in this session, so there is no focus curve.</div>';
    var circles = '<div class="circles" aria-label="Moments I stepped in">' + s.moments.map(function (m, i) {
      var c = CIRCLE[m.outcome], on = i === A.open, open = !m.outcome || m.outcome === "censored";
      var style = "left: " + Math.min(98.5, Math.max(1.5, m.t / span * 100)).toFixed(1) + "%;" + (c ? " background: " + c[0] + "; color: " + c[1] + ";" : open ? " border: 1px dashed var(--hairline-strong);" : " background: var(--surface-2); color: var(--ink-muted);");
      return '<button type="button" class="circle' + (on ? " is-on" : "") + '" data-act="moment" data-index="' + i + '" style="' + style + '" aria-label="Moment ' + (i + 1) + ": " + esc(EE.behaviorName(m.behavior)) + '">' + (i + 1) + "</button>";
    }).join("") + "</div>";
    var segs = (s.strip || []).map(function (r, i) { return [r, i]; }).filter(function (x) { return STRIP[x[0].state]; });
    var strip = '<div class="stripbar" aria-label="Other states">' + segs.map(function (x) {
      var r = x[0];
      return '<button type="button" class="seg" data-act="seg" data-index="' + x[1] + '" style="left: ' + (r.from / span * 100).toFixed(2) + "%; width: " + Math.max(0.5, (r.to - r.from) / span * 100).toFixed(2) + "%; background: " + STRIP[r.state] + ';" title="' + esc(EE.STATE_NAME[r.state] + " " + EE.addClock(s.clock, r.from) + " to " + EE.addClock(s.clock, r.to)) + '"></button>';
    }).join("") + "</div>";
    var leg = function (bg, text, round) { return '<span class="legi"><span style="width: ' + (round ? 10 : 12) + "px; height: " + (round ? 10 : 12) + "px; border-radius: " + (round ? "var(--radius-full)" : "var(--radius-xs)") + "; " + bg + '"></span>' + text + "</span>"; };
    var legend = '<div class="caption storyleg">' + leg("background: var(--energy-low);", "Distracted") + leg("background: " + STRIP.drowsy + ";", "Drowsy") + leg("background: var(--time-sleep);", "Very drowsy")
      + leg("background: " + STRIP.absent + ";", "Away from the desk") + '<span style="flex: 1;"></span>' + leg("background: var(--success);", "worked", 1) + leg("background: var(--time-break);", "snoozed", 1)
      + leg("background: var(--surface-2); border: 1px solid var(--hairline-strong); box-sizing: border-box;", "no reaction", 1) + leg("border: 1px dashed var(--hairline-strong); box-sizing: border-box;", "not counted", 1) + "</div>";
    var pop = "";
    if (A.pop >= 0 && s.strip[A.pop]) {
      var r = s.strip[A.pop];
      var inside = s.moments.filter(function (m) { return m.t >= r.from - 30 && m.t <= r.to + 30; });
      var mid = (r.from + r.to) / 2 / span * 100;
      pop = '<div class="spop" role="dialog" style="left: clamp(0px, calc(' + mid.toFixed(1) + '% - 200px), calc(100% - 400px));"><div style="display: flex; align-items: center; gap: var(--space-sm);"><img src="img/eepy-mark.svg" alt="" width="28" height="28">'
        + '<span style="flex: 1; display: flex; flex-direction: column;"><span class="caption" style="color: var(--ink-muted);">' + EE.addClock(s.clock, r.from) + " – " + EE.addClock(s.clock, r.to) + '</span><b class="body">' + esc(EE.STATE_NAME[r.state]) + " for about " + Math.max(1, Math.round((r.to - r.from) / 60)) + " min</b></span>"
        + '<button type="button" class="close" data-act="pop-close" aria-label="Close" title="Close">×</button></div>'
        + '<div class="popfacts"><div class="fact"><span>What I saw</span><span>' + esc(EE.STATE_NAME[r.state]) + "</span></div>"
        + '<div class="fact"><span>What I did</span><span>' + (inside.length ? inside.map(function (m) { return esc(EE.behaviorName(m.behavior)) + " (" + esc(m.clock) + ")"; }).join(", ") : r.state === "absent" ? "Nothing: I waited" : "Nothing") + "</span></div>"
        + '<div class="fact"><span>Result</span><span>' + (inside.length ? inside.map(function (m) { return esc(EE.OUTCOME[m.outcome] || "No verdict"); }).join(", ") : r.state === "absent" ? "Not counted against them" : "–") + "</span></div></div></div>";
    }
    var cards = n ? '<div class="momentgrid">' + s.moments.map(function (m, i) {
      var color = m.outcome === "success" ? "var(--success)" : "var(--ink-muted)";
      return '<button type="button" class="moment' + (i === A.open ? " is-on" : "") + '" data-act="moment" data-index="' + i + '"><span class="caption" style="color: var(--ink-muted);">' + (i + 1) + " · " + esc(m.clock) + (m.by === "you" ? " · you" : "") + "</span>"
        + '<span class="body-sm"><b>' + esc(EE.behaviorName(m.behavior)) + '</b></span><span class="caption" style="color: ' + color + ';">' + esc(EE.OUTCOME[m.outcome] || "No verdict yet") + "</span></button>";
    }).join("") + "</div>" : '<p class="body-sm" style="color: var(--ink-muted); margin: 0;">Nobody stepped in during this session.</p>';
    EE.main.innerHTML = top("story", picker)
      + '<div class="story"><header class="storyhead"><div style="display: flex; flex-direction: column; gap: var(--space-sm);"><span class="caption eyebrow">' + esc(s.participant) + " · " + esc(dayWord(s).toLowerCase()) + ", " + esc(s.clock) + " to " + esc(s.end_clock) + "</span>"
      + '<h1 class="display-md" style="margin: 0;">' + storyTitle(s) + '</h1><div class="bubble2"><img src="img/eepy-mark.svg" alt="" width="32" height="32"><span class="body">' + storySentence(s) + "</span></div></div>"
      + '<div class="stats2">' + stat("Focused", s.has_state && s.focused_share != null ? EE.pct(s.focused_share) : "–") + stat("Length", Math.round(s.duration_s / 60), " min") + stat("Worked", good, " / " + n) + "</div></header>"
      + '<div class="storyviz">' + EE.vizPanel("Focus, minute by minute:", s.has_state && s.focused_share != null ? EE.pct(s.focused_share) : "", "The curve is how sure I was that " + esc(s.participant) + " was focused. Circles are the moments I stepped in; the strip shows the other states.",
        curve + circles + '<div class="stripwrap">' + strip + pop + "</div>" + legend) + "</div>"
      + '<section style="display: flex; flex-direction: column; gap: var(--space-sm);"><div style="display: flex; align-items: baseline; gap: var(--space-sm);"><span class="headline">Moments I stepped in</span><span class="caption" style="color: var(--ink-muted);">Click any circle, coloured strip or card and I\'ll tell you what happened.</span></div>' + cards + "</section>"
      + (A.open >= 0 ? whyActed(s, s.moments[A.open], A.open) : "") + "</div>";
  }

  function lpills(m) {
    var count = m.ladder === "bedtime" ? 6 : 5, allowed = m.allowed_levels || [];
    var out = "";
    for (var i = 0; i < count; i++) {
      var chosen = i === m.level, ok = !allowed.length || allowed.indexOf(i) >= 0;
      out += '<span class="micro lp' + (chosen ? " is-on" : ok ? "" : " is-off") + '">' + i + "</span>";
    }
    return '<div class="lps" style="grid-template-columns: repeat(' + count + ', minmax(0, 1fr));">' + out + "</div>";
  }

  function whyActed(s, m, i) {
    if (!m) return "";
    var who = esc(s.participant);
    var thought = m.ladder === "bedtime" ? "tired" : "distracted";
    var need = EE.model && EE.model.act_confidence ? EE.model.act_confidence[m.ladder] : null;
    var conf = m.confidence;
    var card1 = '<div class="wcard"><div class="wcard__h"><span class="wcard__ic">' + icon("BookOpen", 16) + '</span><span class="caption" style="color: var(--ink-muted);">1 · What I thought</span></div>'
      + '<span class="headline">' + who + " is " + thought + "</span>" + '<span class="body-sm" style="color: var(--ink-muted);">' + (m.by === "you" ? "you chose this move yourself" : "rather than focused") + "</span>"
      + (conf != null ? '<div style="display: flex; flex-direction: column; gap: var(--space-xxs); margin-top: var(--space-xs);"><div class="body-sm" style="display: flex; justify-content: space-between;"><span style="color: var(--ink-muted);">How sure I was</span><b>' + EE.pct(conf) + "</b></div>"
        + '<span class="surebar surebar--8"><span style="width: ' + (conf * 100).toFixed(0) + '%; background: var(--time-sleep);"></span>' + (need != null ? '<span class="surebar__line" style="left: ' + (need * 100).toFixed(0) + '%;"></span>' : "") + "</span>"
        + (need != null ? '<span class="caption" style="color: var(--ink-muted);">I only act past the line (' + EE.pct(need) + ").</span>" : "") + "</div>"
        : '<span class="caption" style="color: var(--ink-muted); margin-top: var(--space-xs);">No confidence recorded for this moment.</span>') + "</div>";
    var signs = (m.items && m.items.length ? m.items.map(function (it) { return { text: EE.cap(it.type), w: it.share }; })
      : (m.evidence || []).map(function (e, k) { return { text: typeof e === "string" ? e : (e.text || e.feature || ""), w: 1 - k * 0.25 }; })).filter(function (x) { return x.text; });
    var maxW = signs.reduce(function (a, x) { return Math.max(a, x.w || 0); }, 0.0001);
    var card2 = '<div class="wcard"><div class="wcard__h"><span class="wcard__ic">' + icon("Search", 16) + '</span><span class="caption" style="color: var(--ink-muted);">2 · What gave it away</span></div>'
      + (signs.length ? signs.slice(0, 4).map(function (x) {
        return '<div style="display: flex; flex-direction: column; gap: 4px;"><span class="body-sm"><b>' + esc(x.text) + '</b></span><span class="wbar"><span style="width: ' + Math.max(6, (x.w || 0) / maxW * 100).toFixed(0) + '%; background: var(--time-sleep);"></span></span></div>';
      }).join("") + '<span class="caption" style="margin-top: auto; color: var(--ink-muted);">Longer bar = it counted more.</span>'
        : '<span class="body-sm" style="color: var(--ink-muted);">' + (m.by === "you" ? "You pressed it: I didn't need a reason." : "I didn't record the signs for this moment.") + "</span>") + "</div>";
    var arms = (m.arms || []).filter(function (a) { return a.arm !== "wait" && a.mean != null; }).sort(function (x, y) { return y.mean - x.mean; }).slice(0, 3);
    var best = arms.length && m.chosen && arms[0].arm === m.chosen;
    var card3 = '<div class="wcard"><div class="wcard__h"><span class="wcard__ic">' + icon("Search", 16) + '</span><span class="caption" style="color: var(--ink-muted);">3 · What I picked</span></div>'
      + (m.level != null ? lpills(m) : "") + '<span class="body-sm">' + esc(EE.behaviorName(m.behavior)) + "." + (best ? " For " + who + " it usually works best at this point." : m.by === "you" ? " You picked it." : "") + "</span>"
      + (arms.length ? '<div style="display: flex; flex-direction: column; gap: var(--space-xs);">' + arms.map(function (a) {
        var on = a.arm === m.chosen;
        return '<div class="caption armrow2">' + (on ? "<b>" : "<span>") + esc(EE.armName(a.arm)) + (on ? "</b>" : "</span>") + '<span class="wbar wbar--6"><span style="width: ' + Math.max(3, (a.mean + 1) / 2 * 100).toFixed(0) + "%; background: " + (on ? "linear-gradient(90deg, var(--time-focus-start), var(--time-focus))" : "var(--time-busy)") + ';"></span></span>' + (on ? "<b>" : "<span>") + (a.mean >= 0 ? "+" : "") + Number(a.mean).toFixed(2) + (on ? "</b>" : "</span>") + "</div>";
      }).join("") + "</div>" : "")
      + (m.propensity != null ? '<span class="caption" style="color: var(--ink-muted);">' + (m.allowed_levels && m.allowed_levels.length ? "Dashed = not allowed right now. " : "") + "Chance I'd pick this one: " + EE.pct(m.propensity) + ".</span>" : "") + "</div>";
    var outcomeTitle = { success: who + (m.how === "bed" ? " took a rest" : m.how === "break" ? " took a break" : m.how === "reply" ? " replied" : " got back to work"), no_reaction: "No reaction", dismissed: who + " dismissed it", snoozed: who + " snoozed it",
                         interrupted_focus: who + " was already focused", censored: "Not counted" }[m.outcome] || "No verdict yet";
    var card4 = '<div class="wcard"><div class="wcard__h"><span class="wcard__ic" style="color: ' + (m.outcome === "success" ? "var(--success)" : "var(--ink-muted)") + ';">' + icon(m.outcome === "success" ? "Check" : "CircleAlert", 16) + '</span><span class="caption" style="color: var(--ink-muted);">4 · What happened</span></div>'
      + '<span class="headline">' + outcomeTitle + '</span><span class="body-sm" style="color: var(--ink-muted);">' + (m.seconds != null ? "After " + Math.round(m.seconds) + " s." : "") + (m.detail ? " " + esc(EE.cap(m.detail)) + "." : "") + "</span>"
      + (m.reward != null ? '<div style="display: flex; flex-direction: column; gap: var(--space-xxs); margin-top: var(--space-xs);"><div class="body-sm" style="display: flex; justify-content: space-between;"><span style="color: var(--ink-muted);">How good that was for me</span><b>' + (m.reward >= 0 ? "+" : "") + Number(m.reward).toFixed(2) + "</b></div>"
        + '<span class="rewardbar"><span style="left: ' + Math.max(0, Math.min(100, (m.reward + 1) / 2 * 100)).toFixed(0) + '%;"></span></span><div class="caption" style="display: flex; justify-content: space-between; color: var(--ink-muted);"><span>bad</span><span>neutral</span><span>great</span></div></div>'
        : m.included === false ? '<span class="caption" style="margin-top: auto; color: var(--ink-muted);">Left out of my learning: I couldn\'t judge it fairly.</span>' : "") + "</div>";
    return '<section class="whysec" id="why"><div style="display: flex; align-items: center; gap: var(--space-md);"><span class="headline">Why I acted at ' + esc(m.clock) + "</span>"
      + (m.spoken ? '<span class="body-sm" style="color: var(--ink-muted);">I said <i>"' + esc(m.spoken) + '"</i></span>' : m.by === "you" ? '<span class="body-sm" style="color: var(--ink-muted);">You pressed it.</span>' : "")
      + '<div style="flex: 1;"></div>' + EE.iconBtn("Previous moment", "ChevronLeft", 'data-act="moment-prev"' + (i <= 0 ? " disabled" : "")) + EE.iconBtn("Next moment", "ChevronRight", 'data-act="moment-next"' + (i >= s.moments.length - 1 ? " disabled" : "")) + "</div>"
      + (m.sentence ? '<p class="body" style="margin: 0;">' + esc(m.sentence) + (m.contrast ? ' <span style="color: var(--ink-muted);">' + esc(m.contrast) + "</span>" : "") + "</p>" : "")
      + '<div class="wgrid">' + card1 + card2 + card3 + card4 + "</div></section>";
  }

  // --- What I learned (V5-A3-Learned) --------------------------------------------------------------

  var BUCKET = {
    bedtime_early: ["Tired, before 23:00", "gets sleepy", "BedDouble"], bedtime_late: ["Tired, after 23:00", "gets sleepy late at night", "BedDouble"],
    refocus_early: ["Distracted, early on", "drifts off", "Zap"], refocus_late: ["Distracted, after 45 min", "has worked a long time", "Coffee"],
    ambiguous: ["Unclear moments", "is drifting or tired, I can't tell which", "Search"]
  };
  var ORDER = ["bedtime_early", "refocus_early", "refocus_late", "bedtime_late", "ambiguous"];
  function effect(mean, n) {
    if (!n) return ["not tried yet", "var(--ink-muted)"];
    return mean < -0.1 ? ["makes it worse", "var(--energy-low)"] : mean < 0.15 ? ["about no effect", "var(--ink)"] : mean < 0.4 ? ["helps a little", "var(--ink)"] : ["helps a lot", "var(--ink)"];
  }

  EE.screens.learned = {
    render: function () {
      EE.main.innerHTML = top("learned") + '<p class="body" style="padding: var(--space-lg) 80px; color: var(--ink-muted);">Looking at what I learned…</p>';
      Promise.all([EE.post("/api/bandit/run", { kind: "list" }), A.list ? null : loadList()]).then(function (r) {
        var codes = (A.sessionCodes || []).slice();
        ((r[0] && r[0].participants) || []).forEach(function (p) { if (codes.indexOf(p.participant) < 0) codes.push(p.participant); });
        codes.sort();
        A.codes = codes;
        A.listMsg = r[0] && r[0].ok === false ? r[0].message : "";
        if (!A.who || codes.indexOf(A.who) < 0) A.who = codes[0] || "";
        loadLearned();
      });
    },
    act: function (act, el) {
      if (act === "bucket") { A.bucket = el.getAttribute("data-value"); drawLearned(); }
      else if (act === "forget-open") {
        EE.openModal(EE.modal("Forget what I learned about " + esc(A.who) + "?", "I go back to my first guesses for them. Their recorded sessions stay; only what I learned from them is cleared.", "",
          EE.btn("secondary", "Keep it", 'data-act="modal-close" style="background: var(--surface-2);"') + EE.btn("primary", "Forget", 'data-act="forget-confirm"'), "img/eepy-scientist.svg"));
      } else if (act === "forget-confirm") {
        el.disabled = true;
        EE.post("/api/bandit/run", { kind: "reset", participant: A.who }).then(function (res) {
          EE.closeModal();
          A.msg = res.ok ? "Done: I'm back to my first guesses for " + A.who + "." : (res.message || "That didn't work.");
          loadLearned();
          setTimeout(function () { A.msg = ""; }, 8000);
        });
      }
    },
    change: function (e) { if (e.target.id === "who") { A.who = e.target.value; loadLearned(); } }
  };

  function loadLearned() {
    if (EE.view !== "learned") return;
    if (!A.who) {
      EE.main.innerHTML = top("learned") + '<div class="empty"><img src="img/eepy-scientist.svg" alt=""><h1 class="display-md" style="margin: 0;">Nobody to learn about yet.</h1><p class="body-lg" style="color: var(--ink-muted); margin: 0;">' + esc(A.listMsg || "I learn in the “I estimate and learn” mode, one participant at a time.") + "</p></div>";
      return;
    }
    A.learned = null;
    EE.main.innerHTML = top("learned", whoPicker(A.codes, A.who)) + '<p class="body" style="padding: var(--space-lg) 80px; color: var(--ink-muted);">Working it out…</p>';
    EE.post("/api/bandit/run", { kind: "show", participant: A.who }).then(function (res) {
      if (EE.view !== "learned") return;
      A.learned = res;
      drawLearned();
    });
  }

  function drawLearned() {
    var L = A.learned, who = esc(A.who);
    if (!L || EE.view !== "learned") return;
    if (!L.ok) { EE.main.innerHTML = top("learned", whoPicker(A.codes, A.who)) + '<p class="body" style="padding: var(--space-lg) 80px; color: var(--error);">' + esc(L.message || "I couldn't read what I learned.") + "</p>"; return; }
    var buckets = L.buckets || {};
    var tries = function (k) { return (buckets[k] || []).reduce(function (sum, a) { return sum + (a.arm === "wait" ? 0 : a.n || 0); }, 0); };
    var keys = ORDER.filter(function (k) { return buckets[k]; });
    var shown = keys.slice().sort(function (x, y) { return tries(y) - tries(x) || ORDER.indexOf(x) - ORDER.indexOf(y); }).slice(0, 3);
    shown.sort(function (x, y) { return ORDER.indexOf(x) - ORDER.indexOf(y); });
    if (!A.bucket || keys.indexOf(A.bucket) < 0) A.bucket = shown[0];
    var moves = function (k) { return (buckets[k] || []).filter(function (a) { return a.arm !== "wait"; }); };
    var cards = shown.map(function (k) {
      var best = moves(k).slice().sort(function (x, y) { return y.mean - x.mean; })[0] || {};
      var t = tries(k), sure = EE.sure(best.n || 0), eff = effect(best.mean, best.n || t);
      return '<a href="#" class="sit' + (k === A.bucket ? " is-on" : "") + '" data-act="bucket" data-value="' + k + '"><div style="display: flex; align-items: center; gap: var(--space-sm);"><span class="sic">' + icon(BUCKET[k][2], 20) + "</span>"
        + '<span style="display: flex; flex-direction: column;"><b class="body">' + BUCKET[k][0] + '</b><span class="caption" style="color: var(--ink-muted);">when ' + who + " " + BUCKET[k][1] + " · " + Math.round(t) + (t === 1 ? " try" : " tries") + "</span></span></div>"
        + '<span class="caption" style="color: var(--ink-muted); margin-top: var(--space-xs);">' + (t ? "What works best" : "My first guess") + '</span><span class="headline">' + esc(EE.armName(best.arm, best.words)) + "</span>"
        + '<div class="body-sm" style="display: flex; align-items: center; gap: var(--space-sm);"><span style="color: ' + (t ? "var(--success)" : "var(--ink-muted)") + '; font-weight: 700;">' + (t ? eff[0] : "not tried yet") + '</span><span style="color: var(--ink-muted);">·</span>' + sure.html + '<span style="color: var(--ink-muted);">' + sure.words + "</span></div></a>";
    }).join("");
    var list = moves(A.bucket).slice().sort(function (x, y) { return y.mean - x.mean; });
    var bestArm = list.filter(function (a) { return a.n; })[0];
    var rows = list.map(function (a) {
      var sure = EE.sure(a.n || 0), eff = effect(a.mean, a.n), isBest = bestArm && a.arm === bestArm.arm;
      var m = Math.max(-1, Math.min(1, a.mean)), w = Math.abs(m) * 50;
      var bar = !a.n ? '<span class="mbar__open"></span>'
        : '<span class="mbar__fill" style="' + (m >= 0 ? "left: 50%;" : "right: 50%;") + " width: " + w.toFixed(1) + "%; background: " + (m >= 0 ? "var(--success)" : "var(--energy-low)") + ";" + (isBest ? " box-shadow: 0 0 0 2px var(--surface-1), 0 0 0 3px var(--success);" : "") + '"></span>';
      return '<div class="mrow"><span class="body-sm" style="display: flex; align-items: center; gap: var(--space-xs);">' + (isBest ? "<b>" + esc(EE.armName(a.arm, a.words)) + '</b><span class="micro bestchip">best</span>' : esc(EE.armName(a.arm, a.words))) + "</span>"
        + '<span class="mbar"><span class="mbar__mid"></span>' + bar + "</span>"
        + '<span class="body-sm" style="color: ' + eff[1] + ';">' + eff[0] + "</span>"
        + '<span class="caption" style="display: flex; align-items: center; gap: var(--space-xs); color: var(--ink-muted);">' + sure.html + sure.words + (a.n ? " · " + Math.round(a.n) + (Math.round(a.n) === 1 ? " try" : " tries") : "") + "</span></div>";
    }).join("");
    var empty = keys.filter(function (k) { return !tries(k); });
    var others = keys.filter(function (k) { return shown.indexOf(k) < 0; });
    EE.main.innerHTML = top("learned", whoPicker(A.codes, A.who))
      + '<div class="learned"><header class="lhead"><img src="img/eepy-scientist.svg" alt=""><div style="display: flex; flex-direction: column; gap: var(--space-xs);"><h1 class="display-md" style="margin: 0;">What works for ' + who + '</h1><span class="body-lg" style="color: var(--ink-muted);">'
      + (L.updates ? "I tried my moves in different kinds of moments. Pick one to see every move I compared." : "I haven't learned anything about " + who + " yet: these are my first guesses. I learn in the “I estimate and learn” mode.") + "</span></div></header>"
      + '<div class="sits">' + cards + "</div>"
      + (others.length ? '<div class="caption" style="display: flex; gap: var(--space-xs); align-items: center; flex-wrap: wrap; color: var(--ink-muted);">Also: ' + others.map(function (k) { return '<button type="button" class="chip caption' + (k === A.bucket ? " is-on" : "") + '" data-act="bucket" data-value="' + k + '">' + BUCKET[k][0] + "</button>"; }).join("") + "</div>" : "")
      + '<section class="card lcard"><div style="display: flex; align-items: baseline; gap: var(--space-sm);"><span class="headline" style="flex: 1;">' + (BUCKET[A.bucket] || [A.bucket])[0] + ': every move I tried</span><span class="caption" style="color: var(--ink-muted);">' + Math.round(tries(A.bucket)) + " tries so far</span></div>"
      + '<div class="mrow caption" style="color: var(--ink-muted); padding-bottom: 0;"><span>Move</span><span style="display: flex; justify-content: space-between;"><span>← makes it worse</span><span>helps →</span></span><span>Effect</span><span>How sure I am</span></div>'
      + rows + '<span class="caption" style="color: var(--ink-muted);">Waiting and doing nothing counts as a bad result for me: I only wait when no move is allowed right now.</span></section></div>'
      + '<footer class="stepbar"><span class="body-sm learnnote"><span class="learnnote__ic">' + icon("CircleAlert", 20) + "</span><span>" + esc(A.msg)
      + (A.msg ? "" : empty.length === keys.length ? "Nothing learned yet for " + who + ": everywhere I use my first guesses."
        : empty.length ? "Nothing learned yet for " + empty.map(function (k) { return "<b style=\"color: var(--ink);\">" + BUCKET[k][0].toLowerCase() + "</b>"; }).join(" and ") + ": there I use my first guesses."
        : "I've tried moves in every kind of moment.") + "</span></span>"
      + '<div style="flex: 1;"></div>' + EE.btn("secondary", "Forget what I learned", 'data-act="forget-open"' + (L.updates ? "" : " disabled"), "X", "start") + "</footer>";
  }
})();
