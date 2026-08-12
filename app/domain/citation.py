"""Checks that a gap's quote can actually be found in the transcript, and
anchors its timestamp to the moment it was said.

This validates the *citation*, never the judgement — the same category of rule
as the dialogue/explanation timestamp invariant in `gap_analysis`. Nothing here
decides whether a gap is real; a moderator still owns that. It only guarantees
that when a card says "at 01:09 they said X", X was said, and at 01:09.

Measured over 46 real calls before this existed: of 58 `dialogue` citations, 12
did not match the transcript verbatim, 8 carried a timestamp more than 5s off,
and 4 pointed at a different speaker's turn. Critically, **none were
fabricated** — every mismatch was real speech that the model had prefixed with
`Speaker:` labels of its own invention, stitched across turns, or elided with
`...`. So the rule below is deliberately generous about form and strict only
about substance: enough of the quote's actual words must appear, in order, in
the transcript.

Matching is therefore on word runs rather than exact substrings. A quote is
accepted when the words it shares with the transcript — counted only in runs
long enough not to be coincidence — cover most of it. Invented narration and
speaker labels simply fail to match and drag coverage down; a wholly invented
quote shares no long run at all and is rejected.
"""

import re

from app.domain.errors import LLMOutputError
from app.domain.transcript import Transcript
from app.domain.types import Gap

STEP = "gap_analysis"

# A run must be this many words before it counts as a real match. Below about
# four, common connective phrases ("I think we can", "so we would have") match
# by chance in any long transcript.
_MIN_RUN_WORDS = 4

# Share of the quote's words that must land in such runs. The 46-call replay
# put every genuine citation at 0.89 or above and every fabrication would sit
# near zero, so this sits well clear of both.
_MIN_COVERAGE = 0.6


def _words(text: str) -> list[str]:
    """Compares on words alone — ASR punctuation and casing are not evidence."""
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^a-z0-9' ]+", " ", text).split()


def _match_runs(quote: list[str], hay: list[str], index: dict[str, list[int]]):
    """Greedily walks the quote, taking the longest run of its words that
    appears contiguously in the transcript. Returns (matched_words,
    first_hay_position) — position is where the earliest accepted run starts,
    which is the moment the citation should be anchored to."""
    # A quote shorter than the usual run floor has to match in full instead:
    # "coming off Radancy" is a legitimate three-word citation.
    min_run = min(_MIN_RUN_WORDS, len(quote))
    matched = 0
    first = None
    i = 0
    while i < len(quote):
        best_len, best_at = 0, -1
        for start in index.get(quote[i], ()):
            length = 0
            while (
                i + length < len(quote)
                and start + length < len(hay)
                and hay[start + length] == quote[i + length]
            ):
                length += 1
            if length > best_len:
                best_len, best_at = length, start
        if best_len >= min_run:
            matched += best_len
            first = best_at if first is None else min(first, best_at)
            i += best_len
        else:
            i += 1
    return matched, first


def verify_citations(gaps: list[Gap], transcript: Transcript, *, raw_content: str) -> list[Gap]:
    """Returns `gaps` with each `dialogue` timestamp anchored to the turn its
    quote begins in. Raises `LLMOutputError` if a quote's words are not in the
    transcript — an uncheckable citation is exactly what the transcript
    timestamp contract exists to prevent, so it takes the normal failed-step
    retry path rather than reaching a moderator.
    """
    # One flat word list plus a word-position -> turn map, so a quote starting
    # mid-turn or running across a turn boundary still resolves to its turn.
    hay: list[str] = []
    turn_of: list[int] = []
    for turn_index, turn in enumerate(transcript.turns):
        turn_words = _words(turn.text)
        hay.extend(turn_words)
        turn_of.extend([turn_index] * len(turn_words))

    index: dict[str, list[int]] = {}
    for position, word in enumerate(hay):
        index.setdefault(word, []).append(position)

    verified = []
    for gap in gaps:
        if gap.evidence_type != "dialogue":
            verified.append(gap)
            continue

        quote = _words(gap.evidence or "")
        matched, first = _match_runs(quote, hay, index)
        if not quote or first is None or matched / len(quote) < _MIN_COVERAGE:
            raise LLMOutputError(
                STEP,
                raw_content,
                f"gap for theme {gap.theme!r} quotes text that does not appear "
                f"in the transcript: {gap.evidence!r}",
            )

        verified.append(
            Gap(
                theme=gap.theme,
                evidence_type=gap.evidence_type,
                evidence=gap.evidence,
                timestamp=transcript.turns[turn_of[first]].timestamp,
                confidence=gap.confidence,
            )
        )
    return verified
