/* Hero "Neural Aurora" — lightweight 2D-canvas neural network.
   Theme-aware, prefers-reduced-motion aware, soft glow via halo (no costly
   per-node shadowBlur), gentle mouse parallax, occasional signal pulses. */
(function () {
  var canvas = document.getElementById('hero-canvas');
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var TAU = Math.PI * 2;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var W = 0, H = 0, DPR = 1, MAXD = 150;
  var nodes = [], pulses = [];
  var mouse = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 };
  var lastT = 0;

  function isLight() { return document.documentElement.getAttribute('data-theme') === 'light'; }
  function palette() {
    return isLight()
      ? { line: '74,64,165', node: '84,66,190', pulse: '32,118,235', mul: 1.35 }
      : { line: '160,150,245', node: '194,186,252', pulse: '120,213,250', mul: 1.0 };
  }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    var r = canvas.getBoundingClientRect();
    W = r.width; H = r.height;
    if (W === 0 || H === 0) return;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    MAXD = Math.min(168, Math.max(118, W / 9));
    build();
  }

  function build() {
    var count = Math.max(24, Math.min(58, Math.round(W * H / 24000)));
    nodes = [];
    for (var i = 0; i < count; i++) {
      nodes.push({
        x: (0.5 + (Math.random() - 0.5) * 0.96) * W,
        y: (0.5 + (Math.random() - 0.5) * 0.9) * H,
        vx: (Math.random() - 0.5) * 0.14,
        vy: (Math.random() - 0.5) * 0.14,
        r: 1.1 + Math.random() * 1.7,
        ph: Math.random() * TAU
      });
    }
  }

  function spawnPulse() {
    if (nodes.length < 2 || pulses.length > 5) return;
    var a = (Math.random() * nodes.length) | 0, best = -1, bd = MAXD;
    for (var j = 0; j < nodes.length; j++) {
      if (j === a) continue;
      var dx = nodes[a].x - nodes[j].x, dy = nodes[a].y - nodes[j].y;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d < bd) { bd = d; best = j; }
    }
    if (best >= 0) pulses.push({ a: a, b: best, t: 0, sp: 0.010 + Math.random() * 0.010 });
  }

  function frame(now) {
    var dt = lastT ? Math.min(2.6, (now - lastT) / 16.67) : 1; lastT = now;
    if (W === 0 || H === 0) { requestAnimationFrame(frame); return; }
    ctx.clearRect(0, 0, W, H);
    var c = palette(), i, j, n;

    mouse.x += (mouse.tx - mouse.x) * 0.05;
    mouse.y += (mouse.ty - mouse.y) * 0.05;
    var px = (mouse.x - 0.5) * 26, py = (mouse.y - 0.5) * 16;

    if (!reduce) {
      for (i = 0; i < nodes.length; i++) {
        n = nodes[i];
        n.x += n.vx * dt; n.y += n.vy * dt;
        if (n.x < -20) n.vx = Math.abs(n.vx); if (n.x > W + 20) n.vx = -Math.abs(n.vx);
        if (n.y < -20) n.vy = Math.abs(n.vy); if (n.y > H + 20) n.vy = -Math.abs(n.vy);
      }
    }

    for (i = 0; i < nodes.length; i++) {
      for (j = i + 1; j < nodes.length; j++) {
        var a = nodes[i], b = nodes[j];
        var dx = a.x - b.x, dy = a.y - b.y, d = Math.sqrt(dx * dx + dy * dy);
        if (d < MAXD) {
          ctx.strokeStyle = 'rgba(' + c.line + ',' + ((1 - d / MAXD) * 0.5 * c.mul).toFixed(3) + ')';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.x + px, a.y + py);
          ctx.lineTo(b.x + px, b.y + py);
          ctx.stroke();
        }
      }
    }

    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      var pl = reduce ? 1 : (0.62 + 0.38 * Math.sin(now * 0.001 + n.ph));
      var x = n.x + px, y = n.y + py;
      ctx.fillStyle = 'rgba(' + c.node + ',' + (0.12 * pl * c.mul).toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(x, y, n.r * 3.4, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(' + c.node + ',' + (0.8 * pl).toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(x, y, n.r, 0, TAU); ctx.fill();
    }

    if (!reduce) {
      for (var k = pulses.length - 1; k >= 0; k--) {
        var p = pulses[k]; p.t += p.sp * dt;
        var a2 = nodes[p.a], b2 = nodes[p.b];
        if (p.t >= 1 || !a2 || !b2) { pulses.splice(k, 1); continue; }
        var x2 = a2.x + (b2.x - a2.x) * p.t + px, y2 = a2.y + (b2.y - a2.y) * p.t + py;
        var fade = 1 - Math.abs(p.t - 0.5) * 1.4;
        ctx.fillStyle = 'rgba(' + c.pulse + ',' + (0.95 * Math.max(0, fade)).toFixed(3) + ')';
        ctx.shadowColor = 'rgba(' + c.pulse + ',1)';
        ctx.shadowBlur = 10;
        ctx.beginPath(); ctx.arc(x2, y2, 2.3, 0, TAU); ctx.fill();
        ctx.shadowBlur = 0;
      }
      if (Math.random() < 0.045) spawnPulse();
    }

    requestAnimationFrame(frame);
  }

  window.addEventListener('resize', resize, { passive: true });
  window.addEventListener('pointermove', function (e) {
    mouse.tx = e.clientX / window.innerWidth;
    mouse.ty = e.clientY / window.innerHeight;
  }, { passive: true });

  resize();
  requestAnimationFrame(frame);
})();
