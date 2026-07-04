"""Regression tests for the untrusted-markdown sanitizer (stored-XSS guards)."""

from __future__ import annotations

import pytest

from app.services.sanitize import render_untrusted_markdown as R


@pytest.mark.parametrize("src", [
    "hi <script>alert(document.cookie)</script>",
    "[proof](java\nscript:alert(document.cookie))",       # newline-split scheme
    "[x](java\tscript:alert(1))",                          # tab-split scheme
    "[x](java\x00script:alert(1))",                        # NUL-split scheme
    "[x](JAVASCRIPT:alert(1))",                            # case
    "[x](data:text/html,<script>alert(1)</script>)",      # data:
    "[x](vbscript:msgbox(1))",                             # vbscript:
    "<img src=x onerror=alert(1)>",                        # raw html img
])
def test_no_executable_output(src):
    out = R(src).lower()
    # No raw HTML tags survive (they're escaped to inert text), and no
    # dangerous scheme survives inside a real href attribute.
    assert "<script" not in out
    assert "<img" not in out
    assert 'href="javascript:' not in out and "href=javascript:" not in out
    assert 'href="vbscript:' not in out
    assert 'href="data:' not in out


@pytest.mark.parametrize("src", [
    "![a](http://evil.example/x.png)",                    # inline image
    "![a][r]\n\n[r]: http://evil.example/x.png",          # reference-style image
    "![a][r]\n\n[r]: javascript:alert(1)",                # reference image, bad scheme
])
def test_images_are_stripped(src):
    out = R(src)
    assert "evil.example" not in out
    assert "<img" not in out.lower()


def test_safe_links_preserved_with_rel():
    out = R("[docs](https://arxiv.org/abs/2606.01234) and [home](/hardware)")
    assert 'href="https://arxiv.org/abs/2606.01234"' in out
    assert 'href="/hardware"' in out
    assert 'rel="nofollow noopener"' in out


def test_code_not_double_escaped():
    assert "&amp;amp;" not in R("`a & b < c`")
    assert "&amp;amp;" not in R("```\nx & y < z\n```")
    # the entities are present exactly once (properly escaped)
    assert "&amp;" in R("`a & b`")
