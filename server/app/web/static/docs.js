/* Progressive enhancement for the docs. Everything here is optional polish —
   the page is fully readable without JS. Runs after highlight.js. */
(function () {
  "use strict";

  var LANG_LABEL = {
    python: "Python", py: "Python", bash: "Shell", sh: "Shell", shell: "Shell",
    console: "Shell", json: "JSON", yaml: "YAML", yml: "YAML", curl: "cURL",
    http: "HTTP", text: "Text", plaintext: "Text", javascript: "JavaScript",
    js: "JavaScript", toml: "TOML", sql: "SQL"
  };

  function langOf(pre) {
    var code = pre.querySelector("code");
    var cls = (code && code.className) || "";
    var m = cls.match(/language-([\w-]+)/);
    var key = m ? m[1].toLowerCase() : "";
    return LANG_LABEL[key] || (key ? key.toUpperCase() : "Code");
  }

  function copyText(text, btn) {
    var done = function () {
      var old = btn.textContent;
      btn.textContent = "Copied";
      btn.classList.add("copied");
      setTimeout(function () { btn.textContent = old; btn.classList.remove("copied"); }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, done);
    } else {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(ta); done();
    }
  }

  // 1) Wrap standalone <pre> blocks with a bar (language chip + copy button).
  function decorateCodeBlocks(root) {
    root.querySelectorAll(".doc-content pre").forEach(function (pre) {
      if (pre.closest(".doc-tabs")) return;            // tabs get their own copy control
      if (pre.parentElement && pre.parentElement.classList.contains("code-block")) return;
      var wrap = document.createElement("div");
      wrap.className = "code-block";
      var bar = document.createElement("div");
      bar.className = "code-block__bar";
      var lang = document.createElement("span");
      lang.className = "code-block__lang";
      lang.textContent = langOf(pre);
      var copy = document.createElement("button");
      copy.type = "button";
      copy.className = "code-block__copy";
      copy.textContent = "Copy";
      copy.addEventListener("click", function () {
        var code = pre.querySelector("code");
        copyText((code || pre).innerText.replace(/\n$/, ""), copy);
      });
      bar.appendChild(lang); bar.appendChild(copy);
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(bar); wrap.appendChild(pre);
    });
  }

  // 2) Language tabs: <div class="doc-tabs"><div data-lang="Python">...</div>...</div>
  function buildTabs(root) {
    root.querySelectorAll(".doc-tabs").forEach(function (tabs) {
      var panels = Array.prototype.filter.call(tabs.children, function (c) {
        return c.hasAttribute && c.hasAttribute("data-lang");
      });
      if (!panels.length) return;
      var nav = document.createElement("div");
      nav.className = "doc-tabs__nav";
      var copy = document.createElement("button");
      copy.type = "button"; copy.className = "code-block__copy"; copy.textContent = "Copy";
      copy.style.marginLeft = "auto";
      panels.forEach(function (panel, i) {
        panel.classList.add("doc-tabs__panel");
        if (i === 0) panel.classList.add("active");
        var btn = document.createElement("button");
        btn.type = "button"; btn.className = "doc-tabs__btn" + (i === 0 ? " active" : "");
        btn.textContent = panel.getAttribute("data-lang");
        btn.addEventListener("click", function () {
          nav.querySelectorAll(".doc-tabs__btn").forEach(function (b) { b.classList.remove("active"); });
          tabs.querySelectorAll(".doc-tabs__panel").forEach(function (p) { p.classList.remove("active"); });
          btn.classList.add("active"); panel.classList.add("active");
        });
        nav.appendChild(btn);
      });
      copy.addEventListener("click", function () {
        var active = tabs.querySelector(".doc-tabs__panel.active");
        var pre = active && active.querySelector("pre");
        if (pre) copyText(pre.innerText.replace(/\n$/, ""), copy);
      });
      nav.appendChild(copy);
      tabs.insertBefore(nav, tabs.firstChild);
    });
  }

  // 3) Hover anchor links on headings that have ids.
  function addAnchors(root) {
    root.querySelectorAll(".doc-content h2[id], .doc-content h3[id]").forEach(function (h) {
      if (h.querySelector(".anchor")) return;
      var a = document.createElement("a");
      a.className = "anchor"; a.href = "#" + h.id; a.textContent = "#";
      a.setAttribute("aria-label", "Link to this section");
      h.appendChild(a);
    });
  }

  // 4) Scroll-spy: highlight the current section in the right-hand TOC.
  function scrollSpy() {
    var toc = document.querySelector(".doc-toc");
    if (!toc || !("IntersectionObserver" in window)) return;
    var links = {};
    toc.querySelectorAll('a[href^="#"]').forEach(function (a) {
      links[decodeURIComponent(a.getAttribute("href").slice(1))] = a;
    });
    var heads = document.querySelectorAll(".doc-content h2[id], .doc-content h3[id]");
    if (!heads.length) return;
    var visible = new Set();
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) visible.add(e.target.id); else visible.delete(e.target.id);
      });
      var current = null;
      heads.forEach(function (h) { if (!current && visible.has(h.id)) current = h.id; });
      Object.keys(links).forEach(function (id) { links[id].classList.toggle("active", id === current); });
    }, { rootMargin: "-80px 0px -70% 0px", threshold: 0 });
    heads.forEach(function (h) { obs.observe(h); });
  }

  // 5) Sidebar filter box.
  function sidebarFilter() {
    var input = document.getElementById("doc-filter");
    var nav = document.querySelector(".doc-nav");
    if (!input || !nav) return;
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      nav.querySelectorAll("[data-doc-group]").forEach(function (group) {
        var any = false;
        group.querySelectorAll("li").forEach(function (li) {
          var match = li.textContent.toLowerCase().indexOf(q) !== -1;
          li.style.display = match ? "" : "none";
          if (match) any = true;
        });
        group.style.display = any ? "" : "none";
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var root = document;
    if (window.hljs) { try { hljs.highlightAll(); } catch (e) {} }
    buildTabs(root);       // before decorate, so tab panels are marked
    decorateCodeBlocks(root);
    addAnchors(root);
    scrollSpy();
    sidebarFilter();
  });
})();
