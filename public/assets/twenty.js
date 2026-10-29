/* ─────────────────────────────────────────────────────────────
   AgentSee — the copy button on /twenty-questions/

   The prompt is on the page in full, so without JavaScript a
   visitor selects it and copies it. With JavaScript, a button
   does that in one press. The button is added here rather than
   in the HTML so that it never appears unless it can work.
   ───────────────────────────────────────────────────────────── */
(function () {
  'use strict';

  var slot = document.getElementById('copy-slot');
  var pre = document.getElementById('prompt');
  if (!slot || !pre || !navigator.clipboard || !navigator.clipboard.writeText) return;

  var button = document.createElement('button');
  button.type = 'button';
  button.textContent = 'Copy the questions';
  slot.appendChild(button);

  var timer = 0;
  function say(text) {
    button.textContent = text;
    clearTimeout(timer);
    timer = setTimeout(function () { button.textContent = 'Copy the questions'; }, 2400);
  }

  button.addEventListener('click', function () {
    navigator.clipboard.writeText(pre.textContent).then(
      function () { say('Copied. Now paste it into a chat.'); },
      function () {
        // Permission refused, or a browser that lies about support: fall
        // back to selecting the text so a keyboard copy works.
        var range = document.createRange();
        range.selectNodeContents(pre);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        say('Selected. Press copy.');
      }
    );
  });
})();
