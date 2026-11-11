/* ─────────────────────────────────────────────────────────────
   AgentSee — Seymour's room (/seymour/)

   The show's eye, live. Eight numbers describe a pose (tools/eye.py:
   two lids, the tilt of their curve, the hat's angle, the iris on
   two axes, the pupil's size and the catchlight's); the poses and
   the chains come from seymour-data.js, which tools/eyeanim.py
   writes from the same tables the renders use. The lid geometry and
   the chain timing below are ports of lid_paths() and timeline(),
   kept line for line so what plays here is what the reel shows.

   A pad on the board, or its key, plays a pose or a chain; the pad
   stays lit while he holds it. The iris follows the pointer on top
   of whatever pose he is in, and wanders when the pointer rests. He
   blinks on his own, and when the page is clicked. The sliders set
   the eight numbers by hand; the hash carries a pose or the
   numbers, so a link opens on a face. The theme's walking bass, on
   its own, loops through a gain that fades over four bars each way,
   and he plays it: the iris walks the line, the hat nods on two and
   four.

   Reduced motion: poses are set, not played; chains land at once;
   no blinks, no wander, the bass only steps. No dependencies, no
   network but the page's own bass loop once the board is touched,
   no storage.
   ───────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  var D = window.SEYMOUR;
  var mark = document.getElementById('seymour');
  if (!D || !mark) return;

  var AXES = D.axes;
  var REST = D.rest;
  var POSES = {};
  var CHAINS = {};
  D.poses.forEach(function (p) { POSES[p.name] = p; });
  D.chains.forEach(function (c) { CHAINS[c.name] = c; });

  var still = window.matchMedia('(prefers-reduced-motion: reduce)');
  var wide = window.matchMedia('(min-width: 60em)');

  var FPS = 120;        // chains are laid out at this rate, then read by the clock
  var TRANS = 0.38;     // a pose change, seconds (eyeanim's default)
  var LEAD = 0.34;      // into a chain's first pose (the reel's ease_ends)
  var BLINK_MS = 190;

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

  /* ── the eight numbers ─────────────────────────────────────── */

  function axesOf(src) {
    var a = {};
    for (var i = 0; i < AXES.length; i++) {
      var k = AXES[i];
      a[k] = src && src[k] != null ? +src[k] : REST[k];
    }
    return a;
  }
  // a chain step is a pose name, or overrides on rest
  function stepAxes(step) { return typeof step === 'string' ? axesOf(POSES[step]) : axesOf(step); }

  function lerp(a, b, t) {
    var o = {};
    for (var i = 0; i < AXES.length; i++) { var k = AXES[i]; o[k] = a[k] + (b[k] - a[k]) * t; }
    return o;
  }
  function same(a, b) {
    for (var i = 0; i < AXES.length; i++) { if (a[AXES[i]] !== b[AXES[i]]) return false; }
    return true;
  }

  /* cubic in-out: lids accelerate and settle; a linear lid reads mechanical */
  function ease(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function smooth(x) { x = clamp(x, 0, 1); return x * x * (3 - 2 * x); }
  /* shut at the midpoint, open at both ends; a sine, since a lid that
     reverses at a sharp corner reads as a glitch */
  function blinkShape(t) { return Math.pow(Math.sin(Math.PI * t), 0.7); }

  /* ── the lids (eye.py, lid_paths) ──────────────────────────── */

  // The eye's corners are pinned; only the middle of a lid travels, between
  // the open curve and the opposite one. Tilt pushes the two control points
  // opposite ways, the lower lid taking half of it.
  var LID = D.lid;
  function f2(v) { return v.toFixed(2); }
  function lidPaths(lt, lb, tilt) {
    var span = LID.shut - LID.open;
    var ty = LID.open + span * lt;
    var by = LID.shut - span * lb;
    var t = tilt * span * 0.42;
    var top = 'M44 142C72 ' + f2(ty - t) + ' 168 ' + f2(ty + t) + ' 196 142';
    var bot = 'M44 142C72 ' + f2(by - t * 0.5) + ' 168 ' + f2(by + t * 0.5) + ' 196 142';
    return [top + ' L196 -60 L44 -60 Z', top, bot + ' L196 340 L44 340 Z', bot];
  }

  /* ── the chains (eyeanim.py, arc_frames and timeline) ──────── */

  /* The eye roll: one unbroken movement out to a corner, over the top to the
     other, and home, slowing near the corners without stopping. */
  function arcFrames(a, b, roll, fps) {
    var n = Math.max(12, Math.round((roll.dur != null ? roll.dur : 1.6) * fps));
    var rx = roll.rx != null ? roll.rx : 31;
    var ry = roll.ry != null ? roll.ry : 31;
    var a0 = (roll.a0 != null ? roll.a0 : -38) * Math.PI / 180;
    var a1 = (roll.a1 != null ? roll.a1 : -142) * Math.PI / 180;
    var outS = 0.17, inS = 0.83;
    var corners = roll.corners || [0.19, 0.81];
    var width = roll.linger != null ? roll.linger : 0.12;
    var floor = roll.floor != null ? roll.floor : 0.10;

    function pos(s) {
      var env, ang;
      if (s < outS) { env = smooth(s / outS); ang = a0; }
      else if (s < inS) { env = 1; ang = a0 + (a1 - a0) * (s - outS) / (inS - outS); }
      else { env = smooth((1 - s) / (1 - inS)); ang = a1; }
      return [rx * Math.cos(ang) * env, ry * Math.sin(ang) * env];
    }
    function speed(t) {
      var dip = 1 - floor;
      for (var c = 0; c < corners.length; c++) {
        dip -= (1 - floor) * Math.exp(-0.5 * Math.pow((t - corners[c]) / width, 2));
      }
      return Math.max(floor, dip + floor);
    }

    var steps = n * 4, cum = [0], acc = 0;
    for (var i = 1; i <= steps; i++) { acc += speed(i / steps); cum.push(acc); }
    var total = cum[cum.length - 1];

    var out = [];
    for (var f = 1; f < n; f++) {
      var t = f / n;
      var s = cum[Math.min(steps, Math.floor(t * steps))] / total;
      var p = pos(s);
      var fr = lerp(a, b, smooth(Math.max(0, (s - inS) / (1 - inS))));
      fr.ix += p[0];
      fr.iy += p[1];
      out.push(fr);
    }
    return out;
  }

  /* Every frame's axis values, in order: a hold on each step, then the
     transition to the next. `blink` shuts the upper lid through each change;
     `jitter` is a fast small iris tremor at a fixed frequency. */
  function timeline(seq, fps, hold, trans, blink, jitter, roll) {
    var frames = [];
    var holdN = Math.max(1, Math.round(hold * fps));
    var transN = Math.max(2, Math.round(trans * fps));
    for (var i = 0; i < seq.length; i++) {
      var a = stepAxes(seq[i]);
      for (var h = 0; h < holdN; h++) frames.push(axesOf(a));
      if (i + 1 < seq.length) {
        var b = stepAxes(seq[i + 1]);
        if (roll && i === 0) { frames = frames.concat(arcFrames(a, b, roll, fps)); continue; }
        for (var f = 1; f < transN; f++) {
          var fr = lerp(a, b, ease(f / transN));
          if (blink) { var s = blinkShape(f / transN); fr.lt = fr.lt * (1 - s) + s; }
          frames.push(fr);
        }
      }
    }
    if (jitter) {
      for (var j = 0; j < frames.length; j++) {
        var ph = j / fps * 17 * 2 * Math.PI;
        frames[j].ix += jitter * Math.sin(ph);
        frames[j].iy += jitter * 0.55 * Math.sin(ph * 1.7 + 1.1);
      }
    }
    return frames;
  }

  /* ── drawing ───────────────────────────────────────────────── */

  var svg = mark.querySelector('svg');
  var lidTop = svg.querySelector('.lid-top');
  var lidBot = svg.querySelector('.lid-bottom');
  var tf = lidTop.querySelector('.lid-fill'), te = lidTop.querySelector('.lid-edge');
  var bf = lidBot.querySelector('.lid-fill'), be = lidBot.querySelector('.lid-edge');
  var shown = null;

  function draw(a) {
    if (!shown || a.lt !== shown.lt || a.lb !== shown.lb || a.tilt !== shown.tilt) {
      var p = lidPaths(a.lt, a.lb, a.tilt);
      tf.setAttribute('d', p[0]); te.setAttribute('d', p[1]);
      bf.setAttribute('d', p[2]); be.setAttribute('d', p[3]);
      lidTop.style.setProperty('--lid', a.lt.toFixed(4));
      lidBot.style.setProperty('--lid', a.lb.toFixed(4));
    }
    if (!shown || a.hat !== shown.hat) mark.style.setProperty('--hat', a.hat.toFixed(3));
    if (!shown || a.ix !== shown.ix) mark.style.setProperty('--iris-x', a.ix.toFixed(3));
    if (!shown || a.iy !== shown.iy) mark.style.setProperty('--iris-y', a.iy.toFixed(3));
    if (!shown || a.ps !== shown.ps) mark.style.setProperty('--pupil-s', a.ps.toFixed(4));
    if (!shown || a.gl !== shown.gl) mark.style.setProperty('--glint-s', a.gl.toFixed(4));
    shown = a;
    if (numbersOn) showNumbers(a);
  }

  /* ── the numbers under him ─────────────────────────────────── */

  var LABEL = { lt: 'top lid', lb: 'bottom lid', tilt: 'tilt', hat: 'hat', ix: 'iris x', iy: 'iris y', ps: 'pupil', gl: 'glint' };
  var PLACES = { lt: 2, lb: 2, tilt: 2, hat: 1, ix: 1, iy: 1, ps: 2, gl: 2 };
  var numbers = document.getElementById('numbers');
  var numbersOn = false;
  var cells = {};
  AXES.forEach(function (k) {
    var span = document.createElement('span');
    span.className = 'n';
    var i = document.createElement('i'); i.textContent = k;
    var b = document.createElement('b'); b.textContent = '';
    span.appendChild(i); span.appendChild(b);
    span.title = LABEL[k];
    numbers.appendChild(span);
    cells[k] = b;
  });
  function showNumbers(a) {
    for (var i = 0; i < AXES.length; i++) {
      var k = AXES[i], v = a[k].toFixed(PLACES[k]);
      if (cells[k].textContent !== v) cells[k].textContent = v;
    }
  }
  // the pose as the tools print it: lt=0.34 lb=0.05 tilt=0.4 ...
  function plain(a) {
    return AXES.map(function (k) { return k + '=' + String(+a[k].toFixed(3)); }).join(' ');
  }

  /* ── state ─────────────────────────────────────────────────── */

  var cur = axesOf(REST);   // the pose he holds
  var base = cur;           // the pose this frame, mid-chain or not
  var run = null;           // a chain or a pose change in flight
  var poseName = 'attentive';   // the lit pose, or null for the sliders' own
  var raf = 0;

  var opt = {
    track: true, blink: false, loop: false, caption: true
  };

  var captionEl = document.getElementById('caption');
  var captionName = document.getElementById('caption-name');
  var captionNote = document.getElementById('caption-note');
  function caption(name, note) {
    captionName.textContent = name;
    captionNote.textContent = note;
  }

  /* ── the pointer, and looking about ────────────────────────── */

  // How far the iris goes for the pointer, in viewBox units, on top of the
  // pose's own gaze: less than the masthead's 19 x 10, since the poses use
  // the range themselves.
  var REACH_X = 14, REACH_Y = 8;
  var EYE_FX = 120 / 240, EYE_FY = 142 / 210;
  var gaze = { x: 0, y: 0 }, gazeTo = { x: 0, y: 0 };
  var last = null;
  var idleTimer = null, wanderTimer = null;

  function track(px, py) {
    var box = svg.getBoundingClientRect();
    if (!box.width) return;
    var cx = box.left + box.width * EYE_FX;
    var cy = box.top + box.height * EYE_FY;
    // full deflection about half his own width away; he is big here
    var spanX = Math.max(box.width * 0.55, 240);
    var spanY = Math.max(box.height * 0.5, 200);
    gazeTo.x = clamp((px - cx) / spanX, -1, 1) * REACH_X;
    gazeTo.y = clamp((py - cy) / spanY, -1, 1) * REACH_Y;
    kick();
  }

  function onPointer(e) {
    // a finger is not a pointer he can follow: the tap is on the board, below
    // him, and every pose would end up looking at it
    if (!opt.track || still.matches || e.pointerType === 'touch') return;
    last = { x: e.clientX, y: e.clientY };
    stopWandering();
    track(e.clientX, e.clientY);
    clearTimeout(idleTimer);
    idleTimer = setTimeout(startWandering, 2200);
  }

  /* saccades, not drifting: a quick move, then a pause of a plausible length */
  function startWandering() {
    if (!opt.track || still.matches || run || playing() || document.hidden) return;
    gazeTo.x = (Math.random() * 2 - 1) * REACH_X * 0.85;
    gazeTo.y = (Math.random() * 2 - 1) * REACH_Y * 0.6;
    kick();
    wanderTimer = setTimeout(startWandering, 900 + Math.random() * 2100);
  }
  function stopWandering() { clearTimeout(wanderTimer); wanderTimer = null; }

  /* ── blinks ────────────────────────────────────────────────── */

  var blinkAt = -1e9, blinkTimer = null;
  function blinkNow() {
    if (still.matches || run || base.lt > 0.6) return;
    blinkAt = performance.now();
    kick();
  }
  function scheduleBlink() {
    clearTimeout(blinkTimer);
    blinkTimer = setTimeout(function () { if (!document.hidden) blinkNow(); scheduleBlink(); }, 3500 + Math.random() * 3500);
  }

  /* ── playing a pose or a chain ─────────────────────────────── */

  function start(frames, meta) {
    meta.frames = frames;
    meta.t0 = performance.now();
    run = meta;
    kick();
  }

  function pressPose(name, instant) {
    var p = POSES[name];
    if (!p) return;
    var target = axesOf(p);
    poseName = name;
    light('pose', name);
    caption(name, p.note);
    sliders(target);
    hash();
    if (instant || still.matches || same(base, target)) {
      run = null; cur = target; base = target;
      kick();
      return;
    }
    start(timeline([base, target], FPS, 0, TRANS, opt.blink, 0, null), { kind: 'pose', name: name });
  }

  function pressChain(name) {
    var c = CHAINS[name];
    if (!c) return;
    var frames = timeline(c.seq, FPS, c.hold, c.trans, opt.blink, c.jitter, c.roll);
    poseName = null;
    light('chain', name);
    caption(name, c.note);
    if (still.matches) {
      run = null; cur = axesOf(frames[frames.length - 1]); base = cur;
      landed({ kind: 'chain', name: name });
      kick();
      return;
    }
    var n = Math.max(2, Math.round(LEAD * FPS)), lead = [];
    for (var i = 0; i < n; i++) lead.push(lerp(base, frames[0], ease(i / n)));
    start(lead.concat(frames), { kind: 'chain', name: name });
  }

  // a chain has landed: he holds its last pose, and that pad is the lit one
  function landed(r) {
    if (r.kind !== 'chain') return;
    var c = CHAINS[r.name];
    if (opt.loop && !still.matches) { pressChain(r.name); return; }
    var end = c.seq[c.seq.length - 1];
    if (typeof end === 'string') {
      poseName = end;
      light('pose', end);
      caption(end, POSES[end].note);
      sliders(cur);
      hash();
    } else {
      light(null);
      sliders(cur);
    }
  }

  /* ── the frame ─────────────────────────────────────────────── */

  function frame(now) {
    raf = 0;
    var moving = false;

    if (run) {
      // a frame's timestamp can precede the start that asked for it
      var fs = run.frames, x = Math.max(0, (now - run.t0) / 1000 * FPS), i = Math.floor(x);
      if (i >= fs.length - 1) {
        cur = axesOf(fs[fs.length - 1]); base = cur;
        var r = run; run = null;
        landed(r);
      } else {
        base = lerp(fs[i], fs[i + 1], x - i);
        if (run.kind === 'chain') sliders(base);
        moving = true;
      }
    } else {
      base = cur;
    }

    var a = axesOf(base);

    // the gaze: the bass line when he is playing, else the pointer
    var to = gazeTo, k = 0.22;
    if (playing()) { to = bassGaze(a, now); k = 0.35; }
    if (still.matches) { gaze.x = to.x; gaze.y = to.y; }
    else {
      gaze.x += (to.x - gaze.x) * k;
      gaze.y += (to.y - gaze.y) * k;
      if (Math.abs(to.x - gaze.x) > 0.02 || Math.abs(to.y - gaze.y) > 0.02) moving = true;
      else { gaze.x = to.x; gaze.y = to.y; }
    }
    a.ix += gaze.x;
    a.iy += gaze.y;

    // a blink rides on top of the pose
    var bp = (now - blinkAt) / BLINK_MS;
    if (bp >= 0 && bp < 1) { var s = blinkShape(bp); a.lt = a.lt + (1 - a.lt) * s; moving = true; }

    if (playing()) moving = true;
    if (fading()) moving = true;

    draw(a);
    if (moving) kick();
  }

  function kick() { if (!raf && !document.hidden) raf = requestAnimationFrame(frame); }

  /* ── the board ─────────────────────────────────────────────── */

  // The keyboard is the board: poses on the three letter rows in reading
  // order, chains on the same keys with shift. b, n, m and v are left for
  // the room's own controls, the digits for the light.
  var POSE_KEYS = 'qwertyuiopasdfghjklzxc';
  var CHAIN_KEYS = 'qwertyuiopasdfghjk';
  var pads = { pose: {}, chain: {} };

  function pad(kind, item, key) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'pad';
    b.setAttribute('aria-pressed', 'false');
    b.title = item.note;
    var name = document.createElement('span');
    name.className = 'pad-name';
    name.textContent = item.name;
    b.appendChild(name);
    if (key) { var kbd = document.createElement('kbd'); kbd.textContent = key; b.appendChild(kbd); }
    b.addEventListener('click', function (e) {
      touched();
      if (kind === 'pose') pressPose(item.name); else pressChain(item.name);
      // a pointer's click leaves no focus behind, or the next space bar would
      // press this pad again instead of the music
      if (e.detail) b.blur();
    });
    pads[kind][item.name] = b;
    return b;
  }

  var posePads = document.getElementById('pose-pads');
  var chainPads = document.getElementById('chain-pads');
  D.poses.forEach(function (p, i) { posePads.appendChild(pad('pose', p, POSE_KEYS[i] ? POSE_KEYS[i].toUpperCase() : '')); });
  D.chains.forEach(function (c, i) { chainPads.appendChild(pad('chain', c, CHAIN_KEYS[i] ? '⇧' + CHAIN_KEYS[i].toUpperCase() : '')); });

  function light(kind, name) {
    ['pose', 'chain'].forEach(function (k) {
      Object.keys(pads[k]).forEach(function (n) {
        var on = k === kind && n === name;
        pads[k][n].setAttribute('aria-pressed', on ? 'true' : 'false');
        pads[k][n].classList.toggle('playing', on && k === 'chain');
      });
    });
  }

  /* the sliders */
  var RANGE = { lt: [0, 1, 0.01], lb: [0, 1, 0.01], tilt: [-1, 1, 0.01], hat: [-8, 8, 0.1], ix: [-24, 24, 0.5], iy: [-14, 14, 0.5], ps: [0.4, 1.4, 0.01], gl: [0, 1.6, 0.01] };
  var slidersEl = document.getElementById('sliders');
  var inputs = {}, outputs = {};
  AXES.forEach(function (k) {
    var row = document.createElement('label');
    row.className = 'axis';
    var name = document.createElement('span');
    name.className = 'axis-name';
    name.textContent = k;
    var small = document.createElement('small');
    small.textContent = LABEL[k];
    name.appendChild(small);
    var input = document.createElement('input');
    input.type = 'range';
    input.min = RANGE[k][0]; input.max = RANGE[k][1]; input.step = RANGE[k][2];
    input.value = REST[k];
    input.setAttribute('aria-label', LABEL[k]);
    var out = document.createElement('output');
    out.textContent = (+REST[k]).toFixed(PLACES[k]);
    row.appendChild(name); row.appendChild(input); row.appendChild(out);
    slidersEl.appendChild(row);
    inputs[k] = input; outputs[k] = out;
    input.addEventListener('input', own);
  });
  function sliders(a) {
    for (var i = 0; i < AXES.length; i++) {
      var k = AXES[i], v = a[k].toFixed(PLACES[k]);
      if (outputs[k].textContent !== v) { inputs[k].value = a[k]; outputs[k].textContent = v; }
    }
  }
  // a slider moved: the pose is the sliders', set as he is dragged
  function own() {
    touched();
    var a = {};
    for (var i = 0; i < AXES.length; i++) { var k = AXES[i]; a[k] = +inputs[k].value; outputs[k].textContent = a[k].toFixed(PLACES[k]); }
    run = null; cur = a; base = a;
    if (poseName !== null || captionName.textContent !== 'eight numbers') {
      poseName = null;
      light(null);
      caption('eight numbers', 'Yours. Copy them from the board; the link carries them too.');
    }
    hash();
    kick();
  }

  document.getElementById('rest').addEventListener('click', function () { touched(); pressPose('attentive'); });

  var copyBtn = document.getElementById('copy');
  copyBtn.addEventListener('click', function () {
    var text = plain(cur);
    var done = function () { copyBtn.textContent = 'Copied'; setTimeout(function () { copyBtn.textContent = 'Copy the numbers'; }, 1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { window.prompt('The eight numbers', text); });
    else window.prompt('The eight numbers', text);
  });

  /* the options */
  function bindOption(id, key, fn) {
    var box = document.getElementById(id);
    box.addEventListener('change', function () { opt[key] = box.checked; if (fn) fn(box.checked); });
    opt[key] = box.checked;
    return box;
  }
  var numbersBox = bindOption('opt-numbers', 'numbers', function (on) {
    numbersOn = on;
    numbers.hidden = !on;
    if (on && shown) showNumbers(shown);
  });
  var trackBox = bindOption('opt-track', 'track', function (on) {
    if (!on) { stopWandering(); clearTimeout(idleTimer); gazeTo.x = 0; gazeTo.y = 0; kick(); }
    else if (last) track(last.x, last.y);
  });
  bindOption('opt-blink', 'blink');
  bindOption('opt-loop', 'loop');
  bindOption('opt-caption', 'caption', function (on) { captionEl.classList.toggle('off', !on); });
  function setBox(box, on) { box.checked = on; box.dispatchEvent(new Event('change')); }

  /* ── open and shut ─────────────────────────────────────────── */

  var body = document.body;
  var fold = document.getElementById('fold');
  var handle = document.getElementById('handle');
  var tab = document.getElementById('tab');
  var boardOpen = wide.matches;

  function setBoard(open) {
    boardOpen = open;
    body.setAttribute('data-board', open ? 'open' : 'hidden');
    fold.setAttribute('aria-expanded', String(open));
    handle.setAttribute('aria-expanded', String(open));
    tab.setAttribute('aria-expanded', String(open));
    fold.innerHTML = (open ? 'Hide the board ' : 'Show the board ') + '<kbd>B</kbd>';
  }
  function toggleBoard() { setBoard(!boardOpen); }
  fold.addEventListener('click', toggleBoard);
  handle.addEventListener('click', toggleBoard);
  tab.addEventListener('click', toggleBoard);
  if (wide.addEventListener) wide.addEventListener('change', function () { body.removeAttribute('data-board'); boardOpen = wide.matches; });

  /* ── the light ─────────────────────────────────────────────── */

  var theme = '';
  var themeButtons = document.getElementById('themes').querySelectorAll('button');
  var metas = document.querySelectorAll('meta[name="theme-color"]');
  var PAPER = { newsprint: '#f7f3ea', noir: '#0a0a0b' };
  function setTheme(name) {
    theme = name || '';
    if (theme) document.documentElement.setAttribute('data-theme', theme);
    else document.documentElement.removeAttribute('data-theme');
    for (var i = 0; i < themeButtons.length; i++) {
      themeButtons[i].setAttribute('aria-pressed', String(themeButtons[i].getAttribute('data-theme') === theme));
    }
    for (var m = 0; m < metas.length; m++) {
      var media = metas[m].getAttribute('media') || '';
      metas[m].setAttribute('content', theme ? PAPER[theme] : (media.indexOf('dark') >= 0 ? PAPER.noir : PAPER.newsprint));
    }
    hash();
  }
  for (var tb = 0; tb < themeButtons.length; tb++) {
    themeButtons[tb].addEventListener('click', function () { setTheme(this.getAttribute('data-theme')); });
  }

  /* ── the hash: a pose or the numbers, and the light ────────── */

  var hashTimer = null;
  function hash() {
    clearTimeout(hashTimer);
    hashTimer = setTimeout(function () {
      var parts = [];
      if (poseName) parts.push(poseName);
      else {
        AXES.forEach(function (k) { if (cur[k] !== REST[k]) parts.push(k + '=' + String(+cur[k].toFixed(3))); });
      }
      if (theme) parts.push(theme);
      var want = parts.length ? '#' + parts.join(',') : location.pathname + location.search;
      if (location.hash !== want && !(parts.length === 0 && !location.hash)) {
        // replaceState, so the back button is not a tour of every face pulled;
        // Safari rations it, hence the delay, and a refusal costs nothing
        try { history.replaceState(null, '', want); } catch (e) { /* kept as it was */ }
      }
    }, 250);
  }

  function readHash() {
    var h = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (!h) return false;
    var parts = h.split(','), pose = null, mine = null, lit = null;
    parts.forEach(function (p) {
      if (POSES[p]) pose = p;
      else if (p === 'noir' || p === 'newsprint') lit = p;
      else {
        var kv = p.split('=');
        if (kv.length === 2 && AXES.indexOf(kv[0]) >= 0 && isFinite(+kv[1])) {
          mine = mine || axesOf(REST);
          mine[kv[0]] = clamp(+kv[1], RANGE[kv[0]][0], RANGE[kv[0]][1]);
        }
      }
    });
    if (lit !== null) setTheme(lit);
    if (pose) pressPose(pose, true);
    else if (mine) {
      run = null; cur = mine; base = mine;
      poseName = null;
      sliders(mine);
      light(null);
      caption('eight numbers', 'Yours. Copy them from the board; the link carries them too.');
      kick();
    }
    return !!(pose || mine);
  }

  /* ── the bass, and him playing it ──────────────────────────── */

  // James's walking bass (public/assets/score/theme-light.musicxml, P2),
  // on its own: one note a beat, 132 to the minute, two bars that go round.
  // /media/audio/bass-loop.wav is the show's bed cue (tools/README.md): the
  // two bars, cut to join without a seam, 174,545 samples at 48 kHz, which
  // is two bars at 132 to within a sample. Decoded and looped in Web Audio
  // it joins sample for sample, so the bass goes on until it is faded out,
  // and where he is in the bar is read from the clock it plays by. A media
  // element's loop attribute gaps at the join; that is why not <audio>.
  var BPM = 132, SPB = 60 / BPM;
  var BASS = [40, 39, 38, 37, 31, 37, 43, 38];  // MIDI, sounding
  var FADE = 16 * SPB;  // four bars, in seconds
  var LOOP = '/media/audio/bass-loop.wav';

  var actx = null, gainNode = null;
  var source = null, startAt = 0;  // the loop playing now, and its start on the context's clock
  var bytes = null, buffer = null, decoding = false, loadFailed = false, waiting = [];
  var music = { state: 'off', t0: 0, g0: 0, stop: null };
  var presses = 0;  // so a fade-in's callback knows it is still the latest press
  var playBtn = document.getElementById('play');
  var stopBtn = document.getElementById('stop');
  var stateEl = document.getElementById('music-state');
  var fetched = false;

  // the first touch of the board fetches the loop, so a fade-in later does
  // not wait on the network; it is decoded once there is a context to play it
  function touched() {
    if (fetched) return;
    fetched = true;
    if (!window.fetch) { loadFailed = true; return; }
    fetch(LOOP).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.arrayBuffer();
    }).then(function (b) { bytes = b; settle(); }, function () { loadFailed = true; settle(); });
  }

  function ensureAudio() {
    if (actx) { if (actx.state === 'suspended') actx.resume(); return; }
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try {
      actx = new AC();
      gainNode = actx.createGain();
      gainNode.gain.value = 0;
      gainNode.connect(actx.destination);
    } catch (e) { actx = null; gainNode = null; }
  }

  // calls back with the decoded loop: now if it is in hand, else once it is
  function whenReady(ok, fail) {
    waiting.push([ok, fail]);
    settle();
  }
  function settle() {
    if (!waiting.length || !actx) return;
    if (buffer || loadFailed) {
      var w = waiting; waiting = [];
      w.forEach(function (p) { if (buffer) p[0](buffer); else p[1](); });
      return;
    }
    if (!bytes || decoding) return;
    decoding = true;
    var b = bytes; bytes = null;  // decoding may detach the buffer
    actx.decodeAudioData(b, function (buf) { buffer = buf; decoding = false; settle(); },
      function () { loadFailed = true; decoding = false; settle(); });
  }

  function startLoop(buf) {
    source = actx.createBufferSource();
    source.buffer = buf;
    source.loop = true;
    source.connect(gainNode);
    startAt = actx.currentTime + 0.02;
    source.start(startAt);
  }

  // seconds into the loop's own time, what is sounding now
  function pos() {
    if (!source) return 0;
    var t = actx.currentTime - startAt - (actx.outputLatency || actx.baseLatency || 0);
    return t > 0 ? t : 0;
  }

  function playing() { return music.state !== 'off' && source !== null; }
  function fading() { return music.state === 'in' || music.state === 'out'; }

  // The fade is a square law, in by x squared and out by its mirror, which is
  // ffmpeg's afade curve=qua: production can lay the clean cue under the
  // screen recording with the same fade he performed. The gain is kept here
  // as well as scheduled, since reading an AudioParam mid-curve is not the
  // same everywhere.
  function shape(from, to, x) {
    return from < to ? from + (to - from) * x * x : to + (from - to) * (1 - x) * (1 - x);
  }
  function gainNow() {
    if (music.state === 'on') return 1;
    if (music.state === 'off' || !source) return 0;
    var x = clamp((performance.now() - music.t0) / 1000 / FADE, 0, 1);
    return music.state === 'in' ? shape(music.g0, 1, x) : shape(music.g0, 0, x);
  }

  function ramp(from, to) {
    var g = gainNode.gain, t = actx.currentTime;
    if (g.cancelAndHoldAtTime) g.cancelAndHoldAtTime(t); else g.cancelScheduledValues(t);
    g.setValueAtTime(from, t);
    var n = 96, c = new Float32Array(n);
    for (var i = 0; i < n; i++) c[i] = shape(from, to, i / (n - 1));
    g.setValueCurveAtTime(c, t + 0.01, FADE);
  }

  function fadeIn() {
    touched();
    ensureAudio();
    if (!actx) { stateEl.textContent = 'This browser has no Web Audio, so no bass.'; return; }
    clearTimeout(music.stop);
    var g0 = gainNow();  // nought, unless a fade-out is being caught
    var press = ++presses;
    music.state = 'in'; music.t0 = performance.now(); music.g0 = g0;
    musicUI();
    kick();
    whenReady(function (buf) {
      if (music.state !== 'in' || press !== presses) return;  // changed his mind while it loaded
      if (!source) startLoop(buf);
      music.t0 = performance.now();  // the fade is counted from the sound, not the press
      ramp(g0, 1);
      music.stop = setTimeout(function () { if (music.state === 'in') { music.state = 'on'; musicUI(); } }, FADE * 1000);
      musicUI();
    }, function () {
      music.state = 'off';
      musicUI();
      stateEl.textContent = 'The bass would not load.';
    });
  }

  function fadeOut() {
    if (!source) { stopMusic(); return; }  // pressed again before the loop arrived
    clearTimeout(music.stop);
    var g0 = gainNow();
    music.state = 'out'; music.t0 = performance.now(); music.g0 = g0;
    ramp(g0, 0);
    music.stop = setTimeout(stopMusic, FADE * 1000 + 60);
    musicUI();
  }

  function stopMusic() {
    clearTimeout(music.stop);
    music.state = 'off';
    if (source) {
      // a short ramp before the stop, so the button does not click
      var g = gainNode.gain, t = actx.currentTime, s = source;
      if (g.cancelAndHoldAtTime) g.cancelAndHoldAtTime(t); else g.cancelScheduledValues(t);
      g.setTargetAtTime(0, t, 0.01);
      try { s.stop(t + 0.06); } catch (e) { /* already stopped */ }
      source = null;
    }
    if (last && opt.track) track(last.x, last.y); else { gazeTo.x = 0; gazeTo.y = 0; }
    musicUI();
    kick();
  }

  // the one key: in, then out; a fade-out caught early fades back in
  function toggleMusic() {
    if (music.state === 'off' || music.state === 'out') fadeIn(); else fadeOut();
  }
  playBtn.addEventListener('click', function (e) { toggleMusic(); if (e.detail) playBtn.blur(); });
  if (stopBtn) stopBtn.addEventListener('click', function (e) { stopMusic(); if (e.detail) stopBtn.blur(); });

  var lastBar = -1;
  function musicUI() {
    var st = music.state;
    playBtn.innerHTML = (st === 'off' ? 'Fade in ' : st === 'out' ? 'Fade back in ' : 'Fade out ') + '<kbd>space</kbd>';
    playBtn.setAttribute('aria-pressed', String(st !== 'off'));
    stateEl.classList.toggle('on', st !== 'off');
    lastBar = -1;
    barUI();
  }
  // "Fading in · bar 2 of 4" counts the fade's own bars from the press, so
  // he knows how long is left; "Playing · bar 14" counts from the top.
  function barUI() {
    var st = music.state;
    if (st === 'off') { stateEl.textContent = 'Not playing.'; return; }
    if (!source) { stateEl.textContent = 'Fading in · fetching the bass'; lastBar = -1; return; }
    var bar, text;
    if (st === 'on') {
      bar = Math.floor(pos() / SPB / 4) + 1;
      text = 'Playing · bar ' + bar;
    } else {
      bar = clamp(Math.floor((performance.now() - music.t0) / 1000 / SPB / 4) + 1, 1, 4);
      text = (st === 'in' ? 'Fading in · bar ' : 'Fading out · bar ') + bar + ' of 4';
    }
    if (bar === lastBar) return;
    lastBar = bar;
    stateEl.textContent = text;
  }

  // Him playing: the iris walks the line, low notes left and down, high ones
  // right; the pupil tightens on each attack; the hat nods on two and four.
  var bassTo = { x: 0, y: 0 };
  function bassGaze(a, now) {
    var t = pos();
    var beat = Math.floor(t / SPB), ph = t / SPB - beat;
    var midi = BASS[beat % BASS.length];
    bassTo.x = (midi - 37) * 2.0;
    bassTo.y = 1.5 + (37 - midi) * 0.4;
    if (!still.matches) {
      var pluck = 1 - smooth(ph / 0.18);
      a.ps *= 1 - 0.10 * pluck;
      if (beat % 4 === 1 || beat % 4 === 3) a.hat -= 2.2 * (1 - smooth(ph / 0.5));
    }
    barUI();
    return bassTo;
  }

  /* ── keys ──────────────────────────────────────────────────── */

  window.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    var tag = e.target && e.target.tagName;
    var k = e.key;
    if (k === ' ') {
      // on a control the space bar is the control's
      if (tag === 'BUTTON' || tag === 'INPUT' || tag === 'A' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      e.preventDefault();
      toggleMusic();
      return;
    }
    if (!k || k.length !== 1 || tag === 'TEXTAREA' || (tag === 'INPUT' && e.target.type !== 'range' && e.target.type !== 'checkbox')) return;
    var lower = k.toLowerCase();
    if (e.shiftKey) {
      var ci = CHAIN_KEYS.indexOf(lower);
      if (ci >= 0 && ci < D.chains.length) { e.preventDefault(); touched(); pressChain(D.chains[ci].name); }
      return;
    }
    var pi = POSE_KEYS.indexOf(lower);
    if (pi >= 0 && pi < D.poses.length) { e.preventDefault(); touched(); pressPose(D.poses[pi].name); return; }
    switch (lower) {
      case 'b': toggleBoard(); break;
      case 'n': setBox(numbersBox, !numbersBox.checked); break;
      case 'm': toggleMusic(); break;
      case 'v': setBox(trackBox, !trackBox.checked); break;
      case '1': setTheme('newsprint'); break;
      case '2': setTheme('noir'); break;
      case '3': setTheme(''); break;
      default: return;
    }
    e.preventDefault();
  });

  /* ── wiring ────────────────────────────────────────────────── */

  var stage = document.getElementById('stage');
  stage.addEventListener('click', function (e) {
    if (e.target.closest('a, button, input, label')) return;
    blinkNow();
  });

  window.addEventListener('pointermove', onPointer, { passive: true });
  window.addEventListener('pointerdown', onPointer, { passive: true });
  window.addEventListener('hashchange', readHash);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { if (raf) { cancelAnimationFrame(raf); raf = 0; } stopWandering(); }
    else kick();
  });

  function motionChanged() {
    if (still.matches) {
      stopWandering();
      clearTimeout(idleTimer);
      if (run) { cur = axesOf(run.frames[run.frames.length - 1]); base = cur; var r = run; run = null; landed(r); }
      gazeTo.x = 0; gazeTo.y = 0;
    }
    kick();
  }
  if (still.addEventListener) still.addEventListener('change', motionChanged);

  // open on the pose the link names, else at rest
  if (!readHash()) { light('pose', 'attentive'); draw(cur); }
  scheduleBlink();
  if (!still.matches && opt.track) idleTimer = setTimeout(startWandering, 2600);
  musicUI();
})();
