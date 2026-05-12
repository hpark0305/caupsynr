/* ───────────────────────────────────────────────────────────────
   TSL · Neural network hero background
   - 80 nodes, violet connections, cyan pulse particles
   - Mousemove → camera rotation
   - Click → radial pulse burst from nearest node
   Requires Three.js loaded BEFORE this script (window.THREE).
   ─────────────────────────────────────────────────────────────── */
(function () {
  if (typeof THREE === 'undefined') {
    console.warn('[neural.js] Three.js not found — skipping neural background.');
    return;
  }
  const canvas = document.getElementById('neural-canvas');
  if (!canvas) return;

  const VIOLET = new THREE.Color(0x7c6fe0);
  const CYAN   = new THREE.Color(0x4fc3f7);

  /* ─── renderer ─── */
  const renderer = new THREE.WebGLRenderer({
    canvas: canvas,
    antialias: true,
    alpha: true,
    powerPreference: 'high-performance'
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  /* ─── scene / camera ─── */
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
  camera.position.set(0, 0, 16);

  const root = new THREE.Group();
  scene.add(root);

  /* ─── nodes ─── */
  const NODE_COUNT = 80;
  const RADIUS = 7.5;
  const nodes = [];
  const nodeGeom = new THREE.SphereGeometry(0.06, 12, 12);
  const nodeMat = new THREE.MeshBasicMaterial({
    color: 0xc9c2ff,
    transparent: true,
    opacity: 0.95
  });
  const nodeBigMat = new THREE.MeshBasicMaterial({
    color: 0x4fc3f7,
    transparent: true,
    opacity: 1
  });

  for (let i = 0; i < NODE_COUNT; i++) {
    // spherical-ish distribution with a bias toward outer shell
    const phi   = Math.acos(2 * Math.random() - 1);
    const theta = Math.random() * Math.PI * 2;
    const r     = RADIUS * (0.55 + 0.45 * Math.random());
    const pos = new THREE.Vector3(
      r * Math.sin(phi) * Math.cos(theta),
      r * Math.sin(phi) * Math.sin(theta),
      r * Math.cos(phi)
    );
    const mat = (i % 9 === 0) ? nodeBigMat.clone() : nodeMat.clone();
    const mesh = new THREE.Mesh(nodeGeom, mat);
    mesh.position.copy(pos);
    mesh.userData = {
      basePos: pos.clone(),
      velocity: new THREE.Vector3(
        (Math.random() - 0.5) * 0.002,
        (Math.random() - 0.5) * 0.002,
        (Math.random() - 0.5) * 0.002
      ),
      pulse: 0
    };
    root.add(mesh);
    nodes.push(mesh);
  }

  /* ─── edges ─── */
  const MAX_DIST = 3.6;
  const edgePairs = [];
  for (let i = 0; i < NODE_COUNT; i++) {
    for (let j = i + 1; j < NODE_COUNT; j++) {
      const d = nodes[i].position.distanceTo(nodes[j].position);
      if (d < MAX_DIST) edgePairs.push([i, j, d]);
    }
  }
  const linePositions = new Float32Array(edgePairs.length * 2 * 3);
  const lineColors    = new Float32Array(edgePairs.length * 2 * 3);

  function writeEdgeBuffers() {
    for (let e = 0; e < edgePairs.length; e++) {
      const [a, b, d] = edgePairs[e];
      const pa = nodes[a].position, pb = nodes[b].position;
      const o = e * 6;
      linePositions[o]   = pa.x; linePositions[o+1] = pa.y; linePositions[o+2] = pa.z;
      linePositions[o+3] = pb.x; linePositions[o+4] = pb.y; linePositions[o+5] = pb.z;
      const t = 1 - d / MAX_DIST; // closer = stronger
      const c = VIOLET.clone().lerp(CYAN, 0.25 * Math.sin(e));
      const a8 = 0.10 + 0.55 * t;
      lineColors[o]   = c.r * a8;
      lineColors[o+1] = c.g * a8;
      lineColors[o+2] = c.b * a8;
      lineColors[o+3] = c.r * a8;
      lineColors[o+4] = c.g * a8;
      lineColors[o+5] = c.b * a8;
    }
  }
  writeEdgeBuffers();

  const lineGeom = new THREE.BufferGeometry();
  lineGeom.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
  lineGeom.setAttribute('color',    new THREE.BufferAttribute(lineColors, 3));
  const lineMat = new THREE.LineBasicMaterial({
    vertexColors: true,
    transparent: true,
    opacity: 0.85,
    blending: THREE.AdditiveBlending,
    depthWrite: false
  });
  const lines = new THREE.LineSegments(lineGeom, lineMat);
  root.add(lines);

  /* ─── pulse particles ─── */
  const MAX_PULSES = 240;
  const pulseGeom = new THREE.BufferGeometry();
  const pulsePos  = new Float32Array(MAX_PULSES * 3);
  const pulseGeo2 = new THREE.BufferAttribute(pulsePos, 3);
  pulseGeom.setAttribute('position', pulseGeo2);
  const pulseMat = new THREE.PointsMaterial({
    color: 0x4fc3f7,
    size: 0.18,
    transparent: true,
    opacity: 0.95,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true
  });
  const pulsePoints = new THREE.Points(pulseGeom, pulseMat);
  root.add(pulsePoints);

  const pulses = []; // {pos:Vec3, vel:Vec3, life:0..1}

  function spawnPulseBurst(origin, count) {
    for (let i = 0; i < count; i++) {
      if (pulses.length >= MAX_PULSES) break;
      const dir = new THREE.Vector3(
        Math.random() - 0.5,
        Math.random() - 0.5,
        Math.random() - 0.5
      ).normalize().multiplyScalar(0.04 + Math.random() * 0.10);
      pulses.push({
        pos: origin.clone(),
        vel: dir,
        life: 1
      });
    }
  }

  function updatePulses(dt) {
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      p.pos.addScaledVector(p.vel, dt * 60);
      p.vel.multiplyScalar(0.985);
      p.life -= 0.012;
      if (p.life <= 0) pulses.splice(i, 1);
    }
    // write to buffer
    for (let i = 0; i < MAX_PULSES; i++) {
      const p = pulses[i];
      const o = i * 3;
      if (p) {
        pulsePos[o]   = p.pos.x;
        pulsePos[o+1] = p.pos.y;
        pulsePos[o+2] = p.pos.z;
      } else {
        pulsePos[o] = pulsePos[o+1] = pulsePos[o+2] = 9999;
      }
    }
    pulseGeo2.needsUpdate = true;
  }

  /* ─── interaction ─── */
  const target = { x: 0, y: 0 }; // normalised mouse position
  const rotation = { x: 0, y: 0 };
  const raycaster = new THREE.Raycaster();
  const mouseVec = new THREE.Vector2();
  let lastClickAt = 0;

  function onPointerMove(e) {
    const r = canvas.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width)  * 2 - 1;
    const y = -((e.clientY - r.top)  / r.height) * 2 + 1;
    target.x = x;
    target.y = y;
    mouseVec.x = x;
    mouseVec.y = y;
  }

  function findNearestNode() {
    raycaster.setFromCamera(mouseVec, camera);
    let bestIdx = -1, bestD = Infinity;
    for (let i = 0; i < nodes.length; i++) {
      const d = raycaster.ray.distanceToPoint(nodes[i].position);
      if (d < bestD) { bestD = d; bestIdx = i; }
    }
    return bestIdx >= 0 ? nodes[bestIdx] : null;
  }

  function onPointerDown(e) {
    if (e.target !== canvas) return;
    const now = performance.now();
    if (now - lastClickAt < 120) return;
    lastClickAt = now;
    const r = canvas.getBoundingClientRect();
    mouseVec.x = ((e.clientX - r.left) / r.width)  * 2 - 1;
    mouseVec.y = -((e.clientY - r.top)  / r.height) * 2 + 1;
    const n = findNearestNode();
    if (n) {
      n.userData.pulse = 1;
      spawnPulseBurst(n.position, 40);
    }
  }

  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerdown', onPointerDown);

  /* ─── sizing ─── */
  function resize() {
    const w = canvas.clientWidth || canvas.parentElement.clientWidth;
    const h = canvas.clientHeight || canvas.parentElement.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  /* ─── animation loop ─── */
  let last = performance.now();
  let frame = 0;

  function tick() {
    const now = performance.now();
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    frame++;

    // ease rotation toward mouse target
    rotation.x += ((target.y * 0.35) - rotation.x) * 0.04;
    rotation.y += ((target.x * 0.55) - rotation.y) * 0.04;
    root.rotation.x = rotation.x;
    root.rotation.y = rotation.y + frame * 0.0008;

    // node drift + pulse decay
    let buffersDirty = false;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      n.position.addScaledVector(n.userData.velocity, dt * 60);
      // tether to base
      const back = n.userData.basePos.clone().sub(n.position).multiplyScalar(0.004);
      n.userData.velocity.add(back);
      n.userData.velocity.multiplyScalar(0.992);

      // scale pulse decay
      if (n.userData.pulse > 0) {
        n.userData.pulse *= 0.92;
        const s = 1 + n.userData.pulse * 1.8;
        n.scale.setScalar(s);
        n.material.opacity = 0.6 + n.userData.pulse * 0.4;
      } else {
        n.scale.setScalar(1);
      }
      buffersDirty = true;
    }
    if (buffersDirty) {
      writeEdgeBuffers();
      lineGeom.attributes.position.needsUpdate = true;
      lineGeom.attributes.color.needsUpdate = true;
    }

    updatePulses(dt);

    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }
  tick();
})();
