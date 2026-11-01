// The idea conversation (engine/idea.py): the form marked data-say is sent
// with fetch and the office's reply arrives as server-sent events, shown as
// it is written; without script, or if the stream fails, the same form posts
// and the page comes back whole. Nothing leaves the page but the form.
(function () {
  "use strict";
  var form = document.querySelector("form[data-say]");
  var log = document.querySelector("[data-log]");
  if (!form || !log || !window.fetch || !window.TextDecoder || !window.ReadableStream) return;
  var area = form.querySelector("textarea");
  var button = form.querySelector("button[type=submit]");

  function add(cls, html) {
    var d = document.createElement("div");
    d.className = "turn " + cls;
    d.innerHTML = html;
    log.appendChild(d);
    d.scrollIntoView({ block: "end", behavior: "smooth" });
    return d;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var data = new URLSearchParams(new FormData(form)).toString();
    var mine = area ? area.value.trim() : "";
    if (area && !mine) return;
    if (mine) add("guest", "<p></p>").querySelector("p").textContent = mine;
    var agent = add("agent", "<p></p>");
    var p = agent.querySelector("p");
    var text = "";
    var reload = false;
    button.disabled = true;
    if (area) { area.value = ""; area.disabled = true; }

    function handle(chunk) {
      var ev = "message", d = "";
      chunk.split("\n").forEach(function (l) {
        if (l.indexOf("event:") === 0) ev = l.slice(6).trim();
        else if (l.indexOf("data:") === 0) d += l.slice(5).trim();
      });
      if (!d) return;
      var j;
      try { j = JSON.parse(d); } catch (err) { return; }
      if (ev === "delta") { text += j.t; p.textContent = text; }
      else if (ev === "done") { agent.innerHTML = j.html; reload = !!j.reload; }
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
      if (reload) { window.location.reload(); return; }
      button.disabled = false;
      if (area) { area.disabled = false; area.focus(); }
      agent.scrollIntoView({ block: "end", behavior: "smooth" });
    }).catch(function () {
      window.location.reload();
    });
  });
})();
