/* ==========================================================
   sketch.js
   Draws hand-drawn (rough.js) rectangles behind wireframe
   components so the boxes look pencil/pen-sketched rather
   than flat CSS boxes. Requires rough.js loaded first.
   ========================================================== */
(function () {
  function draw() {
    if (typeof rough === "undefined") return;

    var selectors = [
      ".wf-box", ".wf-header", ".wf-input", ".wf-btn",
      ".wf-card", ".wf-dialog", ".wf-topbar a"
    ];
    var targets = document.querySelectorAll(selectors.join(","));

    targets.forEach(function (el) {
      var w = el.offsetWidth;
      var h = el.offsetHeight;
      if (w < 4 || h < 4) return;

      if (getComputedStyle(el).position === "static") {
        el.style.position = "relative";
      }

      var svgNS = "http://www.w3.org/2000/svg";
      var svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("width", w);
      svg.setAttribute("height", h);
      svg.setAttribute("viewBox", "0 0 " + w + " " + h);
      svg.style.position = "absolute";
      svg.style.top = "0";
      svg.style.left = "0";
      svg.style.zIndex = "-1";
      svg.style.pointerEvents = "none";
      svg.style.overflow = "visible";
      el.insertBefore(svg, el.firstChild);

      var fill = "#fffdf6";
      var strokeWidth = 1.6;
      var roughness = 1.7;
      var bowing = 1.3;

      if (el.classList.contains("wf-header")) {
        fill = "#e2ded0";
        strokeWidth = 2;
      } else if (el.classList.contains("wf-btn")) {
        fill = el.classList.contains("ghost") ? "#fffdf6" : "#e9e6d8";
      } else if (el.classList.contains("wf-topbar")) {
        fill = "none";
      } else if (el.tagName === "A") {
        fill = el.classList.contains("active") ? "#e9e6d8" : "none";
        strokeWidth = 1.3;
        roughness = 1.4;
      } else if (el.classList.contains("wf-card")) {
        fill = "#fffdf6";
      } else if (el.classList.contains("wf-dialog")) {
        fill = "#fffef9";
        strokeWidth = 2;
      } else if (el.classList.contains("wf-input")) {
        fill = "#fffdf6";
      }

      var rc = rough.svg(svg);
      var node = rc.rectangle(2, 2, Math.max(w - 4, 1), Math.max(h - 4, 1), {
        stroke: "#3a3a3a",
        strokeWidth: strokeWidth,
        roughness: roughness,
        bowing: bowing,
        fill: fill,
        fillStyle: "solid"
      });
      svg.appendChild(node);
    });
  }

  // draw once, after webfonts/layout have settled so box sizes are final
  if (document.readyState === "complete") {
    draw();
  } else {
    window.addEventListener("load", draw);
  }
})();
