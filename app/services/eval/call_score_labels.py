"""Hand-assigned call scores, for testing whether the scoring rewrite is more
*correct* rather than only more consistent.

Method and its limits: `docs/eval/2026-08-14-call-score-grading-standard.md`,
committed **before** the first transcript was opened. The question asked of each
call is "if I were this rep's manager and had just listened to this, what would
I do next?" — High = nothing to coach, Medium = one nameable coaching point,
Low = I'd want a conversation.

**Read the standard before quoting any number from this.** These are one
non-auditor's judgements over 47 calls, assigned from transcripts carrying the
call type and nothing else — no tier from any prompt version, no subscores, no
gaps. They measure agreement with me, not with Moonlight's standard.

`borderline=True` marks calls where two tiers were genuinely inseparable; they
are reported apart from the headline figure rather than silently resolved, the
same way `gap_audit_labels.py` handles them.

`contaminated=True` marks calls whose model output I had already seen before
grading. They are excluded from the headline figure.

`UNGRADABLE` is not a tier. It marks calls that are not a Joveo rep working a
prospect or customer at all — internal calls, no-shows, and negotiations where
Joveo is the buyer. Forcing a grade onto those is how the pipeline produced a
`Medium`/`Risk` coaching card for a job board refusing Joveo access.
"""

from __future__ import annotations

from dataclasses import dataclass

UNGRADABLE = "UNGRADABLE"


@dataclass(frozen=True)
class Label:
    analysis_id: int
    tier: str  # "High" | "Medium" | "Low" | UNGRADABLE
    reason: str
    borderline: bool = False
    contaminated: bool = False


LABELS: list[Label] = [
    Label(
        45,
        "Medium",
        "Proactive: screener questions already live, his team caught the out-of-area "
        "traffic before the client emailed, and he clawed back half a $127 overspend "
        "unprompted. Coaching point: the repeat multi-location applicant issue was left "
        "at 'I'll see what we can do' with no owner and no date.",
    ),
    Label(
        40,
        "Low",
        "Client volunteered that they are contemplating leaving their incumbent agency "
        "and that an RFP is coming - the biggest opening in the call - and the rep "
        "answered with agency name-drops (Havas, Shaker, Wells Fargo) instead of a "
        "single qualifying question. Decision criteria, stakeholders and what 'support "
        "across the globe' means all left unknown. Credit for honest scoping ('we're not "
        "a creative agency') and real rapport, but 25% of the call is baseball and no "
        "agenda was set.",
        borderline=True,
    ),
    Label(
        269,
        "Low",
        "Client asked three substantive questions - pricing/trial next steps, a "
        "video-translation tooling need they are 'moving pretty quickly' on, and whether "
        "the SOW is signed - and the rep deferred all three to Steve or Doug without "
        "owning any. Client had to propose the next step herself ('would it help if "
        "Nicole sent a few bullet points'). On a contract outstanding since March 30 that "
        "is costing the client money, the rep's answer was 'it's not very new to us'. "
        "Rep also joined late with no agenda. Credit for proactively flagging the landing "
        "page URL change.",
    ),
    Label(
        273,
        "Low",
        "No CRM check before an intro call: client had already met Joveo at Unleash and "
        "told him 'you should have been in your system', and he did not know his own "
        "company was already engaged with her programmatic lead. She said plainly she has "
        "left TA, is not the buyer, and 'I don't need more demos' - he then screen-shared "
        "a WBR dashboard anyway and she had to close it down. A live RFP to replace "
        "Smashfly was mentioned and he asked nothing about timing, criteria or "
        "stakeholders. Roughly four of nineteen minutes are Wizard of Oz small talk after "
        "the business ended. Credit: left with the referral name and permission to use it.",
    ),
    Label(
        36,
        "Medium",
        "Well prepared: project tracker updated before the call with status, recap and "
        "pre/launch/post actions; media plans ready for New Zealand, Mexicali and UK; "
        "pixel issue closed; honest about deprioritising CPQ and the client agreed with "
        "the call. Coaching point: when the client asked 'why wasn't this done at the "
        "start', the rep explained the ingestion mechanism instead of answering, and the "
        "client had to restate the conclusion three times before it was confirmed - a "
        "Joveo colleague had asked her for a feed she never needed to build.",
    ),
    Label(
        58,
        UNGRADABLE,
        "Joveo is the BUYER on this call. Trey Nichols is Talroo's rep advising on bids, "
        "budgets and campaign settings ('our system', 'our end'), while the two Joveo "
        "staff ask him for commercials and a pilot. Avoma marks the Joveo staff as the "
        "reps, so the pipeline coaches them on Value Anchor Defense and Securing Closing "
        "Commitments while they are purchasing media. Exactly the supplier-call case "
        "CLAUDE.md records as undetectable from transcript structure.",
    ),
    Label(
        259,
        "Low",
        "The client asked the one question that decides the deal - 'what are you guys "
        "gonna do different than Recruitics', specifically in Bulgaria and Poland - three "
        "times, escalating each time, and noted he had asked it on a previous call too. "
        "The rep answered with methodology ('we'd look at the markets, the roles, the job "
        "boards'), the client said that is exactly what he is not getting from the "
        "incumbent, and the rep replied 'I'm not sure what you mean'. Nothing was "
        "prepared for a question already asked once. Rep was also away from his computer. "
        "Credit for a genuinely good probe on how Poland is recruited today.",
    ),
    Label(
        34,
        "Medium",
        "Prepared and technically honest: tracker updated with comments, offered to walk "
        "the client through it when she had not read it, explained the Workday "
        "source-configuration limit and proposed a workable single-source fix, and "
        "declined to over-promise on apply-finish tracking ('let me go back and double "
        "check') rather than guessing. Two coaching points: the client volunteered that "
        "she can get Joveo Workday API access - a significant unlock - and it ended at "
        "'let me double check' with no owner or date; and several minutes were spent "
        "watching a slow feed URL load on the call instead of taking it offline.",
    ),
    Label(
        53,
        "High",
        "Agenda sent in advance and worked through: job ingestion, funnel stages, easy "
        "apply, credentials, pixel. Proposed a pixel-swap plan built around the client's "
        "constraints rather than his own ('I don't want to put you on the spot now'), and "
        "asked for a backup point of contact while she was away the next day - genuinely "
        "exemplary risk management. Left with API keys committed, a Rich meeting agreed "
        "and a named backup. Steve was straight about the unsigned SOW rather than hiding "
        "it. Nearest thing to a coaching point is that the Rich meeting was left to the "
        "client to schedule.",
        borderline=True,
    ),
    Label(
        378,
        "Medium",
        "Handled the real objection well: client had been told '$25,000 minimums per "
        "month' while her incumbent delivers on $4,000, and the rep corrected it, asked "
        "for her current campaign data to benchmark apples-to-apples, and probed the "
        "right question - clicks or conversions. Surfaced a genuine improvement (she has "
        "no conversion tracking at all) and gave a concrete fallback on the Facebook "
        "brand-handle blocker. Coaching point: the two sellers were visibly unaligned on "
        "their own product in front of the client ('which social product is that for?'), "
        "and the $25k confusion originated in Joveo's own earlier messaging. "
        "NOTE: Avoma marks Rune Highmore (Yoke, now merged with Joveo) as a CLIENT "
        "speaker though he is selling - a speaker-attribution error, not a rep error.",
    ),
    Label(
        50,
        "High",
        "The call I would actually show other reps. Opened by offering to help build her "
        "internal business case rather than pushing the product. Offered a reference "
        "customer but disciplined about when - 'it needs to be the last step, you should "
        "be right at the finish line' - instead of burning it early. Probed properly: "
        "noticed time-to-fill was suspiciously fast, asked about attrition, and uncovered "
        "that 0-10 day turnover is the real pain, then tied it to cost. David separately "
        "owned a genuine service failure outright ('that's a miss on our end, and I "
        "apologise') with root cause and fix. Marked borderline for one real miss: the "
        "client said 'if we don't move forward with this technology, I won't be here that "
        "long' - a champion-at-risk signal - and it passed with a joke.",
        borderline=True,
    ),
    Label(
        56,
        "Medium",
        "Nargis carried this: tracker current, Mexicali plan with budget tiering agreed, "
        "New Zealand call booked live, weekly rescheduled, and she surfaced a Phenom "
        "pixel item the client had forgotten ('thank you for reminding me'). Scott was "
        "the coaching point - could not say where Raytheon UK stood ('I don't know where "
        "we're at with that right now'), did not know what the Sonic Jobs setup form was "
        "asking for, and the client had to prompt him for the SOW she has been waiting "
        "on. Roughly eleven of forty-two minutes are the two reps waiting before the "
        "client joined.",
    ),
    Label(
        52,
        UNGRADABLE,
        "Joveo is the buyer again - Talroo's account manager advises Joveo staff on bids, "
        "budget pacing and campaign setup for the Uber account they resell. Second such "
        "call in the corpus after id 58. Speaker attribution is badly wrong here too: "
        "three of four speakers are tagged CLIENT, including two Joveo people who are "
        "plainly buying.",
    ),
    Label(
        264,
        "Medium",
        "Strong demo craft: showed the manual flow first then the AI version so the "
        "contrast landed, answered a live silver-medalist question inside the product, "
        "and met the client's real pain - eight nursing homes within fifteen miles and "
        "the same RN applying to all of them - with a specific implementation from an "
        "analogous retail customer rather than a promise, while saying plainly 'I'm "
        "leery to solution on a call'. Deliberately stopped presenting to go back to "
        "discovery, and was honest about not knowing the UKG onboarding integration "
        "status rather than guessing. Left with a named introduction. Coaching points: "
        "disparaged a partner on a recorded call ('UKG can be difficult to work with... "
        "hopefully you can edit it out'), and a long knowledge-graph/Wikidata tangent "
        "near the end pulled the call away from the close. "
        "NOTE: most client speech comes from unresolved speaker-0/speaker-1 ids - this is "
        "the NHS call CLAUDE.md cites as the input gate's R2 abstention case.",
    ),
    Label(
        381,
        "High",
        "Advanced account management. Raised a full system audit ticket before the call, "
        "explained the Indeed single-source-feed deprecation, and owned the "
        "communication miss outright ('I figured you knew about the Indeed switch. "
        "That's on me'). The standout is the weekly deck built explicitly so the client "
        "can take it to HER client - 'you don't have to do anything in here, just take "
        "the deck and show it to them' - with competitor analysis and JD audits she said "
        "BMS 'will eat up'. Arming the champion with an asset for their own stakeholder "
        "is the behaviour to showcase. Also asked whether the campaign structure still "
        "serves her rather than order-taking, and adapted the cadence to her preference. "
        "Borderline only because credentials he supplied had expired, costing live time.",
        borderline=True,
    ),
]


def lookup(analysis_id: int) -> Label | None:
    for label in LABELS:
        if label.analysis_id == analysis_id:
            return label
    return None


def gradable() -> list[Label]:
    """Labels usable for an accuracy figure: a real tier, and not contaminated."""
    return [
        label
        for label in LABELS
        if label.tier != UNGRADABLE and not label.contaminated
    ]
