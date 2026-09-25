# Prompt — paste into the Pagoda Pro project

Copy everything below the line into Cursor (or another agent) **in the Pagoda Pro Next.js repo**. Do not start with Q&A / help chat.

---

You are integrating **Sensei** into **Pagoda Pro**, the live B2B Japan travel marketplace (Next.js App Router, React, TypeScript, Supabase).

Sensei’s brain is a **separate FastAPI service** (pagoda-ai-handover). You do **not** rebuild matching, travel-day logic, or the Tour Library in Pro. You wire UI + a server proxy + the existing job APIs.

## What Pagoda Pro already is (do not reinvent)

- Portals: advisor/agency, guide/operator, admin
- Itineraries with day-by-day jobs
- Tour Library (published tours). Operators own tours (`tour.user_id`) and may appoint guides (`guide_tour_assignments`)
- Booking: advisor Confirm booking → price confirmation email → official booking
- Pricing: purchase JPY → FX protection → Pagoda markup → advisor commission → client price (`pagoda-pricing` / `tour-price`)
- Intake stored as `itineraries.intake_data` JSON (`lib/itinerary-intake.ts`)
- Helper `destinationsForSensei(intake)` already merges overnight stays + day-trip interest cities (e.g. Nara while sleeping in Kyoto)
- Adding a library tour: existing `POST /api/jobs` with `tourId`, date, participants
- Drag / move / delete jobs already exist — reuse them

## What you are building (this sprint only)

**Itinerary Sensei on edit-itinerary.** Not Q&A. Not Pagoda-build replacement.

1. Server route in Pro that calls Sensei `POST /sensei/recommend`
2. Right-hand Sensei panel on the advisor **edit itinerary** page (Planning)
3. Show day calendar + 2–4 tour cards
4. Advisor confirms **which tour** and **which day**, then you call existing `POST /api/jobs`
5. If the job POST fails, show an error. Never pretend the tour was added

## What you are NOT building

- Help chat / FAQ / RAG / admin article editor
- Auto-creating Transferz from intake flight notes
- Sensei sending booking or price-confirmation emails
- A second itinerary model
- Calling FastAPI from the browser with secrets
- Using Sensei `POST /create-itinerary` (that writes a full draft). The panel uses **recommend only**

## Sensei API (already running)

Local default: `http://127.0.0.1:8000`  
Docs: `http://127.0.0.1:8000/docs`

Add to Pro `.env.local`:

```
SENSEI_API_URL=http://127.0.0.1:8000
```

Production: set `SENSEI_API_URL` to the deployed FastAPI URL.

### POST `{SENSEI_API_URL}/sensei/recommend`

Server-side only. Forward intake + dates. Do not invent fields.

**Request (JSON)** — map from `itineraries` dates + `intake_data`. Field names are camelCase except dates:

```ts
{
  arrival_date: string;          // YYYY-MM-DD from itinerary.start_date
  departure_date: string;        // YYYY-MM-DD from itinerary.end_date
  destinationStays: { city: string; nights: number; hotelName?: string }[];
  interestDestinations?: string[];  // REQUIRED to keep day trips (Nara)
  // pass through from intake_data when present:
  adults?: number;
  children?: number;
  infants?: number;
  totalTravelers?: number;
  japanExperiences?: string[];
  chinaExperiences?: string[];
  travelerTypes?: string[];
  travelStyles?: string[];
  tourStyles?: string[];
  tripPace?: string;
  activityLevel?: string;
  topPriorities?: string[];
  mustHaveExperiences?: string;
  experiencesToAvoid?: string[];
  estimatedBudget?: string;
  additionalNotes?: string;
  flightDetails?: string;        // notes only
  preferredSuppliers?: { destination: string; operatorId?: string; guideId?: string }[];
  query?: string;                // advisor natural language, e.g. "food tour in Osaka for Day 3" or "Yuki Tanaka"
  exclude_tour_ids?: string[];   // tour ids already on this itinerary
  guide_name?: string;           // second entry: that guide's published tours
  guide_id?: string;
  host_agency_id?: string;       // Fora-style exclusivity; hide other agencies' introduced guides
  max_cards?: number;            // default 6, max 12
  dietaryNotes?: string;
  mobilityNotes?: string;
  buildMode?: "self" | "pagoda_build";
}
```

**Nights rule:** `sum(destinationStays.nights)` must equal inclusive trip days  
`(end_date - start_date) + 1`. If Sensei returns 422, show that message on the form. Do not pad cities in Pro.

**Response:**

```ts
{
  destinations_for_sensei: string[];
  pace_warning: string | null;
  wrote_jobs: false;             // always false — Pro writes jobs
  wrote_transfers: false;        // never auto-book Transferz from flight notes
  search_mode: "itinerary" | "guide";
  guide_tours: Array<SenseiTourCard>;  // filled when search_mode is guide
  ai_notes: string;
  days: Array<{
    day: number;
    date: string;                // YYYY-MM-DD
    overnight_city: string;
    next_overnight_city: string | null;
    hotel_name: string | null;   // from intake destinationStays — do not invent
    band: "local" | "near" | "far";
    day_kind: "stay" | "travel_near" | "travel_far";
    max_tour_minutes: number;
    day_trip_cities: string[];
    pace_warning: string | null;
    suggested_tours: Array<SenseiTourCard>;
    empty_reason: string | null;
  }>;
}

type SenseiTourCard = {
  tour_id: string;
  tour_name: string;
  description: string;
  location: string;
  duration_minutes: number | null;
  duration_display: string;
  operator: { id: string | null; name: string; email: string | null };
  guide: { id: string | null; name: string | null; status: "appointed" | "to_be_appointed" };
  guides: Array<{ id: string | null; name: string | null; status: "appointed" | "to_be_appointed"; availability_display?: string }>;
  price: number | null;
  price_display: string;
  match_score: number;
  match_reasoning: string;
  needs_confirmation: true;
};
```

## Product rules (non-negotiable)

1. **Published tours with no guide are valid.** Show `guide.status === "to_be_appointed"` as “Guide to be appointed”. Do not hide the card.
2. **Travel days:** `travel_far` (e.g. Tokyo → Hokkaido → Okinawa) must not look like a full sightseeing day. Show `empty_reason` / `pace_warning`. Do not invent a filler tour in the UI.
3. **Never add silently.** Confirm tour + day, then `POST /api/jobs`. `wrote_jobs` from Sensei is always false.
4. **Same pricing as Tour Library** for the same headcount/commission. Use existing `pagoda-pricing` / `tour-price` when creating the job. Do not show guide net rates to advisors.
5. **Flight notes are notes.** Do not create Transferz bookings from Sensei.
6. If Sensei or job create fails, show the real error. A silent fallback that shows the wrong tour is a bug.
7. Use `destinationsForSensei(intake)` (or pass both `destinationStays` and `interestDestinations`) so day trips are not dropped.

## Implementation steps in Pagoda Pro

### Step A — Server proxy

Create `app/api/sensei/recommend/route.ts` (or the equivalent App Router route):

- Auth: only logged-in advisors who can edit that itinerary
- Load itinerary: `start_date`, `end_date`, `intake_data`, existing jobs (`tour_id`s)
- Build the Sensei body from intake (do not flatten to a destinations string)
- `exclude_tour_ids` = current itinerary tour ids
- `fetch(`${SENSEI_API_URL}/sensei/recommend`, { method: "POST", body })`
- Return Sensei JSON, or the 422 `detail` string to the client

Do not call `/create-itinerary` from this panel.

### Step B — Panel UI on edit-itinerary

Right-hand **Sensei** panel, **Planning** tab (desktop). Reuse existing itinerary page layout.

- Input: optional natural-language `query` + button “Recommend from intake”
- Banner: `pace_warning` if present
- Per day: date, overnight city, `day_kind` label (Stay / Half-day travel / Travel day)
- If `suggested_tours.length === 0`: show `empty_reason` (not a fake card)
- Cards: name, city, duration, `price_display`, operator, guide status, match reasoning
- Each card: **Add to this day** (or day picker, defaulting to that day)
- Confirm step: “Add {tour_name} to {date}?” then call existing job create
- After success: refresh itinerary jobs. Advisor can drag/delete as today
- After failure: toast/error, card stays, itinerary unchanged

### Step C — Add to day

Reuse the Tour Library “Add to itinerary” path:

- `POST /api/jobs` with `tourId`, date from the Sensei day, participants from intake (`adults`/`children`/`totalTravelers`)
- Same commission/markup already on the itinerary
- Must succeed for published tours with no `guide_tour_assignments`

### Step D — Intake fields Pro must send

Ensure `lib/itinerary-intake.ts` / the intake form persist:

- `destinationStays[]` with `nights >= 1`
- `interestDestinations[]`
- Optional later: `preferredSuppliers[]` (selectable profile ids, not free-text only)

If nights do not cover the trip dates, show Sensei’s 422 text and let the advisor fix the form.

## Acceptance checks

1. Kyoto 3 nights + `interestDestinations: ["Nara"]` → Nara can appear on a Kyoto stay day
2. Published tour with no guide → card visible, Add to day works
3. Tokyo 1 / Sapporo 1 / Naha 1 → travel days, no 8-hour tour cards, pace warning shown
4. Kyoto → Osaka → full-day/half-day still allowed (local Kansai)
5. Empty city → `empty_reason`, not another region’s tours
6. Failed `POST /api/jobs` → UI does not show the tour as added

## Quality

- Type the Sensei response; do not use `any` on cards
- Log itinerary id, recommended `tour_id`s, created `job_id` — no extra PII, no guide net rates
- Match existing Pro visual language (no new design system)

Start by finding `lib/itinerary-intake.ts`, the edit-itinerary page, and `POST /api/jobs`. Then implement Step A, then B, then C. Stop after the panel works end-to-end. Do not start Q&A.
