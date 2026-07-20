"""Math-normalization tests: LLM math habits must become Pandoc-parsable math.

The real-world failure (Black hole study guide, 2026-07): the LLM wrote
``$ r_s = \\frac{2GM}{c^2} $`` — Pandoc's tex_math_dollars rejects the padded
delimiters, the ``\\frac`` leaked through as raw TeX, vanished from the HTML
output, and aborted the tectonic PDF with "Missing $ inserted".
"""

from __future__ import annotations

from studyguide_creator.sanitize import sanitize_markdown


def test_spaced_inline_dollars_become_math():
    out = sanitize_markdown(r"radius $ r_s = \frac{2GM}{c^2} $, which defines")
    assert r"$r_s = \frac{2GM}{c^2}$" in out
    assert "$ r_s" not in out


def test_single_token_spaced_dollars_become_math():
    out = sanitize_markdown("Where: $ G $ = gravitational constant $ M $ = mass")
    assert "$G$" in out and "$M$" in out
    assert "= gravitational constant" in out


def test_bracket_display_math_becomes_dollars():
    out = sanitize_markdown(r"The radius is: \[ r_s = \frac{2GM}{c^2} \] Where:")
    assert r"$$r_s = \frac{2GM}{c^2}$$" in out


def test_double_escaped_bracket_and_macros_normalize():
    out = sanitize_markdown("radius: \\\\[ r_s = \\\\frac{2GM}{c^2} \\\\]")
    assert "$$r_s = \\frac{2GM}{c^2}$$" in out


def test_paren_inline_math_becomes_dollars():
    out = sanitize_markdown(r"speed \( v = H_0 d \) grows")
    assert r"$v = H_0 d$" in out


def test_currency_is_left_alone():
    text = "It costs $ 5 for adults and $ 6 for kids."
    assert sanitize_markdown(text) == text


def test_correct_math_is_untouched():
    text = r"Energy $E = mc^2$ and $$\int_0^1 x\,dx$$ stay as-is."
    assert sanitize_markdown(text) == text


def test_escaped_link_brackets_left_alone():
    text = r"see \[citation needed\] for details"
    assert sanitize_markdown(text) == text


def test_prose_dollar_spans_left_alone():
    text = "between $ five dollars and change $ overall"
    assert sanitize_markdown(text) == text
