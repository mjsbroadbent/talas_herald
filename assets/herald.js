/* The Talas Herald: date, weather and rates in the top bar, the markets box, section menu,
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

  // ---- date, weather and rates in the top bar; the markets box in the sidebar ----
  // From IradOne's daily market data (updated 03:07 Irad Time). If it can't be read, the
  // rates printed in the page stay and the weather and markets box stay hidden.
  var MARKET_URL = "https://iradone.com/data/today.json";

  function market() {
    var hit = store("herald-market");
    if (hit && Date.now() - hit.at < 30 * 60000) return Promise.resolve(hit.data);
    return fetch(MARKET_URL, { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (j) { store("herald-market", { at: Date.now(), data: j }); return j; });
  }

  function nowTemp(p, hours) {
    var d = irt(), h = d.getUTCHours() + d.getUTCMinutes() / 60, v = p.temps_3h || [];
    if (!v.length) return p.high;
    var i = Math.min(v.length - 1, Math.floor(h / 3)), j = Math.min(v.length - 1, i + 1);
    return v[i] + (v[j] - v[i]) * (i === j ? 0 : (h - hours[i]) / 3);
  }

  function signed(t) { t = Math.round(t); return (t > 0 ? "+" : "") + t + "°"; }
  function money(n) { return n.toFixed(2); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function paintTopbar(j) {
    var w = document.querySelector("[data-weather]");
    var talas = j.weather && j.weather.places && j.weather.places[0];
    if (w && talas) {
      w.textContent = "Talas " + signed(nowTemp(talas, j.weather.hours || [0, 3, 6, 9, 12, 15, 18, 21])) + "C";
      w.title = talas.label.en + ", high " + talas.high + "°, low " + talas.low + "°";
      w.hidden = false;
    }
    var r = document.querySelector(".tb-rates");
    if (!r || !j.rates) return;
    var o = {};
    j.rates.official.rows.forEach(function (x) { o[x.code] = x.som / x.units; });
    var b = j.rates.bazaar, ch = b.usd_change || 0;
    var arrow = ch > 0.004 ? ' <i class="up">&#9650;</i>' : ch < -0.004 ? ' <i class="dn">&#9660;</i>' : "";
    r.innerHTML = "USD <b>" + money(o.USD) + "</b> &nbsp; EUR <b>" + money(o.EUR) + "</b> &nbsp; RUB <b>" +
      o.RUB.toFixed(3) + "</b> &nbsp; KZT <b>" + o.KZT.toFixed(3) + "</b> &nbsp;|&nbsp; Bazaar $ <b>" +
      money(b.rows[0].buy) + "/" + money(b.rows[0].sell) + "</b>" + arrow;
    r.title = "Bank of Irad official rates; Bazar-i Kalan changers this morning (buy/sell)";
  }

  function marketsBox(j) {
    var side = document.querySelector(".sidebar");
    if (!side || side.querySelector(".markets-box") || !j.rates) return;
    var b = j.rates.bazaar, P = j.prices || {}, rows = "";
    b.rows.forEach(function (x) {
      rows += "<tr><th>" + (x.units > 1 ? x.units + " " : "") + x.code + "</th><td>" + money(x.buy) + "</td><td>" + money(x.sell) + "</td></tr>";
    });
    var pick = function (list, id) { return (list || []).filter(function (x) { return x.id === id; })[0]; };
    var goods = [pick(P.food, "bread"), pick(P.food, "potatoes"), pick(P.food, "plov"), pick(P.fuel, "petrol-92"), pick(P.fuel, "diesel")]
      .filter(Boolean).map(function (x) {
        return "<li><span>" + esc(x.label.en) + "</span><b>" + esc(x.text) + "</b></li>";
      }).join("");
    var wx = ((j.weather && j.weather.places) || []).slice(0, 3).map(function (p) {
      return "<li><span>" + esc(p.name.en) + "</span><em>" + esc(p.label.en) + "</em><b>" + p.high + "° / " + p.low + "°</b></li>";
    }).join("");
    var air = (j.air && j.air.places || [])[0], t = j.water && j.water.toktogul;
    var html = '<h3 class="widget-title">Markets &amp; weather</h3>' +
      '<p class="mk-src">Bazar-i Kalan changers this morning, som</p>' +
      '<table class="mk-rates"><thead><tr><th></th><th>Buy</th><th>Sell</th></tr></thead><tbody>' + rows + "</tbody></table>" +
      (b.note ? '<p class="mk-note">' + esc(b.note.en) + "</p>" : "") +
      '<p class="mk-src">Official rate 8.40 to the dollar (Bank of Irad)</p>' +
      (goods ? '<h4 class="mk-h">At the bazaar and the pump, som</h4><ul class="mk-list">' + goods + "</ul>" : "") +
      (wx ? '<h4 class="mk-h">Weather today</h4><ul class="mk-list mk-wx">' + wx + "</ul>" : "") +
      (air ? '<p class="mk-air mk-' + esc(air.category) + '">Nurzhan air: <b>' + air.aqi + "</b> " + esc(air.label.en) +
        (air.inversion ? " (inversion)" : "") + "</p>" : "") +
      (t ? '<p class="mk-src">Toktogul reservoir ' + t.fill_pct.toFixed(1) + "% full, " + esc(t.trend_label.en.toLowerCase()) + "</p>" : "");
    var box = document.createElement("section");
    box.className = "widget markets-box";
    box.innerHTML = html;
    var after = side.querySelector(".popular") || side.querySelector(".jarnama");
    if (after && after.nextSibling) side.insertBefore(box, after.nextSibling);
    else side.appendChild(box);
  }

  function topbar() {
    var d = irt();
    var el = document.querySelector("[data-irad-date]");
    if (el) el.textContent = DAYS[d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
    market().then(function (j) { paintTopbar(j); marketsBox(j); }).catch(function () {});
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
        if (tries >= 3) show("Too many unsuccessful attempts. Please try again later, or contact circulation@talasherald.com.");
        else show("Incorrect username or password.");
        pass.focus();
      }, 900 + Math.floor(Math.random() * 700));
    });
    var forgot = document.getElementById("login-forgot");
    if (forgot) forgot.addEventListener("click", function (e) {
      e.preventDefault();
      show("Password reset is temporarily unavailable. Please contact circulation@talasherald.com.");
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
