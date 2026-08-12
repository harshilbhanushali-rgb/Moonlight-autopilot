"""Hand labels for `call_type`, from reading full transcripts on 2026-08-13.

`call_type` is the fan-out step: it selects both the scoring prompt and the gap
rubric, so a wrong label produces a wrong score *and* gaps from an inapplicable
rubric. Until these labels existed there was no way to measure its accuracy at
all — the only figure available was an 11% flip rate between identical re-runs,
which measures instability, not correctness.

## Scope: in-source calls only

`call_storage` holds 51 calls, but only **32 are still present in
`moonlight_calls`**. The client table went from 296 rows to 197 on 2026-08-12
and 19 of ours were among the deletions. Confirmed with the user: **a call absent
from Koushik's table is not in scope** — treat it as not needed rather than as
data we should reason about. So only the 32 in-source calls are labelled here,
and the remaining 19 are deliberately absent from this file.

One consequence worth knowing: that rule removes the worst *scope* offender —
the Talroo bi-weekly where Joveo is the buyer and the pipeline coached Talroo's
rep — but it does **not** remove the empty and artifact calls below. Those are
still in Koushik's table, so an input gate is still needed.

## Why the six files in `Ground_Truth_call_type/` are not this

Those are the six sections of `Call_examples.md`, which is the material
`app/prompts/call_type/v1.txt` was written from. Scoring the prompt on them
measures nothing, because the type descriptions were authored to make exactly
those six come out right. They are useful only as a floor check — a failure
means something is broken, a pass means nothing. See `problems-and-fixes.md` 8.6
for the last time training material was mistaken for a test set. (That floor
check currently fails 1 of 6: the transcript that *defines* Demo is classified
Discovery, reproducibly across three runs.)

None of the calls below appear in `Call_examples.md`.

## How much to trust these — read before quoting a number

- **One reviewer**, not business-team ground truth. Disagreement with the
  classifier means "worth a human look", not "the classifier is wrong".
- **Labelled blind.** Transcripts and metadata were pulled without the stored
  `call_type`, so a label is not an endorsement of the model's answer.
- **Read in full**, not sampled, so a mid-call pivot cannot have been missed.
- **`ambiguous=True` means a second reviewer could reasonably differ** — excluded
  from any strict score. Recurring account-cadence calls dominate this group,
  because the six types describe stages of a linear new-business funnel and a
  weekly onboarding call is not a stage in one.

Scored by `app/services/eval/call_type_accuracy.py`.
"""

# Calls that cannot be given a call type, with the reason. Kept separate rather
# than force-labelled: inventing a type for a call with no conversation in it
# would corrupt the accuracy figure these labels exist to produce.
#
# **These are all still in Koushik's table**, so they are in scope by the rule
# above and the pipeline will keep receiving them. Each was nonetheless assigned
# a call type, a call score and a set of coaching gaps.
UNCLASSIFIABLE: dict[str, str] = {
    # 1 turn, 30 words, across a 1156s meeting.
    "35f28528-192e-4813-8c5b-56876744ef94": "no conversation — single 30-word turn",
    # The recording captures only the pre-meeting shuffle: Google Meet fails for
    # one attendee, everyone moves to Zoom, recording ends. The real meeting was
    # never captured.
    "7cf8dcfb-9b84-423d-9204-85041d8cf56f": "platform-switch artifact — 27 turns of waiting, then 'see you on Zoom'",
    # Client never joins. Two Joveo employees talk to each other for 20 minutes
    # about the deck they intend to show her. Coaching a rep on this means
    # coaching a conversation that had no customer in it.
    "4ac4eea2-dc3f-431e-96d7-7b1fb0f4622a": "client no-show — internal Joveo-only conversation",
    # A second scope shape, distinct from the empty calls: the counterparty is a
    # supplier, not a hiring employer. HelloWork is a French job board refusing
    # Joveo access over a past programmatic dispute; Joveo is seeking feed/supply
    # access on behalf of Collins. The six types all assume Joveo selling to an
    # employer, so every rubric theme misfires here.
    "0bbe93f1-adb6-425d-b725-60d8776ef10d": "partner/supply negotiation — counterparty is a job board, not a buyer",
}


# avoma_recording_id -> (expected_type, ambiguous, note)
LABELS: dict[str, tuple[str, bool, str]] = {
    "f2b812cc-0ef6-49d2-9339-94a3cb4f0ed7": (
        "Discovery", True,
        "UPS 'Matt - Scott Follow-up': ~50% baseball, then learning their agency "
        "situation, global support needs, Q2/Q3 timeline and RFP process. No "
        "product shown, which rules out either Demo type. Discovery-shaped "
        "content, but it is a short relationship check-in rather than a fit "
        "assessment, so a reviewer could reasonably say none of the six fit",
    ),
    "74a5ff96-4202-44a0-8587-14fd4beadaca": (
        "Discovery", False,
        "AdventHealth intro: learns they run Smashfly, went live with Workday in "
        "December, are running an RFP to replace Smashfly, and that the VP of TA "
        "just left. Only a brief anonymized WBR screen-share, and the client says "
        "'I don't need more demos'. Ends by handing off to the right contact",
    ),
    "5b5cea1d-8677-4688-985f-78b4d253e6e1": (
        "Discovery", False,
        "Aristocrat reconnect: learns everything is centralised through Recruitics, "
        "which job boards they use per market (JustJoin IT, Rocket Jobs, Bulldog), "
        "career-site languages, and where EMEA is underperforming. No product "
        "shown; ends with Joveo owing a market strategy document",
    ),
    "8c2407f0-ae2e-41ad-9f6f-52a0e81a996f": (
        "Technical Integration", False,
        "Paychex/HiringThing: job ingestion on staging, standardising funnel "
        "stages, static vs dynamic easy-apply questions needing partner API "
        "access, per-manco API keys, whether two tracking pixels can coexist "
        "with Appcast, staging->production migration before go-live. Technical "
        "roles both sides, no pricing, no walkthrough",
    ),
    "90f339c5-c18b-4af6-b6d9-9bceece461ca": (
        "Pricing/Negotiation", True,
        "RTX 'Marie - Scott Re: SonicJobs': client confirms they will proceed via "
        "Joveo at a stated 65,000 annual plus an agreed 10% agency fee, with "
        "budget alignment from Kathy, SOW to be copied over and the PO in "
        "progress. Also declines the newsletter product once its scope is "
        "explained. A specific number on a bounded scope — but it ends by "
        "scheduling meetings rather than with a commercial deliverable, which "
        "the prompt treats as arguing against this type",
    ),
    "f03b6bd6-8e71-437d-a971-ce49745d66a2": (
        "Discovery", True,
        "Americare weekly: Steve works the internal business case for an upsell — "
        "probes their KPIs (web drop-off, sub-10-day time to fill, first-10-day "
        "turnover), offers a Discovery Senior Living reference and case-study "
        "collateral, and coaches ROI framing. Price is explicitly NOT discussed "
        "('we've barely touched on price, that's the next conversation'), which "
        "rules out Pricing/Negotiation, and no product is shown. Discovery-shaped "
        "but on an existing customer, and the second half is campaign ops",
    ),
    # --- The four RTX weekly-onboarding calls. Three independent reviewers each
    # landed on Kick-off as least-wrong AND each flagged the same reason for
    # doubt: it is the Nth weekly cadence call, not a first project meeting. Four
    # calls needing a type the taxonomy does not have is the clearest evidence in
    # this file for the gap.
    "6860ac19-cec5-48c8-a4d7-c7ad35419419": (
        "Kick-off", True,
        "RTX weekly: post-signature onboarding checklist — 'contract's done', SOW "
        "being finalised, the first-ever Joveo PO stuck in RTX's new-vendor "
        "setup, SonicJobs assets chased with Heidi, analytics access named as "
        "next. Rest is per-workstream ownership and timelines across Collins "
        "Mexicali, Raytheon UK, Pratt New Zealand, Stepstone Germany, plus "
        "cadence management. No product shown, no quote produced",
    ),
    "c2fbc27e-51a2-4f46-a7cf-030323e336d5": (
        "Kick-off", True,
        "RTX weekly, three weeks after the above: project tracker across five "
        "workstreams — Mexicali intake conflict (Carla's 16 hires vs Roberta's), "
        "an OCC 30-day trial passed through at no charge, SonicJobs' 1,800-1,900 "
        "jobs ingested awaiting OpenAI app approval, a $76,000 agency contract "
        "uncovered at Nord Micro. One sustained technical stretch (17-23min) "
        "explaining UTM source appending on the Phenom feed to a non-technical "
        "owner. Technical Integration is the alternative read",
    ),
    "d48a9880-f15b-40a2-a219-98c256c681e9": (
        "Kick-off", True,
        "RTX weekly: shared project tracker on a live engagement — New Zealand "
        "media plan (drop LinkedIn, Trademe over Seek), Raytheon UK's GBP24k "
        "annual Broadbean comparison and a ~GBP2k/month trial, SonicJobs "
        "discovery-page escalation, apply-start pixel pending with Phenom, SOW "
        "submitted and PO sent. Ownership is explicitly renegotiated: Marie "
        "insists a PM be named and Ruhi takes over from Scott as day-to-day owner",
    ),
    "52280de1-8295-4e75-acfd-a43e61871b72": (
        "Kick-off", True,
        "RTX weekly: first 16 minutes are Joveo staff waiting for Marie, then "
        "~36min of multi-workstream coordination. The longest single segment "
        "(28:00-43:00) is commercial — packaging a 3-month trial against "
        "Broadbean's GBP24,000 annual spend and deciding what benchmark 'wins the "
        "business' — so Pricing/Negotiation is the reasonable alternative. "
        "Labelled Kick-off because the only screen-share is an analysis sheet and "
        "the purpose is running a signed engagement",
    ),
    "faa0f85e-4a55-48fe-872c-36eb02a16125": (
        "Discovery", True,
        "TELUS 'Internal, Referral, and Pricing': Alejandra walks Joveo through "
        "her stack in detail — Jobvite as combined CRM/ATS with two years left, "
        "Workday HCM, OneLogin, previously Avature, the pain being failed "
        "integrations — and states there is 'not a specific issue we're trying to "
        "solve right now'. Doug spends ~20min qualifying fit by explaining why "
        "Joveo has no off-the-shelf referral product. Nothing screen-shared. The "
        "last third turns quote-oriented (54,000 hires, credits pricing model) "
        "but no TELUS-specific number is produced, only a hypothetical $100k",
    ),
    "e8bfc3fb-985f-43d0-a09f-5d6e92e88b3f": (
        "Follow-up Demo", True,
        "NHS 'Talent CRM + Unified Analytics': Doug screen-shares a live Talent "
        "CRM ~20 of 36 minutes — filtering 1.7M candidates to 73k, hiring plans "
        "with AI-interviewer steps, editing an RN posting to Columbus at "
        "$43.50/hr, bulk SMS over Twilio — with Mike's questions anchored to what "
        "is on screen. Relationship is pre-existing ('when we first met, 18 "
        "months ago in Palm Springs') and it ends by advancing that motion. "
        "Ambiguous because Talent CRM itself is new to this buyer",
    ),
    "efe793fa-cfa5-4223-ba16-a0793a87b241": (
        "Demo", True,
        "Meridian/Workada first conversation: Andres self-narrates his business "
        "~10min, then Deb explicitly declines discovery ('rather than spending "
        "the next 30 minutes me drilling you with questions') and walks a deck "
        "09:50-26:00 — volume/precision, regional sourcing, a Scale AI impact "
        "slide (17% cost-per-activation improvement, ~40% activation lift), "
        "per-role channel attack. Ends agreeing a ~$15k/month pilot. Ambiguous "
        "because it is a credentials pitch with no platform screens at all",
    ),
    "641ed63c-cf6d-4a88-9909-27619ac725e6": (
        "Discovery", True,
        "AdventHealth intro to Keisha (brokered by Cathy, see the other "
        "AdventHealth call): first ~16min is rep-led discovery — 27,000 external "
        "hires/year, a decade-long incumbent media partner under re-evaluation, "
        "Brazen being replaced, Workday ATS, and her team catching runaway "
        "campaigns before the vendor does. Scott then explicitly defers the demo "
        "('I don't wanna do a demo, we can set up another call') and shares 5-6 "
        "overview slides. Ends booking 45 minutes the following week. Ambiguous "
        "because that share still runs ~10 of 34 minutes",
    ),
    "6050cf3b-b1fc-4496-9781-06ea603898ed": (
        "Demo", False,
        "Givaudan RFP response, first meeting ('very nice to meet both of you'), "
        "and procurement scopes it explicitly: 'today we are not going to touch "
        "the commercials'. ~35min deck (agentic platform, 5 RFP-mapped "
        "capabilities, ISO 42001/EcoVadis, implementation timeline) then a live "
        "demo of unified analytics, campaign creation, career-site builder "
        "generating a Givaudan landing page, and the chatbot apply flow. "
        "Integration Q&A is reactive and deferred to a future technical call",
    ),
    "74877391-561c-4a53-94c7-edd0ea8f1ac7": (
        "Follow-up Demo", True,
        "Panda Express: built explicitly on prior calls — Scott will 'step back "
        "where we were', Joey Lee confirms 'you walked me through this dashboard, "
        "Yazad'. Incremental walkthrough: Doug demos the AI interviewer on an "
        "instance 'set up specifically for Panda' from their own manager guide, "
        "then a programmatic/analytics recap with competitive displacement "
        "against Appcast. Ambiguous because most of the attending committee is "
        "seeing the platform for the first time",
    ),
    "cb0e260a-47c5-449b-a120-91ac73d70f15": (
        "Follow-up Demo", False,
        "TELUS deep dive: Doug states outright 'this is our second time through' "
        "and 'we did the whole AI interviewer last time'. Prateek runs a deep "
        "dive on a TELUS-specific instance built from their own career-site "
        "assets and a real req, covering career site, multilingual chatbot, live "
        "AI video interview, scoring rubric, auto-scheduling, then backend config "
        "and unified analytics. Closes booking a further call with pricing to follow",
    ),
    "d91b4ac0-af69-4b81-b8d0-06b34374b502": (
        "Discovery", False,
        "Jackson Therapy regroup: ~7min rapport then Brendan pivots with 'back to "
        "job advertising now' into sustained environment learning — Indeed's "
        "4-pack rotation dropping 1,000 live jobs to 400, Appcast as incumbent and "
        "its failure to trace 3 days of unqualified leads, her 15-20% bad-lead "
        "tolerance, Salesforce/Target Recruit with no down-funnel integration. "
        "Nothing shown (chatbot deferred to a video clip). The $20-25k/month "
        "figure is budget-scoping for a future plan, not a quote",
    ),
    "3fd1f2a6-97b1-4d2b-8c2f-a89d3ee43e1e": (
        "Kick-off", True,
        "RTX weekly: Nargis screen-shares the project plan, not the product, "
        "walking open items per workstream (HelloWork call, Mexicali and UK media "
        "plans, Pratt Canada/US awaiting Conrad and Taylor, New Zealand pending "
        "SEEK's Adsync beta). Marie spends ~15min disclosing that RTX corporate "
        "comms will implement SonicJobs, Reddit, GitHub and YouTube in house, "
        "which she calls unethical since Joveo introduced SonicJobs. Post-signature "
        "governance, but explicitly not a first project meeting",
    ),
    "c19bb775-e42b-4ac0-bcf6-f55e83bde8a3": (
        "Kick-off", True,
        "Tri-party RTX <> SonicJobs <> Joveo, and the participants themselves call "
        "it 'the kickoff call' for the Raytheon ChatGPT app, already built and "
        "awaiting OpenAI approval. Heidi does screen-share ~10min in developer "
        "mode (3,125 jobs, a natural-language search narrowing to 290), so both "
        "Demo types are arguable — but the walkthrough serves launch verification "
        "(Marie catches the feed is all-RTX rather than ~1,800 Raytheon US jobs) "
        "and the rest is how the engagement will run, with per-person actions",
    ),
    "f3848947-fd3c-4260-b953-bd1cc28a3cf3": (
        "Discovery", True,
        "Sopra Steria 'playbooks': Yazad meets two of their resourcing sales "
        "consultants for the first time, gives a ~15min VERBAL platform refresh "
        "with no screen-share and an explicit deferral ('we can do a whole demo of "
        "this'), then open-ended fit assessment — headcount (3 going to 5), public "
        "vs regulated sector, which accounts to start with (DWP, Home Office, "
        "Defra, MOJ), incumbents (Oleeo, Aperture, Penna), white vs grey label. "
        "Ambiguous mainly because the counterparty is an RPO channel partner "
        "rather than a hiring employer",
    ),
    "1e67e05f-9a4a-4ea0-852c-29708b766c3c": (
        "Follow-up Demo", True,
        "Discovery Senior Living, ~8 stakeholders across 8 managing companies. "
        "Steve frames it as 'a continuation of last Friday's call... not so much "
        "focused on programmatic', and Ashlie live-shares the programmatic backend "
        "(job groups, publisher networks, apply caps, automations) then unified "
        "analytics then per-manco $40k budget slides. Kick-off is ruled out "
        "because the contract is explicitly unsigned ('the MSA and SOW') and a "
        "separate kickoff is still to be scheduled",
    ),
    "87ad656f-9da6-4364-8419-dbc6f58a189f": (
        "Follow-up Demo", False,
        "IES Communications: Lloyd opens 'this is a continuation of our last "
        "conversation', and Deborah re-shows unified analytics (funnel view, "
        "form drop-off, time-to-apply, TalentNeuron market insights, chatbot "
        "analytics) because newly-hired Shelby missed it, then Cindy walks the "
        "Rocketlane onboarding slide. Commercial terms surface (20k/month minimum, "
        "90-day term with 30-day out) but no quote is produced — the deliverable "
        "is a sample media plan for 5 named roles",
    ),
    "79dac970-5a11-4131-bb03-a4e5d81ecbb4": (
        "Follow-up Demo", True,
        "Syneos reconnect: ~13min beach small talk and intros, then Scott "
        "slide-walks the platform pillars and live-shares Uber's chatbot at "
        "jobs.uber, the Banfield career site, the AI career-site builder "
        "(converting a page to Spanish on the fly) and analytics. Prior "
        "relationship explicit — an intro call in January, and Meaghan references "
        "'you've already shared an intro with the team previously'. Ambiguous "
        "because Meaghan, 3 months in, is a genuine first exposure",
    ),
    "ec138c8a-12bd-4df8-8d5b-06f857c69fc5": (
        "Follow-up Demo", True,
        "UPS follow-up: Jill screen-shares ~26min of real sample deliverables — a "
        "weekly business review with competitor job-posting activity, one-off "
        "Brazil and Mexico driver-wage reports, the publisher quality score — and "
        "grants live Drive access, then Naren decks the global story (40 "
        "countries, single-currency reporting, campus ambassadors). Genuinely "
        "ambiguous with Discovery: the platform itself is explicitly deferred, and "
        "a ~20min middle block is Naren and KJ probing what UPS's incumbent "
        "full-service agency does",
    ),
    "6be6157e-a139-41c0-8ee2-8dfd2b0932a0": (
        "Demo", True,
        "Discovery Senior Living 'FW: Weekly TA Meeting': ~14 client TA "
        "stakeholders plus five Joveo people, Delia opening with 'as they go "
        "through the demo, please ask questions'. Dominant activity is a "
        "full-platform screen-share 08:25-38:41 (AI career site, multilingual "
        "chatbot, end-to-end apply, live AI interview with scoring, "
        "hiring-manager self-scheduling), then a programmatic deck to 52:44. "
        "Ambiguous vs Follow-up Demo: prior calls are referenced and the "
        "Paychex/HiringThing integration is already in flight with a sandbox",
    ),
    "8742c6f5-0a5c-46af-8f72-36c715e4fb5d": (
        "Follow-up Demo", False,
        "Spring Health: opens as a status check-in on live spend (May's $1,920 / "
        "80 applications / one hire, sponsoring a Ciudad Juarez role, "
        "Greenhouse-vs-Salesforce tracking via UTM), then Doug demos ONE "
        "additional capability — the AI interviewer, 19:24-57:31. Client already "
        "runs Joveo programmatic, so this is incremental capability on an "
        "existing relationship, and it ends by advancing that motion (product "
        "follow-up booked, cost range to be worked up)",
    ),
    "1be6ad19-11e0-4caf-8d98-09f662b52a88": (
        "Demo", True,
        "UPS 'Universal Analytics and More': Matt Lavery and Emma Gregory against "
        "five Joveo people. Ashlie screen-shares Unified Analytics ~25 minutes "
        "(CPC/CPA/CPH trending, hiring funnel, job quality score, market "
        "insights, a custom competitive report), then Jill presents the publisher "
        "quality score formula. Matt closes that UPS is in a holding pattern — "
        "budget locked with Radancy as agency of record, mid-migration to Phenom. "
        "Ambiguous vs Follow-up Demo: prior UPS calls exist and Roadie, a UPS "
        "subsidiary already live on Joveo, is the running proof point",
    ),
}


VALID_TYPES = frozenset(
    {
        "Discovery",
        "Demo",
        "Follow-up Demo",
        "Pricing/Negotiation",
        "Technical Integration",
        "Kick-off",
    }
)


def lookup(recording_id: str) -> tuple[str, bool, str] | None:
    """The label for one call, or None if it was never hand-reviewed."""
    return LABELS.get(recording_id)


def strict_labels() -> dict[str, str]:
    """Unambiguous labels only — what a strict accuracy score should use."""
    return {rid: expected for rid, (expected, ambiguous, _) in LABELS.items() if not ambiguous}
