/* Scroll-expand — elements with .scroll-expand grow (scale up, corners
   square off) as they rise through the viewport, driven by scroll progress
   exposed as the CSS var --p (0..1). reduced-motion aware. */
(function () {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var els = [].slice.call(document.querySelectorAll('.scroll-expand'));
  if (!els.length) return;

  var raf = 0;
  function update() {
    raf = 0;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    for (var i = 0; i < els.length; i++) {
      var r = els[i].getBoundingClientRect();
      var center = r.top + r.height / 2;
      // 0 while the element is low in the viewport → 1 as its centre reaches ~42% height
      var p = 1 - (center - vh * 0.42) / (vh * 0.62);
      p = p < 0 ? 0 : p > 1 ? 1 : p;
      els[i].style.setProperty('--p', p.toFixed(3));
    }
  }
  function req() { if (!raf) raf = requestAnimationFrame(update); }

  window.addEventListener('scroll', req, { passive: true });
  window.addEventListener('resize', req, { passive: true });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', update);
  else update();
})();
