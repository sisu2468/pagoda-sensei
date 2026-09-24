# Sensei — implementation steps

**Makato builds the itinerary builder first. He does not start with Q&A sessions.**

Intake first. Then a day calendar. Then travel-day rules. Then published-tour recommendations and add-to-day. Help chat is a later track, after that builder works.

Do not start by writing a Q&A knowledge base. Do not start by writing jobs into an itinerary. Do not ask the model to judge whether Tokyo → Hokkaido → Okinawa is possible.

---

## Step 1 — Freeze the intake contract (source of truth)

Mirror `lib/itinerary-intake.ts` on the Sensei API. Every planning decision reads this JSON, not a flattened “destinations” string.

**Must be present and stored on `itineraries.intake_data`:**

| Field | Why |
|---|---|
| `arrival_date` / `departure_date` | Trip length |
| `destinationStays[]` `{ city, nights, hotelName? }` | Overnight calendar |
| `interestDestinations[]` | Day trips (Nara while sleeping in Kyoto) |
| `adults` / `children` / `infants` / `totalTravelers` | Headcount for price and jobs |
| `japanExperiences` / `chinaExperiences` | Hard filters |
| `travelerTypes`, `travelStyles`, `tourStyles`, `tripPace`, `activityLevel` | Ranking |
| `topPriorities`, `mustHaveExperiences`, `experiencesToAvoid` | Hard filters / tie-break |
| `estimatedBudget` | Ranking, not invented prices |
| `transportationPreferences` | Shown to advisor; do not auto-book Transferz |
| Flight notes (free text) | Notes only |
| `preferredSuppliers[]` `{ destination, operatorId?, guideId? }` | Ranking boost |
| `additionalNotes` | Soft signal |

**Build in this step**

1. One TypeScript type and one Python Pydantic model that match.
2. `destinationsForSensei(intake)` = unique cities from stays **plus** interest cities, order preserved.
3. Validate: at least one overnight city; dates parse; departure after arrival.
4. Warn (do not silently invent cities) if `sum(nights)` ≠ trip nights — return a clear message the advisor can fix on the form.

**Done when:** a sample intake with Kyoto nights + Nara interest produces both cities for Sensei, and a missing stay list fails loudly.

---

## Step 2 — Expand intake into a day calendar (no AI)

Python (or shared TS) only.

```
Day 1  2026-11-01  overnight: Tokyo
Day 2  2026-11-02  overnight: Sapporo     ← city changed
Day 3  2026-11-03  overnight: Naha        ← city changed
```

Rules:

- Walk `destinationStays` in order; repeat each city for `nights`.
- Last night of a stay is the **departure morning** toward the next city (travel day).
- Attach `interestDestinations` as optional day-trip targets **only on local stay days** in a nearby cluster (e.g. Nara while overnight Kyoto).
- Never round-robin leftover days across Hokkaido and Okinawa to “use inventory.”

**Done when:** unit tests cover Kyoto 3 nights + Osaka 2 nights, and Tokyo 1 / Sapporo 1 / Naha 1, with no Claude call.

---

## Step 3 — Classify each day: stay vs travel (no AI)

Curated Japan matrix in code. Not Google Maps. Not the model.

**Clusters (examples to seed, then ops can extend):**

- Kanto: Tokyo, Yokohama, Kamakura, Hakone, Nikko
- Kansai: Osaka, Kyoto, Nara, Kobe
- Chubu: Takayama, Kanazawa, Nagoya
- Hokkaido: Sapporo, Otaru, Niseko, Furano, Hakodate
- Okinawa: Naha, Nago, Ishigaki (treat islands as far from each other unless listed as local)
- Kyushu: Fukuoka, Nagasaki, Kagoshima

**Band when overnight city changes from day D to D+1 (evaluated on day D, the leave day):**

| Band | Example | `day_kind` | `max_tour_minutes` |
|---|---|---|---|
| Local | Kyoto → Osaka, Kyoto → Nara | `stay` or `day_trip` | 480 (full day OK) |
| Near | Tokyo → Kyoto | `travel_near` | 180 (half day, morning origin **or** afternoon arrival — not both) |
| Far | Tokyo → Sapporo, Osaka → Naha, Sapporo → Naha | `travel_far` | 0 for a “full day” product; optional ≤ 90 min only if a published short/transfer product exists |

**Impossible pace flag** (advisor-visible, not a fake itinerary):

- Two or more **far** hops on consecutive days (Tokyo → Hokkaido → Okinawa) → `pace_warning`: “These overnight changes are travel days. A full-day tour is too much to ask.”
- Sensei still returns the calendar. It does **not** stuff 8-hour tours into those days to look complete.

**Done when:** tests assert Tokyo→Sapporo→Naha yields `travel_far` + `pace_warning`, and Kyoto→Nara does not.

---

## Step 4 — Fetch inventory: published tours only

1. `tour.status = published` is the only eligibility gate.
2. **Do not** drop tours with empty `guide_tour_assignments`.
3. Join owner via `tour.user_id` (operator). Assignment is a label: appointed guide name, or “Guide to be appointed.”
4. Filter by calendar cities + interest cities (location/country), cap payload size **per city**, not by deleting unassigned tours.
5. If a city has zero published tours: say so for that day. Do **not** fall back to another island’s catalogue.

**Done when:** a published Kyoto tour with no guide still appears; an unpublished tour never does; Tokyo inventory is not substituted for Okinawa.

---

## Step 5 — Recommend against the calendar (still no silent writes)

For each day:

1. Take `day_kind` + `max_tour_minutes` from Step 3.
2. Candidate pool = published tours in that day’s city (or local cluster for day trips).
3. If `travel_far`: do not offer full-day tours. Offer nothing, or only short/transfer published products, plus the pace note.
4. If `travel_near`: only tours with duration ≤ half day; morning **or** afternoon, consistent with leave/arrive.
5. Rank with intake: experiences, avoid list, preferred operator/guide, headcount.
6. Return 2–4 cards **or** a clear empty state. Never invent a tour.

**Add-to-day (after advisor confirms):** existing `POST /api/jobs` with `tourId`, date, participants. Same pricing as Tour Library. Works with no guide allocated.

**Do not** in this step: insert every suggestion as a job; auto-insert airport transfers from flight notes.

**Done when:** the Tokyo–Hokkaido–Okinawa fixture returns pace warnings and zero full-day tours on hop days; a Kyoto stay week returns 2–4 published Kyoto/Nara options per stay day.

---

## Step 6 — Wire the itinerary panel

1. Edit-itinerary: Sensei panel, Planning tab.
2. Context sent = intake + current day calendar + existing job ids (avoid duplicates).
3. Advisor asks in natural language **or** “fill stay days.”
4. Cards: add to **chosen** day → confirm → job API.
5. Failed add = error on screen, tour not shown as added.
6. Drag / delete remain the existing itinerary UI.

---

## Step 7 — Preferred suppliers on intake (after Steps 1–5 work)

1. Searchable operator/guide profiles per destination; store ids.
2. Rank their published tours first (assigned or not).
3. If none match, say so and show other published tours.

---

## Step 8 — Help chat (not first — after the itinerary builder)

**Do not start here.** This is not Makato’s first workstream.

1. `sensei_articles` + embeddings; audience advisor vs guide/operator.
2. Seed FAQs (including travel days, published-without-guide, Transferz vs flight notes).
3. Low confidence → related questions / support. No invented policy.

---

## Step 9 — Optional Pagoda-build draft (last)

Only when Steps 3–5 pass the fixtures:

- Stay days filled with confirmed-quality recommendations
- Travel-far days left light, with notes
- Humans review before the advisor sees it as a finished proposal
- Still no silent “tour added” without a successful job row

---

## Build order (who does what)

| Order | Work | Owner |
|---|---|---|
| 1 | Intake schema parity + `destinationsForSensei` | **Makato** (itinerary builder) |
| 2 | Day calendar expander + tests | **Makato** |
| 3 | City cluster + local/near/far matrix + tests | **Makato** + ops (city list) |
| 4 | Published-tour fetch; remove assignment filter | **Makato** |
| 5 | Recommend endpoint (read-only) | **Makato** |
| 6 | Confirm add-to-day on edit-itinerary | **Makato** + marketplace UI |
| 7 | Preferred suppliers field | Intake UI |
| 8 | Knowledge chat / Q&A sessions | **Not first.** After 1–6 work |
| 9 | Reviewable draft for Pagoda-build | Ops + Sensei |

---

## Fixtures that must pass before calling it “working”

1. Kyoto 3 nights, `interestDestinations: [Nara]` → Nara can appear on a stay day.
2. Published tour, no guide assigned → still recommended and addable.
3. Tokyo 1 night → Sapporo 1 night → Naha 1 night → all change days are `travel_far`; **no full-day tour**; pace warning shown.
4. Kyoto → Osaka (1 night each) → full-day or half-day in Kansai still allowed (local).
5. Zero published tours in a city → explicit empty day, not another region’s tours.

---

## Out of this sequence (do not pull in)

- Live flight / train routing APIs
- Auto Transferz from flight text
- Requiring a guide assignment to recommend
- Letting Claude decide travel time between islands
- Writing a full itinerary of full-day tours to “look complete”
