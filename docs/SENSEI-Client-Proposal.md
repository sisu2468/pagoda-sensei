# Pagoda Travel — Sensei
## Product proposal for the client

**Prepared for:** Pagoda Travel  
**Product:** Sensei — in-marketplace AI assistant  
**Date:** 24 September 2026  
**Status:** Proposal + current delivery snapshot

---

## 1. Understanding (confirmed)

1. Pagoda is already a live B2B marketplace. Sensei sits on top of it. It does not replace portals, itineraries, Tour Library, pricing, or booking.
2. Advisors, operators, and appointed guides already work in separate portals with different permissions. Sensei must respect those roles, not flatten them.
3. Sensei has two jobs, in this **build order**: (1) recommend real published tours while an advisor is building an itinerary, using intake; (2) later, answer “how do I use Pagoda?” from curated marketplace knowledge.
4. Answers about product behaviour come only from Pagoda’s own Q&A. If Sensei is not confident, it says so and offers related questions or Pagoda support. It never invents policy. **Q&A is not the first thing to build.**
5. Tour recommendations come only from **published** Tour Library inventory. Sensei never invents a tour, price, or operator.
6. **A published tour is eligible even if no guide has been allocated yet.** Guide assignment is operational, not a filter. Hiding unassigned published tours is why the current matching engine under-delivers. The operator still owns the tour (`tour.user_id`); a guide can be appointed later.
7. Adding a tour is never silent. The advisor chooses the recommendation and the day. After that, existing move / delete behaviour continues to apply.
8. Intake already distinguishes overnight stays from day-trip interest cities. Sensei must use both, so a Nara day trip is not lost when the client sleeps in Kyoto.
9. **Intake is the planning source of truth.** From overnight stays Sensei builds a day calendar, then marks **travel days**. If the client is in Tokyo today, Hokkaido tomorrow, and Okinawa the next day, those days cannot take a full-day tour — only transit / a short activity, or an explicit “too much to ask” note for the advisor. Geography is decided in code from intake, not guessed by the model.
10. Preferred guides or operators on intake should be favoured when recommending. If they have no matching tour, Sensei says so and offers alternatives — including other published tours with or without a guide allocated.
11. Flight notes on intake are notes only. Sensei does not auto-create Transferz bookings.
12. Human Pagoda-build remains until Sensei drafts are consistently good enough. Sensei assists; advisors and Pagoda ops still review.

**Makato builds the itinerary builder first. He does not start with Q&A sessions.**

| Order | What | Who |
|---|---|---|
| **1st** | Intake → day calendar → travel-day rules → published-tour recommend → confirm add to a day | **Makato** |
| 2nd | Help chat / Q&A (advisor + guide/operator) | After the itinerary builder works |
| 3rd | Optional Pagoda-build first draft (human review) | Only when itinerary quality is signed off |

Section 4 is that first workstream. The FAQ list in section 5 is a content catalogue for later, not the first sprint.

---

## 2. What exists today vs what Sensei will be

### Live on Pagoda today

- Advisor, operator/guide, and admin portals
- Itineraries with day-by-day jobs
- Tour Library, pricing, commission, booking, chat, PDF/proposal
- Intake form stored on the itinerary
- Build modes: “I will build this myself” vs “Pagoda team builds this for me”
- Pagoda-build currently emails operations. Humans still assemble the trip.

### Already built for Sensei (backend)

An AI matching service can:

- Read structured intake (destinations, dates, styles, experience chips, must-haves, avoidances)
- Fetch published tours from the live Tour Library
- Recommend 2–3 real tours per day, with match reasoning
- Optionally write a first draft into `itineraries` + `jobs`
- Run long trips asynchronously so the advisor can poll for completion

**Known defect in that engine:** it drops any published tour with no row in `guide_tour_assignments`, then skips those tours again when writing jobs. That shrinks real inventory and is a primary reason matching “doesn’t work.” Sensei must not repeat this.

This is a **drafting engine**, not the in-product Sensei experience yet.

### Not built yet (the client-facing product)

| Area | Status |
|---|---|
| Advisor help chat (Q&A) | Not started |
| Operator / guide help chat (Q&A) | Not started |
| Admin-editable knowledge base | Not started |
| Unanswered-question backlog | Not started |
| Sensei panel on edit-itinerary | Not started |
| “Recommend 2–4 tours, then add to a chosen day” | Not started |
| Preferred guide/operator field on intake | Not started |
| Sensei draft as a reviewable Pagoda-build option | Partial (write exists; in-app review flow does not) |

**Overall:** the matching brain is roughly one fifth of Sensei. The help product, the itinerary panel, and preferred-supplier logic are the remaining work.

---

## 3. Architecture

Sensei is a layer on the existing marketplace. It does not introduce a second itinerary model.

```
Advisor / Guide portal
        │
        ▼
   Sensei Chat API
        │
        ├── Knowledge mode  → curated Q&A retrieval  → grounded answer
        └── Planning mode   → published Tour Library → 2–4 recommendations
                                      │
                                      ▼
                         Advisor confirms tour + day
                                      │
                                      ▼
                         Existing job create / move / delete
```

### 3.1 Q&A data model (second workstream — not first)

This store is for help chat **after** the itinerary builder ships. Do not start implementation here.

| Table | Purpose |
|---|---|
| `sensei_articles` | Curated Q&A. One question, one approved answer, audience, category, related articles |
| `sensei_article_embeddings` | Vector of question + answer for retrieval |
| `sensei_conversations` | Chat thread (user, portal, itinerary id if any) |
| `sensei_messages` | User question, retrieved article ids, answer, confidence |
| `sensei_unanswered` | Low-confidence or unmatched questions → content backlog |

**`sensei_articles` fields**

- `id`, `audience` (`advisor` \| `guide` \| `operator` \| `all`)
- `category` (login, intake, library, booking, pricing, messaging, transfers, pdf, build-mode, roster, invoices, …)
- `title` (the question)
- `answer` (approved markdown)
- `related_article_ids`
- `escalate_to_support` (boolean)
- `status` (`draft` \| `published` \| `archived`)
- `updated_by`, `updated_at`

Retrieval searches **published articles for that audience only**. The model may rephrase; it may not add marketplace behaviour that is not in the retrieved articles.

If top similarity is below threshold:  
“I don’t have a confident answer for that. Related questions: … Or contact Pagoda support.”

### 3.2 Itinerary recommendations (first workstream — Makato)

| Input | Source |
|---|---|
| Natural-language request | Sensei panel on edit-itinerary |
| Overnight cities | `intake_data.destinationStays` |
| Day-trip cities | `intake_data.interestDestinations` merged in (same rule as `destinationsForSensei`) |
| Party size | Intake / job participants |
| Experience chips, budget, style | Intake |
| Preferred suppliers | Intake preferred guide/operator ids |
| Inventory | **All** `tour` rows with `status = published`. Guide allocation is optional. |

**Eligibility rule (must be explicit):**

| Tour state | Sensei may recommend? | Advisor may add to a day? |
|---|---|---|
| Published, guide appointed | Yes | Yes |
| Published, **no guide allocated yet** | **Yes** | **Yes** |
| Draft / unpublished / archived | No | No |

Guide assignment is used only to **label** the card (“Appointed: …” or “Guide to be appointed”) and later for ops. It is never a reason to hide a published tour.

The operator who owns the tour remains the booking counterpart: price-confirmation and official booking emails still go to the operator, which is the correct path when no team guide is appointed yet.

**Output:** 2–4 published tours. Each card shows name, city, duration, advisor-visible price (same rules as Tour Library), operator name, guide status, and why it fits.

**Add to day:** advisor picks tour + day → existing `POST /api/jobs` (tourId, date, participants). Price on the line uses existing `pagoda-pricing` / `tour-price`. Drag and delete stay as they are today. Adding must succeed whether or not a guide is already allocated.

If nothing **published** matches: a clear message, not a filler tour — and not a silent skip of unassigned published inventory.

### 3.2.1 Travel days (from intake, before any tour pick)

Overnight `destinationStays` are expanded into a day → city calendar. When the overnight city **changes**, that day is a **travel day**. Sensei does not ask the model whether Tokyo → Hokkaido → Okinawa is realistic.

| Overnight change | Band | Full-day tour? | What Sensei may offer |
|---|---|---|---|
| Same city / same cluster (Kyoto ↔ Nara, Osaka ↔ Kyoto) | Local | Yes | Full published tours; day trips from `interestDestinations` |
| Shinkansen-scale (Tokyo ↔ Kyoto) | Near | No | Half-day / morning at origin **or** afternoon at destination |
| Flight-scale (Tokyo ↔ Hokkaido, Honshu ↔ Okinawa, Hokkaido ↔ Okinawa) | Far | **No** | Transit only, or a short activity. Card/note: this hop is too much for a full-day tour |

A trip that is Tokyo (1 night) → Hokkaido (1 night) → Okinawa is three far hops. Sensei must not fill those days with 8-hour tours. It flags the pace to the advisor instead of pretending the catalogue can make it work.

This table is a curated Japan city/region matrix in code — not a live routing engine, and not Claude guessing distances.

### 3.3 Preferred guides / operators (after itinerary recommend works)

Stored on `itineraries.intake_data`, for example:

```json
"preferredSuppliers": [
  { "destination": "Kyoto", "operatorId": "…", "guideId": "…" }
]
```

When recommending or drafting:

1. Prefer tours **owned by** those operators, or assigned to those guides
2. Still include that operator’s other **published** tours even if no guide is allocated yet
3. If the preferred supplier has no matching published tour, say so and offer other published tours
4. Never invent inventory for a preferred name
5. Never exclude a published tour only because `guide_tour_assignments` is empty

### 3.4 Events (all phases)

Log: question text (no extra PII), retrieved article ids, confidence, recommended `tour_id`s, confirmed `job_id`s, skip/error reasons. Do not log secrets or guide net rates to advisor-facing logs.

---

## 4. Itinerary builder — what Makato builds first

This is the first delivery. **Not Q&A.**

Sensei on **edit itinerary** reads the **intake form**, builds a day calendar, marks travel days, recommends **published** tours (guide assigned or not), and adds a tour only after the advisor confirms the tour and the day.

### 4.1 Intake (source of truth)

- Overnight stays (`destinationStays`: city + nights)
- Day-trip cities (`interestDestinations`) merged so Nara is not lost when the client sleeps in Kyoto
- Party size, experience chips, pace, avoid list, budget, preferred operator/guide ids
- Flight notes stay notes — no auto Transferz

### 4.2 Day calendar + travel days (code, not the model)

From stays: each date gets an overnight city. When the city changes, that day is a travel day.

- Local (Kyoto ↔ Osaka / Nara): full-day tour allowed
- Near (Tokyo ↔ Kyoto): half day only
- Far (Tokyo → Hokkaido → Okinawa): **no full-day tour**; tell the advisor it is too much

### 4.3 Recommendations + add to a day

- Pool = all **published** tours (no guide allocated still eligible)
- 2–4 cards, advisor-visible price, “Guide to be appointed” when needed
- Advisor picks tour + day → existing add-to-itinerary job API
- Drag / delete stay as they are today
- Empty city or impossible hop: a clear message, not a fake full-day tour

### 4.4 Where it sits

Right-hand **Sensei** panel on edit-itinerary (Planning). Same chrome can later host help chat; Makato does not build that tab first.

### Not in this first build

- Advisor / guide Q&A sessions
- Admin FAQ editor
- Sensei replacing Pagoda-build humans

---

## 5. Seed FAQ outline

Titles only. Full answers are written and approved in admin before publish.

**This list is not Makato’s first sprint.** It is the later help-chat catalogue. He builds the itinerary builder (section 4) first.

### 5.1 Advisor — first 100 questions

**Login, roles, portals (1–10)**  
1. How do I log in to Pagoda?  
2. What is the difference between the advisor portal and the guide portal?  
3. I was invited by an agency — how do I join?  
4. What can an advisor do that a guide cannot?  
5. Can I use the same email as a guide and as an advisor?  
6. I forgot my password — what do I do?  
7. Why can’t I see operator tools in my account?  
8. Who in my agency can view this itinerary?  
9. How do I update my profile and agency details?  
10. Who do I contact if I cannot access my portal?

**Creating an itinerary (11–20)**  
11. How do I create a new itinerary?  
12. What is required before I can start adding tours?  
13. How do I name and date an itinerary?  
14. Can I duplicate an existing itinerary?  
15. Where is the itinerary saved?  
16. Can two advisors in my agency edit the same itinerary?  
17. How do I find an itinerary I created last month?  
18. What is the difference between a draft itinerary and a booked one?  
19. Can I build an itinerary before the client has confirmed dates?  
20. How do I delete or archive an itinerary?

**Intake form — overview (21–24)**  
21. What is the Asia Luxury Travel Request Form used for?  
22. Where is intake data stored?  
23. Do I have to finish the intake before adding tours?  
24. Can I edit intake after tours are already on the itinerary?

**Intake — Sections 1–8 (25–48)**  
25. Section 1: What advisor and client details are required?  
26. Section 1: How do I record the lead traveler vs the group?  
27. Section 2: How do I enter traveler counts (adults, children, infants)?  
28. Section 2: Should I add traveler ages?  
29. Section 3: How do overnight hotel stays work (`destination stays`)?  
30. Section 3: What are interest / day-trip cities vs overnight cities?  
31. Section 3: How do I add Nara if the client sleeps in Kyoto?  
32. Section 3: What if the client is open to other destinations?  
33. Section 4: How do I record Japan experience interests?  
34. Section 4: How do I record China experience interests?  
35. Section 4: Why were other countries removed from the form?  
36. Section 5: How do traveler types affect planning?  
37. Section 5: How should I describe budget?  
38. Section 6: What do tour style and trip pace mean?  
39. Section 6: How do I set activity level?  
40. Section 7: How do I list must-have experiences?  
41. Section 7: How do I list things to avoid?  
42. Section 7: How do top priorities get used later?  
43. Section 8: Where do I put free-text flight details?  
44. Section 8: Are flight details a Transferz booking?  
45. Section 8: How do I add extra notes for Pagoda or the operator?  
46. What happens if a required intake field is left blank?  
47. Can the client fill intake, or only the advisor?  
48. How does Sensei read the intake when recommending tours?

**Tour Library vs custom jobs (49–56)**  
49. What is the Tour Library?  
50. What is a custom job?  
51. When should I use a library tour vs a custom request?  
52. Are library prices the prices my client pays?  
53. Who owns a library tour?  
54. Can I request a tour that is not in the library?  
55. Why is a tour in the library not available for my dates?  
56. Can Sensei recommend a published tour if no guide is allocated yet? (“Guide to be appointed”)

**Adding / moving / deleting tours (57–66)**  
57. How do I add a Tour Library item to a day?  
58. How do I choose the date and headcount when adding?  
59. Can I add the same tour twice on different days?  
60. How do I move a tour to another day?  
61. How do I delete a tour from a day?  
62. Does deleting a tour cancel a booking?  
63. Can I drag tours between days?  
64. What if the day already has a morning tour?  
65. Who is assigned when I add a library tour?  
66. Why did adding a tour fail?

**Commission, client price, USD (67–76)**  
67. What is the commission slider?  
68. How is the client price calculated from the purchase price?  
69. What is Pagoda markup vs advisor commission?  
70. Why does the line price change when I change headcount?  
71. Why does the itinerary price differ from the library card?  
72. How do I show a USD estimate?  
73. Is the USD figure a guaranteed rate?  
74. Can the operator see my commission?  
75. Can I see the guide’s net rate?  
76. What is FX protection on JPY purchase prices?

**Confirm booking and price confirmation (77–86)**  
77. What does “Confirm booking” do?  
78. Who receives the price confirmation email?  
79. What does the operator receive vs what my client sees?  
80. When does a request become an official booking?  
81. What if the appointed guide is not the operator?  
82. Why might a managed guide not receive the email?  
83. What should I do if price confirmation is delayed?  
84. Can I change the itinerary after confirming?  
85. What happens if the operator declines or changes price?  
86. How do I know a booking is fully confirmed?

**Messaging (87–90)**  
87. How do I message a guide or operator from an itinerary?  
88. Is chat visible to the client?  
89. Who is in the thread — the operator or the appointed guide?  
90. What should I not send in chat (pricing, personal data)?

**Transfers vs flights (91–94)**  
91. What is Transferz?  
92. How is Transferz different from the flight-details box on intake?  
93. How do I add an airport transfer to a day?  
94. Will Sensei book a transfer because I typed a flight number?

**PDF / proposal (95–97)**  
95. How do I export a PDF or proposal?  
96. What is included in the proposal vs hidden from the client?  
97. Can I send the PDF before booking is confirmed?

**Build mode (98–100)**  
98. What is “I will build this itinerary myself”?  
99. What is “Pagoda team builds this for me”?  
100. When will Sensei draft the first proposal, and who still reviews it?

---

### 5.2 Guide / operator — first 100 questions

**Login, roles, operator vs guide (1–16)**  
1. How do I log in to the guide / operator portal?  
2. What is an operator vs an appointed guide?  
3. Who owns a tour — the operator or the guide who runs it?  
4. How does an operator appoint a team guide to a tour?  
5. What can an appointed guide see and do?  
6. What can only the operator do?  
7. What is a managed guide?  
8. Why does a managed guide have an email like `@managed.pagoda.local`?  
9. Can a managed guide receive booking or price-confirmation emails?  
10. If a team guide is appointed, who gets the price confirmation email?  
11. If a team guide is appointed, who gets the official booking email?  
12. What happens if we email the appointed guide instead of the operator?  
13. How do I switch between operator and guide views if I have both?  
14. How do I invite a team guide?  
15. How do I remove a guide from a tour assignment?  
16. Who should advisors message — me or my appointed guide?

**Tours and publishing (17–32)**  
17. How do I create a new tour?  
18. What fields are required before I can publish?  
19. How do I publish a tour to the Tour Library?  
20. How do I unpublish or archive a tour?  
21. Who can edit a tour I own?  
22. How do I set price per adult and per child (JPY)?  
23. How do advisors see my price vs what I earn?  
24. How do I add photos?  
25. How do I set location and duration?  
26. How do I set activity type?  
27. Why is my tour not showing in the library?  
28. Why can’t an advisor add my tour to an itinerary?  
29. What does “no guide assignment” mean — and can advisors still add that published tour?  
30. Can two guides be assigned to one tour?  
31. How do I clone a tour for a similar product?  
32. How do I update price without breaking existing itinerary lines?

**Assignments and roster (33–44)**  
33. How do I appoint a guide to a tour?  
34. How do I see my roster of team guides?  
35. How do I mark who is available on a date?  
36. Where is the availability calendar?  
37. What if the appointed guide is unavailable for the advisor’s dates?  
38. Can I reassign a tour after an advisor has added it?  
39. Who confirms availability — operator or appointed guide?  
40. How do I handle a guide substitution?  
41. What is `guide_tour_assignments` in practical terms?  
42. Can a guide decline an assignment?  
43. How do I see all jobs assigned to my team?  
44. How do I filter jobs by date or city?

**Pricing, confirmation, booking emails (45–62)**  
45. What is a price confirmation request?  
46. Who must reply to price confirmation?  
47. Why did the email come to the operator, not the guide who will run the day?  
48. What should I check before confirming a price?  
49. How do headcount and child rates affect the confirmation?  
50. What if I need to change the price?  
51. What if I cannot operate that date?  
52. What does the advisor see after I confirm?  
53. When does the job become an official booking?  
54. What email is sent on official booking, and to whom?  
55. Will a managed guide receive that email?  
56. How do I make sure the operating guide still sees the job in-app?  
57. What if the advisor’s commission changes after I quoted?  
58. Can I see the advisor’s client price?  
59. Should I quote net to the advisor in chat?  
60. What is FX protection and does it change my JPY price?  
61. How do I invoice after the job?  
62. Who sends the invoice — operator or appointed guide?

**Jobs, availability, day-of operations (63–78)**  
63. How do I see jobs on my calendar?  
64. How do I confirm I can operate a job?  
65. How do I decline a job?  
66. What details are on the job (meeting point, headcount, notes)?  
67. How do I update job status?  
68. What if the itinerary day moves after I confirmed?  
69. What if the advisor deletes the tour from the itinerary?  
70. How do I add internal notes the advisor should not see?  
71. How do I communicate a meeting-time change?  
72. What do I do for a no-show or late client?  
73. How are airport transfers different from walking tours?  
74. Do I handle Transferz bookings in this portal?  
75. How do I report a problem with a booking?  
76. How do I see past completed jobs?  
77. Can I export my schedule?  
78. Who do I call for day-of emergencies?

**Chat, profiles, payouts (79–90)**  
79. How do I reply to an advisor in chat?  
80. Is the appointed guide on the same thread as the operator?  
81. What should I never put in chat?  
82. How do I update my public profile?  
83. How do specialties and city on my profile affect matching?  
84. How do I set or update daily rate (if used)?  
85. How and when do I get paid?  
86. Who invoices — Pagoda, the operator, or the guide?  
87. How do I add bank / payout details?  
88. What if an advisor asks me to book off-platform?  
89. How do I flag a content error on my tour?  
90. How do I request Pagoda to feature a tour?

**Sensei for guides / operators (91–100)**  
91. What can Sensei help me with in this portal?  
92. Will Sensei change my tours without asking?  
93. Can Sensei confirm a booking for me?  
94. Why did Sensei tell me to contact support?  
95. How is operator help different from advisor help?  
96. What should I do if Sensei’s answer does not match what Pagoda ops told me?  
97. How do I suggest a new help article?  
98. Does Sensei email my managed guides?  
99. Does Sensei show advisors my net price?  
100. Who reviews Sensei’s answers about booking emails?

---

## 6. UX outline

### 6.1 Help chat (advisor and guide) — second workstream

Build this **after** the itinerary builder. Same right-hand panel, Help tab — not Makato’s first delivery.

**Data:** `sensei_articles` + embeddings; conversations/messages; unanswered queue; admin editor.

**Advisor portal:** Sensei in main nav; starter questions (create itinerary, intake, add a tour, commission, confirm booking, PDF); advisor articles only.

**Guide / operator portal:** same chrome; starter questions for publish, appoint a guide, price confirmation email, availability, invoice, roster. Copy must distinguish **operator** (owns tours, receives booking emails) vs **appointed / managed guide** (`@managed.pagoda.local` cannot receive SMTP mail).

```
┌─────────────────────────────────────────────────────────┐
│  Pagoda portal                     [ Sensei ]           │
├────────────────────────────────────────────┬────────────┤
│  Existing page (itinerary, library, …)     │  Sensei    │
│                                            │            │
│                                            │  Ask about │
│                                            │  Pagoda…   │
│                                            │            │
│                                            │  • How do  │
│                                            │    I add a │
│                                            │    tour?   │
│                                            │  • Confirm │
│                                            │    booking │
│                                            │            │
│                                            │  [ Ask ]   │
└────────────────────────────────────────────┴────────────┘
```

- Grounded answer + source article titles  
- Related questions  
- “I’m not confident — contact Pagoda support” when retrieval is weak  
- Guide portal uses the same chrome with operator-safe copy

### 6.2 Itinerary Sensei — recommend and add to a day

On **edit itinerary**, Sensei sits in the same right-hand panel, with a **Planning** tab.

1. Advisor types, e.g. “Family-friendly food tour in Osaka for Day 3”
2. Sensei returns 2–4 **published** tours (cards, advisor-visible price). A missing guide assignment does **not** exclude the tour; the card shows “Guide to be appointed” when needed.
3. Each card: **Add to day** → day picker (default Day 3 if stated)
4. Confirm → existing add-to-itinerary path  
5. If add fails, the UI says it failed. The tour is not shown as added.  
6. After success, the advisor can drag or delete as today.

Never add a tour because the message “looked like” a request. Confirm tour + day.

### 6.3 Intake — preferred guides / operators

On the intake destination step, under each overnight city (and optionally each interest city):

- **Preferred operator / guide (optional)**  
- Search Pagoda profiles (not free-text only, so we store ids)  
- Allow more than one per destination later; v1 can be one primary + optional second  
- Helper text: “Sensei will prefer their published tours. If they have none that fit, you will see alternatives.”

---

## 7. Delivery plan

Work stays on existing Pagoda routes: intake JSON, Tour Library, `POST /api/jobs`, `pagoda-pricing` / `tour-price`, itinerary drag/delete. Sensei does not fork a second itinerary.

| Phase | What the client gets | When |
|---|---|---|
| **1 — Itinerary builder (Makato first)** | Intake calendar, travel days, 2–4 published tours, confirm add to a day | **Start here** |
| **2 — Knowledge Sensei** | Advisor + guide/operator Q&A chat; admin-edited articles | After phase 1 works |
| **3 — Pagoda-build draft** | Optional Sensei first draft, still human-reviewed | After phase 1 quality is signed off |

**Sequence**

1. Intake contract + day calendar + travel-day bands  
2. Published-tour recommend (including tours with no guide allocated)  
3. Confirm + add to day on edit-itinerary  
4. Preferred suppliers on intake  
5. **Then** Q&A schema, article editor, advisor/guide help chat  
6. Optional Pagoda-build “Sensei draft” behind review

**Reuse from the current matching service**

- Published-tour fetch (remove the “must have a guide assigned” filter)  
- Day-by-day ranking from intake  
- Async job polling for long drafts  

**Change before that engine is used in-product**

- **Do not filter or skip published tours that have no guide allocated**  
- Recommend and wait for confirm; do not write every suggestion as a job  
- Merge overnight + day-trip cities  
- Use marketplace client pricing, not raw adult JPY alone  
- If inventory cannot satisfy the request, return an actionable error — do not silently substitute another city’s tours  
- Do not auto-create transfers from intake flight notes

---

## 8. Out of scope for v1

- Sensei talking to the **end client** (B2C chat)
- Automatic **Transferz** booking from flight text
- Sensei **sending** price-confirmation or booking emails
- Sensei **confirming availability** or accepting jobs for a guide
- Showing **guide net rates** to advisors
- Multi-language chat (English first)
- Alternative proposal variants (2–3 full itinerary options) — listed as a later option
- Replacing Pagoda-build humans on day one
- Inventing custom (non-library) experiences
- Hotel booking engine
- Changing commission / FX / markup rules
- Voice, WhatsApp, or email-bot Sensei
- Training on live advisor–guide chat transcripts without a privacy review

---

## 9. Quality rules (non-negotiable)

- Ground how-to answers in published Pagoda articles, not generic travel advice  
- Recommend any **published** tour that fits — including tours with no guide allocated yet  
- Do not treat “no guide assigned” as “not in the library.” That filter is a bug.  
- Never claim an email was sent, a tour was added, or a booking was confirmed unless the system action succeeded  
- Price-confirmation and booking emails go to the **operator** (tour owner). When a team guide is appointed they still go to the operator, not to a managed guide’s placeholder inbox  
- Do not expose guide net prices where the product already hides them  
- If Sensei cannot do the task, return a clear error the advisor or guide can act on  
- Do not put a **full-day tour** on a far travel day (e.g. Tokyo today, Hokkaido tomorrow, Okinawa the next). Say it is too much and offer transit / a short option, or leave the day light.  

---

## 10. Decision asked of the client

1. Confirm Makato builds the **itinerary builder first** (intake, travel days, published-tour recommend, add to a day). Q&A sessions are **not** the first build.  
2. Nominate reviewers later for advisor articles and for operator-vs-guide booking articles (ops + one operator) when help chat starts.  
3. Confirm: Sensei may select **any published tour**, including those with no guide allocated yet. Guide assignment is later ops, not a recommendation gate.  
4. Confirm v1 preferred suppliers: selectable Pagoda profiles (ids), not free-text names only.  
5. Confirm travel-day rule: far overnight hops (Tokyo → Hokkaido → Okinawa) are not full-tour days.  
6. Confirm Pagoda-build stays human until draft quality is signed off.

Once those are agreed, Makato starts with the **intake contract and day calendar**, then recommendations on edit-itinerary. Help chat comes after.
