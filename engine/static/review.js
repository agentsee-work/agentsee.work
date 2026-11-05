// The review (engine/review.py): "At this moment" puts the player's time in the
// note's field, and a note's time seeks the player. The listeners sit on the
// document, so the notes section office.js puts back in place keeps them.
// Without script the field is typed by hand and the button stays hidden.
(function () {
  "use strict";
  var video = document.querySelector("video[data-episode]");
  if (!video) return;

  function mmss(t) {
    t = Math.round(t);
    var h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  }

  function reveal() {
    var here = document.querySelector("form[data-note] [data-here]");
    if (here) here.hidden = false;
  }
  reveal();
  document.addEventListener("office:swap", reveal);

  document.addEventListener("click", function (e) {
    var here = e.target.closest ? e.target.closest("form[data-note] [data-here]") : null;
    if (here) {
      var form = here.closest("form");
      var at = form.querySelector("[name=at]");
      at.value = mmss(video.currentTime);
      if (!video.paused) video.pause();
      var text = form.querySelector("textarea");
      if (text) text.focus();
      return;
    }
    var a = e.target.closest ? e.target.closest("a[data-t]") : null;
    if (!a) return;
    e.preventDefault();
    video.currentTime = +a.getAttribute("data-t");
    video.scrollIntoView({ block: "center", behavior: "smooth" });
    var p = video.play();
    if (p && p.catch) p.catch(function () {});
  });
})();
