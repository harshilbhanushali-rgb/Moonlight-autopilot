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
    # --- graded by independent blind graders, same standard and anchors ---
    Label(
        31,
        "Medium",
        "Solid weekly across five workstreams (Pratt Canada, Raytheon UK, Collins "
        "Mexicali, New Zealand, Stepstone), with a consolidated notes doc as single "
        "source of truth, a proactive flag on the Collins entity-name/'confidential' "
        "posting issue, and an unprompted Stepstone SOW update needing no client "
        "escalation. The deciding moment is the New Zealand budget: the client asked "
        "whether most of the ~NZ$80,000 was really Indeed Australia, then had to "
        "explain to the rep why the proposal must break that out - 'if I were the New "
        "Zealand TA decision maker... do we really need $80,000?' - and the rep's "
        "answer was 'I think I said New Zealand and Australia, but maybe we'll be more "
        "explicit.' Coaching point: the client supplied the rationale that should "
        "defend Joveo's own recommendation. Second, smaller point: media plans for two "
        "client-facing calls the next day were still unuploaded ('just some final "
        "edits'), and the client said she had no time to review before them. "
        "CONTAMINATION NOTE: the grading standard discloses prior model output for 'the "
        "RTX weekly onboarding call classified Pricing/Negotiation'. THREE calls match "
        "that description (31, 43, 54) - the disclosure was not specific enough, which "
        "a grader caught. Resolved by re-running the selection the display script made: "
        "it takes the first such call in id order, so the seen call is THIS one. Graded "
        "here purely on the transcript, and excluded from the headline figure anyway.",
        contaminated=True,
    ),
    Label(
        32,
        "Medium",
        "Comprehensive analytics walkthrough - saved views, scheduled reports with "
        "auto-forward, funnel data, invalid-click taxonomy, JD quality scoring and "
        "optimiser, publisher comparison - and honest where the product falls short "
        "('unfortunately, no' on whether the optimiser flags what it changed). She also "
        "recovered well when the client compared Joveo unfavourably to Recruitics on "
        "comparative date ranges, finding the period-over-period feature after first "
        "saying 'that view is not possible currently' ('this helps a lot, thank you for "
        "pointing this out'). Coaching point: on a demo of an analytics product she "
        "left two data anomalies unexplained and unowned - clicks from Arizona, Alabama "
        "and Virginia answered with 'maybe someone from Sonic Jobs' until the client "
        "dropped it, and 'senior systems engineer, 4 clicks and 5 apply starts. This "
        "was slightly odd' with no follow-up. Second point: the client had to correct "
        "the rep's strategy on resume databases outright ('in the future it probably "
        "may be best not to proactively include databases as a recommended solution... "
        "that's outside talent attraction, it's not our specialty') after time had "
        "already gone into OCC pricing.",
    ),
    Label(
        35,
        "Medium",
        "Genuinely good technical handling: when the client said 'I'm not super "
        "technical, I just wanna ask clarifying questions to make sure it is doable "
        "before I ask the Workday team,' the rep stepped back and walked the whole UTM "
        "chain (Phenom feed ingest, source parameter appended on Joveo's side, OCC "
        "redirect, Workday attribution, one parameter not two), then refused to over- "
        "promise - 'I'll go back and double check whether the feed goes to Phenom apply "
        "start or directly to Workday' - which stopped the client raising a pointless "
        "Workday ticket. Also protected scope proactively on creative (comms owns it), "
        "gave a client-favourable answer on the OCC free trial (pass-through feed, no "
        "charge), and pushed to review Sonic Jobs content before go-live given 'broader "
        "visibility and high impact.' Coaching point: the client's one explicit risk "
        "question - 'I wanna make sure it doesn't impact the approval we're waiting for "
        "from OpenAI' - got a one-word 'Yep' with no verification, on the highest- "
        "visibility item on the account. Also left the Carla/Roberta intake discrepancy "
        "for the client to chase by reply-all rather than owning the reconciliation.",
    ),
    Label(
        41,
        "Medium",
        "Well-prepared team demo: preread circulated the day before, agenda built from "
        "the client's own pre-sent questions, roles announced upfront, and a "
        "competitive-insights report pulled specifically for UPS rather than the "
        "generic demo. Matt's hard question - 1,200 locations, how do I separate "
        "Palatine from Addison from Jefferson Street - got a concrete answer (filter to "
        "individual sites/MSAs, any ingested job field is filterable, organic volume "
        "factored in so over-served buildings aren't sponsored), not methodology, and "
        "KJ volunteered the limits of the competitor spend data unprompted ('this is "
        "not 100% accurate, plus or minus 10 range, it's directional'). Jill then "
        "rescued a hard freeze into a committed next step, which prompted Matt to open "
        "up international unaided. Coaching point: Scott said out loud on a strategic "
        "account 'I know we didn't really have this thing planned for any sort of next "
        "steps,' and although he named the RFP ('Matt, I know you're looking at some "
        "RFP processes'), nobody asked its timing, criteria or decision-makers, and "
        "nobody asked when the Radency agency-of-record agreement - the actual gate "
        "Matt described - expires.",
    ),
    Label(
        43,
        "Low",
        "The client discloses, visibly upset, that RTX comms is taking the entire "
        "channel proposal Joveo built (Sonic Jobs, Stack Overflow, Reddit, GitHub) in- "
        "house and executing direct with vendors Joveo introduced. Three reps responded "
        "with sympathy and acceptance - 'it is kind of the cost of doing business', 'it "
        "happens... they'll have to deal with the consequences', 'you don't have to "
        "stress about it because it's not in your control' - and made no recovery "
        "attempt: nobody asked the size of the budget leaving, nobody asked to get in "
        "front of the comms leader or to see the policy being cited, and the one "
        "commercial idea (Yelena's 'we could have pushed the budget and cut the fee') "
        "was stated in the past conditional, never offered as a live proposal. Two "
        "further misses: Scott had let the Raytheon UK media plan stall after follow-up "
        "questions and only chased it when a colleague prompted him ('I haven't seen "
        "anything since I got those follow-up questions'), and on an account whose "
        "flagship project is LLM job discovery he did not know about Joveo's own OpenAI "
        "ads announcement ('I feel like I need to get caught up on it all'). Borderline "
        "because the emotional handling was genuinely warm and non-defensive and the "
        "champion was preserved - she ended with 'I feel better after talking to you "
        "guys' - and the decision was made above her by a leader she had already "
        "challenged and lost to.",
        borderline=True,
    ),
    Label(
        44,
        "Medium",
        "Two good judgement calls: she raised the missing Greenhouse apply-tracking "
        "pixel unprompted, then on hearing the client migrates to Ashby by end of "
        "September redirected it - 'it might make sense that we just place the pixel on "
        "Ashby if we're moving' - saving throwaway work, and set out a clean division "
        "of labour for the deeper integration (heavy dev on Joveo, client just supplies "
        "API keys). Closed with dated, owned next steps. Three coaching points, which "
        "is why this sits close to Low. Two different clients asked for help sourcing "
        "Spanish-speaking and Hispanic candidates ('maybe if there's a national "
        "organization for Spanish speaking providers... I wanted to explore what's out "
        "there'); the rep said 'we're here to help you out with that,' pivoted to CPQA "
        "budget mechanics, and it never appeared in the closing next steps. Nicole had "
        "to ask about Greenhouse attribution four times, ending in 'how are both things "
        "happening at the same time that we would be able to pay per applicant, but we "
        "can't track if we're getting applicants?', and had to state the answer herself "
        "('so the reason you can't confirm is because we just haven't received any "
        "applications'). And Megan's live retention risk - 'Emily gets results when she "
        "posts on Indeed for $400, but we don't seem to get results posting the same "
        "jobs through Joveo... I wanna keep the money directed where we're getting the "
        "results' - got a tracking-infrastructure answer rather than a commitment to "
        "diagnose the performance gap, with the client organising the joint working "
        "session herself.",
        borderline=True,
    ),
    Label(
        47,
        UNGRADABLE,
        "Joveo is the buyer/supplicant here, not the seller. Laetitia is HelloWork, a "
        "French job board that sells annual unlimited-posting packages plus CV-database "
        "access, and the entire call is three Joveo staff asking her to re-open access "
        "after HelloWork blacklisted them over a broken programmatic agreement ('the "
        "trust has been broken at some point'). Saket ends up asking HER what her "
        "package durations are and offering 'we will do whatever it takes, whatever we "
        "need to put in writing' to be let back in. Same supplier-negotiation shape as "
        "the Talroo calls (ids 52, 58) — grading Joveo's 'selling' here would coach "
        "reps for buying media.",
    ),
    Label(
        48,
        "Medium",
        "Warm inbound (Andres sought Joveo out from his Scale AI experience) and Deb "
        "converted it: probed the competitive set ('are you looking at other "
        "programmatic solutions?'), gave a concrete Scale case with numbers, was "
        "straight about pilot policy, and left with a $15k/month pilot, a call with the "
        "chief innovation officer and a request for his current CPAs. Coaching point: "
        "she opened by explicitly opting out of discovery — 'rather than spending the "
        "next 30 minutes me drilling you with questions, let's keep this really "
        "conversational' — and then talked for roughly twenty minutes straight. Andres "
        "had already stated his ramp target (1.2k to 5,000 daily contributors in two "
        "months); she described the correct sizing method on a slide (activations x "
        "apps-per-activation x CPA) and then never ran it, accepting the $500/day "
        "figure he floated and calling it 'a perfect pilot amount' with no math behind "
        "it.",
    ),
    Label(
        49,
        "Medium",
        "Nargis carried Joveo's side: she asked for the feed to inspect, asked the "
        "right operational questions (dev-environment access, change turnaround), and "
        "unprompted caught that the feed Sonic Jobs held lacked Joveo tracking "
        "parameters so Marie would see nothing in the dashboard — then committed to it "
        "same-day when Marie asked her to prioritise. Two coaching points, both on the "
        "same partner-led call. Marie, not Joveo, caught that the demo was running RTX- "
        "wide jobs including Collins Aerospace (3,125) rather than the ~1,800 Raytheon "
        "US feed, and Scott and Nargis then visibly disagreed on air about which feed "
        "had been sent ('didn't we create another feed?'). And when Marie asked "
        "directly how OpenAI would prioritise competing feeds, naming Joveo's own "
        "OpenAI partnership, Scott's answer was 'I need to dig into it myself' — the "
        "partner's CEO then answered the strategy question about Joveo's account.",
    ),
    Label(
        54,
        "Low",
        "Marie spent two minutes explaining that her comms partner expects the agency "
        "to lead — 'they brought us the ideas, and they're going to guide us and tell "
        "us exactly what do we need to do' — and asked pointedly whether Joveo still "
        "stands behind the Sonic Jobs recommendation in its own proposal. Four minutes "
        "later Scott said 'I didn't look what we had proposed. I knew they had two "
        "offerings... I couldn't remember which one we were offering.' He had also "
        "stalled the Mexicali media plan on a question Marie answered from her own "
        "careers site in thirty seconds, saying 'when I asked the question, I was "
        "literally typing where is Mexico... I didn't realize it was a city.' Marie "
        "then wrote Joveo's strategy for them — package a 3-month trial, differentiate "
        "against Broadbean, don't dilute the £24k by recommending boards outside "
        "Broadbean's scope, anonymise a sample WBR to show. Credit for Scott's one real "
        "question, 'what is the benchmark that wins the business — applicants or "
        "CPAs?', and for Nargis's consultative framing, but a rep who cannot recall his "
        "own proposal on a live evaluation needs a conversation.",
    ),
    Label(
        55,
        "Medium",
        "Strong enterprise call. Scott set an agenda tied to last meeting's follow-ups "
        "and made room for Matt's own question up front; Jill's partnership walkthrough "
        "used specific proof rather than claims (the US Army posting a warehouse job "
        "with a $45k enlistment bonus, the Brazil flood re-targeting, the Seattle "
        "8-week forecast automation that cut media spend 15% and went into the client's "
        "annual review); Naren and KJ ran genuine discovery, and KJ's localisation "
        "probe (Egyptian vs Qatari Arabic, Taiwan vs Hong Kong) got Emma to volunteer "
        "that regional activation on the career site is 'a huge gap, full "
        "transparency'. KJ was also credibly honest about scope: 'we don't do hiring "
        "events, I'll be very honest about it, but we partner with the best who does.' "
        "Coaching point is the close: the global deck started with six minutes left, "
        "was cut off mid-section, and Scott ended with 'I guess I'll send you a follow "
        "up... I gotta run' — no next meeting, and no owner for the in-house-versus- "
        "partner capability matrix KJ had just offered, which is the direct answer to "
        "the full-service question Matt opened the call with.",
    ),
    Label(
        57,
        "Medium",
        "Good structure and real preparation — short intro, pre-call research ('I saw "
        "you're using Symphony on your career site'), then he handed her the floor and "
        "asked three sharpening questions (how do you catch overspend, what engages "
        "candidates on site, can you spin up market-specific messaging). Showing the "
        "anonymised health-care WBR was the right asset at the right moment: Keisha "
        "interrupted to ask him to go back to the competitor-insights view, said 'our "
        "markets eat that up', and asked for a 45-minute session with Robert Shaw, her "
        "senior leader of innovation. But she said plainly that a decade-long incumbent "
        "is under complete evaluation and 'I'm probably looking to make a shift, to be "
        "quite frank, come January', and he asked not one question about it — no "
        "timeline, no process, no other vendors, no budget, no what-would-win-it — then "
        "volunteered that he is on vacation the following week. Her closing question "
        "about how Joveo supports Tenet and HCA end to end was deferred with 'if you "
        "think of anything, share it', and the last six minutes ran past the meeting "
        "she said she was already late for.",
        borderline=True,
    ),
    Label(
        59,
        "Medium",
        "Well prepared and proactive: shared workspace created before the call, Pratt "
        "US media plan in hand, an engineering-built source-parameter guide with "
        "screenshots so 'we don't leave you all to wonder what needs to happen', a "
        "same-day owner for the HelloWork supply escalation, and a committed time for "
        "the Raytheon US budget. She also raised the analytics-instance structure "
        "before setup rather than after ('instead of backtracking later, if we can "
        "start thinking about now'), which is genuinely good account planning. Coaching "
        "point: she walked the client through a media plan she could not defend - 'is "
        "data partners a tie-in to programmatic display?', 'is programmatic (Joveo) "
        "referring to programmatic display?', 'are we not doing any display ads?' all "
        "got 'I'll get that clarified' - on a deck about to go to Taylor, the economic "
        "buyer, and the client also had to correct the $200k budget to be inclusive of "
        "the 10% fee.",
    ),
    Label(
        257,
        "Medium",
        "Good discovery once it started: he opened the Indeed problem wide and let her "
        "lay out the rotation behaviour, the Appcast spam trend and her 15-20% bad-lead "
        "threshold, then probed easy-apply, down-funnel data, the feed and 'do you have "
        "an idea on the budget' (answer: $20-25k/month on rehab). He answered her "
        "decisive concern directly - 'you'll have real time visibility into every click "
        "on every single individual job board' - and Ashlie tied talent rediscovery and "
        "multi-apply to the client's own stated pain that ~80% of applicants didn't "
        "want travel, while refusing to solution live. Two coaching points: 7.5 of 39 "
        "minutes are World Cup and July 4th small talk before 'back to job advertising "
        "now', and the call ended with no meeting booked and no date on the media plan, "
        "on a client openly weighing whether to move her whole account off the "
        "incumbent.",
    ),
    Label(
        258,
        "Low",
        "The media side was handled well - he raised publisher pushback on the $1.50 "
        "bid himself, was honest that CPA had drifted to ~$40 ('much higher than I "
        "would like it'), fixed the missing header/priority columns in her file without "
        "being asked, and flagged that easy roles like line cook were soaking up apps "
        "ahead of the hard recs. But the deal is at the pricing stage and the champion "
        "asked twice for ammunition - 'I probably will need a little bit more "
        "information to help bake into what I'm presenting' and 'I'm sure you guys have "
        "materials and one pagers and everything already done' - ahead of a named date "
        "(her boss, Tuesday), and he mentioned an application-ease insights page then "
        "drifted into a story about KJ founding Mobolt without ever committing to send "
        "anything. She also said 'I'm not gonna make it here much longer if we don't "
        "fix this' and he moved on. Coaching: he owned no next step; every close was "
        "'if there's anything you need from me, just let me know'. He also admitted the "
        "bid pushback had waited two weeks ('I was gonna talk with you about it about "
        "two weeks ago'). Borderline because the account-management execution alone "
        "would read Medium.",
        borderline=True,
    ),
    Label(
        260,
        "Medium",
        "Strong demo craft from Doug and Yazad: a live end-to-end candidate interview "
        "then the backend, and a genuinely good handle on the hardest question of the "
        "call - Joey's bias worry about scoring 'professionalism' - which Yazad "
        "reframed rather than promised ('rather than say professional or "
        "unprofessional... if someone talks way too fast or uses curse words, we can "
        "tag those things'), pre-caveating that the AI would judge answers not "
        "behaviours. The Appcast differentiation was specific and evidence-backed "
        "(three named migrations, an 18%-to-24% conversion figure, the Polish-job-board "
        "restriction), and 'don't give us only the hardest to do stuff' was a well- "
        "judged pilot ask. Coaching points, all Scott's: he told six client "
        "stakeholders 'we haven't put our dance steps together', he didn't know how "
        "long his own call was and started wrapping 30 minutes early, and nobody could "
        "answer Leslie's Paradox-integration question - the client's stated biggest "
        "gap, raised three times - with 'I will have to check. I don't know.' The call "
        "also closed with no scheduled next step while the sponsor left for a two-week "
        "vacation.",
    ),
    Label(
        261,
        "Low",
        "Marie asked four times, escalating each time, whether Joveo can actually "
        "deliver local radio and billboard recommendations for New Zealand, and never "
        "got an answer. Nargis first answered a different question entirely - a "
        "digression about click tracking and impressions - and Marie had to correct "
        "her: 'I don't think that it has anything to do with digital tracking or "
        "digital clicks.' Marie then told them plainly that a local agency is bidding "
        "against them, the local TA lead believes that agency will execute better, and "
        "Taylor is holding the line for Joveo pending this answer; the reply was still "
        "'we'll probably go back and get an honest and real assessment', on an ask "
        "Scott conceded 'was part of our original' brief. Separately, a SonicJobs "
        "discovery page went out with branded copy RTX never approved on a project "
        "Marie says her own reputation rides on, and she had to ask who at Joveo owns "
        "it - Scott's answer blamed the vendor for emailing her direct, and she "
        "corrected him that it went to both of them two hours earlier, unread. Scott "
        "also had no Stepstone update on a project 'looking to move pretty quickly' and "
        "told the client 'I've got so lazy now, it's hard for me to find my notes "
        "anymore'. Credit: Nargis ran the tracker, committed dates, and took SonicJobs "
        "PM ownership on the spot.",
    ),
    Label(
        262,
        "Medium",
        "Strong RFP-aligned demo: Yaz mapped every stated requirement (single-currency "
        "Swiss franc reporting, ISO 42001 for their bias-audit question, EcoVadis for "
        "their sustainability commitment), untangled the SuccessFactors/Phenom two-way "
        "integration live rather than hand-waving it, and closed with a career site his "
        "CTO's team had actually pre-built for Givaudan. Coaching point is pacing: 44 "
        "of 72 minutes went to company history and slides, Jesse opened the demo saying "
        "'I know we have about 15 minutes left', and the client had to redirect them at "
        "52:03 with 'just because of the time, can we see how to launch the campaign?' "
        "Second point: the technical call with Phenom and the Phenom client references "
        "were both offered and neither was scheduled or delivered on the call, and the "
        "next step was left entirely to the client's own July timeline.",
    ),
    Label(
        263,
        "Medium",
        "Genuinely well-qualified: Doug refused to over-promise on referrals ('this is "
        "not something we have off the shelf') and walked the client through the "
        "tax/HCM/payroll complexity rather than agreeing to build it, Prateek asked the "
        "disqualifying question outright ('what's the problem statement? what problem "
        "are you trying to solve?') and got the honest answer that there isn't one yet, "
        "and both budget ('is this already budgeted for?' - no) and timeline (Q4) were "
        "pinned down. Doug's insistence on validating assumptions before pricing "
        "surfaced the constraint that decides the deal size: AI is not permitted in "
        "their EU/US/Canada entities, cutting billable volume from 1.5M applications to "
        "700K. Coaching point: this was booked as a pricing call and the volumes "
        "arrived only because the client happened to read Scott's email mid-meeting "
        "('Scott, I just saw your email'), so no price could be quoted and a fourth "
        "call became necessary; Prateek then proposed a specific 15-minute Wednesday "
        "slot and let it dissolve into 'send me the pricing and then we can set up a "
        "call' without a date.",
    ),
    Label(
        265,
        "Low",
        "The client stated her one operational ask three times - cap applications at 8 "
        "per job, with a concrete rationale (recruiters are hiring an intern just to "
        "work the backlog, and candidates sitting past 15 are lost to competitors) - "
        "Nargis pushed back with 15, the client said 'I would rather stick to 8, Ruby', "
        "and the call then moved to pitching the AI interviewer without anyone ever "
        "confirming the cap, the number, or who sets it; the only committed action at "
        "the end is the client redoing her own spreadsheet. She separately flagged live "
        "budget waste in the same breath - 'the systems aren't talking to each other, "
        "so if we close the job and fill it, they're still spending sponsorship dollars "
        "on ones we don't need anymore' - and it was never picked up at all. Third "
        "issue a manager would want to raise: Nargis volunteered to this client that "
        "Discovery Senior Living, whom the client had just named as a competitor she "
        "benchmarked against, is in talks with Joveo and has 'quite a bit of "
        "dissatisfaction' with its current vendor. Credit to David for the honest "
        "technical answer on publisher pause lag and for probing applicant-to-hire "
        "ratio.",
        borderline=True,
    ),
    Label(
        266,
        "Medium",
        "Well-prepared and unusually honest for a demo: Prateek built the environment "
        "on Telus's own career-site assets and seeded a real Telus job, showed a "
        "deliberately vague answer scoring 1/5 to prove the rubric works, and when the "
        "client pressed on evaluating English and French proficiency inside one "
        "interview he probed the use case twice and then said plainly 'that is "
        "something we don't have, and we'll have to build it' while offering three "
        "chained interviews as a workable alternative - same honesty on the referral "
        "product and on cross-candidate ranking still being in QA. Two coaching points, "
        "both concrete. The demo environment was not checked beforehand: Spanish had to "
        "be enabled mid-demo and the page refreshed, he could not locate the HTML "
        "template setting ('I don't know where that feature is'), and he could not find "
        "the unified analytics instance at all so Scott had to rescue it from his own "
        "screen. And pacing put 45 minutes into the interviewer flow, leaving the "
        "client's most business-critical question - 'we had 40 candidates through "
        "Indeed, 10 hired, this is my ROI, how much of that can I get here?' - to a "
        "six-minute sprint Scott himself described as 'I literally flew through that', "
        "after which she asked for a cheat sheet she could read on her own.",
    ),
    Label(
        267,
        "Low",
        "The client delivered a won deal she had fought for internally ($65K plus 10% "
        "agency fee, budget aligned with Kathy) and then told him in the same breath "
        "that Raytheon will 'likely just directly renew with Sonic Jobs' in subsequent "
        "years because Joveo is only passing a feed through - the rep said nothing, "
        "asked nothing, and made no case for year two. Her one technical question, "
        "whether Joveo tracking can ride the Sonic Jobs feed, got 'I'll have to find "
        "out. I don't know. I didn't know there was a difference there' with no owner "
        "date attached. He was also selling a newsletter product he could not describe "
        "('I asked about that myself... security clearance jobs or whatever it may be, "
        "I'm just pulling that out'), and once he half-explained it the client "
        "disqualified it on the spot over an existing ClearanceJobs partnership. She "
        "set every next step herself and recapped her own homework; he committed to "
        "nothing but availability, including for Friday's call with comms that she "
        "explicitly framed as Joveo's audition ('I do want comms to really see Joveo is "
        "a capable partner').",
    ),
    Label(
        268,
        "Medium",
        "Exceptionally well prepared: the campaign manager had analysed all 1,725 live "
        "Indeed jobs and the rep presented a per-managing-company budget split, a role "
        "mix with percentages (32/38/25/5), named gaps in the incumbent's setup (no "
        "apply caps, no expired-job sweep) and a 30/60/90 plan, and Nargis had the "
        "Paychex/Hiring Thing pixel, XML feed and API stages already validated. Handled "
        "a multi-stakeholder room well - Jorge's cross-over budget fear got a direct "
        "'campaigns and analytics completely separated', Camille's job-family concern "
        "got 'we're not limited to job title'. Coaching point: the client asked twice "
        "for a forecast to judge whether $40k/month buys the pipeline she needs, and "
        "the rep had to answer 'what I don't have is the performance predictions' - a "
        "budget-recommendation deck was presented with no projected outcomes attached "
        "to it. Secondary: several minutes lost to screen-share fumbling ('toggling "
        "between the browsers is breaking me here', 'I have lost your presentation').",
    ),
    Label(
        270,
        "Low",
        "Roughly the first twelve minutes of a forty-eight minute call are Florida "
        "beaches, weather and mutual acquaintances, with no agenda set - the rep opened "
        "business with 'I got a lot we can cover... but I guess how's everything on "
        "your house?' and later did not know the meeting length ('we scheduled 45 "
        "minutes or an hour?'). The new decision-maker stated the two disqualifiers "
        "plainly and unprompted - 'little to no spend', '$0', and 'I don't have as much "
        "control over the narrative of where we move in those directions' - and neither "
        "budget ownership, authority nor timing was probed once. She then asked four "
        "separate times for something tangible to carry internally (a visualisation of "
        "the social integration, resource guides, the anonymised ChatGPT-visibility "
        "audit output, 'a short paragraph explaining that unique scenario ... so that I "
        "can effectively communicate it') and left with 'we'll send some follow up' - "
        "no itemised deliverable, no owner, no date, and no next meeting, only 'if "
        "there's a time to reconnect'. Credit: Deborah's conversion-before-budget story "
        "from Lowe's, her honest Phenom/Paradox comparison and the LLM third-party- "
        "validation advice were genuinely valuable, which is why this sits against "
        "Medium.",
        borderline=True,
    ),
    Label(
        271,
        "Medium",
        "Strong demo craft: Prateek ran the whole candidate journey live rather than on "
        "slides - chat in Spanish, apply, real OTP email, an actual AI voice interview, "
        "then hiring-manager self-scheduling against a live calendar - and answered "
        "Delia's knock-out-question concern properly by first asking whether it was "
        "already configured in the ATS, then giving three configurable paths. He was "
        "honest about limits rather than guessing ('if someone can't see or can't hear, "
        "that is not something we're trying to solve in the AI interview yet'), and "
        "Nargis converted Delia's twice-repeated integration question into a firm 'it's "
        "a confirmation that we will do the integration with Paychex'. Coaching point "
        "is time allocation: the client had to run the clock for the reps ('in the "
        "interest of time, I know we have about 15, 14 minutes left, if we could touch "
        "base on the programmatic service'), the programmatic half that Delia called "
        "'top of mind for us at the moment' got squeezed, and unified analytics was "
        "dropped entirely ('apologise for not getting to it') - so Anna's 'we're just "
        "sort of in the dark, where is our money going' was deferred to a future "
        "session. Next Friday's hour was booked live, which limits the damage.",
    ),
    Label(
        272,
        "Medium",
        "A crisis call handled with real integrity. Indeed had told the client to "
        "sponsor direct and buy Employer Brand Hub for '4 times more applicants' while "
        "warning that programmatic partners get worse visibility, and Nargis responded "
        "by proposing Joveo pause its own Indeed sponsorship so the client could spend "
        "direct short-term - 'we will not hold you to anything to say you only have to "
        "spend with us programmatically' - which is a rep giving up her own revenue to "
        "protect the client, and it drew 'I'm really looking for a partner to help us "
        "navigate all of these changes'. Yelena added substantive strategy (GEO/LLM "
        "content freshness, hiring-event landing pages, a Meta pivot for the CNA/LPN "
        "audience, lift-and-shift career site with the URL retained). Coaching point: "
        "the client asked three times what Joveo actually knows about Indeed "
        "deprioritising programmatic partners ('I don't know what insight you guys "
        "have', 'What do you guys know?') and got sympathy and 'this is what we all "
        "guess' - nobody offered to pull cross-account performance data to test "
        "Indeed's claim before she moves budget away, and her specific question 'can "
        "Indeed tell what's being sponsored from Jovio on behalf of Americare?' was "
        "asked twice and never answered.",
    ),
    Label(
        274,
        "Medium",
        "Good account continuity and discovery in the back half - the rep recalled the "
        "target list from prior conversations (DWP, Home Office, Environment Agency, "
        "Met, Defra, MOJ) and confirmed it, sized the partner sales team (3 going to "
        "5), probed incumbent vendors and white- versus grey-label preference, and "
        "recovered well from Ali's pushback on training by offering three partnership "
        "models, landing on 'that probably sounds more like us'. He was also honest "
        "about scope ('if it's not something we can help solve, we'll also be very "
        "clear about that'). But the meeting was titled and booked for playbooks and "
        "none were brought, which the client had to say out loud herself at the end ('I "
        "was kind of presuming you were gonna show us playbooks today'); three separate "
        "asks for tangible collateral - Ali's 'some stills of what this looks like in "
        "practice', Nikki's 'something to hand that tells you what problems Jovio "
        "fixes', and her client-fit 'checklist' - all closed with 'let's book a longer "
        "session' rather than a named deliverable and date, and no date was booked, "
        "only 'July onwards, I can get something booked in'. Also dropped a live "
        "competitor: told they are still evaluating Scotty for the exact AI-interviewer "
        "use case, he asked 'are you guys still looking at Scotty or no?', got 'yeah', "
        "and moved on. Opened with roughly ten minutes of uninterrupted platform "
        "monologue to two people he had never met.",
        borderline=True,
    ),
    Label(
        275,
        "Medium",
        "Doug opened the demo with discovery rather than product - he put up the "
        "client's own AI workflow schematic and asked 'what's the genesis of this' "
        "before showing anything, which got Megan to lay out their whole end-to-end "
        "map, and he was disciplined about limits ('it can't switch languages in the "
        "middle of an interview', 'we are not currently a language assessment tool', "
        "'it's not really an assessment tool'). He also handled Marilyn's hard question "
        "about scoring nuanced clinical answers by proposing to feed their structured "
        "recruiter guidance into the scoring model, and closed with a concrete ask for "
        "45 minutes with product. Coaching point: the live demo was not customised at "
        "all for a mental-health provider hiring therapists - the registered-nurse flow "
        "asked 'do you currently possess a valid class a commercial driver's license', "
        "he had to say 'this is a demo, so work with me' and later 'this is kind of a "
        "bad example', and he opened by asking them to 'pretend registered nurse equals "
        "psychiatrist'. Secondary: Megan asked directly for a cost range ('the business "
        "is very eager to invest'), and Steve took it away to 'a set of questions' with "
        "no number and no date.",
    ),
    Label(
        276,
        "Low",
        "The stated agenda was unified analytics plus a conversational-AI demo and "
        "landing pages; only analytics was delivered, and the conversational AI got one "
        "slide of chatbot metrics before time ran out. Lloyd asked the question that "
        "decides the deal for him - can you show where candidates come from beyond the "
        "job boards we already understand, specifically social - three separate times "
        "('other than Facebook, I don't see anything that is social media related'; the "
        "Instagram/Snapchat question at the pilot discussion) and never got a real "
        "answer, only 'this is just dummy data'. Worst moment: on his pilot request the "
        "two sellers contradicted each other in front of him - Deborah offered 'sign it "
        "with annual to terminate for convenience', Cindy overrode her with 'I won't do "
        "that, I will always have the 90 day', then floated a free pilot and retracted "
        "it mid-sentence ('you can do it as a no, no cost also. I'm Kevin, you can't. "
        "I'm sorry. I'm covering you there'). Cindy also invented a three-month minimum "
        "on the spot after the 20k figure had already been given, swore about a partner "
        "on a recorded call ('without ****ing off Indeed'), and Deborah volunteered "
        "that she has been describing Talentneuron 'a little inaccurately in the past'. "
        "Credit: real answers on iCIMS pixel placement without reposting, and clear "
        "homework on both sides at the end.",
    ),
    Label(
        377,
        "Medium",
        "Well-run account call: explicit agenda stated up front and worked through, he "
        "owned the BMS make-good with a self-imposed deadline and tied it to her QBR "
        "date unprompted ('we'll be happy to have an answer by then'), answered her "
        "retargeting-dependency question with a ranked recommendation and reasons "
        "rather than a shrug, handled her security team's pixel question crisply ('we "
        "do not share any data that we receive, and we really don't receive much - job "
        "ID, candidate ID'), and dug for the root of the social-tracking blocker by "
        "asking what the original pixel-placement conversation had been. Coaching "
        "point: he presented a 22k/month reengage proposal to an account currently "
        "running 4k of social and only then said 'let me know if there's a cap' - "
        "budget qualification came after the number, not before - and the whole "
        "proposal ended with 'I'll share this with you' and 'take a look at the "
        "performance when you can', with no decision date, no follow-up booked, and no "
        "ask about who approves it. He also knew her approver was on holiday in "
        "Switzerland and left the pending TAS budget at 'waiting on a stakeholder' "
        "without offering to unblock it.",
    ),
    Label(
        379,
        "Medium",
        "Good action hygiene: he enumerated the four pending campaigns to bring order "
        "to a diffuse pipeline, committed to a written recap of 'what I owe you, what "
        "you owe', gave a concrete status on the make-good ('I submitted a credit "
        "ticket first this week... once that gets put through, then I will discuss with "
        "Nirala the additional make good'), was transparent that it was his first one, "
        "and cleared the Meta blocker by confirming the campaign can run without BMS "
        "account access. He also got himself added to the client's meeting with Dan at "
        "Yoke, which is the right instinct given the confusion. Coaching point, and it "
        "is why this is borderline Low: the live commercial issue on this account is an "
        "alleged 25k minimum spend that the client says is 'a little out there' and "
        "that she is getting 'different stories' about, and the rep said twice of his "
        "own company's product roadmap 'I don't know if that's actually true', while "
        "Jennifer's answer was a hedge ('technically you don't have to have a minimum "
        "budget, but in order to get results...'). Nobody gave her a straight answer, "
        "and she is carrying Joveo's own messaging problem into a separate meeting with "
        "a third party.",
        borderline=True,
    ),
    Label(
        380,
        "High",
        "Built the QBR deck explicitly as an asset for the client to take to HER client "
        "- 'I want this to become your guide that you can use with the client, change "
        "whatever you want' - and had already folded in her feedback from the previous "
        "week and added J&J as a competitor because she had asked. Before presenting he "
        "made her reconcile the June numbers live against his own screenshot (1,323 "
        "clicks / 368 applies matched) and had already corrected the deck for the UAT "
        "mix-up, so the data was verified rather than asserted. The JD analysis is real "
        "consulting, not a metrics dump: BMS's internal 'therapeutic area specialist' "
        "title is unsearchable against competitors' 'oncology sales specialist', tied "
        "to keyword performance, plus salary transparency and career path - she said "
        "'they eat that up for sure'. He removed a dependency on her by committing to "
        "produce monthlies on the 1st or 2nd rather than chasing her moving MBR date, "
        "offered to weight them to EMEA because that is 90% of her audience, and took "
        "immediate ownership of the direct-employers feed defect with a named owner. "
        "Borderline only because roughly two and a half minutes went to a Google Drive "
        "access mix-up on Joveo's invite, and because when she said the minimum spend "
        "was 'such a huge monthly minimum that I can't see any of our clients being "
        "able to swing that', Jennifer answered with a hedge rather than a straight "
        "number.",
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
