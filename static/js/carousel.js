/* People carousel — slide through researcher cards. Centred active card with
   neighbours peeking. Move via prev/next buttons, arrow keys, OR by dragging /
   swiping sideways. GSAP-smoothed if present, CSS fallback otherwise.
   reduced-motion aware. */
(function () {
  var root = document.querySelector('[data-carousel]');
  if (!root) return;
  var viewport = root.querySelector('.lab-viewport');
  var track = root.querySelector('.lab-track');
  var cards = [].slice.call(track.children);
  if (cards.length < 2) return;
  var prev = root.querySelector('[data-prev]');
  var next = root.querySelector('[data-next]');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var i = 0, curX = 0;

  function targetX(idx) {
    var c = cards[idx];
    return -(c.offsetLeft - (viewport.clientWidth - c.offsetWidth) / 2);
  }
  function setX(x) {
    curX = x;
    if (window.gsap) gsap.set(track, { x: x });
    else track.style.transform = 'translateX(' + x + 'px)';
  }
  function go(n, animate) {
    i = Math.max(0, Math.min(cards.length - 1, n));
    var x = targetX(i);
    if (window.gsap) gsap.killTweensOf(track);
    if (window.gsap && animate !== false && !reduce) {
      gsap.to(track, { x: x, duration: 0.7, ease: 'power3.out', onUpdate: function () { curX = gsap.getProperty(track, 'x'); } });
    } else setX(x);
    curX = x;
    cards.forEach(function (c, idx) { c.classList.toggle('is-active', idx === i); });
    if (prev) prev.disabled = i === 0;
    if (next) next.disabled = i === cards.length - 1;
  }

  if (prev) prev.addEventListener('click', function () { go(i - 1); });
  if (next) next.addEventListener('click', function () { go(i + 1); });
  root.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowLeft') go(i - 1);
    else if (e.key === 'ArrowRight') go(i + 1);
  });

  // ── drag / swipe ──
  var dragging = false, startX = 0, startTrackX = 0, moved = 0;
  viewport.addEventListener('pointerdown', function (e) {
    dragging = true; startX = e.clientX; startTrackX = curX; moved = 0;
    if (window.gsap) gsap.killTweensOf(track);
    track.classList.add('is-dragging');
    try { viewport.setPointerCapture(e.pointerId); } catch (err) {}
  });
  viewport.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    var dx = e.clientX - startX;
    moved = Math.max(moved, Math.abs(dx));
    setX(startTrackX + dx);
  });
  function endDrag() {
    if (!dragging) return;
    dragging = false;
    track.classList.remove('is-dragging');
    var best = 0, bd = Infinity;
    for (var k = 0; k < cards.length; k++) {
      var d = Math.abs(curX - targetX(k));
      if (d < bd) { bd = d; best = k; }
    }
    go(best);
  }
  viewport.addEventListener('pointerup', endDrag);
  viewport.addEventListener('pointercancel', endDrag);
  // swallow the click that follows a real drag so card links don't fire
  track.addEventListener('click', function (e) { if (moved > 6) { e.preventDefault(); e.stopPropagation(); } }, true);

  var t;
  window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(function () { go(i, false); }, 120); }, { passive: true });

  go(0, false);
  window.addEventListener('load', function () { go(i, false); });
})();
