"""Make LLM-emitted markdown safe for Quarto -> Pandoc -> LaTeX (tectonic).

Ported from Zollege's ``MdBookExportService::sanitizeMarkdownForPdf``. Tectonic's
default fonts have no emoji glyphs, and a few common LLM markdown habits (raw
``<details>`` blocks, long fill-in-the-blank runs) compile to broken or overflowing
LaTeX. We neutralize those without touching legitimate prose. HTML/EPUB tolerate
the same cleaned markdown fine, so we apply it uniformly.
"""

from __future__ import annotations

import re

# Misc symbols & dingbats, emoji blocks, variation selectors, ZWJ, regional
# indicators — none have glyphs in tectonic's default fonts.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FFFF☀-➿︀-️‍\U0001F1E6-\U0001F1FF]",
    flags=re.UNICODE,
)
_DETAILS_BLOCK_RE = re.compile(r"<details[^>]*>(.*?)</details>", flags=re.IGNORECASE | re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary[^>]*>(.*?)</summary>", flags=re.IGNORECASE | re.DOTALL)
_LEFTOVER_TAGS_RE = re.compile(r"</?(?:details|summary)[^>]*>", flags=re.IGNORECASE)
_ESCAPED_BLANK_RE = re.compile(r"(?:\\_){8,}")
_UNDERSCORE_BLANK_RE = re.compile(r"_{8,}")
_DASH_BLANK_RE = re.compile(r"(?<!^)-{8,}(?!$)", flags=re.MULTILINE)
_DOT_BLANK_RE = re.compile(r"\.{8,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+$", flags=re.MULTILINE)

# --- math normalization -------------------------------------------------------
# Pandoc's tex_math_dollars requires the opening $ to be immediately followed
# (and the closing $ immediately preceded) by a non-space, and single-backslash
# \( .. \) / \[ .. \] delimiters are not enabled at all. LLMs routinely emit
# "$ r_s = \frac{2GM}{c^2} $" or "\[ E = mc^2 \]"; Pandoc then treats the body
# as prose and the \frac passes through as raw TeX — silently dropped from HTML
# output and fatal in the PDF ("Missing $ inserted" aborts tectonic). Rewrite
# such spans into well-formed $...$/$$...$$ so both formats get real math.
_SPACED_DOLLAR_RE = re.compile(r"\$[ \t]*([^$\n]+?)[ \t]*\$")
_BRACKET_MATH_RE = re.compile(r"\\{1,2}\[(.+?)\\{1,2}\]", flags=re.DOTALL)
_PAREN_MATH_RE = re.compile(r"\\{1,2}\((.+?)\\{1,2}\)", flags=re.DOTALL)
_ESCAPED_MACRO_RE = re.compile(r"\\\\(?=[A-Za-z])")


def _math_inner_ok(inner: str) -> bool:
    """Only rewrite spans that plausibly ARE math, never currency/prose."""
    inner = inner.strip()
    if not inner:
        return False
    # "$ 5 for A and $ 6 ..."-style quantities: digits then a word boundary,
    # with no TeX markup anywhere, is prose about money — leave it alone.
    if re.match(r"\d[\d,.]*\s", inner) and not re.search(r"[\\^=]", inner):
        return False
    if re.search(r"[\\_^=]", inner):
        return True
    return " " not in inner and len(inner) <= 30


def _normalize_math(markdown: str) -> str:
    def dollar(m: re.Match[str]) -> str:
        inner = m.group(1)
        if not _math_inner_ok(inner):
            return m.group(0)
        return "$" + _ESCAPED_MACRO_RE.sub("\\\\", inner.strip()) + "$"

    def display(m: re.Match[str]) -> str:
        inner = m.group(1)
        if not _math_inner_ok(inner):
            return m.group(0)
        return "$$" + _ESCAPED_MACRO_RE.sub("\\\\", inner.strip()) + "$$"

    def paren(m: re.Match[str]) -> str:
        inner = m.group(1)
        if not _math_inner_ok(inner):
            return m.group(0)
        return "$" + _ESCAPED_MACRO_RE.sub("\\\\", inner.strip()) + "$"

    markdown = _BRACKET_MATH_RE.sub(display, markdown)
    markdown = _PAREN_MATH_RE.sub(paren, markdown)
    markdown = _SPACED_DOLLAR_RE.sub(dollar, markdown)
    return markdown


def _flatten_details(match: re.Match[str]) -> str:
    inner = match.group(1)
    inner = _SUMMARY_RE.sub(lambda m: f"**{m.group(1)}**\n\n", inner)
    return inner.strip() + "\n\n"


def sanitize_markdown(markdown: str) -> str:
    """Return ``markdown`` with LaTeX-hostile constructs neutralized."""
    markdown = _normalize_math(markdown)

    # <details><summary>X</summary>Y</details> -> "**X**\n\nY"
    markdown = _DETAILS_BLOCK_RE.sub(_flatten_details, markdown)
    markdown = _LEFTOVER_TAGS_RE.sub("", markdown)

    markdown = _EMOJI_RE.sub("", markdown)

    # Collapse fill-in-the-blank runs LaTeX treats as one unbreakable token and
    # overflows the page. Handle escaped (\_\_\_) and raw forms.
    markdown = _ESCAPED_BLANK_RE.sub(r"\\_" * 10, markdown)
    markdown = _UNDERSCORE_BLANK_RE.sub("_" * 10, markdown)
    markdown = _DASH_BLANK_RE.sub("-" * 10, markdown)
    markdown = _DOT_BLANK_RE.sub("." * 10, markdown)

    markdown = _TRAILING_WS_RE.sub("", markdown)
    return markdown
