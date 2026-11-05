/* ─────────────────────────────────────────────────────────────
   AgentSee — the arrival at the door: Seymour

   The first time a browser comes to the door (no cookie of ours yet,
   which the server decides and says with the #arrival block), the
   masthead's eye is drawn big over the page and does, in a sequence,
   what the site's eye does on a click: it notices you, looks you
   over, blinks twice, is surprised, then pleased, then closes. As
   the lids meet, black spreads from the seam up and down until the
   screen is dark; then the black parts along the same seam and the
   door is behind it. About eight seconds; a tap, a click or a key
   skips to the end. Reduced motion, or no script at all, gets the
   door at once.

   The moods are the site's (public/assets/styles.css, "moods"):
   classes on the mark for one run of their animation. The look
   about, the close and the lids are this host's (static/see.css,
   "the arrival").

   No dependencies, no network, no storage.
   ───────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  var stage = document.getElementById('arrival');
  if (!stage) return;
  var mark = stage.querySelector('.mark');
  var still = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  if (!mark || (still && still.matches)) { stage.remove(); return; }

  var timers = [];
  var done = false;

  function at(ms, fn) { timers.push(setTimeout(fn, ms)); }
  function play(cls, ms) {
    mark.classList.add(cls);
    at(ms, function () { mark.classList.remove(cls); });
  }
  function end() {
    if (done) return;
    done = true;
    timers.forEach(clearTimeout);
    stage.remove();
    document.removeEventListener('keydown', onKey);
  }
  function onKey(e) {
    if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') end();
  }

  stage.classList.add('playing');
  stage.addEventListener('click', end);
  document.addEventListener('keydown', onKey);

  /* The sequence: each step is a class the stylesheets animate, and the
     time is when it begins. The close is a transition, so the lids have
     met when the black starts; the black has covered the screen when
     it starts to part; and what it parts onto is the door, because the
     stage goes transparent at that moment, under the black. */
  at(300, function () { play('react-greet', 700); });       // the hat tips: it has noticed you
  at(900, function () { play('looking', 1600); });          // it looks you over
  at(2600, function () { play('react-blink', 170); });      // a blink,
  at(3000, function () { play('react-blink', 170); });      // and another
  at(3500, function () { play('react-surprise', 800); });   // surprise
  at(4400, function () { play('react-delight', 900); });    // into delight
  at(5500, function () { mark.classList.add('closing'); }); // the slow close, .9s
  at(6500, function () { stage.classList.add('dark'); });   // the lids met: black from the seam, up and down, .5s
  at(7200, function () { stage.classList.add('opening'); }); // your eyes open onto the door, .9s
  at(8200, end);
})();
