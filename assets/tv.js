/* Television listings, from IradOne's own schedule (iradone.com/schedule.js
 * and schedule.json), set in Herald type. Needs IradOne's scripts on the page. */
(function () {
  "use strict";

  var IRADONE = "https://iradone.com/";

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function render(list, day, from, to, nowMin) {
    list.innerHTML = "";
    day.slots.filter(function (s) { return s.end > from && s.start < to && s.id !== "signoff"; }).forEach(function (s) {
      var li = el("li", nowMin !== null && nowMin >= s.start && nowMin < s.end ? "tv-now" : "");
      li.appendChild(el("span", "tv-time", s.time));
      var body = el("span");
      var a = el("a", "tv-title", s.title);
      a.href = IRADONE + "shows/" + s.id + ".html";
      body.appendChild(a);
      if (s.detail) body.appendChild(el("span", "tv-detail", s.detail));
      li.appendChild(body);
      list.appendChild(li);
    });
    if (!list.children.length) list.appendChild(el("li", "tv-loading", "No listings."));
  }

  function init() {
    var lists = document.querySelectorAll("[data-tv]");
    if (!lists.length) return;
    var S = window.IradSchedule;
    var fail = function () {
      Array.prototype.forEach.call(lists, function (l) { l.innerHTML = '<li class="tv-loading">Listings are unavailable right now.</li>'; });
    };
    if (!S) return fail();
    var getJson = function (u) { return fetch(u, { cache: "no-cache" }).then(function (r) { if (!r.ok) throw new Error(u); return r.json(); }); };
    var sport = window.IradSport && window.IradSport.ready ? window.IradSport.ready.catch(function () {}) : Promise.resolve();
    Promise.all([getJson(IRADONE + "schedule.json"), getJson(IRADONE + "news/index.json").catch(function () { return []; }), sport])
      .then(function (res) {
        var data = res[0], news = res[1];
        var bt = S.broadcastToday();
        Array.prototype.forEach.call(lists, function (list) {
          var mode = list.getAttribute("data-tv");
          var offset = mode === "tomorrow" ? 1 : 0;
          var date = S.addDays(bt.date, offset);
          var day = S.buildDay(data, date, news, bt.date);
          if (mode === "evening") render(list, day, Math.max(bt.minute, 18 * 60), 24 * 60, bt.minute);
          else render(list, day, 5 * 60, 29 * 60, offset === 0 ? bt.minute : null);
        });
        var heads = document.querySelectorAll("[data-tv-day]");
        var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
        var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
        Array.prototype.forEach.call(heads, function (h) {
          var d = S.addDays(bt.date, parseInt(h.getAttribute("data-tv-day"), 10));
          h.textContent = (h.getAttribute("data-tv-day") === "0" ? "Today, " : "Tomorrow, ") +
            DAYS[d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()];
        });
      })
      .catch(fail);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
