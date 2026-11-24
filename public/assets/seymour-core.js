/* ─────────────────────────────────────────────────────────────
   AgentSee — Seymour's numbers, lids and chains

   The show's eye is eight numbers (tools/eye.py: two lids, the tilt
   of their curve, the hat's angle, the iris on two axes, the
   pupil's size and the catchlight's). The poses and the chains come
   from seymour-data.js, which tools/eyeanim.py writes from the same
   tables the renders use. The lid geometry and the chain timing
   here are ports of lid_paths() and timeline(), kept line for line
   so what plays on a page is what the reel shows.

   One copy, for every page that moves him: his room (seymour.js)
   and the small faces (seymour-face.js) on an episode's page and in
   the guest office. No dependencies, no network, no storage.
   ───────────────────────────────────────────────────────────── */

(function () {
  'use strict';

  var D = window.SEYMOUR;
  if (!D) return;

  var AXES = D.axes;
  var REST = D.rest;
  var POSES = {};
  var CHAINS = {};
  D.poses.forEach(function (p) { POSES[p.name] = p; });
  D.chains.forEach(function (c) { CHAINS[c.name] = c; });

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

  window.SEYMOUR_CORE = {
    AXES: AXES, REST: REST, POSES: POSES, CHAINS: CHAINS,
    clamp: clamp, axesOf: axesOf, stepAxes: stepAxes, lerp: lerp, same: same,
    ease: ease, smooth: smooth, blinkShape: blinkShape,
    lidPaths: lidPaths, arcFrames: arcFrames, timeline: timeline
  };
})();
