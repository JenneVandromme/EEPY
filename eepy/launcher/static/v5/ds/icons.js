/* EEPY design system icons, for the /v5 page without React.
   The icon data is copied from the design system's bundle.js: a subset of Lucide v0.460.0
   (ISC License, (c) Lucide Contributors). icon("Check", 16) returns an <svg> string. */
(function () {
  var ICONS = {
    ArrowRight: [['path', { d: 'M5 12h14' }], ['path', { d: 'm12 5 7 7-7 7' }]],
    Plus: [['path', { d: 'M5 12h14' }], ['path', { d: 'M12 5v14' }]],
    ChevronLeft: [['path', { d: 'm15 18-6-6 6-6' }]],
    ChevronRight: [['path', { d: 'm9 18 6-6-6-6' }]],
    Search: [['circle', { cx: '11', cy: '11', r: '8' }], ['path', { d: 'm21 21-4.3-4.3' }]],
    X: [['path', { d: 'M18 6 6 18' }], ['path', { d: 'm6 6 12 12' }]],
    Check: [['path', { d: 'M20 6 9 17l-5-5' }]],
    CircleAlert: [['circle', { cx: '12', cy: '12', r: '10' }], ['line', { x1: '12', x2: '12', y1: '8', y2: '12' }], ['line', { x1: '12', x2: '12.01', y1: '16', y2: '16' }]],
    Mail: [['rect', { width: '20', height: '16', x: '2', y: '4', rx: '2' }], ['path', { d: 'm22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7' }]],
    Sparkles: [['path', { d: 'M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z' }], ['path', { d: 'M20 3v4' }], ['path', { d: 'M22 5h-4' }], ['path', { d: 'M4 17v2' }], ['path', { d: 'M5 18H3' }]],
    Play: [['polygon', { points: '6 3 20 12 6 21 6 3' }]],
    Menu: [['line', { x1: '4', x2: '20', y1: '12', y2: '12' }], ['line', { x1: '4', x2: '20', y1: '6', y2: '6' }], ['line', { x1: '4', x2: '20', y1: '18', y2: '18' }]],
    AlarmClock: [["circle", {"cx": "12", "cy": "13", "r": "8"}], ["path", {"d": "M12 9v4l2 2"}], ["path", {"d": "M5 3 2 6"}], ["path", {"d": "m22 6-3-3"}], ["path", {"d": "M6.38 18.7 4 21"}], ["path", {"d": "M17.64 18.67 20 21"}]],
    BedDouble: [["path", {"d": "M2 20v-8a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v8"}], ["path", {"d": "M4 10V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v4"}], ["path", {"d": "M12 4v6"}], ["path", {"d": "M2 18h20"}]],
    Briefcase: [["path", {"d": "M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"}], ["rect", {"width": "20", "height": "14", "x": "2", "y": "6", "rx": "2"}]],
    UtensilsCrossed: [["path", {"d": "m16 2-2.3 2.3a3 3 0 0 0 0 4.2l1.8 1.8a3 3 0 0 0 4.2 0L22 8"}], ["path", {"d": "M15 15 3.3 3.3a4.2 4.2 0 0 0 0 6l7.3 7.3c.7.7 2 .7 2.8 0L15 15Zm0 0 7 7"}], ["path", {"d": "m2.1 21.8 6.4-6.3"}], ["path", {"d": "m19 5-7 7"}]],
    Dumbbell: [["path", {"d": "M14.4 14.4 9.6 9.6"}], ["path", {"d": "M18.657 21.485a2 2 0 1 1-2.829-2.828l-1.767 1.768a2 2 0 1 1-2.829-2.829l6.364-6.364a2 2 0 1 1 2.829 2.829l-1.768 1.767a2 2 0 1 1 2.828 2.829z"}], ["path", {"d": "m21.5 21.5-1.4-1.4"}], ["path", {"d": "M3.9 3.9 2.5 2.5"}], ["path", {"d": "M6.404 12.768a2 2 0 1 1-2.829-2.829l1.768-1.767a2 2 0 1 1-2.828-2.829l2.828-2.828a2 2 0 1 1 2.829 2.828l1.767-1.768a2 2 0 1 1 2.829 2.829z"}]],
    Zap: [["path", {"d": "M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"}]],
    BookOpen: [["path", {"d": "M12 7v14"}], ["path", {"d": "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"}]],
    Coffee: [["path", {"d": "M10 2v2"}], ["path", {"d": "M14 2v2"}], ["path", {"d": "M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"}], ["path", {"d": "M6 2v2"}]],
    Bus: [["path", {"d": "M8 6v6"}], ["path", {"d": "M15 6v6"}], ["path", {"d": "M2 12h19.6"}], ["path", {"d": "M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3"}], ["circle", {"cx": "7", "cy": "18", "r": "2"}], ["path", {"d": "M9 18h5"}], ["circle", {"cx": "16", "cy": "18", "r": "2"}]],
    GraduationCap: [["path", {"d": "M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0z"}], ["path", {"d": "M22 10v6"}], ["path", {"d": "M6 12.5V16a6 3 0 0 0 12 0v-3.5"}]],
    Brain: [["path", {"d": "M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"}], ["path", {"d": "M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"}], ["path", {"d": "M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"}], ["path", {"d": "M17.599 6.5a3 3 0 0 0 .399-1.375"}], ["path", {"d": "M6.003 5.125A3 3 0 0 0 6.401 6.5"}], ["path", {"d": "M3.477 10.896a4 4 0 0 1 .585-.396"}], ["path", {"d": "M19.938 10.5a4 4 0 0 1 .585.396"}], ["path", {"d": "M6 18a4 4 0 0 1-1.967-.516"}], ["path", {"d": "M19.967 17.484A4 4 0 0 1 18 18"}]],
    Cookie: [["path", {"d": "M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5"}], ["path", {"d": "M8.5 8.5v.01"}], ["path", {"d": "M16 15.5v.01"}], ["path", {"d": "M12 12v.01"}], ["path", {"d": "M11 17v.01"}], ["path", {"d": "M7 14v.01"}]],
    Music: [["path", {"d": "M9 18V5l12-2v13"}], ["circle", {"cx": "6", "cy": "18", "r": "3"}], ["circle", {"cx": "18", "cy": "16", "r": "3"}]],
    Footprints: [["path", {"d": "M4 16v-2.38C4 11.5 2.97 10.5 3 8c.03-2.72 1.49-6 4.5-6C9.37 2 10 3.8 10 5.5c0 3.11-2 5.66-2 8.68V16a2 2 0 1 1-4 0Z"}], ["path", {"d": "M20 20v-2.38c0-2.12 1.03-3.12 1-5.62-.03-2.72-1.49-6-4.5-6C14.63 6 14 7.8 14 9.5c0 3.11 2 5.66 2 8.68V20a2 2 0 1 0 4 0Z"}], ["path", {"d": "M16 17h4"}], ["path", {"d": "M4 13h4"}]],
    Pause: [["rect", {"x": "14", "y": "4", "width": "4", "height": "16", "rx": "1"}], ["rect", {"x": "6", "y": "4", "width": "4", "height": "16", "rx": "1"}]],
    ChevronDown: [["path", {"d": "m6 9 6 6 6-6"}]]
  };
  function attrs(o) {
    var s = "";
    for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) s += " " + k + '="' + o[k] + '"';
    return s;
  }
  window.icon = function (name, size, label) {
    var nodes = ICONS[name] || [];
    size = size || 20;
    var a11y = label ? ' role="img" aria-label="' + label + '"' : ' aria-hidden="true"';
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false" class="ds-icon"' + a11y + '>'
      + nodes.map(function (n) { return "<" + n[0] + attrs(n[1]) + "></" + n[0] + ">"; }).join("") + "</svg>";
  };
  window.icon.names = Object.keys(ICONS);
})();
