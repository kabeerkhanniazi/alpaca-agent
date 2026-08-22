"""Autonomous defined-risk options trading agent for Alpaca.

Sells vertical credit spreads only — never a naked short. Every proposed
position passes a deterministic nine-rule risk gate before it can reach the
broker, and no language model is involved anywhere in the decision path.
"""

__version__ = "1.0.0"
