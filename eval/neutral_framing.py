"""The neutral-framing arm of the verifier leniency experiment. Eval only.

The shipped verifier asks *"does this QUOTE support this CLAIM?"* — a question
that hands the model a conclusion and asks it to agree, and it duly agrees with
71% of findings. The same model asked the neutral question *"does this call
exhibit theme X?"* produced 0/25 false positives on the falsifiability probe.
This module builds that neutral question so the two can be scored against the
same hand labels.

Held constant against production, so that framing is the only variable:

- **Same context per gap kind.** `dialogue` gaps get the same ±`window_turns`
  excerpt, located by the same re-anchored timestamp (`gap_verification._window`
  is imported rather than reimplemented — a second copy would drift and quietly
  make the arms incomparable). `explanation` gaps get the whole transcript.
- **Same theme string**, not the rubric's fuller description. Adding the
  description would move two variables at once.
- **Same demand for a quote.** Both arms must name the words behind the answer;
  that hardening is not what is under test.

Withheld, because withholding it *is* the treatment: the claim, and the gap's
own pre-selected evidence. A body that still showed either would be the leading
question wearing a different hat.

**Why these prompts are Python constants and not files under `app/prompts/`:**
`PromptRegistry.latest()` picks the highest version label it finds, so dropping
a `v2-*.txt` into `gap_verification/` would silently repoint the production
verifier at an unvalidated prompt. Nothing here can reach production.

Three answers, not two. `cannot_tell` exists because a ±3-turn excerpt genuinely
cannot settle a whole-call theme like "Seller-Dominated Conversation", and a
reframed verifier that dropped a gap on "I can't tell from this" would be
deleting findings for a structural reason rather than a substantive one. Only an
affirmative `absent` drops a gap — see `is_kept`. That also keeps this arm
inside the same boundary as the shipped pass: it can only ever remove.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.domain.errors import LLMOutputError
from app.domain.gap_verification import _window
from app.llm.client import LLMMessage, StructuredOutputError

STEP = "neutral_verification"


class Presence(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    # Not "unsure" — this is specifically "the input given cannot settle it",
    # which is a property of the excerpt, not of the model's confidence.
    CANNOT_TELL = "cannot_tell"


class PresenceItem(BaseModel):
    index: int
    presence: Presence
    reason: str
    evidence_quote: str


class NeutralPresenceResponse(BaseModel):
    findings: list[PresenceItem]


def is_kept(presence: Presence) -> bool:
    """Only an affirmative absence drops a gap."""
    return presence is not Presence.ABSENT


NEUTRAL_DIALOGUE_PROMPT = """You are auditing a sales call against a coaching rubric.

You will be given a numbered list of items. Each names ONE coaching theme — a
description of something that can go wrong on a sales call — and a short EXCERPT
from the middle of a real call.

For each item, answer one question: does this excerpt exhibit that theme?

Return exactly one answer per item, using the item's index:

  "present"      - the excerpt shows the problem the theme describes. You can
                   point at the words where it happens.
  "absent"       - the excerpt does not show that problem. This includes the
                   case where the excerpt looks superficially like the theme but
                   the problem is resolved, handled by someone else, or does not
                   apply to this client's situation.
  "cannot_tell"  - the theme describes a whole-call pattern (how much of the
                   call one side talked, whether something was ever agreed) that
                   an excerpt of a few turns cannot settle either way.

Use "cannot_tell" honestly. It is the right answer for a theme about the shape
of the whole call, and a wrong "absent" is more costly than an admitted limit.
But do not hide behind it for a theme the excerpt plainly does or does not show.

You must also return `evidence_quote`:

  - For "present": the exact words in the excerpt that ARE the problem. Not a
    paraphrase, not a restatement of the theme. If you cannot point at a
    specific span showing the behaviour, the answer is not "present".
  - For "absent": the words that come closest to looking like the theme, and
    that you judged insufficient — or the single word "none" if nothing in the
    excerpt resembles it at all.
  - For "cannot_tell": the single word "none".

Read the whole excerpt before answering. A problem raised and then resolved a
turn or two later is not present: if someone answers the question, supplies the
missing commitment, or corrects the omission within the excerpt, the theme is
"absent" even though one line in isolation would look like evidence for it.

Most calls do not exhibit most themes. "absent" is a normal, expected answer.
Equally, do not read a theme so strictly that nothing could ever satisfy it.
Judge the theme as written.
"""

NEUTRAL_EXPLANATION_PROMPT = """You are auditing a sales call against a coaching rubric.

You will be given the FULL TRANSCRIPT of one call, then a numbered list of
items. Each names ONE coaching theme — a description of something that can go
wrong on a sales call. These themes are mostly about things that should have
happened and may not have: success metrics agreed, an escalation path set, a
timeline committed to, customer proof given.

For each item, answer one question: does this call exhibit that theme?

Return exactly one answer per item, using the item's index:

  "present"      - you searched the transcript and the thing genuinely does not
                   happen anywhere in it, so the theme applies to this call.
  "absent"       - it does happen. The theme does not apply.
  "cannot_tell"  - the theme does not describe anything checkable about this
                   call, or asserts the absence of something that was never
                   applicable to it.

The search is not optional, and `evidence_quote` is where you show it:

  - For "absent": the exact line where the supposedly missing thing DOES
    happen. Quote it verbatim, with its [mm:ss].
  - For "present": the exact line that comes CLOSEST to satisfying the theme —
    the nearest miss you found while searching — and why it falls short. If you
    cannot name a nearest miss, you have not searched. Go back and look.
  - For "cannot_tell": the single word "none".

That nearest-miss requirement exists to stop this check rubber-stamping. A theme
is only "present" once you have hunted for the thing, found the closest
candidate, and judged it insufficient.

Search the WHOLE transcript, including the last few minutes. Commitments,
cadences and next steps are usually agreed at the very end of a call, which is
exactly where a hurried reader stops looking.

Most calls do not exhibit most themes. A theme that would be true of almost any
call is usually a template, not an observation — check before you accept it.
"""


def dialogue_body(transcript, batch, window_turns: int) -> str:
    """One block per gap: the theme and the excerpt, and nothing else."""
    return "\n\n".join(
        f"--- ITEM {index} ---\n"
        f"THEME: {gap.theme}\n"
        f"EXCERPT:\n{_window(transcript, gap, window_turns)}"
        for index, gap in batch
    )


def explanation_body(transcript, batch) -> str:
    items = "\n\n".join(f"--- ITEM {index} ---\nTHEME: {gap.theme}" for index, gap in batch)
    return f"FULL TRANSCRIPT:\n{transcript.render_for_prompt()}\n\n{items}"


async def judge_presence(
    llm_client, *, prompt_text: str, body: str, response_key: str, batch
) -> dict[int, PresenceItem]:
    """One gateway call for one batch. Returns {gap index -> answer}.

    Index strictness mirrors `gap_verification._judge` deliberately: a missing
    answer would let an unassessed gap through as if it had been judged, and an
    unexpected index means the response is not describing the batch we sent.
    """
    try:
        response = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=prompt_text),
                LLMMessage(role="user", content=body),
            ],
            response_model=NeutralPresenceResponse,
            response_key=response_key,
        )
    except StructuredOutputError as exc:
        raise LLMOutputError(STEP, exc.raw_content, exc.detail) from exc

    returned = {item.index: item for item in response.parsed.findings}
    asked = {index for index, _ in batch}
    if returned.keys() != asked:
        raise LLMOutputError(
            STEP,
            response.content,
            f"neutral framing returned answers for {sorted(returned)} "
            f"but was asked about {sorted(asked)}",
        )
    return returned
