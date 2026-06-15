/* UI extras — cookie banner, mask-reveal, split-text headlines, magnetic
   buttons, and the testimonial slider. The cookie banner runs everywhere;
   the rest are public-site only (skipped in the researcher portal). All are
   reduced-motion aware. */
(function () {
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var inPortal = !!document.querySelector('.portal-wrap, .lin-wrap');

  /* ── Cookie consent banner ── */
  (function () {
    var bar = document.getElementById('cookie-banner');
    if (!bar) return;
    var KEY = 'cookie-consent';
    try { if (localStorage.getItem(KEY)) return; } catch (e) { return; }
    bar.hidden = false;
    requestAnimationFrame(function () { bar.classList.add('show'); });
    function dismiss(v) {
      try { localStorage.setItem(KEY, v); } catch (e) {}
      bar.classList.remove('show');
      setTimeout(function () { bar.hidden = true; }, 400);
    }
    var ok = document.getElementById('cookie-accept');
    var no = document.getElementById('cookie-decline');
    if (ok) ok.addEventListener('click', function () { dismiss('all'); });
    if (no) no.addEventListener('click', function () { dismiss('essential'); });
  })();

  if (inPortal) return; // remaining effects are public-site only

  /* ── Mask reveal — images/cards unmask as they enter the viewport ── */
  (function () {
    var els = [].slice.call(document.querySelectorAll('.mask-reveal'));
    if (!els.length) return;
    if (reduce || !('IntersectionObserver' in window)) {
      els.forEach(function (el) { el.classList.add('in'); });
      return;
    }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } });
    }, { threshold: 0.18, rootMargin: '0px 0px -8% 0px' });
    els.forEach(function (el) { io.observe(el); });
  })();

  /* ── Split-text headline reveal — wraps words and staggers them up.
     Opt-in via .tt-split so it never fights the scroll-reveal blocks. ── */
  (function () {
    var heads = [].slice.call(document.querySelectorAll('.tt-split'));
    if (!heads.length) return;
    heads.forEach(function (h) {
      if (h.dataset.split) return;
      h.dataset.split = '1';
      var words = (h.textContent || '').trim().split(/\s+/);
      h.textContent = '';
      words.forEach(function (w, idx) {
        var outer = document.createElement('span'); outer.className = 'sw';
        var inner = document.createElement('span'); inner.className = 'sw-i';
        inner.textContent = w;
        if (!reduce) inner.style.transitionDelay = (idx * 0.05) + 's';
        outer.appendChild(inner);
        h.appendChild(outer);
        if (idx < words.length - 1) h.appendChild(document.createTextNode(' '));
      });
    });
    if (reduce || !('IntersectionObserver' in window)) {
      heads.forEach(function (h) { h.classList.add('in'); });
      return;
    }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } });
    }, { threshold: 0.25 });
    heads.forEach(function (h) { io.observe(h); });
  })();

  /* ── Magnetic buttons — gently pull toward the cursor on hover ── */
  (function () {
    if (reduce) return;
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    var mags = [].slice.call(document.querySelectorAll('.btn-primary, .btn-lg, [data-magnetic], .lab-nav button'));
    mags.forEach(function (el) {
      var strength = 0.28;
      el.addEventListener('pointermove', function (e) {
        var r = el.getBoundingClientRect();
        var mx = e.clientX - (r.left + r.width / 2);
        var my = e.clientY - (r.top + r.height / 2);
        el.style.transform = 'translate(' + (mx * strength).toFixed(1) + 'px,' + (my * strength).toFixed(1) + 'px)';
      });
      el.addEventListener('pointerleave', function () { el.style.transform = ''; });
    });
  })();

  /* ── Testimonial slider — autoplay + dots, pauses on hover ── */
  (function () {
    var root = document.querySelector('[data-testimonials]');
    if (!root) return;
    var track = root.querySelector('.tmo-track');
    if (!track) return;
    var cards = [].slice.call(track.children);
    if (cards.length < 2) return;
    var dotsWrap = root.querySelector('[data-tmo-dots]');
    var i = 0, timer = null, AUTO = 6500;

    var dots = cards.map(function (_, idx) {
      var b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('aria-label', 'Show testimonial ' + (idx + 1));
      b.addEventListener('click', function () { go(idx); restart(); });
      if (dotsWrap) dotsWrap.appendChild(b);
      return b;
    });
    function go(n) {
      i = (n + cards.length) % cards.length;
      track.style.transform = 'translateX(' + (-i * 100) + '%)';
      dots.forEach(function (d, idx) { d.classList.toggle('on', idx === i); });
    }
    function restart() {
      if (timer) clearInterval(timer);
      if (!reduce) timer = setInterval(function () { go(i + 1); }, AUTO);
    }
    root.addEventListener('mouseenter', function () { if (timer) clearInterval(timer); });
    root.addEventListener('mouseleave', restart);
    go(0); restart();
  })();
})();
