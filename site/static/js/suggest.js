/* Catalog titles first so local make up / make dev still suggest. Daemon hits replace when they arrive. */
(function () {
  var MIN = 2;
  var DELAY = 150;
  var LIMIT = 8;
  var DAEMON_MS = 800;
  var labels = {
    airport: "Airport",
    state: "State",
    document: "Document",
    page: "Plan page",
    funding: "Grant",
    master_plan: "Airport master plan",
    alp: "Airport Layout Plan",
    statute: "Statute",
    sasp: "State aviation plan",
    notice: "Notice",
    other: "Planning document"
  };

  function catalogUrl() {
    var v = document.documentElement.getAttribute("data-cache") || "";
    return "/data/search.json" + (v ? "?v=" + encodeURIComponent(v) : "");
  }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (char) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char];
    });
  }

  function badge(item) {
    var label = labels[item.kind] || labels[item.type] || item.type;
    if (item.page) label += " · p. " + item.page;
    if (item.outlook) label += " · " + item.outlook.charAt(0).toUpperCase() + item.outlook.slice(1);
    return label;
  }

  function outlookValue(form) {
    var checked = form.querySelector("input[name=outlook]:checked");
    var outlook = checked ? checked.value : "";
    if (outlook === "growing" || outlook === "declining" || outlook === "maintaining") return outlook;
    return "";
  }

  function scopeFilter(form) {
    var outlook = outlookValue(form);
    if (outlook) return "type = airport AND outlook = \"" + outlook + "\"";
    var checked = form.querySelector("input[name=scope]:checked");
    var scope = checked ? checked.value : "all";
    if (scope === "plans") return "kind IN [master_plan, alp, other]";
    if (scope === "projects") return "type = funding";
    return null;
  }

  function daemonBody(q, form) {
    var body = {
      q: q,
      limit: LIMIT,
      matchingStrategy: "last",
      attributesToRetrieve: ["title", "url", "type", "kind", "page", "outlook"],
      attributesToHighlight: ["title"]
    };
    var filter = scopeFilter(form);
    if (q.length < 4 && !filter) {
      body.filter = "type IN [airport, state, document, funding]";
    } else if (filter) {
      body.filter = filter;
    }
    return body;
  }

  function catalogHits(items, q, form) {
    var needle = q.toLowerCase();
    var outlook = outlookValue(form);
    var checked = form.querySelector("input[name=scope]:checked");
    var scope = checked ? checked.value : "all";
    return items.filter(function (item) {
      if (outlook) {
        if (item.type !== "airport" || item.outlook !== outlook) return false;
      } else {
        var kind = item.kind || item.type;
        if (scope === "plans" && kind !== "master_plan" && kind !== "alp" && kind !== "other") {
          return false;
        }
        if (scope === "projects" && item.type !== "funding") return false;
      }
      var hay = ((item.title || "") + " " + (item.text || "")).toLowerCase();
      return hay.indexOf(needle) !== -1;
    }).slice(0, LIMIT).map(function (item) {
      return { title: item.title, url: item.url, type: item.type, kind: item.type, outlook: item.outlook || "" };
    });
  }

  function bind(input) {
    var form = input.form;
    var list = form && form.querySelector(".search-suggest");
    if (!form || !list) return;
    var timer = 0;
    var active = -1;
    var hits = [];
    var catalog = null;
    var pending = null;

    fetch(catalogUrl()).then(function (res) { return res.json(); }).then(function (items) {
      catalog = items;
    }).catch(function () {});

    function close() {
      list.hidden = true;
      list.innerHTML = "";
      active = -1;
      hits = [];
      input.setAttribute("aria-expanded", "false");
    }

    function paint() {
      if (!hits.length) {
        close();
        return;
      }
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      list.innerHTML = hits.map(function (item, index) {
        var selected = index === active ? "true" : "false";
        return "<li role=\"option\"><a href=\"" + esc(item.url) + "\" aria-selected=\"" + selected + "\">" +
          esc(item.title) + "<span class=\"meta\">" + esc(badge(item)) + "</span></a></li>";
      }).join("");
    }

    function move(delta) {
      if (!hits.length) return;
      active = (active + delta + hits.length) % hits.length;
      paint();
    }

    function showCatalog(q) {
      var apply = function () {
        if (input.value.trim() !== q) return;
        hits = catalogHits(catalog, q, form);
        active = -1;
        paint();
      };
      if (catalog) {
        apply();
        return;
      }
      fetch(catalogUrl()).then(function (res) { return res.json(); }).then(function (items) {
        catalog = items;
        apply();
      }).catch(function () {});
    }

    function lookup() {
      var q = input.value.trim();
      if (q.length < MIN) {
        close();
        return;
      }
      showCatalog(q);
      if (pending) pending.abort();
      pending = new AbortController();
      var ctrl = pending;
      window.setTimeout(function () { ctrl.abort(); }, DAEMON_MS);
      fetch("/search/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(daemonBody(q, form)),
        signal: ctrl.signal
      }).then(function (res) {
        if (!res.ok) throw new Error("query");
        return res.json();
      }).then(function (body) {
        if (input.value.trim() !== q) return;
        var next = (body.hits || []).map(function (hit) {
          return { title: hit.title, url: hit.url, type: hit.type, kind: hit.kind, page: hit.page };
        });
        if (!next.length) return;
        hits = next;
        active = -1;
        paint();
      }).catch(function () {});
    }

    input.setAttribute("aria-expanded", "false");
    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(lookup, DELAY);
    });
    input.addEventListener("keydown", function (event) {
      if (list.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        move(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(-1);
      } else if (event.key === "Escape") {
        close();
      } else if (event.key === "Enter" && active >= 0 && hits[active]) {
        event.preventDefault();
        window.location = hits[active].url;
      }
    });
    document.addEventListener("click", function (event) {
      if (!form.contains(event.target)) close();
    });
  }

  document.querySelectorAll(".search-form input[name=q]").forEach(bind);
})();
