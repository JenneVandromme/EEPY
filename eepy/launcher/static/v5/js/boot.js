/* The /v5 page, last part: read the configured model once (HMM, thresholds, line words), then start. */
(function () {
  "use strict";
  var EE = window.EE;
  EE.model = null;
  EE.getJSON("/api/v5/model").then(function (m) {
    if (m && m.states) { EE.model = m; var s = EE.screens[EE.view]; if (s && s.panelUpdate) s.panelUpdate(); }
  }).catch(function () { /* an older launcher: the words fall back to the button labels */ });
  // Decode the big illustrations once, up front: decoding the 440 KB builder drawing the moment Waking up
  // appears made its loading ring stutter.
  ["img/eepy-builder.svg", "img/eepy-wave.png", "img/eepy-scientist.svg", "img/eepy-mark.svg"].forEach(function (src) {
    var img = new Image();
    img.decoding = "async";
    img.src = src;
    if (img.decode) img.decode().catch(function () { /* shown anyway when needed */ });
    (EE.preloaded = EE.preloaded || []).push(img);
  });
  EE.start();
})();
