// The signature pad (engine/release.py): one canvas per form marked data-sign,
// drawn on with a finger, a pen or a mouse, or given a photo of a signature
// from the file field; on submit the drawing goes into the hidden field as a
// PNG and the number of strokes beside it, and a blank pad stops the form.
// Nothing leaves the page until the form is sent; no library.
(function () {
  "use strict";
  var forms = document.querySelectorAll("form[data-sign]");
  Array.prototype.forEach.call(forms, function (form) {
    var canvas = form.querySelector("canvas.sig");
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");
    var strokesField = form.querySelector("[name=strokes]");
    var imageField = form.querySelector("[name=signature]");
    var empty = form.querySelector("[data-empty]");
    var strokes = 0, drawing = false, last = null;
    var ratio = window.devicePixelRatio || 1;

    function size() {
      var r = canvas.getBoundingClientRect();
      canvas.width = Math.round(r.width * ratio);
      canvas.height = Math.round(r.height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.lineWidth = 2.4;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.strokeStyle = getComputedStyle(canvas).color || "#111";
    }
    function clear() {
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.restore();
      strokes = 0;
      strokesField.value = "0";
    }
    function point(e) {
      var r = canvas.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    }
    function down(e) {
      drawing = true;
      last = point(e);
      if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
      e.preventDefault();
    }
    function move(e) {
      if (!drawing) return;
      var p = point(e);
      ctx.beginPath();
      ctx.moveTo(last[0], last[1]);
      ctx.lineTo(p[0], p[1]);
      ctx.stroke();
      last = p;
      e.preventDefault();
    }
    function up() {
      if (!drawing) return;
      drawing = false;
      strokes += 1;
      strokesField.value = String(strokes);
      if (empty) empty.hidden = true;
    }

    size();
    canvas.style.touchAction = "none";
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("pointerleave", up);

    var clearButton = form.querySelector("[data-clear]");
    if (clearButton) clearButton.addEventListener("click", clear);

    var file = form.querySelector("input[type=file]");
    if (file) {
      file.addEventListener("change", function () {
        var f = file.files && file.files[0];
        if (!f) return;
        var img = new Image();
        var url = URL.createObjectURL(f);
        img.onload = function () {
          clear();
          var w = canvas.width / ratio, h = canvas.height / ratio;
          var s = Math.min(w / img.width, h / img.height);
          var dw = img.width * s, dh = img.height * s;
          ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
          strokes = 1;
          strokesField.value = "1";
          if (empty) empty.hidden = true;
          URL.revokeObjectURL(url);
        };
        img.src = url;
      });
    }

    form.addEventListener("submit", function (e) {
      if (!strokes) {
        e.preventDefault();
        if (empty) empty.hidden = false;
        return;
      }
      imageField.value = canvas.toDataURL("image/png");
    });
  });
})();
