/* The Talas Herald: date and weather in the top bar, section menu,
 * the metered paywall and the archive search. */
(function () {
  "use strict";

  var IRT_MS = 6 * 3600000;
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December"];

  function store(key, value) {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null");
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) { return null; }
  }

  function irt() { return new Date(Date.now() + IRT_MS); }

  // ---- date and weather ----
  function topbar() {
    var d = irt();
    var el = document.querySelector("[data-irad-date]");
    if (el) el.textContent = DAYS[d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
    var w = document.querySelector("[data-weather]");
    if (!w) return;
    var hit = store("herald-wx");
    var show = function (j) {
      if (!j || !j.current) return;
      var t = Math.round(j.current.temperature_2m);
      w.textContent = "Talas " + (t > 0 ? "+" : "") + t + "°C";
      w.hidden = false;
    };
    if (hit && Date.now() - hit.at < 30 * 60000) return show(hit.data);
    fetch("https://api.open-meteo.com/v1/forecast?latitude=42.52&longitude=72.24&current=temperature_2m&timezone=auto")
      .then(function (r) { return r.json(); })
      .then(function (j) { store("herald-wx", { at: Date.now(), data: j }); show(j); })
      .catch(function () {});
  }

  // ---- section menu (small screens) ----
  function menu() {
    var btn = document.querySelector(".nav-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var nav = btn.closest(".sections");
      var open = nav.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // ---- metered paywall: N free stories a month, then a subscribe panel you can close ----
  function meter() {
    var art = document.querySelector("[data-meter]");
    var free = parseInt(document.body.getAttribute("data-meter-free") || "0", 10);
    if (!art || !free) return;
    var base = document.body.getAttribute("data-base") || "";
    var d = irt();
    var month = d.getUTCFullYear() + "-" + (d.getUTCMonth() + 1);
    var m = store("herald-meter");
    if (!m || m.month !== month) m = { month: month, read: [] };
    var slug = art.getAttribute("data-meter");
    if (m.read.indexOf(slug) === -1) m.read.push(slug);
    store("herald-meter", m);
    var n = m.read.indexOf(slug) + 1;
    var head = art.querySelector(".story-head");
    if (n <= free) {
      var left = free - n;
      var note = document.createElement("p");
      note.className = "meter-count";
      note.innerHTML = left === 0
        ? "This is your last free article this month. <a href=\"" + base + "subscribe.html\">Subscribe</a> for unlimited access."
        : "You have " + left + " free article" + (left === 1 ? "" : "s") + " left this month. <a href=\"" + base + "subscribe.html\">Subscribe</a>";
      if (head) head.parentNode.insertBefore(note, head);
      return;
    }
    var panel = document.createElement("div");
    panel.className = "meter-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Subscribe to the Talas Herald");
    panel.innerHTML =
      '<div class="meter-inner"><button class="meter-close" type="button">Not now ✕</button>' +
      "<h2>You've read your " + free + " free articles this month</h2>" +
      "<p>Independent reporting from the courts, the councils and the record costs money. Subscribe from 40 som a month, or 20 som for students.</p>" +
      '<a class="btn" href="' + base + 'subscribe.html">See subscription options</a></div>';
    document.body.appendChild(panel);
    panel.querySelector(".meter-close").addEventListener("click", function () { panel.remove(); });
  }

  // ---- archive search ----
  function archiveSearch() {
    var input = document.getElementById("archive-q");
    var data = document.getElementById("archive-data");
    var out = document.getElementById("archive-results");
    if (!input || !data || !out) return;
    var items = JSON.parse(data.textContent);
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      out.innerHTML = "";
      if (q.length < 2) return;
      var words = q.split(/\s+/);
      items.filter(function (it) {
        var h = (it.h + " " + it.s + " " + it.d.slice(0, 4)).toLowerCase();
        return words.every(function (w) { return h.indexOf(w) !== -1; });
      }).slice(0, 40).forEach(function (it) {
        var li = document.createElement("li");
        var t = document.createElement("time");
        var p = it.d.split("-");
        t.textContent = parseInt(p[2], 10) + " " + MONTHS[parseInt(p[1], 10) - 1].slice(0, 3) + " " + p[0];
        var a = document.createElement("a");
        a.href = it.u;
        a.textContent = it.h;
        var s = document.createElement("span");
        s.className = "li-sec";
        s.textContent = it.s;
        li.appendChild(t); li.appendChild(a); li.appendChild(s);
        out.appendChild(li);
      });
      if (!out.children.length) out.innerHTML = '<li><span></span><span>No headlines match.</span></li>';
    });
  }


  // ---- subscriber login: the account system is never reachable, so every attempt fails.
  // Nothing typed here is sent anywhere or stored. ----
  function login() {
    var form = document.getElementById("login-form");
    if (!form) return;
    var err = document.getElementById("login-error");
    var btn = form.querySelector(".login-btn");
    var pass = document.getElementById("login-pass");
    var tries = 0;
    var show = function (msg) { err.textContent = msg; err.hidden = false; };
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var user = document.getElementById("login-user").value.trim();
      if (!user || !pass.value) { show("Please enter your email or subscriber number and your password."); return; }
      btn.disabled = true;
      btn.textContent = "Logging in\u2026";
      err.hidden = true;
      setTimeout(function () {
        tries += 1;
        pass.value = "";
        btn.disabled = false;
        btn.textContent = "Log in";
        if (tries >= 3) show("Too many unsuccessful attempts. Please try again later, or contact circulation@talasherald.ir.ad.");
        else show("Incorrect username or password.");
        pass.focus();
      }, 900 + Math.floor(Math.random() * 700));
    });
    var forgot = document.getElementById("login-forgot");
    if (forgot) forgot.addEventListener("click", function (e) {
      e.preventDefault();
      show("Password reset is temporarily unavailable. Please contact circulation@talasherald.ir.ad.");
    });
  }


  // ---- front page slider (jQuery-free rewrite, 2016) ----
  function slider() {
    var slides = document.querySelectorAll(".slide");
    var thumbs = document.querySelectorAll(".sl-thumbs a");
    if (slides.length < 2) return;
    var cur = 0, timer;
    function show(n) {
      slides[cur].classList.remove("on"); if (thumbs[cur]) thumbs[cur].classList.remove("on");
      cur = (n + slides.length) % slides.length;
      slides[cur].classList.add("on"); if (thumbs[cur]) thumbs[cur].classList.add("on");
    }
    function play() { clearInterval(timer); timer = setInterval(function () { show(cur + 1); }, 6000); }
    document.querySelector(".sl-prev").addEventListener("click", function () { show(cur - 1); play(); });
    document.querySelector(".sl-next").addEventListener("click", function () { show(cur + 1); play(); });
    Array.prototype.forEach.call(thumbs, function (t, i) {
      t.addEventListener("mouseenter", function () { show(i); play(); });
    });
    play();
  }

  // ---- most popular tabs ----
  function tabs() {
    var box = document.querySelector(".popular");
    if (!box) return;
    box.addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-tab]");
      if (!b) return;
      Array.prototype.forEach.call(box.querySelectorAll("[data-tab]"), function (x) { x.classList.toggle("on", x === b); });
      Array.prototype.forEach.call(box.querySelectorAll("[data-pane]"), function (x) { x.hidden = x.getAttribute("data-pane") !== b.getAttribute("data-tab"); });
    });
  }

  // ---- header search lands on the archive search ----
  function searchQuery() {
    var input = document.getElementById("archive-q");
    var m = location.search.match(/[?&]q=([^&]*)/);
    if (!input || !m) return;
    input.value = decodeURIComponent(m[1].replace(/\+/g, " "));
    input.dispatchEvent(new Event("input"));
  }

  function init() { topbar(); menu(); meter(); archiveSearch(); searchQuery(); login(); slider(); tabs(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
