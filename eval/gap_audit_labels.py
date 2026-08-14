"""Hand labels from the 2026-08-12 gap audit, for scoring the entailment verifier.

These are the only human judgements available anywhere in this project about
whether a stored gap is *correct*. Without them, a verifier change can only be
reported as a keep/drop rate — you cannot tell whether the drops were right.
That is why they live in the repo rather than in a scratch file.

## What they are

35 of the 86 gaps in `analysis` as of 2026-08-12, labelled by reading the
transcript around each gap (`eval/verification_replay.py` produces
the verdicts these are scored against). Keyed by
`(analysis_id, theme-prefix)` — prefix because the theme strings contain an em
dash that does not survive every round trip.

    True  = evidence supports the claim; a moderator should see this card
    False = evidence contradicts the claim, is irrelevant to it, or the claim
            is factually disproved by the transcript

## How much to trust them — read this before quoting a number

- **One reviewer.** These are not business-team ground truth. Disagreement
  between the verifier and a label means "worth a human look", not "the
  verifier is wrong".
- **Not a random sample.** Gaps were chosen partly *because* they looked
  interesting, so the ratio of good to bad here (10:18 excluding borderline) is
  almost certainly worse than the true base rate. Any precision figure
  extrapolated from it is an estimate, not a measurement.
- **7 are marked borderline** and should be excluded from a strict score. They
  are genuine judgement calls where a second reviewer could reasonably differ.
- **Frozen against a specific corpus.** They describe those 86 gap rows. If
  `analysis` is re-run and overwritten, these labels no longer refer to
  anything — which is one more reason not to overwrite those rows.

Measured against these labels, the verifier as shipped retained 9/10 good gaps
and removed 12/18 bad ones (run 2 of 2). See `problems-and-fixes.md` Part 8.
"""

# (analysis_id, theme prefix) -> (should_keep, is_borderline, note)
LABELS: dict[tuple[int, str], tuple[bool, bool, str]] = {
    # --- evidence genuinely supports the claim ---
    (40, "Swim Lanes"): (True, False, "agency/partner swim lanes really are unclear"),
    (41, "Competitive Intelligence"): (True, False, "Radancy dissatisfaction never probed"),
    (41, "Seller-Dominated"): (True, False, "ran 8min over; next steps squeezed into overtime"),
    (43, "IC-Level Only"): (True, False, "Marie: decision 'way above me'"),
    (50, "Buzzword Fatigue"): (True, True, "client explicitly asks for case studies"),
    (53, "Technical Prerequisites"): (True, False, "pixel/API unknowns days before go-live"),
    (257, "Outcome-Based Positioning"): (True, True, "feature-led, though some outcomes given"),
    (259, "Buzzword Fatigue"): (True, True, "client says he can't tell what they'd do"),
    (260, "Missed Strategic Deal-Driving"): (True, False, "soft close, DM on vacation 2wks"),
    (260, "Unanswered / Poorly Handled"): (True, False, "Paradox integration Q deferred"),
    (260, "Imprecise Messaging"): (True, True, "anonymous '10 largest health system' stat"),
    (263, "IC-Level Only"): (True, False, "'not budgeted, need VPs + finance + procurement'"),
    (264, "Demo Not Customised"): (True, False, "retail example shown to a healthcare buyer"),
    (274, "Outcome-Based Positioning"): (True, False, "15min capability tour, no outcome framing"),
    # --- evidence contradicts, is irrelevant, or the claim is disproved ---
    (31, "No Competitive Framing"): (False, False, "evidence is about Phenom feed/UTM codes"),
    (40, "Outcome-Based Positioning"): (False, True, "quote is honest scoping, not a gap"),
    (41, "Demo Not Customised"): (False, False, "quote says '...fully customized to your stages'"),
    (43, "No Competitive Framing"): (False, False, "Marie commissions a Joveo/Broadbeam slide"),
    (44, "No Committed Timeline"): (False, False, "'send the plan within two days'"),
    (53, "No Committed Timeline"): (False, False, "sign-off pleasantry; call sets Fri/Mon/next wk"),
    (53, "Escalation Path"): (False, True, "rep asks for the backup contact in the quote"),
    (57, "No Pre-Call Research"): (False, False, "'I saw you're using Symphony on your career site'"),
    (58, "Reactive Pricing"): (False, False, "Joveo is the buyer; coaches Talroo's rep"),
    (58, "IC-Level Only"): (False, False, "names Talroo's rep as the IC; Joveo staff present"),
    (58, "No Competitive Framing"): (False, False, "bid optimisation, not a pricing negotiation"),
    (257, "No Pre-Call Research"): (False, False, "'I know you mentioned in one of your emails'"),
    (259, "No Pre-Call Research"): (False, False, "evidence is about contract start, not stack"),
    (260, "Seller-Dominated"): (False, False, "INVERTED: told he has 30 more minutes"),
    (260, "Wrong People on the Call"): (False, False, "colleague answers fully 7 seconds later"),
    (262, "Wrong People on the Call"): (False, False, "Yazad handles it and offers the tech team"),
    (262, "Demo Not Customised"): (False, False, "fires on the presenter's honesty disclaimer"),
    (264, "Missed Strategic Deal-Driving"): (False, False, "quote IS the client's buying signal"),
    (265, "No Committed Timeline"): (False, True, "'3 to 4 weeks' discussed just before"),
    (274, "Buzzword Fatigue"): (False, False, "HALLUCINATED: Uber + Banfield named w/ figures"),
    (274, "No Pre-Call Research"): (False, False, "legit discovery Q about the partner's clients"),
}


def lookup(analysis_id: int, theme: str | None) -> tuple[bool, bool, str] | None:
    """The label for one gap, or None if it was never hand-reviewed.

    Matches on theme *prefix*, so a theme string that has been re-encoded
    (em dash, smart quotes) still resolves.
    """
    for (labelled_id, prefix), value in LABELS.items():
        if labelled_id == analysis_id and (theme or "").startswith(prefix):
            return value
    return None
