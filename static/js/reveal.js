/* Scroll-reveal — content fades/slides up as it enters the viewport
   (motion-forward, upsunday-style). Progressive enhancement: classes are
   added via JS, so no-JS and reduced-motion users see content normally.
   Skips the hero (it has its own intro) and the researcher portal. */
(function () {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!('IntersectionObserver' in window)) return;

  function run() {
    var sels = [
      '.section-header', '.ri-card', '.pub-item', '.pub-list-item',
      '.stat-item', '.apps-stat-item', '.member-card', '.professor-card',
      '.ev-photo', '.merge-card', '.settings-card',
      'section:not(.hero) > .container > h2',
      'section:not(.hero) > .container > h3',
      'section:not(.hero) > .container > p',
      'section:not(.hero) > .container > .btn'
    ];
    var seen = [];
    sels.forEach(function (s) {
      document.querySelectorAll(s).forEach(function (el) {
        if (el.closest('.lin-wrap') || el.closest('.portal-wrap')) return; // skip portal layouts
        if (seen.indexOf(el) < 0) seen.push(el);
      });
    });
    if (!seen.length) return;

    seen.forEach(function (el) {
      el.classList.add('reveal');
      // stagger reveal-siblings inside the same parent
      var sibs = [], ch = el.parentNode.children;
      for (var i = 0; i < ch.length; i++) if (ch[i].classList.contains('reveal')) sibs.push(ch[i]);
      var idx = sibs.indexOf(el);
      if (idx > 0) el.style.transitionDelay = Math.min(idx * 0.08, 0.42) + 's';
    });

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add('is-visible'); io.unobserve(en.target); }
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -6% 0px' });
    seen.forEach(function (el) { io.observe(el); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
