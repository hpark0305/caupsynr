/* Custom cursor — a lerping ring + an exact dot that grow over interactive
   elements (upsunday-style). Fine-pointer, non-portal pages only so it never
   gets in the way of data entry in the researcher portal. */
(function () {
  if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
  if (document.querySelector('.portal-wrap, .lin-wrap')) return;
  var ring = document.getElementById('cursor-ring');
  var dot = document.getElementById('cursor-dot');
  var label = document.getElementById('cursor-label');
  if (!ring || !dot) return;

  document.body.classList.add('has-cursor');

  var x = window.innerWidth / 2, y = window.innerHeight / 2;
  var rx = x, ry = y, visible = false;

  window.addEventListener('pointermove', function (e) {
    if (e.pointerType && e.pointerType !== 'mouse') return;
    x = e.clientX; y = e.clientY;
    dot.style.transform = 'translate(' + x + 'px,' + y + 'px)';
    if (label) label.style.transform = 'translate(' + x + 'px,' + y + 'px)';
    if (!visible) { visible = true; document.body.classList.add('cursor-on'); }
  }, { passive: true });

  window.addEventListener('pointerdown', function () { document.body.classList.add('cursor-down'); });
  window.addEventListener('pointerup', function () { document.body.classList.remove('cursor-down'); });
  document.addEventListener('mouseleave', function () { document.body.classList.remove('cursor-on'); visible = false; });

  // grow when over something interactive
  var hoverSel = 'a, button, input, textarea, select, label, [data-cursor], .work-card, .lab-card, .note-card, .ri-card, .tmo-dots button';
  function isHover(t) { return t && t.closest && t.closest(hoverSel); }
  document.addEventListener('pointerover', function (e) {
    if (isHover(e.target)) document.body.classList.add('cursor-hover');
  });
  document.addEventListener('pointerout', function (e) {
    if (isHover(e.target) && !isHover(e.relatedTarget)) document.body.classList.remove('cursor-hover');
  });

  // "View" label over media/case cards (upsunday-style)
  var viewSel = '.work-card, .note-card, .lab-card, .ev-photo, [data-cursor-view]';
  function isView(t) { return t && t.closest && t.closest(viewSel); }
  document.addEventListener('pointerover', function (e) {
    if (isView(e.target)) document.body.classList.add('cursor-view');
  });
  document.addEventListener('pointerout', function (e) {
    if (isView(e.target) && !isView(e.relatedTarget)) document.body.classList.remove('cursor-view');
  });

  (function loop() {
    rx += (x - rx) * 0.18;
    ry += (y - ry) * 0.18;
    ring.style.transform = 'translate(' + rx + 'px,' + ry + 'px)';
    requestAnimationFrame(loop);
  })();
})();
