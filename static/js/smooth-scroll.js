/* Smooth scroll — eased wheel scrolling, homepage only. Uses the NATIVE
   scroll position (window.scrollTo) instead of hijacking layout with a
   transform, so the fixed header, sticky elements, and scroll-driven effects
   (scroll-expand.js) keep working. Disabled for touch, reduced-motion, coarse
   pointers, and when scrolling inside a nested scroller. */
(function () {
  if (!document.body.classList.contains('page-home')) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

  var target = window.scrollY || window.pageYOffset || 0;
  var current = target;
  var running = false;
  var EASE = 0.12;

  function maxScroll() {
    return Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
  }
  function frame() {
    current += (target - current) * EASE;
    if (Math.abs(target - current) < 0.5) { current = target; running = false; }
    window.scrollTo(0, current);
    if (running) requestAnimationFrame(frame);
  }
  function insideScroller(node) {
    while (node && node !== document.body && node.nodeType === 1) {
      if (node.scrollHeight > node.clientHeight + 4) {
        var oy = getComputedStyle(node).overflowY;
        if (oy === 'auto' || oy === 'scroll') return true;
      }
      node = node.parentNode;
    }
    return false;
  }
  window.addEventListener('wheel', function (e) {
    if (e.ctrlKey) return;                 // let pinch-zoom through
    if (insideScroller(e.target)) return;  // let nested scrollers behave normally
    e.preventDefault();
    var dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1);
    target = Math.max(0, Math.min(maxScroll(), target + dy));
    if (!running) { running = true; current = window.scrollY; requestAnimationFrame(frame); }
  }, { passive: false });

  // keep our target in sync when the user scrolls another way (keys, bar, anchors)
  window.addEventListener('scroll', function () {
    if (!running) { target = window.scrollY; current = target; }
  }, { passive: true });
})();
