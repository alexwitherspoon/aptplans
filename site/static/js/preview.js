/* File pane: loading overlay, then ready or error. Same-origin HEAD confirms the bytes exist. */
(function () {
  var TIMEOUT_MS = 120000;

  function sameOrigin(src) {
    return src && (src.charAt(0) === "/" || src.indexOf(location.origin) === 0);
  }

  function ready(view, timer) {
    window.clearTimeout(timer);
    view.classList.remove("is-loading", "is-error");
    view.setAttribute("aria-busy", "false");
  }

  function fail(view, timer) {
    window.clearTimeout(timer);
    view.classList.remove("is-loading");
    view.classList.add("is-error");
    view.setAttribute("aria-busy", "false");
  }

  function confirmSrc(src, ok, bad) {
    if (!sameOrigin(src) || !window.fetch) {
      ok();
      return;
    }
    fetch(src.split("#")[0], { method: "HEAD", cache: "no-cache" })
      .then(function (res) {
        if (res.ok) ok();
        else bad();
      })
      .catch(bad);
  }

  document.querySelectorAll(".file-view").forEach(function (view) {
    var frame = view.querySelector("iframe.file-frame");
    if (!frame) return;

    var timer = window.setTimeout(function () {
      fail(view, timer);
    }, TIMEOUT_MS);

    function onReady() {
      ready(view, timer);
    }

    function onFail() {
      fail(view, timer);
    }

    frame.addEventListener("error", onFail);
    frame.addEventListener("load", function () {
      confirmSrc(frame.getAttribute("src"), onReady, onFail);
    });

    var src = frame.getAttribute("data-src");
    if (src) frame.setAttribute("src", src);
  });
})();
