// The office's one script (engine/guests.py). A form marked data-live is sent
// with fetch and the section it belongs to comes back as the server renders
// it and is put in place, with the word beside the button; nothing reloads
// and nothing jumps. The conversation's form (data-say) streams the office's
// reply as server-sent events, shown as it is written. Without script, or if
// a request fails, every form posts as a form and the page comes back whole.
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

  function talk(form) {  // the conversation: the reply streamed into the log as it is written
    var sect = form.closest("section") || document;
    var log = sect.querySelector("[data-log]");
    var area = form.querySelector("textarea");
    var mine = area ? area.value.trim() : "";
    if (!log || (area && !mine)) return;
    var data = new URLSearchParams(new FormData(form)).toString();
    if (mine) add(log, "guest", "<p></p>").querySelector("p").textContent = mine;
    var agent = add(log, "agent", '<p class="writing"><span></span><span></span><span></span></p>');
    var p = agent.querySelector("p");
    var text = "", sections = [];
    busy(form, true);
    if (area) { area.value = ""; grow(area); area.disabled = true; }

    function handle(chunk) {
      var ev = "message", d = "";
      chunk.split("\n").forEach(function (l) {
        if (l.indexOf("event:") === 0) ev = l.slice(6).trim();
        else if (l.indexOf("data:") === 0) d += l.slice(5).trim();
      });
      if (!d) return;
      var j;
      try { j = JSON.parse(d); } catch (err) { return; }
      if (ev === "delta") { text += j.t; p.className = ""; p.textContent = text; }
      else if (ev === "done") { agent.innerHTML = j.html; sections = j.sections || []; }
      else if (ev === "fail") { agent.innerHTML = '<p class="honesty"><b></b></p>'; agent.querySelector("b").textContent = j.m; }
    }

    fetch(form.action, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Accept": "text/event-stream", "Content-Type": "application/x-www-form-urlencoded" },
      body: data
    }).then(function (r) {
      if (!r.ok || !r.body) throw new Error("no stream");
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
      // The start, the draft and the caps change a section: it comes back rendered, and is put in place.
      sections.forEach(function (s) { swap(s.id, s.html); });
      if (sections.some(function (s) { return s.id === "conversation"; })) return;
      busy(form, false);
      if (area) { area.disabled = false; area.focus(); }
      agent.scrollIntoView({ block: "end", behavior: "smooth" });
    }).catch(function () {
      window.location.reload();
    });
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
