/* Interaction & animation layer — count-up numbers, SVG icon line-drawing,
   3D tilt cards, scroll-progress gauge, scroll-reactive marquees, hide-on-
   scroll header, CTA cursor spotlight, hero scroll-cue, and page-transition
   curtain. Public site only (skips the portal); all reduced-motion aware. */
(function () {
  if (document.querySelector('.portal-wrap, .lin-wrap')) return;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function fine() { return window.matchMedia('(hover: hover) and (pointer: fine)').matches; }
  var hasIO = 'IntersectionObserver' in window;

  /* ── Count-up numbers ── */
  function countUp() {
    if (reduce || !hasIO) return;
    var els = [].slice.call(document.querySelectorAll('.stat-num, .recog-metric'));
    if (!els.length) return;
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { io.unobserve(en.target); run(en.target); } });
    }, { threshold: 0.6 });
    els.forEach(function (el) { io.observe(el); });
    function run(el) {
      var m = el.textContent.trim().match(/^(\D*)(\d[\d,]*)(.*)$/);
      if (!m) return;
      var pre = m[1], hasComma = m[2].indexOf(',') >= 0;
      var target = parseInt(m[2].replace(/,/g, ''), 10), suf = m[3];
      if (!isFinite(target)) return;
      var dur = 1100, start = performance.now();
      function fmt(v) { return hasComma ? v.toLocaleString() : String(v); }
      (function step(now) {
        var p = Math.min((now - start) / dur, 1);
        var e = 1 - Math.pow(1 - p, 3);
        el.textContent = pre + fmt(Math.round(target * e)) + suf;
        if (p < 1) requestAnimationFrame(step);
        else el.textContent = pre + fmt(target) + suf;
      })(start);
    }
  }

  /* ── SVG icon line-drawing (research cards) ── */
  function drawIcons() {
    if (reduce) return;
    var icons = [].slice.call(document.querySelectorAll('.ri-card-icon'));
    if (!icons.length) return;
    function shapesOf(svg) { return svg.querySelectorAll('path, line, circle, rect, polyline, polygon'); }
    icons.forEach(function (svg) {
      shapesOf(svg).forEach(function (s) {
        var len = 120;
        try { if (s.getTotalLength) { var l = s.getTotalLength(); if (l) len = l; } } catch (e) {}
        s.style.strokeDasharray = len;
        s.style.strokeDashoffset = len;
        s.style.transition = 'stroke-dashoffset 1.1s var(--ease-out)';
      });
    });
    function reveal(svg) { shapesOf(svg).forEach(function (s, i) { s.style.transitionDelay = (i * 0.08) + 's'; s.style.strokeDashoffset = '0'; }); }
    if (!hasIO) { icons.forEach(reveal); return; }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { reveal(en.target); io.unobserve(en.target); } });
    }, { threshold: 0.4 });
    icons.forEach(function (svg) { io.observe(svg); });
  }

  /* ── 3D tilt cards ── */
  function tilt() {
    if (reduce || !fine()) return;
    var cards = [].slice.call(document.querySelectorAll('.work-card, .note-card, .recog-card, .ri-grid .ri-card:not(.ri-card-new)'));
    cards.forEach(function (card) {
      card.addEventListener('pointermove', function (e) {
        var r = card.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width - 0.5;
        var py = (e.clientY - r.top) / r.height - 0.5;
        card.style.transition = 'transform .08s linear';
        card.style.transform = 'perspective(900px) rotateX(' + (-py * 6).toFixed(2) + 'deg) rotateY(' + (px * 6).toFixed(2) + 'deg) translateY(-6px)';
      });
      card.addEventListener('pointerleave', function () { card.style.transition = ''; card.style.transform = ''; });
    });
  }

  /* ── Scroll-progress gauge ── */
  function progress() {
    var bar = document.getElementById('scroll-progress');
    if (!bar) return;
    function upd() {
      var h = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.transform = 'scaleX(' + (h > 0 ? Math.max(0, Math.min(1, window.scrollY / h)) : 0) + ')';
    }
    window.addEventListener('scroll', upd, { passive: true });
    window.addEventListener('resize', upd, { passive: true });
    upd();
  }

  /* ── Scroll-reactive marquees ── */
  function marquees() {
    if (reduce) return;
    var tracks = [].slice.call(document.querySelectorAll('.kw-track, .logo-track'));
    if (!tracks.length) return;
    var items = tracks.map(function (t) {
      t.style.animation = 'none';
      return { el: t, x: 0, half: t.scrollWidth / 2, base: t.classList.contains('logo-track') ? 0.4 : 0.7 };
    });
    var last = window.scrollY, vel = 0;
    window.addEventListener('scroll', function () { var y = window.scrollY; vel = y - last; last = y; }, { passive: true });
    window.addEventListener('resize', function () { items.forEach(function (it) { it.half = it.el.scrollWidth / 2; }); }, { passive: true });
    (function frame() {
      vel *= 0.9;
      var boost = Math.max(-14, Math.min(14, vel * 0.12));
      items.forEach(function (it) {
        if (!it.half) it.half = it.el.scrollWidth / 2;
        it.x -= (it.base + boost);
        if (it.x <= -it.half) it.x += it.half;
        else if (it.x > 0) it.x -= it.half;
        it.el.style.transform = 'translateX(' + it.x.toFixed(2) + 'px)';
      });
      requestAnimationFrame(frame);
    })();
  }

  /* ── Hide-on-scroll header ── */
  function headerHide() {
    var header = document.querySelector('.site-header');
    if (!header) return;
    var last = window.scrollY, ticking = false;
    function upd() {
      var y = window.scrollY;
      var nav = document.getElementById('site-nav');
      if (nav && nav.classList.contains('open')) { header.classList.remove('nav-hidden'); }
      else if (y > last && y > 140) header.classList.add('nav-hidden');
      else header.classList.remove('nav-hidden');
      last = y; ticking = false;
    }
    window.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(upd); } }, { passive: true });
  }

  /* ── CTA cursor spotlight ── */
  function ctaSpotlight() {
    if (!fine()) return;
    var cta = document.querySelector('.cta-spotlight');
    if (!cta) return;
    cta.addEventListener('pointermove', function (e) {
      var r = cta.getBoundingClientRect();
      cta.style.setProperty('--mx', (e.clientX - r.left) + 'px');
      cta.style.setProperty('--my', (e.clientY - r.top) + 'px');
      cta.classList.add('lit');
    });
    cta.addEventListener('pointerleave', function () { cta.classList.remove('lit'); });
  }

  /* ── Hero scroll-cue ── */
  function heroCue() {
    var cue = document.querySelector('.hero-cue');
    if (!cue) return;
    cue.addEventListener('click', function (e) {
      var t = document.getElementById('after-hero');
      if (!t) return;
      e.preventDefault();
      window.scrollTo({ top: t.getBoundingClientRect().top + window.scrollY, behavior: reduce ? 'auto' : 'smooth' });
    });
  }

  /* ── Page-transition curtain (outgoing) ── */
  function pageCurtain() {
    if (reduce) return;
    var curtain = document.getElementById('page-curtain');
    if (!curtain) return;
    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href]');
      if (!a) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      var href = a.getAttribute('href');
      if (!href || href.charAt(0) === '#' || a.target === '_blank' || a.hasAttribute('download')) return;
      if (/^(mailto:|tel:|javascript:)/i.test(href)) return;
      var url;
      try { url = new URL(a.href, location.href); } catch (err) { return; }
      if (url.origin !== location.origin) return;
      if (url.pathname === location.pathname && url.search === location.search) return;
      e.preventDefault();
      try { sessionStorage.setItem('curtain-nav', '1'); } catch (err) {}
      curtain.classList.add('cover');
      setTimeout(function () { window.location.href = a.href; }, 360);
    });
  }

  function init() {
    countUp(); drawIcons(); tilt(); progress(); marquees();
    headerHide(); ctaSpotlight(); heroCue(); pageCurtain();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
