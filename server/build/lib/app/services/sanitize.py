"""Markdown rendering for UNTRUSTED (bot/routine-supplied) content.

Growth-API content is LLM/arXiv-derived and prompt-injectable, so raw HTML and
dangerous URL schemes must never survive. We render safely by construction:

1. Raw HTML is disabled at the parser (python-markdown's documented EscapeHtml
   recipe: deregister the ``html_block`` preprocessor + ``html`` inline pattern),
   so ``<script>`` becomes escaped text, not a tag.
2. A treeprocessor walks the parsed ElementTree (not rendered strings, so
   newline/tab/NUL-split schemes can't hide) and, for every ``<a>``, strips the
   ``href`` unless — after removing ASCII control/whitespace — its scheme is
   http/https/mailto or it is site-relative/anchor. All ``<img>`` elements are
   dropped (no third-party hot-linking, inline OR reference-style).

This closes the regex-substring bypasses (contiguous-only match, reference
images, ``src:`` schemes) and avoids the double-escaping of code spans that a
pre-escape pass caused.
"""

from __future__ import annotations

import re

import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

_SAFE_EXTS = ["fenced_code", "tables", "sane_lists"]
# ASCII control chars + space: browsers strip tab/LF/CR (and treat NUL oddly)
# while parsing URLs, so we must strip them BEFORE checking the scheme.
_CTRL = re.compile(r"[\x00-\x20]")


def _safe_href(url: str | None) -> str | None:
    """Return the href if it uses a safe scheme, else None (drop it)."""
    if not url:
        return None
    stripped = _CTRL.sub("", url)
    low = stripped.lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return url
    if stripped.startswith(("/", "#")):  # site-relative or in-page anchor
        return url
    if ":" not in stripped.split("/", 1)[0]:  # no scheme in first segment -> relative
        return url
    return None  # unknown/dangerous scheme (javascript:, data:, vbscript:, ...)


class _LinkImageSanitizer(Treeprocessor):
    def run(self, root):
        parents = {child: parent for parent in root.iter() for child in parent}
        for el in list(root.iter()):
            if el.tag == "a":
                safe = _safe_href(el.get("href"))
                if safe is None:
                    el.attrib.pop("href", None)
                else:
                    el.set("href", safe)
                    el.set("rel", "nofollow noopener")
            elif el.tag == "img":
                parent = parents.get(el)
                if parent is not None:
                    parent.remove(el)
        return root


class _SafeMarkdown(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        md.treeprocessors.register(_LinkImageSanitizer(md), "ql_sanitize", 1)


def render_untrusted_markdown(text: str) -> str:
    """Render untrusted markdown to HTML with raw HTML + bad URLs neutralised."""
    md = markdown.Markdown(extensions=[*_SAFE_EXTS, _SafeMarkdown()])
    return md.convert(text or "")
