/* Cursor paint — soft pastel watercolour that flows from the pointer.
   Each move spawns drifting radial-gradient blobs that grow + fade, so the
   trail reads like flowing paint/water. 2D canvas, reduced-motion aware. */
(function () {
  var canvas = document.getElementById('paint-canvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) return;

  var W = 0, H = 0, DPR = 1, blobs = [], last = { x: null, y: null };
  // pastel palette: peach, sky blue, lavender, cream, mint
  var palette = ['236,196,170', '184,210,245', '222,210,246', '245,228,206', '202,231,221'];

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    var r = canvas.getBoundingClientRect();
    W = r.width; H = r.height;
    if (!W || !H) return;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  function spawn(x, y, vx, vy) {
    var c = palette[(Math.random() * palette.length) | 0];
    blobs.push({
      x: x, y: y,
      vx: vx * 0.28 + (Math.random() - 0.5) * 0.5,
      vy: vy * 0.28 + (Math.random() - 0.5) * 0.5,
      r: 55 + Math.random() * 85, life: 1, col: c
    });
    if (blobs.length > 140) blobs.shift();
  }

  window.addEventListener('pointermove', function (e) {
    var r = canvas.getBoundingClientRect();
    var x = e.clientX - r.left, y = e.clientY - r.top;
    if (x < -40 || y < -40 || x > W + 40 || y > H + 40) { last.x = last.y = null; return; }
    var vx = last.x != null ? x - last.x : 0, vy = last.y != null ? y - last.y : 0;
    var n = 2 + Math.min(6, Math.sqrt(vx * vx + vy * vy) / 6);
    for (var i = 0; i < n; i++) spawn(x, y, vx, vy);
    last.x = x; last.y = y;
  }, { passive: true });

  function frame() {
    requestAnimationFrame(frame);
    if (!W || !H) return;
    ctx.clearRect(0, 0, W, H);
    for (var i = blobs.length - 1; i >= 0; i--) {
      var b = blobs[i];
      b.x += b.vx; b.y += b.vy; b.vx *= 0.95; b.vy *= 0.95; b.r *= 1.007; b.life -= 0.011;
      if (b.life <= 0) { blobs.splice(i, 1); continue; }
      var a = b.life * b.life * 0.22;
      var g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, b.r);
      g.addColorStop(0, 'rgba(' + b.col + ',' + a.toFixed(3) + ')');
      g.addColorStop(1, 'rgba(' + b.col + ',0)');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2); ctx.fill();
    }
  }

  window.addEventListener('resize', resize, { passive: true });
  resize();
  requestAnimationFrame(frame);
})();
