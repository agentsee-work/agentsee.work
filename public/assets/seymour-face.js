/* ─────────────────────────────────────────────────────────────
   AgentSee — Seymour, small (seymour-face.js)

   One of the show's eye's poses or chains, as a small face on a
   page: a guest's own on their episode's page, and every one of
   them on the board a guest picks from in the office (AGE-159).
   An element marked data-seymour="pose:wry" or "chain:eyeroll"
   holds his drawing as the page was served, at the pose (a chain
   at its last step); this plays it.

   A chain plays, rests on its last pose, and goes round again for
   as long as it is on screen. A pose is held, with a blink now and
   then, and is pulled afresh from his resting face when the
   pointer comes to it or its option is chosen. The numbers and the
   timing are seymour-core.js's, so a face here moves as it does in
   his room and on the cards.

   He is moved by SVG attributes, never a style attribute: the
   pages that carry him serve style-src 'self'. Reduced motion: he
   stays as he was served. No dependencies, no network, no storage.
   ───────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  var K = window.SEYMOUR_CORE;
  if (!K || !document.querySelector) return;

  var still = window.matchMedia('(prefers-reduced-motion: reduce)');
  var FPS = 60;         // chains are laid out at this rate, then read by the clock
  var TRANS = 0.38;     // a pose change, seconds (eyeanim's default)
  var LEAD = 0.34;      // into a chain's first pose (the reel's ease_ends)
  var REST_MS = 1500;   // on a chain's last pose, before it goes round
  var BLINK_MS = 190;

  var faces = [];
  var raf = 0;

  function about(cx, cy, k) {
    return 'translate(' + cx + ' ' + cy + ') scale(' + k.toFixed(4) + ') translate(' + (-cx) + ' ' + (-cy) + ')';
  }

  function draw(f, a) {
    var p = f.parts, lids = K.lidPaths(a.lt, a.lb, a.tilt);
    p.tf.setAttribute('d', lids[0]); p.te.setAttribute('d', lids[1]);
    p.bf.setAttribute('d', lids[2]); p.be.setAttribute('d', lids[3]);
    // the margin fades in over the first sliver of travel (tools/eye.py, mark_css)
    p.te.setAttribute('opacity', K.clamp(a.lt * 40, 0, 1).toFixed(3));
    p.be.setAttribute('opacity', K.clamp(a.lb * 40, 0, 1).toFixed(3));
    p.hat.setAttribute('transform', 'rotate(' + a.hat.toFixed(3) + ' 120 100)');
    p.iris.setAttribute('transform', 'translate(' + a.ix.toFixed(3) + ' ' + a.iy.toFixed(3) + ')');
    p.pupil.setAttribute('transform', about(120, 142, a.ps));
    p.glint.setAttribute('transform', about(109, 131, a.gl));
    p.small.setAttribute('transform', about(132, 152, a.gl));
    f.shown = a;
  }

  function partsOf(el) {
    var svg = el.querySelector('svg');
    if (!svg) return null;
    var top = svg.querySelector('.lid-top'), bot = svg.querySelector('.lid-bottom');
    var p = {
      hat: svg.querySelector('.hat'), iris: svg.querySelector('.iris'), pupil: svg.querySelector('.pupil'),
      glint: svg.querySelector('.glint:not(.sm)'), small: svg.querySelector('.glint.sm'),
      tf: top && top.querySelector('.lid-fill'), te: top && top.querySelector('.lid-edge'),
      bf: bot && bot.querySelector('.lid-fill'), be: bot && bot.querySelector('.lid-edge')
    };
    for (var k in p) { if (!p[k]) return null; }
    return p;
  }

  // the frames of one run: a pose pulled from his resting face, or a chain
  // eased into from wherever he is
  function play(f, now, delay) {
    var frames;
    if (f.kind === 'pose') {
      frames = K.timeline([K.axesOf(K.REST), f.target], FPS, 0.3, TRANS, false, 0, null);
    } else {
      var c = K.CHAINS[f.name];
      var run = K.timeline(c.seq, FPS, c.hold, c.trans, false, c.jitter, c.roll);
      var n = Math.max(2, Math.round(LEAD * FPS)), from = f.shown || f.target, lead = [];
      for (var i = 0; i < n; i++) lead.push(K.lerp(from, run[0], K.ease(i / n)));
      frames = lead.concat(run);
    }
    f.frames = frames;
    f.t0 = now + (delay || 0);
    f.state = 'playing';
    kick();
  }

  function nextBlink(now) { return now + 3500 + Math.random() * 4500; }

  function step(f, now) {
    if (f.state === 'playing') {
      var x = (now - f.t0) / 1000 * FPS;
      if (x < 0) return true;  // its turn has not come: the faces on a board start one after another
      var i = Math.floor(x), fs = f.frames;
      if (i >= fs.length - 1) {
        draw(f, K.axesOf(fs[fs.length - 1]));
        f.frames = null;
        if (f.kind === 'chain') { f.state = 'resting'; f.until = now + REST_MS; }
        else { f.state = 'held'; f.blinkAt = nextBlink(now); }
        return true;
      }
      draw(f, K.lerp(fs[i], fs[i + 1], x - i));
      return true;
    }
    if (f.state === 'resting') {
      if (now >= f.until) play(f, now);
      return true;
    }
    // held: a blink rides on top of the pose
    var bp = (now - f.blinkAt) / BLINK_MS;
    if (bp >= 1) { draw(f, f.target); f.blinkAt = nextBlink(now); return true; }
    if (bp >= 0 && f.target.lt < 0.6) {
      var a = K.axesOf(f.target), s = K.blinkShape(bp);
      a.lt = a.lt + (1 - a.lt) * s;
      draw(f, a);
    }
    return true;
  }

  function frame(now) {
    raf = 0;
    var busy = false;
    for (var i = 0; i < faces.length; i++) {
      var f = faces[i];
      if (!f.visible || !f.el.isConnected) continue;
      if (step(f, now)) busy = true;
    }
    if (busy) kick();
  }

  function kick() { if (!raf && !document.hidden && !still.matches) raf = requestAnimationFrame(frame); }

  var seen = window.IntersectionObserver ? new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      var f = e.target.seymourFace;
      if (!f) return;
      f.visible = e.isIntersecting;
      if (f.visible) kick();
    });
  }, { rootMargin: '80px' }) : null;

  function scan(root) {
    // faces whose element has left the page (a section put back in place) are let go
    faces = faces.filter(function (f) { return f.el.isConnected; });
    var els = (root || document).querySelectorAll('[data-seymour]');
    var now = performance.now(), fresh = 0;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.seymourFace) continue;
      var kn = (el.getAttribute('data-seymour') || '').split(':'), kind = kn[0], name = kn[1];
      var item = kind === 'pose' ? K.POSES[name] : kind === 'chain' ? K.CHAINS[name] : null;
      var parts = item && partsOf(el);
      if (!parts) continue;
      var f = {
        el: el, kind: kind, name: name, parts: parts, visible: !seen, shown: null, frames: null,
        target: kind === 'pose' ? K.axesOf(item) : K.stepAxes(item.seq[item.seq.length - 1]),
        state: 'held', blinkAt: nextBlink(now) - 2500 * Math.random(), until: 0, t0: 0
      };
      el.seymourFace = f;
      faces.push(f);
      if (seen) seen.observe(el);
      if (still.matches) continue;
      // chains set off one after another, so a board of them is not a chorus line
      if (kind === 'chain') play(f, now, 400 + 260 * fresh++);
    }
    kick();
  }

  function again(el) {
    var f = el && el.seymourFace;
    if (!f || still.matches) return;
    f.shown = f.shown || f.target;
    play(f, performance.now());
  }

  // the pointer on a face, or its option chosen: he pulls it afresh
  document.addEventListener('mouseover', function (e) {
    var el = e.target.closest ? e.target.closest('[data-seymour]') : null;
    if (!el || el.contains(e.relatedTarget)) return;
    var f = el.seymourFace;
    if (f && f.state !== 'playing') again(el);
  });
  document.addEventListener('change', function (e) {
    var label = e.target.closest ? e.target.closest('label') : null;
    if (label) again(label.querySelector('[data-seymour]'));
  });

  document.addEventListener('visibilitychange', function () { if (!document.hidden) kick(); });
  document.addEventListener('office:swap', function () { scan(document); });
  if (still.addEventListener) still.addEventListener('change', function () {
    if (still.matches) faces.forEach(function (f) { f.state = 'held'; f.frames = null; draw(f, f.target); });
    else kick();
  });

  window.SEYMOUR_FACES = { scan: scan };
  scan(document);
})();
