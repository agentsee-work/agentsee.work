// The time's calendar (engine/schedule.py). A tap on a day opens a sheet: the
// three ranks, gold, silver and bronze, each showing the day it holds, and the
// day's half-hours inside the hosts' window, in the guest's own zone with
// London's beside each. Choosing puts the day in the rank picked, replacing
// what was there; a tap on a chosen day clears it and the ranks behind it
// move up. The chosen days wear their medals on the grid and in the list at
// the bottom; the form carries the instants. Without script, three plain
// date-and-time fields stand in.
(function () {
  "use strict";
  if (!window.Intl || !Intl.DateTimeFormat) return;
  var RANKS = ["Preferred", "Backup", "Backup"];
  var MEDALS = ["🥇", "🥈", "🥉"];  // gold, silver, bronze
  var LONDON = "Europe/London";
  var state = { form: null, slots: [null, null, null] };

  function sync() {  // the form on the page now; a fresh one (after a save) starts with nothing chosen
    var f = document.querySelector("form[data-calendar]");
    if (f !== state.form) state = { form: f, slots: [null, null, null] };
    return f;
  }
  function zone() {
    var sel = state.form && state.form.querySelector("select[name=tz]");
    return (sel && sel.value) || LONDON;
  }
  function fmt(date, tz, parts) {
    var o = { timeZone: tz, hour12: false };
    if (parts.indexOf("time") >= 0) { o.hour = "2-digit"; o.minute = "2-digit"; }
    if (parts.indexOf("day") >= 0) { o.weekday = "short"; o.day = "numeric"; o.month = "short"; }
    return new Intl.DateTimeFormat("en-GB", o).format(date);
  }
  function city(tz) { return tz.split("/").pop().replace(/_/g, " "); }
  function label(date) {  // "Mon 30 Nov, 21:00 Sydney (Mon 30 Nov, 10:00 London)"
    var tz = zone();
    if (tz === LONDON) return fmt(date, tz, "day time") + " London";
    return fmt(date, tz, "day time") + " " + city(tz) + " (" + fmt(date, LONDON, "day time") + " London)";
  }
  function dayName(day) {  // "Monday 16 November", from the date alone
    return new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" })
      .format(new Date(day + "T12:00:00Z"));
  }
  function rankOf(day) {
    for (var i = 0; i < 3; i++) if (state.slots[i] && state.slots[i].day === day) return i;
    return -1;
  }
  function compact() {  // no gaps: the ranks behind a cleared one move up
    state.slots = state.slots.filter(Boolean).concat([null, null, null]).slice(0, 3);
  }

  function render() {
    var f = sync();
    if (!f) return;
    var list = f.querySelector("[data-chosen]");
    list.innerHTML = "";
    var n = 0;
    state.slots.forEach(function (c, i) {
      var inp = f.querySelector("input[name=when" + i + "]");
      if (inp) inp.value = c ? c.iso : "";
      if (!c) return;
      n++;
      var li = document.createElement("li");
      var rank = document.createElement("span"); rank.className = "rank"; rank.textContent = MEDALS[i] + " " + RANKS[i];
      var when = document.createElement("span"); when.className = "when"; when.textContent = label(new Date(c.iso));
      var x = document.createElement("button"); x.type = "button"; x.className = "remove"; x.textContent = "×";
      x.setAttribute("aria-label", "Remove " + RANKS[i].toLowerCase());
      x.addEventListener("click", function () { clear(i); });
      li.appendChild(rank); li.appendChild(when); li.appendChild(x);
      list.appendChild(li);
    });
    f.querySelectorAll(".cal button.day").forEach(function (b) {
      // The medal is a badge on the cell beside the button, never inside it, so the button and its
      // number keep their exact size and place whether chosen or not; only the outline says so.
      var i = rankOf(b.dataset.day), cell = b.parentNode, medal = cell.querySelector(".medal");
      b.classList.toggle("chosen", i >= 0);
      if (i < 0) { if (medal) medal.remove(); return; }
      if (!medal) { medal = document.createElement("span"); medal.className = "medal"; cell.insertBefore(medal, b); }
      if (medal.textContent !== MEDALS[i]) medal.textContent = MEDALS[i];
    });
    var hint = f.querySelector("[data-hint]");
    var said = !n ? "Tap a day to begin."
      : n < 3 ? "Tap another day for a backup, or ask for these. A tap on a chosen day clears it."
      : "Three chosen. Tap a day to swap one in, or a chosen day to clear it; or ask for these.";
    if (hint && hint.textContent !== said) hint.textContent = said;
    var button = f.querySelector("button[type=submit]");
    if (button) button.disabled = !state.slots[0];
  }

  function clear(i) {
    state.slots[i] = null;
    compact();
    render();
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

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function open(button) {
    close();
    var f = sync();
    if (!f) return;
    var day = button.dataset.day, offset = +button.dataset.offset, tz = zone();
    var held = rankOf(day);
    if (held >= 0) { clear(held); return; }  // a chosen day, tapped again: cleared
    var free = state.slots.indexOf(null);  // the next free rank, or none
    var rank = free >= 0 ? free : 2;  // all three held: the bronze gives way unless another is picked
    var back = el("div", "sheet-back"); back.addEventListener("click", close);
    var sheet = el("div", "sheet"); sheet.setAttribute("role", "dialog");
    var title = el("h3", null, dayName(day));
    var note = el("p", "muted", button.title);
    var ranks = el("div", "ranks"), note2 = el("p", "muted swap");
    var rankButtons = [];

    function pick(i) {
      rank = i;
      rankButtons.forEach(function (b, j) { b.setAttribute("aria-pressed", j === i ? "true" : "false"); });
      var was = state.slots[i];
      note2.textContent = was ? "Replaces " + label(new Date(was.iso)) + "." : "";
    }

    RANKS.forEach(function (name, i) {
      var b = el("button", "rank"); b.type = "button";
      var open_ = state.slots[i] || i === free;  // a rank held, or the next free one; never a gap
      b.appendChild(el("span", null, MEDALS[i] + " " + name));
      b.appendChild(el("small", null, state.slots[i] ? fmt(new Date(state.slots[i].iso), tz, "day") : "free"));
      if (!open_) b.disabled = true;
      b.addEventListener("click", function () { pick(i); });
      rankButtons.push(b);
      ranks.appendChild(b);
    });

    var sel = el("select"); sel.setAttribute("aria-label", "The time");
    var monday = new Date(day + "T12:00:00Z").getUTCDay() === 1, easy = null;
    times(day, offset).forEach(function (d) {
      var o = el("option"), lon = fmt(d, LONDON, "time");
      o.value = d.toISOString();
      o.textContent = (tz === LONDON ? lon + " London" : fmt(d, tz, "time") + " " + city(tz) + " (" + lon + " London)")
        + (monday && lon === "10:00" ? ", the easy one" : "");
      if (lon === "10:00") easy = o.value;
      sel.appendChild(o);
    });
    if (easy) sel.value = easy;
    var row = el("div", "row");
    var ok = el("button", "signal", "Choose"); ok.type = "button";
    var no = el("button", null, "Cancel"); no.type = "button";
    ok.addEventListener("click", function () {
      state.slots[rank] = { day: day, iso: sel.value };
      close();
      render();
    });
    no.addEventListener("click", close);
    row.appendChild(ok); row.appendChild(no);
    [title, note, ranks, note2, sel, row].forEach(function (x) { sheet.appendChild(x); });
    document.body.appendChild(back);
    document.body.appendChild(sheet);
    pick(rank);
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
