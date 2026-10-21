/* @ds-bundle: {"format":4,"namespace":"DesignSystem","components":[{"name":"Button"},{"name":"IconButton"},{"name":"PricingTabs"},{"name":"TextInput"},{"name":"Icon"},{"name":"Badge"},{"name":"PricingCard"},{"name":"TemplateCard"},{"name":"MockupTile"},{"name":"SpotlightCard"},{"name":"TopNav"},{"name":"Footer"},{"name":"ComparisonTable"},{"name":"Accordion"},{"name":"Checkbox"},{"name":"RadioGroup"},{"name":"Switch"},{"name":"Select"},{"name":"Textarea"},{"name":"Toast"},{"name":"Modal"},{"name":"Tooltip"},{"name":"VizPanel"},{"name":"DayTimeline"},{"name":"WeekTimeline"},{"name":"FocusCurve"}]} */
/* Icons: a subset of Lucide v0.460.0 (ISC License, (c) Lucide Contributors), icon nodes copied verbatim from the published package. */
(function () {
  var React = window.React;
  var h = React.createElement;

  function cx() {
    var out = [];
    for (var i = 0; i < arguments.length; i++) if (arguments[i]) out.push(arguments[i]);
    return out.join(' ');
  }
  function omit(obj, keys) {
    var out = {};
    for (var k in obj) if (Object.prototype.hasOwnProperty.call(obj, k) && keys.indexOf(k) < 0) out[k] = obj[k];
    return out;
  }

  /* ---- Icon ---- */
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
  function pascal(name) {
    return String(name || '').replace(/(^|[-_ ])([a-z0-9])/g, function (_, __, c) { return c.toUpperCase(); });
  }
  function iconNode(name) {
    var key = pascal(name);
    if (ICONS[key]) return ICONS[key];
    var lib = window.lucide && window.lucide.icons && window.lucide.icons[key];
    return lib ? lib[2] : null; /* full Lucide UMD: ['svg', attrs, children] */
  }
  function Icon(props) {
    var size = props.size || 20;
    var node = iconNode(props.name);
    var a11y = props.label ? { role: 'img', 'aria-label': props.label } : { 'aria-hidden': 'true' };
    return h('svg', Object.assign({
      xmlns: 'http://www.w3.org/2000/svg', width: size, height: size, viewBox: '0 0 24 24',
      fill: 'none', stroke: 'currentColor', strokeWidth: props.strokeWidth || 2,
      strokeLinecap: 'round', strokeLinejoin: 'round', focusable: 'false',
      className: cx('ds-icon', props.className)
    }, a11y), (node || []).map(function (n, i) { return h(n[0], Object.assign({ key: i }, n[1])); }));
  }
  Icon.names = Object.keys(ICONS);

  /* ---- Button ---- */
  function Button(props) {
    var variant = props.variant || 'primary';
    var icon = props.icon, pos = props.iconPosition || 'end';
    var rest = omit(props, ['variant', 'icon', 'iconPosition', 'className', 'children', 'type']);
    var glyph = icon ? h(Icon, { name: icon, size: 16 }) : null;
    return h('button', Object.assign({ type: props.type || 'button' }, rest, {
      className: cx('ds-btn', 'ds-btn--' + variant, props.className)
    }), pos === 'start' ? glyph : null, h('span', { className: 'ds-btn__label' }, props.children), pos === 'end' ? glyph : null);
  }

  /* ---- IconButton ---- */
  function IconButton(props) {
    var rest = omit(props, ['icon', 'label', 'className', 'children', 'type']);
    return h('button', Object.assign({ type: props.type || 'button', 'aria-label': props.label, title: props.label }, rest, {
      className: cx('ds-iconbtn', props.className)
    }), props.children || h(Icon, { name: props.icon, size: 18 }));
  }

  /* ---- PricingTabs ---- */
  function PricingTabs(props) {
    var items = props.items || [];
    var controlled = props.value !== undefined;
    var st = React.useState(props.defaultValue !== undefined ? props.defaultValue : (items[0] && items[0].value));
    var current = controlled ? props.value : st[0];
    var refs = React.useRef([]);
    function select(v) {
      if (!controlled) st[1](v);
      if (props.onChange) props.onChange(v);
    }
    function enabledIndexes() {
      var out = [];
      items.forEach(function (it, i) { if (!it.disabled) out.push(i); });
      return out;
    }
    function onKey(e, i) {
      var en = enabledIndexes(), p = en.indexOf(i), next = null;
      if (e.key === 'ArrowRight') next = en[(p + 1) % en.length];
      else if (e.key === 'ArrowLeft') next = en[(p - 1 + en.length) % en.length];
      else if (e.key === 'Home') next = en[0];
      else if (e.key === 'End') next = en[en.length - 1];
      if (next === null || next === undefined) return;
      e.preventDefault();
      select(items[next].value);
      if (refs.current[next]) refs.current[next].focus();
    }
    return h('div', { role: 'tablist', 'aria-label': props.label || 'Plans', className: cx('ds-tabs', props.className) },
      items.map(function (it, i) {
        var sel = it.value === current;
        return h('button', {
          key: it.value, type: 'button', role: 'tab', id: it.id,
          'aria-selected': sel ? 'true' : 'false', 'aria-controls': it.controls,
          tabIndex: sel ? 0 : -1, disabled: it.disabled, 'data-state': it.state,
          ref: function (el) { refs.current[i] = el; },
          className: cx('ds-tab', sel && 'is-selected'),
          onClick: function () { select(it.value); },
          onKeyDown: function (e) { onKey(e, i); }
        }, it.label);
      }));
  }

  /* ---- TextInput ---- */
  var uid = 0;
  function TextInput(props) {
    var idRef = React.useRef(null);
    if (!idRef.current) idRef.current = props.id || ('ds-input-' + (++uid));
    var id = idRef.current;
    var hasError = !!props.error;
    var msgId = id + '-msg';
    var rest = omit(props, ['label', 'hint', 'error', 'icon', 'className', 'id', 'type']);
    return h('div', { className: cx('ds-field', hasError && 'is-error', props.disabled && 'is-disabled', props.className) },
      props.label ? h('label', { className: 'ds-field__label', htmlFor: id }, props.label) : null,
      h('div', { className: 'ds-field__control' },
        props.icon ? h(Icon, { name: props.icon, size: 16, className: 'ds-field__icon' }) : null,
        h('input', Object.assign({ type: props.type || 'text' }, rest, {
          id: id, className: cx('ds-input', props.icon && 'has-icon'),
          'aria-invalid': hasError ? 'true' : undefined,
          'aria-describedby': (hasError || props.hint) ? msgId : undefined
        }))),
      hasError
        ? h('p', { id: msgId, className: 'ds-field__msg ds-field__msg--error' }, h(Icon, { name: 'CircleAlert', size: 14 }), h('span', null, props.error))
        : props.hint ? h('p', { id: msgId, className: 'ds-field__msg' }, props.hint) : null);
  }

  /* ================= Batch 2 ================= */
  function srOnly(text) { return h('span', { className: 'ds-sr' }, text); }

  /* ---- Badge ---- */
  function Badge(props) {
    var tone = props.tone || 'neutral';
    return h('span', { className: cx('ds-badge', 'ds-badge--' + tone, props.className) },
      tone !== 'neutral' ? h('span', { className: 'ds-badge__dot', 'aria-hidden': 'true' }) : null, props.children);
  }

  /* ---- PricingCard ---- */
  function PricingCard(props) {
    var cta = props.cta || {};
    return h('article', { className: cx('ds-card', 'ds-pricing', props.featured && 'is-featured', props.className), 'aria-label': props.name },
      h('div', { className: 'ds-pricing__head' },
        h('h3', { className: 'ds-pricing__name' }, props.name),
        props.badge ? h(Badge, { tone: 'accent' }, props.badge) : null),
      h('p', { className: 'ds-pricing__price' }, h('span', { className: 'ds-pricing__amount' }, props.price),
        props.period ? h('span', { className: 'ds-pricing__period' }, ' ' + props.period) : null),
      props.description ? h('p', { className: 'ds-pricing__desc' }, props.description) : null,
      cta.label ? h(Button, { variant: props.featured ? 'primary' : 'secondary', onClick: cta.onClick, className: 'ds-pricing__cta' }, cta.label) : null,
      (props.features || []).length ? h('ul', { className: 'ds-pricing__features' },
        props.features.map(function (f, i) { return h('li', { key: i }, h(Icon, { name: 'Check', size: 16 }), h('span', null, f)); })) : null);
  }

  /* ---- TemplateCard ---- */
  function TemplateCard(props) {
    var Tag = props.href ? 'a' : (props.onClick ? 'button' : 'div');
    var extra = props.href ? { href: props.href } : (props.onClick ? { type: 'button', onClick: props.onClick } : {});
    return h(Tag, Object.assign({ className: cx('ds-card', 'ds-template', (props.href || props.onClick) && 'is-interactive', props.className) }, extra),
      h('div', { className: 'ds-template__media' + (props.contain ? ' is-contain' : '') }, props.image ? h('img', { src: props.image, alt: props.alt || '', loading: 'lazy' }) : null),
      h('div', { className: 'ds-template__body' },
        h('span', { className: 'ds-template__title' }, props.title),
        props.meta ? h('span', { className: 'ds-template__meta' }, props.meta) : null));
  }

  /* ---- MockupTile ---- */
  function MockupTile(props) {
    return h('figure', { className: cx('ds-card', 'ds-mockup', props.float && 'is-float', props.className) },
      props.image ? h('img', { className: 'ds-mockup__img', src: props.image, alt: props.alt || '', style: props.ratio ? { aspectRatio: props.ratio } : null }) : props.children,
      props.caption ? h('figcaption', { className: 'ds-mockup__caption' }, props.caption) : null);
  }

  /* ---- SpotlightCard ---- */
  function SpotlightCard(props) {
    var tone = props.tone || 'violet';
    var smallTextOk = tone === 'violet' || tone === 'orange';
    var action = props.action;
    return h('section', { className: cx('ds-spotlight', 'ds-spotlight--' + tone, props.wide && 'is-wide', props.className) },
      h('div', { className: 'ds-spotlight__text' },
        props.eyebrow && smallTextOk ? h('p', { className: 'ds-spotlight__eyebrow' }, props.eyebrow) : null,
        h('h3', { className: 'ds-spotlight__title' }, props.title),
        props.body ? h('p', { className: 'ds-spotlight__body' }, props.body) : null,
        action ? h(Button, { variant: 'primary', icon: 'ArrowRight', onClick: action.onClick, className: 'ds-spotlight__action' }, action.label) : null),
      props.image ? h('img', { className: 'ds-spotlight__img', src: props.image, alt: props.alt || '' }) : null);
  }

  /* ---- TopNav ---- */
  var navUid = 0;
  function TopNav(props) {
    var st = React.useState(false), open = st[0], setOpen = st[1];
    var idRef = React.useRef('ds-nav-' + (++navUid));
    var links = props.links || [];
    function linkEls(cls) {
      return links.map(function (l, i) {
        return h('a', { key: i, href: l.href || '#', className: cls, 'aria-current': l.current ? 'page' : undefined, onClick: l.onClick }, l.label);
      });
    }
    return h('header', { className: cx('ds-nav', props.compact && 'is-compact', open && 'is-open', props.className) },
      h('div', { className: 'ds-nav__bar' },
        h('div', { className: 'ds-nav__brand' }, props.brand),
        h('nav', { className: 'ds-nav__links', 'aria-label': 'Main' }, linkEls('ds-nav__link')),
        h('div', { className: 'ds-nav__actions' },
          props.secondaryAction ? h(Button, { variant: 'secondary', className: 'ds-nav__secondary', onClick: props.secondaryAction.onClick }, props.secondaryAction.label) : null,
          props.primaryAction ? h(Button, { variant: 'primary', onClick: props.primaryAction.onClick }, props.primaryAction.label) : null,
          h(IconButton, { className: 'ds-nav__toggle', icon: open ? 'X' : 'Menu', label: open ? 'Close menu' : 'Open menu', 'aria-expanded': open ? 'true' : 'false', 'aria-controls': idRef.current, onClick: function () { setOpen(!open); } }))),
      h('nav', { id: idRef.current, className: 'ds-nav__panel', 'aria-label': 'Main', hidden: !open },
        linkEls('ds-nav__panel-link'),
        props.secondaryAction ? h(Button, { variant: 'secondary', onClick: props.secondaryAction.onClick }, props.secondaryAction.label) : null));
  }

  /* ---- Footer ---- */
  function Footer(props) {
    return h('footer', { className: cx('ds-footer', props.className) },
      h('div', { className: 'ds-footer__brand' }, props.brand, props.tagline ? h('p', { className: 'ds-footer__tagline' }, props.tagline) : null),
      (props.columns || []).map(function (col, i) {
        return h('nav', { key: i, className: 'ds-footer__col', 'aria-label': col.title },
          h('h2', { className: 'ds-footer__title' }, col.title),
          h('ul', null, col.links.map(function (l, j) { return h('li', { key: j }, h('a', { href: l.href || '#' }, l.label)); })));
      }),
      props.legal ? h('p', { className: 'ds-footer__legal' }, props.legal) : null);
  }

  /* ---- ComparisonTable ---- */
  function ComparisonTable(props) {
    var plans = props.plans || [];
    function cell(v) {
      if (v === true) return h(React.Fragment, null, h(Icon, { name: 'Check', size: 16, className: 'ds-compare__yes' }), srOnly('Included'));
      if (v === false || v == null) return h(React.Fragment, null, h('span', { 'aria-hidden': 'true', className: 'ds-compare__no' }, '—'), srOnly('Not included'));
      return v;
    }
    return h('div', { className: cx('ds-compare', props.className), role: 'region', 'aria-label': props.caption || 'Plan comparison', tabIndex: 0 },
      h('table', null,
        props.caption ? h('caption', { className: 'ds-sr' }, props.caption) : null,
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, props.featureLabel || 'Features'),
          plans.map(function (p, i) { return h('th', { key: i, scope: 'col', className: i === props.featured ? 'is-featured' : undefined }, p); }))),
        (props.groups || [{ rows: props.rows || [] }]).map(function (g, gi) {
          return h('tbody', { key: gi },
            g.title ? h('tr', { className: 'ds-compare__group' }, h('th', { scope: 'colgroup', colSpan: plans.length + 1 }, g.title)) : null,
            g.rows.map(function (r, ri) {
              return h('tr', { key: ri }, h('th', { scope: 'row' }, r.feature),
                r.values.map(function (v, vi) { return h('td', { key: vi, className: vi === props.featured ? 'is-featured' : undefined }, cell(v)); }));
            }));
        })));
  }

  /* ---- Accordion (FAQ) ---- */
  var accUid = 0;
  function Accordion(props) {
    var items = props.items || [];
    var st = React.useState(props.defaultOpen || []), open = st[0], setOpen = st[1];
    var base = React.useRef('ds-acc-' + (++accUid)).current;
    function toggle(i) {
      var isOpen = open.indexOf(i) >= 0;
      if (isOpen) setOpen(open.filter(function (x) { return x !== i; }));
      else setOpen(props.multiple ? open.concat([i]) : [i]);
    }
    return h('div', { className: cx('ds-accordion', props.className) },
      items.map(function (it, i) {
        var isOpen = open.indexOf(i) >= 0;
        return h('div', { key: i, className: cx('ds-accordion__item', isOpen && 'is-open') },
          h('h3', { className: 'ds-accordion__heading' },
            h('button', { type: 'button', id: base + '-b' + i, 'aria-expanded': isOpen ? 'true' : 'false', 'aria-controls': base + '-p' + i, className: 'ds-accordion__trigger', onClick: function () { toggle(i); } },
              h('span', null, it.question), h(Icon, { name: 'Plus', size: 20, className: 'ds-accordion__icon' }))),
          h('div', { id: base + '-p' + i, role: 'region', 'aria-labelledby': base + '-b' + i, className: 'ds-accordion__panel', hidden: !isOpen },
            h('div', { className: 'ds-accordion__answer' }, it.answer)));
      }));
  }

  /* ---- Form controls ---- */
  var ctlUid = 0;
  function useId(given, pfx) { var r = React.useRef(null); if (!r.current) r.current = given || ('ds-' + pfx + '-' + (++ctlUid)); return r.current; }

  function Checkbox(props) {
    var id = useId(props.id, 'cb');
    var ref = React.useRef(null);
    React.useEffect(function () { if (ref.current) ref.current.indeterminate = !!props.indeterminate; }, [props.indeterminate]);
    var rest = omit(props, ['label', 'description', 'error', 'indeterminate', 'className', 'id', 'data-state']);
    var descId = (props.description || props.error) ? id + '-d' : undefined;
    return h('div', { className: cx('ds-check', props.error && 'is-error', props.disabled && 'is-disabled', props.className), 'data-state': props['data-state'] },
      h('input', Object.assign({}, rest, { ref: ref, id: id, type: 'checkbox', className: 'ds-check__input', 'aria-describedby': descId, 'aria-invalid': props.error ? 'true' : undefined })),
      h('span', { className: 'ds-check__box', 'aria-hidden': 'true' },
        h(Icon, { name: 'Check', size: 14, strokeWidth: 3, className: 'ds-check__tick' }),
        h('span', { className: 'ds-check__dash' })),
      h('label', { htmlFor: id, className: 'ds-check__label' }, props.label,
        props.description || props.error ? h('span', { id: descId, className: 'ds-check__desc' }, props.error ? h(React.Fragment, null, h(Icon, { name: 'CircleAlert', size: 14 }), props.error) : props.description) : null));
  }

  function RadioGroup(props) {
    var name = useId(props.name, 'radio');
    var controlled = props.value !== undefined;
    var st = React.useState(props.defaultValue), cur = controlled ? props.value : st[0];
    var errId = name + '-err';
    return h('fieldset', { className: cx('ds-radio-group', props.error && 'is-error', props.className), 'aria-describedby': props.error ? errId : undefined },
      props.label ? h('legend', { className: 'ds-field__label' }, props.label) : null,
      (props.options || []).map(function (o) {
        var id = name + '-' + o.value;
        return h('div', { key: o.value, className: cx('ds-check', 'ds-radio', o.disabled && 'is-disabled') },
          h('input', { id: id, type: 'radio', name: name, value: o.value, className: 'ds-check__input', checked: cur === o.value, disabled: o.disabled,
            onChange: function () { if (!controlled) st[1](o.value); if (props.onChange) props.onChange(o.value); } }),
          h('span', { className: 'ds-check__box', 'aria-hidden': 'true' }, h('span', { className: 'ds-radio__dot' })),
          h('label', { htmlFor: id, className: 'ds-check__label' }, o.label, o.description ? h('span', { className: 'ds-check__desc' }, o.description) : null));
      }),
      props.error ? h('p', { id: errId, className: 'ds-field__msg ds-field__msg--error' }, h(Icon, { name: 'CircleAlert', size: 14 }), h('span', null, props.error)) : null);
  }

  function Switch(props) {
    var id = useId(props.id, 'sw');
    var controlled = props.checked !== undefined;
    var st = React.useState(!!props.defaultChecked), on = controlled ? props.checked : st[0];
    return h('div', { className: cx('ds-switch', props.disabled && 'is-disabled', props.className) },
      h('button', { type: 'button', role: 'switch', id: id, 'aria-checked': on ? 'true' : 'false', disabled: props.disabled, 'data-state': props['data-state'],
        className: 'ds-switch__track', onClick: function () { if (!controlled) st[1](!on); if (props.onChange) props.onChange(!on); } },
        h('span', { className: 'ds-switch__thumb' })),
      h('label', { htmlFor: id, className: 'ds-check__label' }, props.label, props.description ? h('span', { className: 'ds-check__desc' }, props.description) : null));
  }

  function fieldShell(props, id, control) {
    var msgId = id + '-msg';
    return h('div', { className: cx('ds-field', props.error && 'is-error', props.disabled && 'is-disabled', props.className) },
      props.label ? h('label', { className: 'ds-field__label', htmlFor: id }, props.label) : null,
      control(props.error || props.hint ? msgId : undefined),
      props.error ? h('p', { id: msgId, className: 'ds-field__msg ds-field__msg--error' }, h(Icon, { name: 'CircleAlert', size: 14 }), h('span', null, props.error))
        : props.hint ? h('p', { id: msgId, className: 'ds-field__msg' }, props.hint) : null);
  }

  function Select(props) {
    var id = useId(props.id, 'sel');
    var rest = omit(props, ['label', 'hint', 'error', 'options', 'placeholder', 'className', 'id']);
    return fieldShell(props, id, function (desc) {
      return h('div', { className: 'ds-field__control ds-select' },
        h('select', Object.assign({}, rest, { id: id, className: 'ds-input ds-select__el', 'aria-describedby': desc, 'aria-invalid': props.error ? 'true' : undefined }),
          props.placeholder ? h('option', { value: '', disabled: true }, props.placeholder) : null,
          (props.options || []).map(function (o) { return h('option', { key: o.value, value: o.value, disabled: o.disabled }, o.label); })),
        h(Icon, { name: 'ChevronRight', size: 16, className: 'ds-select__chev' }));
    });
  }

  function Textarea(props) {
    var id = useId(props.id, 'ta');
    var controlled = props.value !== undefined;
    var st = React.useState(props.defaultValue || ''), val = controlled ? props.value : st[0];
    var rest = omit(props, ['label', 'hint', 'error', 'className', 'id', 'value', 'defaultValue', 'onChange']);
    var max = props.maxLength;
    var p2 = Object.assign({}, props, { hint: max ? h(React.Fragment, null, props.hint, h('span', { className: 'ds-field__count' }, String(val).length + ' / ' + max)) : props.hint });
    return fieldShell(p2, id, function (desc) {
      return h('textarea', Object.assign({}, rest, { id: id, value: val, className: 'ds-input ds-textarea', 'aria-describedby': desc, 'aria-invalid': props.error ? 'true' : undefined,
        onChange: function (e) { if (!controlled) st[1](e.target.value); if (props.onChange) props.onChange(e); } }));
    });
  }

  /* ---- Toast ---- */
  function Toast(props) {
    var tone = props.tone || 'neutral';
    var icon = tone === 'success' ? 'Check' : tone === 'error' ? 'CircleAlert' : null;
    React.useEffect(function () {
      if (!props.duration || !props.onClose) return;
      var t = setTimeout(props.onClose, props.duration);
      return function () { clearTimeout(t); };
    }, [props.duration]);
    return h('div', { className: cx('ds-toast', 'ds-toast--' + tone, props.className), role: tone === 'error' ? 'alert' : 'status' },
      props.image ? h('img', { className: 'ds-toast__img', src: props.image, alt: '' }) : icon ? h(Icon, { name: icon, size: 18, className: 'ds-toast__icon' }) : null,
      h('div', { className: 'ds-toast__text' },
        h('p', { className: 'ds-toast__title' }, props.title),
        props.description ? h('p', { className: 'ds-toast__desc' }, props.description) : null),
      props.action ? h('button', { type: 'button', className: 'ds-toast__action', onClick: props.action.onClick }, props.action.label) : null,
      props.onClose ? h('button', { type: 'button', className: 'ds-toast__close', 'aria-label': 'Dismiss', onClick: props.onClose }, h(Icon, { name: 'X', size: 16 })) : null);
  }
  function ToastRegion(props) {
    return h('div', { className: cx('ds-toast-region', props.className), 'aria-live': 'polite' }, props.children);
  }

  /* ---- Modal ---- */
  var modalUid = 0;
  function Modal(props) {
    var ref = React.useRef(null);
    var base = React.useRef('ds-modal-' + (++modalUid)).current;
    React.useEffect(function () {
      if (!props.open) return;
      var prev = document.activeElement;
      var node = ref.current;
      var sel = 'button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
      var first = node && node.querySelector('[data-autofocus]') || (node && node.querySelector(sel));
      if (first) first.focus();
      function onKey(e) {
        if (e.key === 'Escape') { e.stopPropagation(); if (props.onClose) props.onClose(); }
        if (e.key === 'Tab' && node) {
          var f = node.querySelectorAll(sel); if (!f.length) return;
          var a = f[0], z = f[f.length - 1];
          if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
          else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
        }
      }
      document.addEventListener('keydown', onKey);
      var ov = document.body.style.overflow; document.body.style.overflow = 'hidden';
      return function () { document.removeEventListener('keydown', onKey); document.body.style.overflow = ov; if (prev && prev.focus) prev.focus(); };
    }, [props.open]);
    if (!props.open) return null;
    var tone = props.tone || 'default';
    var content = h('div', { className: 'ds-modal-layer' },
      h('div', { className: 'ds-modal-scrim', onClick: props.dismissible === false ? undefined : props.onClose }),
      h('div', { ref: ref, role: tone === 'destructive' ? 'alertdialog' : 'dialog', 'aria-modal': 'true', 'aria-labelledby': base + '-t', 'aria-describedby': props.description ? base + '-d' : undefined, className: cx('ds-modal', props.className) },
        h('div', { className: 'ds-modal__head' },
          props.image ? h('img', { className: 'ds-modal__img', src: props.image, alt: '' }) : null,
          h('h2', { id: base + '-t', className: 'ds-modal__title' }, props.title),
          h(IconButton, { icon: 'X', label: 'Close', className: 'ds-modal__close', onClick: props.onClose })),
        props.description ? h('p', { id: base + '-d', className: 'ds-modal__desc' }, props.description) : null,
        props.children ? h('div', { className: 'ds-modal__body' }, props.children) : null,
        props.actions ? h('div', { className: 'ds-modal__actions' }, props.actions) : null));
    return window.ReactDOM && window.ReactDOM.createPortal && props.portal !== false ? window.ReactDOM.createPortal(content, document.body) : content;
  }

  /* ---- Tooltip ---- */
  var tipUid = 0;
  function Tooltip(props) {
    var st = React.useState(!!props.open), show = st[0], setShow = st[1];
    var id = React.useRef('ds-tip-' + (++tipUid)).current;
    var shown = props.open !== undefined ? props.open : show;
    React.useEffect(function () {
      if (!shown) return;
      function k(e) { if (e.key === 'Escape') setShow(false); }
      document.addEventListener('keydown', k);
      return function () { document.removeEventListener('keydown', k); };
    }, [shown]);
    var child = React.Children.only(props.children);
    var trigger = React.cloneElement(child, { 'aria-describedby': id });
    return h('span', { className: cx('ds-tip', 'ds-tip--' + (props.side || 'top')),
      onMouseEnter: function () { setShow(true); }, onMouseLeave: function () { setShow(false); },
      onFocus: function () { setShow(true); }, onBlur: function () { setShow(false); } },
      trigger, h('span', { role: 'tooltip', id: id, className: 'ds-tip__bubble', hidden: !shown }, props.content));
  }

  /* ================= Time visualisation ================= */
  function toMin(t) { var p = String(t).split(':'); return (+p[0]) * 60 + (+(p[1] || 0)); }
  function fmt(m) { m = ((m % 1440) + 1440) % 1440; var hh = Math.floor(m / 60), mm = m % 60; return hh + ':' + (mm < 10 ? '0' : '') + mm; }
  function makeScale(start, end) {
    var s = toMin(start), e = toMin(end); if (e <= s) e += 1440;
    function norm(t) { var m = toMin(t); if (m < s) m += 1440; return m; }
    return { s: s, e: e, span: e - s, norm: norm, pct: function (t) { return Math.max(0, Math.min(100, (norm(t) - s) / (e - s) * 100)); }, pctM: function (m) { return (m - s) / (e - s) * 100; } };
  }
  var TYPE_NAME = { busy: 'Busy', done: 'Done', focus: 'Focus with Eepy', break: 'Break', free: 'Free' };

  function DayTimeline(props) {
    var sc = makeScale(props.start || '7:00', props.end || '0:00');
    var size = props.size || 'md';
    var hover = React.useState(null), hv = hover[0], setHv = hover[1];
    var segs = props.segments || [];
    var majors = props.majorTicks || (function () {
      var out = []; for (var m = Math.ceil(sc.s / 60) * 60; m < sc.e; m += 60) { if ((m / 60) % 6 === 0 && m - sc.s > 60 && sc.e - m > 60) out.push(fmt(m)); } return out;
    })();
    var majorM = majors.map(function (t) { return sc.norm(t); });
    var ticks = [];
    for (var m = Math.ceil(sc.s / 60) * 60; m <= sc.e; m += 60) ticks.push(m);
    var summary = (props.title ? props.title + '. ' : '') + 'From ' + fmt(sc.s) + ' to ' + fmt(sc.e) + '.';
    return h('div', { className: cx('ds-tl', 'ds-tl--' + size, props.showEnds === false && 'no-ends', props.className), role: 'group', 'aria-label': summary },
      h('div', { className: 'ds-tl__above', 'aria-hidden': 'true' },
        props.showEnds !== false ? h('span', { className: 'ds-tl__end ds-tl__end--wake', style: { left: '0%' } }, h(Icon, { name: 'AlarmClock', size: size === 'sm' ? 16 : 20 })) : null,
        segs.map(function (sg, i) {
          if (!sg.iconAbove || !sg.icon) return null;
          var c = (sc.pct(sg.from) + sc.pct(sg.to)) / 2;
          return h('span', { key: 'a' + i, className: 'ds-tl__aboveicon ds-tl__aboveicon--' + (sg.type || 'busy'), style: { left: c + '%' } }, h(Icon, { name: sg.icon, size: 18 }));
        }),
        (props.pins || []).map(function (p, i) {
          return h('span', { key: 'p' + i, className: 'ds-tl__pin', style: { left: sc.pct(p.at) + '%' }, title: p.label }, h(Icon, { name: p.icon || 'Sparkles', size: 14 }));
        }),
        props.showEnds !== false ? h('span', { className: 'ds-tl__end ds-tl__end--sleep', style: { left: '100%' } }, h(Icon, { name: 'BedDouble', size: size === 'sm' ? 16 : 20 })) : null),
      h('div', { className: 'ds-tl__track' },
        segs.map(function (sg, i) {
          var a = sc.pct(sg.from), b = sc.pct(sg.to), type = sg.type || 'busy';
          var label = (sg.label || TYPE_NAME[type]) + ', ' + fmt(sc.norm(sg.from)) + '–' + fmt(sc.norm(sg.to));
          return h('div', { key: i, className: cx('ds-tl__seg', 'ds-tl__seg--' + type, hv === i && 'is-hover'),
            style: { left: 'calc(' + a + '% + 2px)', width: 'calc(' + (b - a) + '% - 4px)' },
            onMouseEnter: function () { setHv(i); }, onMouseLeave: function () { setHv(null); } },
            sg.icon && !sg.iconAbove && (b - a) > 6 ? h(Icon, { name: sg.icon, size: size === 'sm' ? 14 : 16, className: 'ds-tl__segicon' }) : null,
            hv === i ? h('span', { className: 'ds-tl__tip', role: 'tooltip' }, label) : null);
        }),
        props.now ? h('span', { className: 'ds-tl__now', style: { left: sc.pct(props.now) + '%' }, title: 'Now ' + props.now }) : null),
      h('div', { className: 'ds-tl__ticks', 'aria-hidden': 'true' },
        ticks.map(function (m) { return h('span', { key: m, className: cx('ds-tl__tick', majorM.indexOf(m) >= 0 && 'is-major'), style: { left: sc.pctM(m) + '%' } }); })),
      props.labels !== false ? h('div', { className: 'ds-tl__labels', 'aria-hidden': 'true' },
        h('span', { className: 'ds-tl__label ds-tl__label--wake', style: { left: '0%' } }, fmt(sc.s)),
        majorM.map(function (m) { return h('span', { key: m, className: 'ds-tl__label', style: { left: sc.pctM(m) + '%' } }, fmt(m)); }),
        (props.highlightLabels || []).map(function (t) { return h('span', { key: 'h' + t, className: 'ds-tl__label ds-tl__label--focus', style: { left: sc.pct(t) + '%' } }, fmt(sc.norm(t))); }),
        h('span', { className: 'ds-tl__label ds-tl__label--sleep', style: { left: '100%' } }, fmt(sc.e))) : null,
      h('ol', { className: 'ds-sr' }, segs.map(function (sg, i) {
        return h('li', { key: i }, fmt(sc.norm(sg.from)) + ' to ' + fmt(sc.norm(sg.to)) + ': ' + (sg.label || TYPE_NAME[sg.type || 'busy']));
      }).concat((props.pins || []).map(function (p, i) { return h('li', { key: 'p' + i }, 'At ' + p.at + ': ' + (p.label || 'activity')); }))));
  }

  function WeekTimeline(props) {
    return h('div', { className: cx('ds-week', props.className) },
      (props.days || []).map(function (d, i) {
        return h('section', { key: i, className: cx('ds-week__day', d.today && 'is-today'), 'aria-label': d.day },
          h('header', { className: 'ds-week__head' },
            h('div', null, h('h3', { className: 'ds-week__title' }, d.day), d.sub ? h('p', { className: 'ds-week__sub' }, d.sub) : null),
            d.highlight ? h('span', { className: 'ds-week__hl' }, d.highlight) : d.note ? h('span', { className: 'ds-week__note' }, d.note) : null),
          h(DayTimeline, Object.assign({ size: 'sm' }, d.timeline, { title: d.day })));
      }));
  }

  function FocusCurve(props) {
    var sc = makeScale(props.start || '9:00', props.end || '18:00');
    var W = 1000, H = props.height || 200, pad = 28;
    var pts = (props.points || []).map(function (p) { return [sc.pctM(sc.norm(p[0])) * W / 100, H - pad / 2 - Math.max(0, Math.min(1, p[1])) * (H - pad * 1.5), p]; });
    var hover = React.useState(null), hv = hover[0], setHv = hover[1];
    var ref = React.useRef(null);
    var gid = React.useRef('ds-fc-' + Math.random().toString(36).slice(2, 8)).current;
    function line(points) {
      if (points.length < 2) return '';
      var d = 'M' + points[0][0] + ' ' + points[0][1];
      for (var i = 0; i < points.length - 1; i++) {
        var p0 = points[i - 1] || points[i], p1 = points[i], p2 = points[i + 1], p3 = points[i + 2] || p2;
        var c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6, c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
        d += ' C' + c1x + ' ' + Math.min(H, c1y) + ' ' + c2x + ' ' + Math.min(H, c2y) + ' ' + p2[0] + ' ' + p2[1];
      }
      return d;
    }
    var top = line(pts);
    var area = pts.length ? top + ' L' + pts[pts.length - 1][0] + ' ' + H + ' L' + pts[0][0] + ' ' + H + ' Z' : '';
    var peak = pts.reduce(function (best, p) { return !best || p[2][1] > best[2][1] ? p : best; }, null);
    function onMove(e) {
      if (!ref.current || !pts.length) return;
      var r = ref.current.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * W;
      var best = pts.reduce(function (b, p) { return Math.abs(p[0] - x) < Math.abs(b[0] - x) ? p : b; }, pts[0]);
      setHv(best);
    }
    var label = props.label || 'Focus level';
    return h('figure', { className: cx('ds-curve', props.className) },
      h('div', { className: 'ds-curve__plot', ref: ref, onMouseMove: onMove, onMouseLeave: function () { setHv(null); } },
        h('svg', { viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', className: 'ds-curve__svg', role: 'img',
          'aria-label': label + ' from ' + fmt(sc.s) + ' to ' + fmt(sc.e) + (peak ? '. Peak ' + Math.round(peak[2][1] * 100) + '% at ' + peak[2][0] : '') },
          h('defs', null,
            h('linearGradient', { id: gid, x1: 0, y1: 1, x2: 0, y2: 0 },
              h('stop', { offset: '0%', className: 'ds-curve__stop-low' }),
              h('stop', { offset: '55%', className: 'ds-curve__stop-mid' }),
              h('stop', { offset: '100%', className: 'ds-curve__stop-high' }))),
          h('path', { d: area, fill: 'url(#' + gid + ')', className: 'ds-curve__area' }),
          h('path', { d: top, className: 'ds-curve__line', vectorEffect: 'non-scaling-stroke' }),
          hv ? h('line', { x1: hv[0], x2: hv[0], y1: 0, y2: H, className: 'ds-curve__cross', vectorEffect: 'non-scaling-stroke' }) : null),
        peak && props.showPeak !== false ? h('span', { className: 'ds-curve__peak', style: { left: peak[0] / W * 100 + '%', top: peak[1] / H * 100 + '%' } },
          h('span', { className: 'ds-curve__badge' }, h(Icon, { name: 'Zap', size: 18 })), h('span', { className: 'ds-curve__peaklabel' }, (props.peakLabel || 'Peak focus') + ' · ' + peak[2][0])) : null,
        hv ? h('span', { className: 'ds-curve__dot', style: { left: hv[0] / W * 100 + '%', top: hv[1] / H * 100 + '%' } }) : null,
        hv ? h('span', { className: 'ds-curve__tip', role: 'tooltip', style: { left: hv[0] / W * 100 + '%' } }, hv[2][0] + ' · ' + label + ' ' + Math.round(hv[2][1] * 100) + '%') : null),
      props.timeline ? h(DayTimeline, Object.assign({ start: props.start, end: props.end, showEnds: false }, props.timeline)) : null,
      props.caption ? h('figcaption', { className: 'ds-curve__caption' }, props.caption) : null,
      h('table', { className: 'ds-sr' }, h('caption', null, label), h('tbody', null, (props.points || []).map(function (p, i) { return h('tr', { key: i }, h('th', { scope: 'row' }, p[0]), h('td', null, Math.round(p[1] * 100) + '%')); }))));
  }

  function VizPanel(props) {
    return h('section', { className: cx('ds-viz', props.align === 'center' && 'is-center', props.className), 'aria-label': props.ariaLabel },
      props.title ? h('h2', { className: 'ds-viz__title' }, props.title, props.highlight ? h(React.Fragment, null, ' ', h('span', { className: 'ds-viz__hl' }, props.highlight)) : null) : null,
      props.subtitle ? h('p', { className: 'ds-viz__sub' }, props.subtitle) : null,
      h('div', { className: 'ds-viz__body' }, props.children));
  }

  window.DesignSystem = Object.assign(window.DesignSystem || {}, {
    Button: Button, IconButton: IconButton, PricingTabs: PricingTabs, TextInput: TextInput, Icon: Icon, Badge: Badge, PricingCard: PricingCard, TemplateCard: TemplateCard, MockupTile: MockupTile, SpotlightCard: SpotlightCard, TopNav: TopNav, Footer: Footer, ComparisonTable: ComparisonTable, Accordion: Accordion, Checkbox: Checkbox, RadioGroup: RadioGroup, Switch: Switch, Select: Select, Textarea: Textarea, Toast: Toast, ToastRegion: ToastRegion, Modal: Modal, Tooltip: Tooltip, DayTimeline: DayTimeline, WeekTimeline: WeekTimeline, FocusCurve: FocusCurve, VizPanel: VizPanel
  });
})();
