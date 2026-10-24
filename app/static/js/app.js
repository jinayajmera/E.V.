/* Small progressive-enhancement script. The site works without it; no inline JS is used (strict CSP). */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  // mobile navigation
  var t = $(".nav-toggle"), nav = $(".nav");
  if (t && nav) t.addEventListener("click", function () {
    var o = nav.classList.toggle("open"); t.setAttribute("aria-expanded", o ? "true" : "false");
  });

  // confirm before destructive / irreversible actions
  document.addEventListener("submit", function (e) {
    var m = e.target.getAttribute && e.target.getAttribute("data-confirm");
    if (m && !window.confirm(m)) e.preventDefault();
  });

  // progress bars (width kept out of inline style attributes because of the CSP)
  $$("[data-w]").forEach(function (el) { el.style.width = "0"; });
  requestAnimationFrame(function () { setTimeout(function () {
    $$("[data-w]").forEach(function (el) { el.style.width = el.getAttribute("data-w") + "%"; });
  }, 60); });

  // auto-submit filter selects
  $$("[data-autosubmit]").forEach(function (el) { el.addEventListener("change", function () { el.form.submit(); }); });

  // print buttons
  $$("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });

  // show / hide password
  $$("[data-pw-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var i = document.getElementById(b.getAttribute("data-pw-toggle"));
      if (!i) return; var s = i.type === "password"; i.type = s ? "text" : "password"; b.textContent = s ? "Hide" : "Show";
    });
  });

  // password strength meter
  $$("[data-strength]").forEach(function (i) {
    var bar = document.getElementById(i.getAttribute("data-strength")); if (!bar) return;
    i.addEventListener("input", function () {
      var v = i.value, s = 0;
      if (v.length >= 8) s++; if (v.length >= 12) s++; if (/[a-z]/.test(v) && /[A-Z]/.test(v)) s++;
      if (/\d/.test(v)) s++; if (/[^A-Za-z0-9]/.test(v)) s++;
      var k = bar.firstElementChild; k.style.width = (s * 20) + "%";
      k.style.background = s <= 2 ? "#c0392b" : s === 3 ? "#d98f12" : "#1f8a4c";
    });
  });

  // photo preview + client-side size check (server validates again)
  $$("input[type=file][data-preview]").forEach(function (inp) {
    inp.addEventListener("change", function () {
      var img = document.getElementById(inp.getAttribute("data-preview")), f = inp.files && inp.files[0];
      if (!f) return;
      if (f.size > 3 * 1024 * 1024) { window.alert("Please choose a photograph smaller than 3 MB."); inp.value = ""; return; }
      if (img && window.URL) { img.src = URL.createObjectURL(f); img.hidden = false; var ph = img.nextElementSibling; if (ph && ph.classList.contains("ph")) ph.hidden = true; }
    });
  });

  // identity number: upper-case as the person types
  $$("input[data-upper]").forEach(function (i) { i.addEventListener("input", function () { i.value = i.value.toUpperCase(); }); });

  // disable the submit button after the first click (prevents double submission of a vote)
  $$("form[data-once]").forEach(function (f) {
    f.addEventListener("submit", function () {
      $$("button[type=submit]", f).forEach(function (b) { setTimeout(function () { b.disabled = true; }, 0); });
    });
  });

  // copy to clipboard
  $$("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var txt = b.getAttribute("data-copy");
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { var o = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = o; }, 1500); });
    });
  });

  // flash messages fade out (except errors)
  $$(".flash.success,.flash.info").forEach(function (el) { setTimeout(function () { el.style.transition = "opacity .5s"; el.style.opacity = "0"; setTimeout(function () { el.remove(); }, 600); }, 6000); });
})();
