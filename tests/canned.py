"""Canned LLM responses shared by the batch tests.

The scoring step's response shape is the one thing here worth centralising: it
carries ten category rows, so inlining it turns every "and the call scores
High" fixture into a wall of JSON that obscures what the test is about.
"""

import json

from app.domain.types import CallScore


def scoring_response(tier: CallScore = CallScore.HIGH, *, categories: int = 10) -> str:
    """Ten scored categories whose mean lands in `tier`'s band.

    Phase A made the tier arithmetic over the subscores, so a fixture can no
    longer just name the tier it wants — it has to supply scores that produce
    it. See app/domain/scoring.py for the thresholds.
    """
    score = {CallScore.HIGH: "5", CallScore.MEDIUM: "3", CallScore.LOW: "1"}[tier]
    return json.dumps(
        {
            "categories": [
                {"name": f"Category {i}", "score": score, "evidence": "they said something"}
                for i in range(1, categories + 1)
            ]
        }
    )


HIGH_SCORE_RESPONSE = scoring_response(CallScore.HIGH)
