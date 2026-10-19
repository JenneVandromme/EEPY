/* The /v5 page, last part: read the configured model once (HMM, thresholds, line words), then start. */
(function () {
  "use strict";
  var EE = window.EE;
  EE.model = null;
  EE.getJSON("/api/v5/model").then(function (m) {
    if (m && m.states) { EE.model = m; var s = EE.screens[EE.view]; if (s && s.panelUpdate) s.panelUpdate(); }
  }).catch(function () { /* an older launcher: the words fall back to the button labels */ });
  EE.start();
})();
