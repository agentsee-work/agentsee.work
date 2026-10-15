/* AgentSee — the episode player.
   A chapter, a moment or a transcript turn is a link to #t=SECONDS. With this
   script the link seeks the player instead of changing the page; without it
   the link is a time, which is still true. Nothing else here: no state, no
   storage, no requests. */
(function () {
  'use strict';
  var audio = document.querySelector('audio[data-player]');
  if (!audio) return;

  function play() { var p = audio.play(); if (p && p.catch) p.catch(function () {}); }

  function seek(t, andPlay) {
    if (audio.readyState >= 1) {
      audio.currentTime = t;
      if (andPlay) play();
      return;
    }
    audio.addEventListener('loadedmetadata', function once() {
      audio.removeEventListener('loadedmetadata', once);
      audio.currentTime = t;
      if (andPlay) play();
    });
    audio.preload = 'metadata';
    audio.load();
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[data-t]') : null;
    if (!a) return;
    e.preventDefault();
    seek(+a.getAttribute('data-t'), true);
    if (history.replaceState) history.replaceState(null, '', a.getAttribute('href'));
  });

  // Arriving at /podcast/ep1/#t=181 lands at 3:01, paused: autoplay is rude.
  var m = /[#&]t=(\d+)/.exec(location.hash);
  if (m) seek(+m[1], false);
})();
