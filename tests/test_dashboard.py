"""Dashboard smoke tests.

Runs the Streamlit script end to end and asserts it renders without raising.
A broken dashboard is a submission-day problem — this catches it here instead.

Marked ``integration`` because it hits the live Alpaca API for account state.
Run the rest of the suite with ``-m "not integration"`` to stay fully offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

REPO_ROOT = Path(__file__).resolve().parent.parent


def render():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=300)
    app.run()
    return app


def markup(app) -> str:
    return "\n".join(m.value for m in app.markdown)


@pytest.mark.integration
def test_dashboard_renders_without_error():
    app = render()
    assert not app.exception, [str(e.value) for e in app.exception]


@pytest.mark.integration
def test_every_panel_survives_the_restyle():
    """The restyle must not have dropped a single panel."""
    blob = markup(render())
    for anchor in ("portfolio", "risk-gate", "performance", "market", "journal"):
        assert f'id="{anchor}"' in blob, f"{anchor} section missing"


@pytest.mark.integration
def test_all_four_journal_tabs_survive():
    assert len(render().tabs) == 4


@pytest.mark.integration
def test_greeks_and_headroom_bars_survive():
    """The Greeks row and both headroom bars must survive the restyle.

    These panels need live account data, so the assertion is written against
    both legitimate outcomes: the panels render when Alpaca answers, and the
    documented placeholder renders when it does not. Anything else — a blank
    region, a traceback — fails.

    Written this way deliberately: an earlier version asserted the populated
    case only and went red during an Alpaca outage, which is a bad test. A
    third-party blip should not turn the suite red, but a dropped panel must.
    """
    blob = markup(render())

    if "Live data unavailable" in blob:
        # Degraded path: the placeholder must be there instead, and the
        # journal-derived panels must still have rendered below it.
        assert 'id="risk-gate"' in blob
        assert 'id="performance"' in blob
        return

    for label in ("Net delta", "Theta / day", "Vega", "Gamma"):
        assert label in blob, f"Greeks row lost {label}"
    assert "Portfolio delta · Rule 3" in blob
    assert "Daily drawdown · Rule 8" in blob


@pytest.mark.integration
def test_an_alpaca_outage_does_not_blank_the_page():
    """Whatever Alpaca is doing, the journal-derived panels must render."""
    blob = markup(render())
    assert 'id="journal"' in blob
    assert 'oa-rule-name">R1 ·' in blob


@pytest.mark.integration
def test_performance_metrics_survive():
    blob = markup(render())
    for label in ("Win rate", "Realized P&amp;L", "Positions closed",
                  "Orders filled", "Avg credit", "Gate approval rate"):
        assert label in blob, f"Performance panel lost {label}"


@pytest.mark.integration
def test_risk_gate_lists_all_nine_rules():
    blob = markup(render())
    for n in range(1, 10):
        assert f'oa-rule-name">R{n} ·' in blob, f"rule R{n} missing from the gate panel"


@pytest.mark.integration
def test_no_control_can_reach_the_broker():
    """The whole page must expose exactly one button: Refresh.

    This dashboard is deployed publicly. The mockup's EXECUTE LIVE and KILL
    SWITCH are rendered as status spans precisely so a stranger cannot click
    them; if a real button for either ever appears, this fails.
    """
    app = render()
    labels = [b.label for b in app.button]
    assert labels == ["Refresh now"], f"unexpected controls on the page: {labels}"


@pytest.mark.integration
def test_status_pills_are_spans_not_buttons():
    blob = markup(render())
    assert "MODE:" in blob and "KILL-SWITCH:" in blob
    # They must be pill spans, and must not appear as clickable elements.
    assert "oa-pill" in blob
    assert "<button" not in blob


@pytest.mark.integration
def test_no_mockup_placeholder_survives():
    """None of the mockup's hardcoded chrome may reach the page."""
    blob = markup(render())
    for literal in ("NEURAL COMMAND", "Neural Link", "EXECUTE LIVE", "Logout",
                    "Mainframe", "Liquidity", "2d 3h 15m"):
        assert literal not in blob, f"mockup literal leaked into the render: {literal}"


@pytest.mark.integration
def test_no_dead_links():
    """Every anchor href must resolve to an id that exists on the page."""
    import re

    blob = markup(render())
    ids = set(re.findall(r'id="([\w-]+)"', blob))
    for href in re.findall(r'href="#([\w-]+)"', blob):
        assert href in ids, f"dead link: #{href} has no matching element"


@pytest.mark.integration
def test_stylesheet_carries_no_script_or_link_tags():
    """Streamlit strips <script>; <link> would be a silent network dependency."""
    blob = markup(render())
    assert "<script" not in blob.lower()
    assert "<link" not in blob.lower()
