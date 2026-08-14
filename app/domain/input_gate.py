"""Decides whether a call is an assessable NB sales conversation at all.

Every call the fetcher stored used to get an `analysis` row and four LLM steps,
so calls with nothing in them produced confident-looking cards. Measured over
the 32 in-scope calls, four were not sales calls: a single 30-word turn scored
Discovery/Low, a recording that captured only the meeting moving to Zoom scored
Discovery/Low, a client no-show where two Joveo employees talked to each other
scored Follow-up Demo/Low, and a supply negotiation where Joveo was the buyer
came back Medium/**Risk** with a coaching gap.

The first three share a failure mode worth stating plainly: a `Low` score on a
call where nobody spoke reads to a moderator as "this rep performed badly" when
it means "there was no conversation". Those are opposite messages and a card
cannot distinguish them.

**This does not breach the "gap detection is purely LLM reasoning" boundary.**
That constraint forbids code re-deriving a *gap* call from transcript text. The
gate decides whether a call is analysed at all and never inspects it for
coaching content.

**Why it lives here and not in the analyser:** running in the fetcher means a
rejected call costs no gateway request. Rejected calls are still *stored*,
carrying their reason — `filter_new_calls` decides novelty purely by absence
from `call_storage`, so a call we declined to store would be re-fetched from
Avoma on every run, forever, with no record that we ever saw it.

What is deliberately NOT here:

- **Domain matching against the account.** The first version of this compared
  speaker email domains to `moonlight_accounts.domain` and rejected 27 of 51
  calls, 24 of them good — account domains carry `www.`/`about.` prefixes, the
  subdomain sits on the account side, and subsidiaries mail from the parent
  (account `prattwhitney.com`, speakers on `rtx.com`). See
  `problems-and-fixes.md` 8.17. Do not reintroduce it without re-measuring.
- **Supplier/partner detection.** The HelloWork call has a real non-rep
  speaking 1,898 words on the account's own registered domain, so it is
  indistinguishable from an ordinary client call by any structural signal.
  Needs account-level classification we do not own; accepted as out of scope.
- **A minimum amount of client speech.** A client who only says "can you hear
  me?" still leaves nothing to coach, but no threshold is invented here — see
  `eval/input_gate_report.py` for the distribution it would come
  from.
"""

from dataclasses import dataclass

from app.core.input_gate_config import InputGateConfig
from app.domain.transcript import Transcript
from app.domain.types import ExclusionReason


@dataclass(frozen=True)
class GateVerdict:
    accepted: bool
    reason: ExclusionReason | None = None
    # Human-readable evidence for the decision, stored as
    # `call_storage.excluded_detail` so a reviewer can see why without
    # re-reading the transcript.
    detail: str | None = None
    # True when the client-speech rule could not reach an answer and abstained.
    # Distinct from "passed": it means the call was analysed without that check,
    # which the phase-5 report surfaces rather than hiding.
    client_speech_skipped: bool = False


def _word_count(text: str) -> int:
    return len(text.split())


def evaluate_input_gate(transcript: Transcript, config: InputGateConfig) -> GateVerdict:
    """Both rules, in order. Needs no account data — everything is in the
    transcript."""
    if not config.enabled:
        return GateVerdict(accepted=True)

    total_words = sum(_word_count(turn.text) for turn in transcript.turns)

    # R1 first, so a call failing both reports the more specific reason.
    if total_words < config.min_words:
        return GateVerdict(
            accepted=False,
            reason=ExclusionReason.NO_CONVERSATION,
            detail=(
                f"{total_words} words across {len(transcript.turns)} "
                f"turn{'' if len(transcript.turns) == 1 else 's'}, "
                f"below the {config.min_words}-word floor"
            ),
        )

    if not config.require_client_speech:
        return GateVerdict(accepted=True)

    is_rep_by_id = {speaker.id: speaker.is_rep for speaker in transcript.speakers}

    rep_words = 0
    non_rep_words = 0
    unlinked_words = 0
    for turn in transcript.turns:
        words = _word_count(turn.text)
        is_rep = is_rep_by_id.get(turn.speaker_id)
        if is_rep is None:
            unlinked_words += words
        elif is_rep:
            rep_words += words
        else:
            non_rep_words += words

    if non_rep_words > 0:
        return GateVerdict(accepted=True)

    # Nobody on the client's side is *linked* to any speech. Either genuinely
    # nobody spoke, or Avoma failed to resolve whoever did — and those must not
    # be treated alike. Abstaining on unattributable speech is what takes this
    # rule from one measured false positive to none.
    if unlinked_words > 0:
        return GateVerdict(
            accepted=True,
            client_speech_skipped=True,
            detail=(
                f"client-speech check skipped: {unlinked_words} words from "
                f"speaker ids Avoma did not resolve, {rep_words} words from reps"
            ),
        )

    return GateVerdict(
        accepted=False,
        reason=ExclusionReason.NO_CLIENT_SPEECH,
        detail=(
            f"no non-rep speaker had any turns; {rep_words} words spoken by "
            f"{sum(1 for r in is_rep_by_id.values() if r)} rep(s)"
        ),
    )
