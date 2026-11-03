// The time's calendar (engine/schedule.py). A tap on a day opens a sheet with
// the half-hours that fall inside the hosts' window on that day, in the
// guest's own zone with London's beside it; a choice becomes the time they
// would prefer, then a backup, then another, shown as chips; the form carries
// the instants. Without script, three plain date-and-time fields stand in.
(function () {
  "use strict";
  if (!window.Intl || !Intl.DateTimeFormat) return;
  var SLOTS = ["The time you would prefer", "A backup", "Another backup"];
  var LONDON = "Europe/London";
  var state = { form: null, chosen: [] };

  function sync() {  // the form on the page now; a fresh one (after a save) starts with nothing chosen
    var f = document.querySelector("form[data-calendar]");
    if (f !== state.form) state = { form: f, chosen: [] };
    return f;
  }
  function zone() {
    var sel = state.form && state.form.querySelector("select[name=tz]");
    return (sel && sel.value) || LONDON;
  }
  function fmt(date, tz, withDay) {
    var o = { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false };
    if (withDay) { o.weekday = "short"; o.day = "numeric"; o.month = "short"; }
    return new Intl.DateTimeFormat("en-GB", o).format(date);
  }
  function city(tz) { return tz.split("/").pop().replace(/_/g, " "); }
  function label(date) {  // "Mon 30 Nov, 21:00 Sydney (Mon 30 Nov, 10:00 London)"
    var tz = zone();
    if (tz === LONDON) return fmt(date, tz, true) + " London";
    return fmt(date, tz, true) + " " + city(tz) + " (" + fmt(date, LONDON, true) + " London)";
  }

  function render() {
    var f = sync();
    if (!f) return;
    var list = f.querySelector("[data-chosen]");
    list.innerHTML = "";
    SLOTS.forEach(function (name, i) {
      var inp = f.querySelector("input[name=when" + i + "]");
      var c = state.chosen[i];
      if (inp) inp.value = c ? c.iso : "";
      if (!c) return;
      var li = document.createElement("li");
      var b = document.createElement("b"); b.textContent = name;
      var span = document.createElement("span"); span.textContent = label(new Date(c.iso));
      var x = document.createElement("button"); x.type = "button"; x.className = "remove"; x.textContent = "×";
      x.setAttribute("aria-label", "Remove " + name.toLowerCase());
      x.addEventListener("click", function () { state.chosen.splice(i, 1); render(); });
      li.appendChild(b); li.appendChild(span); li.appendChild(x);
      list.appendChild(li);
    });
    f.querySelectorAll(".cal button.day").forEach(function (b) {
      b.classList.toggle("chosen", state.chosen.some(function (c) { return c.day === b.dataset.day; }));
    });
    var hint = f.querySelector("[data-hint]");
    var said = !state.chosen.length ? "Tap a day to begin."
      : state.chosen.length < 3 ? "Tap another day for a backup, or ask for these." : "Three is the most; ask for these.";
    if (hint && hint.textContent !== said) hint.textContent = said;
    var button = f.querySelector("button[type=submit]");
    if (button) button.disabled = !state.chosen.length;
  }

  function times(day, offset) {  // the half-hours inside the window on that day, as instants
    var f = state.form, from = f.dataset.from.split(":"), to = f.dataset.to.split(":");
    var p = day.split("-").map(Number), out = [];
    var start = Date.UTC(p[0], p[1] - 1, p[2], +from[0], +from[1]) - offset * 60000;
    var end = Date.UTC(p[0], p[1] - 1, p[2], +to[0], +to[1]) - offset * 60000;
    for (var t = start; t <= end; t += 30 * 60000) out.push(new Date(t));
    return out;
  }

  function close() {
    ["sheet", "sheet-back"].forEach(function (cls) { var el = document.querySelector("." + cls); if (el) el.remove(); });
  }

  function open(button) {
    close();
    var f = sync();
    if (!f || state.chosen.length >= 3) return;
    var day = button.dataset.day, offset = +button.dataset.offset;
    if (state.chosen.some(function (c) { return c.day === day; })) return;  // one time a day
    var tz = zone();
    var back = document.createElement("div"); back.className = "sheet-back"; back.addEventListener("click", close);
    var sheet = document.createElement("div"); sheet.className = "sheet"; sheet.setAttribute("role", "dialog");
    var kicker = document.createElement("p"); kicker.className = "kicker"; kicker.textContent = SLOTS[state.chosen.length];
    var title = document.createElement("h3");
    title.textContent = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" })
      .format(new Date(day + "T12:00:00Z"));
    var note = document.createElement("p"); note.className = "muted"; note.textContent = button.title;
    var sel = document.createElement("select"); sel.setAttribute("aria-label", "The time");
    var monday = new Date(day + "T12:00:00Z").getUTCDay() === 1, easy = null;
    times(day, offset).forEach(function (d) {
      var o = document.createElement("option"), lon = fmt(d, LONDON, false);
      o.value = d.toISOString();
      o.textContent = (tz === LONDON ? lon + " London" : fmt(d, tz, false) + " " + city(tz) + " (" + lon + " London)")
        + (monday && lon === "10:00" ? ", the easy one" : "");
      if (lon === "10:00") easy = o.value;
      sel.appendChild(o);
    });
    if (easy) sel.value = easy;
    var row = document.createElement("div"); row.className = "row";
    var ok = document.createElement("button"); ok.type = "button"; ok.className = "signal"; ok.textContent = "Choose";
    var no = document.createElement("button"); no.type = "button"; no.textContent = "Cancel";
    ok.addEventListener("click", function () { state.chosen.push({ day: day, iso: sel.value }); close(); render(); });
    no.addEventListener("click", close);
    row.appendChild(ok); row.appendChild(no);
    [kicker, title, note, sel, row].forEach(function (el) { sheet.appendChild(el); });
    document.body.appendChild(back);
    document.body.appendChild(sheet);
    sel.focus();
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".cal button.day") : null;
    if (b) open(b);
  });
  document.addEventListener("change", function (e) {
    if (e.target.matches("form[data-calendar] select[name=tz]")) render();
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  document.addEventListener("office:swap", function () { if (sync()) render(); });  // the time block put back (office.js)
  render();
})();
