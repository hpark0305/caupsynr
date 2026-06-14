/* Hero flow-field — monochrome flowing filaments (silk / energy / neural flow).
   Particles advect along a smooth vector field and leave fading trails, so the
   result reads as flowing strands, not crawling dots. 2D canvas, no libs.
   Theme-aware (single ink colour per theme), prefers-reduced-motion aware. */
(function () {
  var canvas = document.getElementById('hero-field');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  var W = 0, H = 0, DPR = 1, parts = [], t = 0, lastT = 0, frames = 0, painted = false;

  function isLight() { return document.documentElement.getAttribute('data-theme') === 'light'; }

  function spawn() {
    return { x: Math.random() * W, y: Math.random() * H, life: 60 + Math.random() * 160 };
  }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    var r = canvas.getBoundingClientRect();
    W = r.width; H = r.height;
    if (W === 0 || H === 0) return;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    var N = Math.round(Math.min(1100, Math.max(360, W * H / 1500)));
    parts = [];
    for (var i = 0; i < N; i++) parts.push(spawn());
    painted = false; frames = 0;
  }

  // smooth, slowly-evolving vector field → coherent flowing strands
  function angle(x, y) {
    var s = 0.0016;
    return (Math.sin(x * s + t) +
            Math.cos(y * s * 1.25 - t * 0.8) +
            Math.sin((x + y) * s * 0.6 + t * 0.5)) * 1.5;
  }

  function frame(now) {
    var dt = lastT ? Math.min(2.5, (now - lastT) / 16.67) : 1; lastT = now;
    if (W === 0 || H === 0) { requestAnimationFrame(frame); return; }
    var light = isLight();

    if (!painted) { ctx.fillStyle = light ? '#ffffff' : '#08080c'; ctx.fillRect(0, 0, W, H); painted = true; }
    // gentle fade so trails dissolve into the background
    ctx.fillStyle = light ? 'rgba(255,255,255,0.034)' : 'rgba(8,8,12,0.038)';
    ctx.fillRect(0, 0, W, H);

    if (!reduce) t += 0.0016 * dt;

    ctx.lineWidth = 1;
    ctx.strokeStyle = light ? 'rgba(36,39,52,0.085)' : 'rgba(206,212,235,0.075)';
    ctx.beginPath();
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      var a = angle(p.x, p.y);
      var nx = p.x + Math.cos(a) * 1.3 * dt;
      var ny = p.y + Math.sin(a) * 1.3 * dt;
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(nx, ny);
      p.x = nx; p.y = ny; p.life -= dt;
      if (p.x < -12 || p.x > W + 12 || p.y < -12 || p.y > H + 12 || p.life < 0) parts[i] = spawn();
    }
    ctx.stroke();

    frames++;
    if (reduce && frames > 540) return; // build a static pattern, then freeze
    requestAnimationFrame(frame);
  }

  window.addEventListener('resize', resize, { passive: true });
  // clean repaint when the theme is toggled
  new MutationObserver(function () { painted = false; })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  resize();
  requestAnimationFrame(frame);
})();
