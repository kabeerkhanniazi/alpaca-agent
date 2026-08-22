"""Dashboard smoke test.

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


@pytest.mark.integration
def test_dashboard_renders_without_error():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=180)
    app.run()

    assert not app.exception, [str(e.value) for e in app.exception]
    assert app.title[0].value.endswith("Autonomous Options Agent")


@pytest.mark.integration
def test_dashboard_shows_all_four_panels():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(REPO_ROOT / "streamlit_app.py"), default_timeout=180)
    app.run()

    headings = {s.value for s in app.subheader}
    assert {"Portfolio", "Performance", "Market", "Trade journal"} <= headings
