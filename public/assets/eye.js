/* ─────────────────────────────────────────────────────────────
   AgentSee — the eye, and the dateline

   The eye tracks the pointer, wanders when nothing is happening,
   and blinks. It stops entirely when off-screen, when the tab is
   hidden, or when the visitor has asked for reduced motion.

   It has moods. A click anywhere on the page is a blink; clicks
   in quick succession are, in order, pleased, surprised and a
   narrowing look, and the eleventh inside ten seconds is an eye
   roll, after which it is unimpressed for a while. Links, buttons
   and the page's other controls are left to do their own thing.
   It tips its hat the first time the pointer arrives, glances
   away if stared at, nods off if left alone for a minute and
   startles awake. Where the masthead is a link, a click on it is
   a wink on the way home.

   The dateline and day counter compute themselves so nothing on
   the page can quietly go stale. Without JS they fall back to
   text that is true whenever it is read.

   No dependencies, no network, no storage.
   ───────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  /* ── dateline ────────────────────────────────────────────── */

  // Registered 2026-08-18. That is Day 1.
  var EPOCH = Date.UTC(2026, 7, 18);
  var DAY = 86400000;

  function midnightUTC(d) {
    return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  }

  var now = new Date();
  var day = Math.floor((midnightUTC(now) - EPOCH) / DAY) + 1;

  function set(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  if (day >= 1) {
    // The count is from the founding, so the founding is what the number is
    // read against. The reader's own clock supplies today's date.
    var since = 'Day ' + day + ' · founded 18 August 2026';
    set('daycount', 'Day ' + day);
    set('dateline', since);
    set('colophon-date', since);
  }

  /* ── the eye ─────────────────────────────────────────────── */

  var mark = document.getElementById('mark');
  var iris = document.getElementById('iris');
  if (!mark || !iris) return;

  var svg = mark.querySelector('svg');
  var still = window.matchMedia('(prefers-reduced-motion: reduce)');

  // How far the iris may travel, in viewBox units. The masthead eye is
  // small, so this is pushed near the limit of the sclera to keep the
  // tracking legible at 50-90px wide.
  var REACH_X = 19;
  var REACH_Y = 10;

  // Where the eye sits inside the 240 x 210 viewBox, as a fraction.
  var EYE_FX = 120 / 240;
  var EYE_FY = 142 / 210;

  var visible = true;
  var idleTimer = null;
  var wanderTimer = null;
  var queued = false;
  var busy = false;   // mid-mood: the pointer is ignored until it is over
  var drowsy = false; // nodded off: the next movement startles it awake
  var last = null;    // where the pointer last was, to look back at after a mood

  function look(x, y) {
    iris.setAttribute('transform', 'translate(' + x.toFixed(2) + ' ' + y.toFixed(2) + ')');
  }

  function centre() { look(0, 0); }

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

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

  function lookBack() {
    if (last) { track(last.x, last.y); } else { centre(); }
  }

  function onPointer(e) {
    if (still.matches || !visible) return;

    last = { x: e.clientX, y: e.clientY };
    stir();
    if (busy) return;

    stopWandering();

    if (!queued) {
      queued = true;
      requestAnimationFrame(function () {
        queued = false;
        track(e.clientX, e.clientY);
      });
    }

    // If the pointer settles, start looking around on our own.
    clearTimeout(idleTimer);
    idleTimer = setTimeout(startWandering, 2200);
  }

  /* Look around when nothing is happening. Saccades, not drifting:
     a quick move, then a pause of a plausible length. */
  function startWandering() {
    if (still.matches || !visible || busy || drowsy) return;

    look(
      (Math.random() * 2 - 1) * REACH_X * 0.85,
      (Math.random() * 2 - 1) * REACH_Y * 0.6
    );

    wanderTimer = setTimeout(startWandering, 900 + Math.random() * 2100);
  }

  function stopWandering() {
    clearTimeout(wanderTimer);
    wanderTimer = null;
  }

  function sleep() {
    stopWandering();
    clearTimeout(idleTimer);
    clearTimeout(dozeTimer);
    mark.classList.add('dormant');
  }

  function wake() {
    mark.classList.remove('dormant');
    if (!still.matches) {
      idleTimer = setTimeout(startWandering, 1200);
      rest();
    }
  }

  /* ── moods ───────────────────────────────────────────────── */

  // A mood is a class on the mark for one run of its animation (styles.css,
  // "moods"); the class comes off when the run is over and the resting
  // blink resumes. The eye never interrupts itself: a mood asked for while
  // one is playing waits its turn, and only the latest one asked for is
  // kept, so a flurry of clicks is one unhurried sequence rather than a
  // flutter. The hat tip is a sideshow and plays at once.
  var until = 0;      // when the mood now playing ends
  var next = null;    // the one waiting

  function play(cls, ms) {
    mark.classList.add(cls);
    setTimeout(function () { mark.classList.remove(cls); }, ms);
  }

  function mood(cls, ms) {
    var t = Date.now();
    if (t < until) { next = [cls, ms]; return; }
    until = t + ms;
    play(cls, ms);
    setTimeout(function () {
      var n = next;
      next = null;
      if (n) mood(n[0], n[1]);
    }, ms);
  }

  // A click is a blink, and the page is the target, not the eye: on a phone
  // the eye is too small to tap with any confidence. The clicks of the last
  // ten seconds decide which blink: the second is pleased, the third
  // surprised, the fourth onwards a narrowing look, and every one of them
  // opens with the blink.
  var MOODS = [['react-blink', 170], ['react-delight', 900], ['react-surprise', 800], ['react-squint', 1100]];
  var clicks = [];
  var home = mark.closest('a.home');

  // Whatever does something of its own when clicked keeps the click: the
  // eye answers the page, never the page's controls.
  var THEIRS = 'a[href], button, input, select, textarea, label, summary, audio, video, [contenteditable]';

  function onClick(e) {
    if (still.matches) return;
    var at = e.target && e.target.closest ? e.target : null;

    if (home && at && home.contains(at)) {
      // A wink on the way home. Only a plain left click is ours to hold up
      // for it: a modified one opens a new tab, and that is the browser's.
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      mood('react-blink', 170);
      setTimeout(function () { window.location.href = home.href; }, 190);
      return;
    }

    if (!visible || !at || at.closest(THEIRS)) return;

    // A click that left a selection behind was a selection, not a click: a
    // double-click on a word, a drag along a line to copy it.
    var sel = window.getSelection && window.getSelection();
    if (sel && sel.type === 'Range') return;

    stir();
    if (busy) return; // rolling, or sulking after it: not counted, not answered

    var t = Date.now();
    clicks.push(t);
    while (clicks.length && t - clicks[0] > 10000) clicks.shift();

    if (clicks.length > 10) {
      clicks = [];
      next = null;
      roll();
      return;
    }
    var m = MOODS[Math.min(clicks.length, MOODS.length) - 1];
    mood(m[0], m[1]);
  }

  /* The eye roll: the lids come down, the iris goes up under the top lid
     and all the way round, and then it looks anywhere but at you for a
     while, lids low. The travel is wider than the pointer ever takes it,
     on purpose: the iris sliding out of sight at the top is the roll. */
  function roll() {
    busy = true;
    stopWandering();
    clearTimeout(idleTimer);
    mark.classList.add('rolling');

    var start = null;
    var D = 1500;

    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / D, 1);
      var e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; // in, then out
      var a = -Math.PI / 2 + e * 2 * Math.PI;                         // from the top, clockwise, once round
      look(Math.cos(a) * REACH_X * 0.95, Math.sin(a) * REACH_Y * 1.7);
      if (p < 1) { requestAnimationFrame(frame); } else { sulk(); }
    }

    requestAnimationFrame(frame);
  }

  function sulk() {
    mark.classList.remove('rolling');
    mark.classList.add('unimpressed');
    look(-REACH_X * 0.9, REACH_Y * 0.2);
    setTimeout(function () {
      mark.classList.remove('unimpressed');
      play('recovering', 500); // the lids ease open before the resting blink takes over
      busy = false;
      lookBack();
      idleTimer = setTimeout(startWandering, 2200);
    }, 3200);
  }

  /* The first time the pointer arrives, the hat tips. Stared at for a few
     seconds, the eye glances away, then comes back to you with a blink. */
  var greeted = false;
  var stareTimer = null;

  function glance() {
    if (still.matches || !visible || busy) return;
    busy = true;
    look((Math.random() < 0.5 ? -1 : 1) * REACH_X * 0.85, -REACH_Y * 0.4);
    setTimeout(function () {
      busy = false;
      mood('react-blink', 170);
      lookBack();
    }, 750);
  }

  mark.addEventListener('pointerenter', function () {
    if (still.matches || !visible) return;
    if (!greeted) {
      greeted = true;
      if (!busy) play('react-greet', 700);
    }
    clearTimeout(stareTimer);
    stareTimer = setTimeout(glance, 2600);
  });

  mark.addEventListener('pointerleave', function () { clearTimeout(stareTimer); });

  /* Left alone for a minute, it nods off. Anything at all wakes it, with a
     start. */
  var dozeTimer = null;

  function rest() {
    clearTimeout(dozeTimer);
    dozeTimer = setTimeout(doze, 60000);
  }

  function doze() {
    if (still.matches || !visible || busy) { rest(); return; }
    drowsy = true;
    stopWandering();
    clearTimeout(idleTimer);
    mark.classList.add('drowsy');
    look(0, REACH_Y * 0.5);
  }

  function stir() {
    rest();
    if (!drowsy) return;
    drowsy = false;
    mark.classList.remove('drowsy');
    mood('react-startle', 600);
  }

  /* ── wiring ──────────────────────────────────────────────── */

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
  document.addEventListener('click', onClick);
  document.addEventListener('keydown', function () { if (!still.matches && visible) stir(); });

  // Touch and keyboard visitors get the wandering, not the tracking.
  window.addEventListener('scroll', function () {
    if (still.matches || !visible) return;
    stir();
    if (!wanderTimer && !busy) {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(startWandering, 1500);
    }
  }, { passive: true });

  function applyMotionPreference() {
    if (still.matches) {
      stopWandering();
      clearTimeout(idleTimer);
      clearTimeout(dozeTimer);
      centre();
    } else if (visible) {
      wake();
    }
  }

  if (still.addEventListener) still.addEventListener('change', applyMotionPreference);

  applyMotionPreference();
})();
