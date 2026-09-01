"""Cheap guards on the stylesheet.

Layout is not unit-testable from here, but the specific mistake that broke the
iPhone layout is: an override written with the same specificity as the rule it
means to beat, placed earlier in the file, silently loses.
"""
import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "web" / "styles.css"


@pytest.fixture(scope="module")
def css():
    return CSS.read_text()


def _last_index(text, needle):
    index = text.rfind(needle)
    assert index != -1, "expected to find {!r} in styles.css".format(needle)
    return index


def test_the_narrow_screen_override_comes_after_the_rule_it_overrides():
    """`.field-row` is two columns by default and one on a narrow screen. Both
    selectors have the same specificity, so source order decides — and when the
    override sat earlier in the file it never applied, leaving two 180px
    columns in a 350px space and pushing the whole page off an iPhone."""
    text = CSS.read_text()

    base = text.index(".field-row { display: grid; grid-template-columns: 1fr 1fr;")
    override = _last_index(text, ".field-row { grid-template-columns: 1fr; }")
    assert override > base, (
        "the narrow-screen .field-row rule must come after the base rule, "
        "or it loses on source order"
    )


def test_the_override_is_inside_a_narrow_media_query(css):
    """Guards against the rule being moved out of its media block."""
    blocks = re.findall(r"@media \(max-width: (\d+)px\) \{(.*?)\n\}", css, re.S)
    narrow = [body for width, body in blocks if int(width) <= 500]
    assert any("grid-template-columns: 1fr;" in body for body in narrow)


def test_hidden_beats_display_rules(css):
    """Class rules like `.field { display: flex }` outrank the user agent's
    `[hidden] { display: none }`, which hid nothing until this was added."""
    assert "[hidden] { display: none !important; }" in css


def test_every_media_query_is_closed(css):
    assert css.count("@media") == len(re.findall(r"@media[^{]*\{", css))
    assert css.count("{") == css.count("}")
