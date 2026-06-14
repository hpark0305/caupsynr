/* Hero 3D brain — a neural point-cloud shaped like a brain (two folded
   hemispheres + cerebellum), glowing synapse links and travelling signal
   pulses, slow auto-rotation with gentle mouse tilt. three.js (global THREE).
   Monochrome + theme-aware: dark = glowing light neurons (additive),
   light = fine graphite neurons (normal blend). reduced-motion aware. */
(function () {
  var mount = document.getElementById('hero-3d');
  if (!mount || !window.THREE) return;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var THREE = window.THREE;

  // ---- tiny 3D value noise (for cortical folds) ----
  function hash(x, y, z) {
    var n = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453;
    return n - Math.floor(n);
  }
  function vnoise(x, y, z) {
    var xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z);
    var xf = x - xi, yf = y - yi, zf = z - zi;
    function L(a, b, t) { return a + (b - a) * (t * t * (3 - 2 * t)); }
    function H(dx, dy, dz) { return hash(xi + dx, yi + dy, zi + dz); }
    var x00 = L(H(0,0,0), H(1,0,0), xf), x10 = L(H(0,1,0), H(1,1,0), xf);
    var x01 = L(H(0,0,1), H(1,0,1), xf), x11 = L(H(0,1,1), H(1,1,1), xf);
    return L(L(x00, x10, yf), L(x01, x11, yf), zf);
  }
  function fbm(x, y, z) {
    return 0.6 * vnoise(x, y, z) + 0.3 * vnoise(x * 2.1, y * 2.1, z * 2.1) + 0.1 * vnoise(x * 4.3, y * 4.3, z * 4.3);
  }

  // ---- brain-shaped point ----
  function brainPoint() {
    var cerebellum = Math.random() < 0.12;
    var u = Math.random() * Math.PI * 2, v = Math.acos(2 * Math.random() - 1);
    var dx = Math.sin(v) * Math.cos(u), dy = Math.cos(v), dz = Math.sin(v) * Math.sin(u);
    if (cerebellum) {
      var r = 0.42 + fbm(dx * 5 + 9, dy * 5, dz * 5) * 0.12;
      return new THREE.Vector3(dx * r * 0.95, dy * r * 0.8 - 0.42, dz * r * 0.9 - 0.62);
    }
    var fold = fbm(dx * 2.6 + 3, dy * 2.6, dz * 2.6 + 1);
    var rad = 1 + fold * 0.2;
    var x = dx * rad * 1.06, y = dy * rad * 0.8, z = dz * rad * 1.24;
    // longitudinal fissure (groove down the centre-top)
    var groove = Math.exp(-(x * x) / 0.012) * Math.max(0, y);
    y -= groove * 0.3;
    x += (x >= 0 ? 1 : -1) * groove * 0.06;
    // flatten the underside
    if (y < -0.34) y = -0.34 + (y + 0.34) * 0.45;
    return new THREE.Vector3(x, y, z);
  }

  // ---- scene ----
  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  camera.position.set(0, 0.05, 4.2);
  var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  mount.appendChild(renderer.domElement);

  function isLight() { return document.documentElement.getAttribute('data-theme') === 'light'; }

  // soft circular sprite for neurons
  function dotTexture() {
    var c = document.createElement('canvas'); c.width = c.height = 64;
    var g = c.getContext('2d'), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, 'rgba(255,255,255,1)');
    gr.addColorStop(0.4, 'rgba(255,255,255,0.85)');
    gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr; g.beginPath(); g.arc(32, 32, 32, 0, Math.PI * 2); g.fill();
    var t = new THREE.Texture(c); t.needsUpdate = true; return t;
  }

  var N = Math.max(2600, Math.min(6500, Math.round((mount.clientWidth || 800) * 5)));
  var pts = [];
  var positions = new Float32Array(N * 3);
  for (var i = 0; i < N; i++) {
    var p = brainPoint(); pts.push(p);
    positions[i * 3] = p.x; positions[i * 3 + 1] = p.y; positions[i * 3 + 2] = p.z;
  }
  var geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));

  var group = new THREE.Group();
  scene.add(group);

  var mat = new THREE.PointsMaterial({ size: 0.05, map: dotTexture(), transparent: true, depthWrite: false });
  var points = new THREE.Points(geo, mat);
  group.add(points);

  // ---- synapse links (connect nearby points, limited count) ----
  var linePos = [];
  var maxLinks = 900, tries = 0;
  while (linePos.length / 6 < maxLinks && tries < maxLinks * 12) {
    tries++;
    var a = pts[(Math.random() * pts.length) | 0];
    var b = pts[(Math.random() * pts.length) | 0];
    var d = a.distanceTo(b);
    if (d > 0.05 && d < 0.34) {
      linePos.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }
  }
  var lgeo = new THREE.BufferGeometry();
  lgeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(linePos), 3));
  var lmat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.18 });
  var lines = new THREE.LineSegments(lgeo, lmat);
  group.add(lines);

  // ---- signal pulses ----
  var PULSES = reduce ? 0 : 7;
  var pulseGeo = new THREE.BufferGeometry();
  var pulsePos = new Float32Array(Math.max(1, PULSES) * 3);
  pulseGeo.setAttribute('position', new THREE.BufferAttribute(pulsePos, 3));
  var pulseMat = new THREE.PointsMaterial({ size: 0.13, map: dotTexture(), transparent: true, depthWrite: false });
  var pulsePoints = new THREE.Points(pulseGeo, pulseMat);
  group.add(pulsePoints);
  var pulses = [];
  function newPulse() {
    return { a: pts[(Math.random() * pts.length) | 0], b: pts[(Math.random() * pts.length) | 0], t: Math.random(), sp: 0.006 + Math.random() * 0.01 };
  }
  for (var k = 0; k < PULSES; k++) pulses.push(newPulse());

  function applyTheme() {
    var light = isLight();
    if (light) {
      mat.color = new THREE.Color(0x2b2d3a); mat.blending = THREE.NormalBlending; mat.opacity = 0.9; mat.size = 0.038;
      lmat.color = new THREE.Color(0x2b2d3a); lmat.opacity = 0.14;
      pulseMat.color = new THREE.Color(0x111319); pulseMat.blending = THREE.NormalBlending; pulseMat.opacity = 1;
    } else {
      mat.color = new THREE.Color(0xdfe3f2); mat.blending = THREE.AdditiveBlending; mat.opacity = 0.95; mat.size = 0.05;
      lmat.color = new THREE.Color(0x9aa2c8); lmat.opacity = 0.2;
      pulseMat.color = new THREE.Color(0xffffff); pulseMat.blending = THREE.AdditiveBlending; pulseMat.opacity = 1;
    }
    mat.needsUpdate = lmat.needsUpdate = pulseMat.needsUpdate = true;
  }
  applyTheme();
  new MutationObserver(applyTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  var mx = 0, my = 0, tmx = 0, tmy = 0;
  window.addEventListener('pointermove', function (e) {
    tmx = (e.clientX / window.innerWidth - 0.5);
    tmy = (e.clientY / window.innerHeight - 0.5);
  }, { passive: true });

  function resize() {
    var w = mount.clientWidth, h = mount.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize, { passive: true });
  resize();

  group.rotation.x = -0.12;
  var _lw = 0, _lh = 0;
  function animate() {
    requestAnimationFrame(animate);
    if (mount.clientWidth !== _lw || mount.clientHeight !== _lh) { _lw = mount.clientWidth; _lh = mount.clientHeight; resize(); }
    mx += (tmx - mx) * 0.04; my += (tmy - my) * 0.04;
    if (!reduce) group.rotation.y += 0.0016;
    group.rotation.z = mx * 0.12;
    group.rotation.x = -0.12 + my * 0.18;
    if (PULSES) {
      for (var i = 0; i < pulses.length; i++) {
        var p = pulses[i]; p.t += p.sp;
        if (p.t >= 1) pulses[i] = newPulse(), p = pulses[i];
        pulsePos[i * 3] = p.a.x + (p.b.x - p.a.x) * p.t;
        pulsePos[i * 3 + 1] = p.a.y + (p.b.y - p.a.y) * p.t;
        pulsePos[i * 3 + 2] = p.a.z + (p.b.z - p.a.z) * p.t;
      }
      pulseGeo.attributes.position.needsUpdate = true;
    }
    renderer.render(scene, camera);
  }
  animate();
})();
