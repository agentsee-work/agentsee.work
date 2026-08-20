/* ─────────────────────────────────────────────────────────────
   AgentSee — the eye
   Tracks the pointer, wanders when nothing is happening, blinks.
   Pauses entirely when off-screen or when the visitor has asked
   for reduced motion. No dependencies, no network, no storage.
   ───────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  /* ── day counter ─────────────────────────────────────────── */

  // agentsee.work was registered 2026-08-18. Day 1 is that day.
  var EPOCH = Date.UTC(2026, 7, 18);
  var DAY = 86400000;

  function today() {
    var now = new Date();
    return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  }

  var day = Math.floor((today() - EPOCH) / DAY) + 1;
  if (day >= 1) {
    var label = 'Day ' + day;
    ['daycount', 'daycount-foot'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = label;
    });
  }

  /* ── the eye ─────────────────────────────────────────────── */

  var mark = document.getElementById('mark');
  var iris = document.getElementById('iris');
  if (!mark || !iris) return;

  var svg = mark.querySelector('svg');
  var still = window.matchMedia('(prefers-reduced-motion: reduce)');

  // How far the iris may travel inside the eye, in viewBox units.
  var REACH_X = 15;
  var REACH_Y = 8.5;

  // Where the eye sits inside the 240 x 210 viewBox, as a fraction.
  var EYE_FX = 120 / 240;
  var EYE_FY = 142 / 210;

  var visible = true;
  var idleTimer = null;
  var wanderTimer = null;
  var queued = false;

  function look(x, y) {
    iris.setAttribute('transform', 'translate(' + x.toFixed(2) + ' ' + y.toFixed(2) + ')');
  }

  function centre() {
    look(0, 0);
  }

  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }

  /* Follow the pointer. */
  function track(px, py) {
    var box = svg.getBoundingClientRect();
    if (!box.width) return;

    var cx = box.left + box.width * EYE_FX;
    var cy = box.top + box.height * EYE_FY;

    // Full deflection at roughly a third of the viewport away.
    var spanX = Math.max(window.innerWidth * 0.32, 300);
    var spanY = Math.max(window.innerHeight * 0.32, 260);

    look(
      clamp((px - cx) / spanX, -1, 1) * REACH_X,
      clamp((py - cy) / spanY, -1, 1) * REACH_Y
    );
  }

  function onPointer(e) {
    if (still.matches || !visible) return;

    stopWandering();

    if (queued) return;
    queued = true;
    requestAnimationFrame(function () {
      queued = false;
      track(e.clientX, e.clientY);
    });

    // If the pointer settles, start looking around on our own.
    clearTimeout(idleTimer);
    idleTimer = setTimeout(startWandering, 2200);
  }

  /* Look around when nothing is happening. Saccades, not drifting:
     a quick move, then a pause of a plausible length. */
  function startWandering() {
    if (still.matches || !visible) return;

    var hold = 900 + Math.random() * 2100;

    // Bias toward the horizontal, the way real eyes scan.
    look(
      (Math.random() * 2 - 1) * REACH_X * 0.85,
      (Math.random() * 2 - 1) * REACH_Y * 0.6
    );

    wanderTimer = setTimeout(startWandering, hold);
  }

  function stopWandering() {
    clearTimeout(wanderTimer);
    wanderTimer = null;
  }

  function sleep() {
    stopWandering();
    clearTimeout(idleTimer);
    mark.classList.add('dormant');
  }

  function wake() {
    mark.classList.remove('dormant');
    if (!still.matches) idleTimer = setTimeout(startWandering, 1200);
  }

  /* Don't animate an eye nobody is looking at. */
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (visible) { wake(); } else { sleep(); centre(); }
    }, { threshold: 0.05 }).observe(mark);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { sleep(); } else if (visible) { wake(); }
  });

  window.addEventListener('pointermove', onPointer, { passive: true });
  window.addEventListener('pointerdown', onPointer, { passive: true });

  // Touch and keyboard visitors get the wandering, not the tracking.
  window.addEventListener('scroll', function () {
    if (!wanderTimer && !still.matches && visible) {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(startWandering, 1500);
    }
  }, { passive: true });

  function applyMotionPreference() {
    if (still.matches) { stopWandering(); clearTimeout(idleTimer); centre(); }
    else if (visible) { wake(); }
  }

  if (still.addEventListener) still.addEventListener('change', applyMotionPreference);

  applyMotionPreference();
})();
