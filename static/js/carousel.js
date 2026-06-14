/* People carousel — horizontal slide of researcher cards, centred active card
   with neighbours peeking, prev/next buttons. GSAP-smoothed if available,
   graceful CSS fallback otherwise. reduced-motion aware. */
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
  var i = 0;

  function offsetFor(idx) {
    var card = cards[idx];
    // centre the active card in the viewport
    return card.offsetLeft - (viewport.clientWidth - card.offsetWidth) / 2;
  }

  function go(n, animate) {
    i = Math.max(0, Math.min(cards.length - 1, n));
    var x = -offsetFor(i);
    if (window.gsap && animate !== false && !reduce) {
      gsap.to(track, { x: x, duration: 0.7, ease: 'power3.out' });
    } else {
      if (window.gsap) gsap.set(track, { x: x });
      else track.style.transform = 'translateX(' + x + 'px)';
    }
    cards.forEach(function (c, idx) { c.classList.toggle('is-active', idx === i); });
    if (prev) prev.disabled = i === 0;
    if (next) next.disabled = i === cards.length - 1;
  }

  if (prev) prev.addEventListener('click', function () { go(i - 1); });
  if (next) next.addEventListener('click', function () { go(i + 1); });
  root.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowLeft') go(i - 1);
    if (e.key === 'ArrowRight') go(i + 1);
  });

  var t;
  window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(function () { go(i, false); }, 120); }, { passive: true });

  // start centred on the first card once layout (and images) settle
  go(0, false);
  window.addEventListener('load', function () { go(i, false); });
})();
