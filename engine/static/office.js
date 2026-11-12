// The office's one script (engine/guests.py). A form marked data-live is sent
// with fetch and the section it belongs to comes back as the server renders
// it and is put in place, with the word beside the button; nothing reloads
// and nothing jumps. The conversation's form (data-say) streams the office's
// reply as server-sent events, shown as it is written; if the line drops, or
// the page comes up while a reply is still being written, the script asks
// /idea/live after it every few seconds and puts it in place when it lands.
// Without script, every form posts as a form and the page comes back whole.
(function () {
  "use strict";
  if (!window.fetch || !window.FormData || !window.TextDecoder || !window.ReadableStream) return;

  function grow(area) {
    area.style.height = "auto";
    area.style.height = Math.min(area.scrollHeight, 240) + "px";
  }

  function fade(root) {  // "Saved." goes after a moment; a refusal stays until the next try
    root.querySelectorAll("[data-said]").forEach(function (p) {
      if (p.classList.contains("honesty")) return;
      setTimeout(function () { p.classList.add("fading"); }, 2400);
      setTimeout(function () { p.remove(); }, 3200);
    });
  }

  function settle(root) {  // what a section needs once it is on the page
    var area = root.querySelector("form[data-say] textarea");
    if (area) { grow(area); area.focus(); }
    fade(root);
    if (root.querySelector("[data-writing]")) poll();  // the page came up mid-reply: ask after it
  }

  var asking = false, landed = null;
  function poll(then) {  // a reply is being written (the page said so, or the stream broke): ask every few seconds,
    // and put the sections in place when it has landed; the box comes back open with them
    if (then) landed = then;
    if (asking) return;
    asking = true;
    setTimeout(function () {
      fetch("/idea/live", { credentials: "same-origin", headers: { "Accept": "application/json" } }).then(function (r) {
        if (!r.ok) throw new Error("no answer");
        return r.json();
      }).then(function (j) {
        asking = false;
        if (j.go) { window.location.href = j.go; return; }
        if (j.writing) { poll(); return; }
        (j.sections || []).forEach(function (s) { swap(s.id, s.html); });
        var then = landed;
        landed = null;
        if (then) then();
      }).catch(function () { asking = false; poll(); });
    }, 3000);
  }

  function swap(id, html) {  // the section as the server now renders it, in place of the old one
    var old = document.getElementById(id);
    if (!old) return;
    var t = document.createElement("template");
    t.innerHTML = html.trim();
    var fresh = t.content.firstElementChild;
    if (!fresh) return;
    old.replaceWith(fresh);
    settle(fresh);
    document.dispatchEvent(new CustomEvent("office:swap", { detail: { id: id } }));  // for time.js's calendar
  }

  function word(form, text, bad) {  // the sentence beside the button, in place of any earlier one
    var old = form.querySelector("[data-said]");
    if (old) old.remove();
    var p = document.createElement("p");
    p.className = "said" + (bad ? " honesty" : "");
    p.setAttribute("data-said", "");
    var b = document.createElement("b");
    b.textContent = text;
    p.appendChild(b);
    var button = form.querySelector("button[type=submit]");
    var slot = button ? button.closest("p, .composer, .row") : null;
    if (slot && slot.parentNode === form) form.insertBefore(p, slot); else form.appendChild(p);
    if (!bad) fade(form);
  }

  function busy(form, on) {
    var button = form.querySelector("button[type=submit]");
    if (!button) return;
    button.disabled = on;
    if (on) button.setAttribute("aria-busy", "true"); else button.removeAttribute("aria-busy");
  }

  function send(form) {  // a save: the answer is the section, rendered afresh, or the refusal
    busy(form, true);
    fetch(form.action, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Office": "live", "Accept": "application/json" },
      body: new FormData(form)
    }).then(function (r) {
      return r.json();
    }).then(function (j) {
      if (j.go) { window.location.href = j.go; return; }
      if (!j.ok) { word(form, j.error || "That did not go through; try again", true); return; }
      (j.sections || []).forEach(function (s) { swap(s.id, s.html); });
    }).catch(function () {
      form.removeAttribute("data-live");
      form.submit();  // as without script
    }).then(function () {
      busy(form, false);
    });
  }

  function add(log, cls, html) {
    var d = document.createElement("div");
    d.className = "turn " + cls;
    d.innerHTML = html;
    log.appendChild(d);
    d.scrollIntoView({ block: "end", behavior: "smooth" });
    return d;
  }

  function same(a, b) {  // the guest's words, as typed and as the page renders them back
    return a.replace(/\s+/g, " ").trim() === b.replace(/\s+/g, " ").trim();
  }

  function talk(form) {  // the conversation: the reply streamed into the log as it is written
    var sect = form.closest("section") || document;
    var log = sect.querySelector("[data-log]");
    var area = form.querySelector("textarea");
    var mine = area ? area.value.trim() : "";
    if (!log || (area && !mine)) return;
    var data = new URLSearchParams(new FormData(form)).toString();
    var bubble = mine ? add(log, "guest", "<p></p>") : null;
    if (bubble) bubble.querySelector("p").textContent = mine;
    var agent = add(log, "agent", '<p class="writing"><span></span><span></span><span></span></p><p class="muted patience"></p>');
    var p = agent.querySelector("p"), patience = agent.querySelector(".patience");
    var text = "", settled = false;
    busy(form, true);
    if (area) { area.value = ""; grow(area); area.disabled = true; }
    // Words while nothing has come yet: a cold start reads the episodes before a word, and a run-sheet is a page.
    var waits = [
      setTimeout(function () { patience.textContent = "The office is reading the episodes first; a moment."; }, 8000),
      setTimeout(function () { patience.textContent = "Still at it. A run-sheet is a page, and takes a minute or so."; }, 30000)
    ];

    function release() {  // the box, back to the guest
      busy(form, false);
      if (area) { area.disabled = false; area.focus(); }
    }
    function finish(j) {  // done: the turn whole, the sections it changed in place, the box back; the stream's
      // close is not waited for, so the mail the office may send after is not waited for either
      settled = true;
      waits.forEach(clearTimeout);
      var t = document.createElement("template");
      t.innerHTML = j.html;  // the turn as the server renders it, in place of the one written live
      var whole = t.content.firstElementChild;
      if (whole) { agent.replaceWith(whole); agent = whole; }
      var sections = j.sections || [];
      sections.forEach(function (s) { swap(s.id, s.html); });
      if (sections.some(function (s) { return s.id === "conversation"; })) return;  // the box came back with it
      release();
      agent.scrollIntoView({ block: "end", behavior: "smooth" });
    }
    function refused(j) {  // fail: the sentence; the words back in the box when the office did not keep them
      settled = true;
      waits.forEach(clearTimeout);
      agent.innerHTML = '<p class="honesty"><b></b></p>';
      agent.querySelector("b").textContent = j.m;
      if (!j.kept && bubble) bubble.remove();
      if (j.writing) {  // an earlier reply is still being written: its arrival brings the box back, the words in it
        poll(function () {
          var fresh = document.querySelector("#conversation form[data-say] textarea");
          if (fresh && !j.kept && mine) { fresh.value = mine; grow(fresh); }
        });
        return;
      }
      if (!j.kept && area && mine) { area.value = mine; grow(area); }
      release();
    }
    function lost() {  // the stream broke, or closed without a word: the office is most likely still writing, and
      // keeps the reply whether or not anyone is listening, so ask after it rather than start again
      if (settled) return;
      settled = true;
      waits.forEach(clearTimeout);
      if (!patience.parentNode) agent.appendChild(patience);
      patience.textContent = "The line dropped. The office is finishing its reply; it appears here when it has.";
      poll(function () {  // landed; if what was said never reached the office, it goes back in the box
        var turns = document.querySelectorAll("#conversation .turn.guest"), last = turns[turns.length - 1];
        var fresh = document.querySelector("#conversation form[data-say] textarea");
        if (mine && fresh && !(last && same(last.textContent, mine))) { fresh.value = mine; grow(fresh); }
      });
    }

    function handle(chunk) {
      var ev = "message", d = "";
      chunk.split("\n").forEach(function (l) {
        if (l.indexOf("event:") === 0) ev = l.slice(6).trim();
        else if (l.indexOf("data:") === 0) d += l.slice(5).trim();
      });
      if (!d) return;  // a comment: the office keeping the line open while the model is silent
      var j;
      try { j = JSON.parse(d); } catch (err) { return; }
      if (ev === "delta") { waits.forEach(clearTimeout); patience.remove(); text += j.t; p.className = ""; p.textContent = text; }
      else if (ev === "done") finish(j);
      else if (ev === "fail") refused(j);
    }

    fetch(form.action, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Accept": "text/event-stream", "Content-Type": "application/x-www-form-urlencoded" },
      body: data
    }).then(function (r) {
      if (!r.ok || !r.body || (r.headers.get("Content-Type") || "").indexOf("text/event-stream") < 0) {
        settled = true;
        window.location.reload();  // not the stream: signed out, or turned away at the door; the page says which
        return;
      }
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (x) {
          if (x.done) { if (buf.trim()) handle(buf); return; }
          buf += dec.decode(x.value, { stream: true });
          var parts = buf.split("\n\n");
          buf = parts.pop();
          parts.forEach(handle);
          return pump();
        });
      }
      return pump();
    }).then(function () {
      if (!settled) lost();
    }).catch(lost);
  }

  document.addEventListener("input", function (e) {
    if (e.target.matches("form[data-say] textarea")) grow(e.target);
  });
  document.addEventListener("keydown", function (e) {  // Enter sends; Shift+Enter breaks a line
    if (!e.target.matches("form[data-say] textarea") || e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    var form = e.target.form, button = form.querySelector("button[type=submit]");
    if (button && button.disabled) return;
    e.preventDefault();
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
  });
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || !(form instanceof HTMLFormElement)) return;  // the pad refused an empty signature
    if (form.hasAttribute("data-say")) { e.preventDefault(); talk(form); }
    else if (form.hasAttribute("data-live")) { e.preventDefault(); send(form); }
  });
  settle(document);
})();
