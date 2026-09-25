# Prompt — open this in the other Sensei workspace

Copy **everything below the line** into a new Cursor chat in the other folder.

- If that folder is **Pagoda Pro** (Next.js): integrate the panel.
- If that folder is **pagoda-ai-handover** (FastAPI): continue the API.
- If you are unsure: look for `package.json` + `app/` (Pro) vs `app/main.py` + `uvicorn` (API).

Do **not** start with Q&A / help chat. Itinerary builder first.

---

You are building **Sensei** for Pagoda Travel.

Pagoda Pro is a live B2B Japan marketplace (Next.js App Router, React, TypeScript, Supabase): advisor / operator / guide / admin portals, itineraries, Tour Library, pricing, booking, chat, PDF. Operators own tours (`tour.user_id`) and may appoint guides (`guide_tour_assignments`). Intake lives on `itineraries.intake_data` (`lib/itinerary-intake.ts`). `destinationsForSensei(intake)` merges overnight stays + day-trip cities so Nara is not lost when the client sleeps in Kyoto.

Sensei is an AI layer on that product. It does not replace portals, jobs, or pricing.

## Build order (locked)

1. **Itinerary builder** — intake → day calendar → travel days → published-tour recommend → advisor confirms add to a day  
2. Help chat / Q&A (later)  
3. Optional Pagoda-build first draft (later, humans still review)

Makato / this workstream builds **(1) first**. Do not start FAQ databases, embeddings, or help-chat UI.

## Product rules (non-negotiable)

- Intake is the source of truth. Do not flatten to a destinations string.
- `sum(destinationStays.nights)` must equal inclusive trip days `(departure - arrival) + 1`. If not, return a clear error. Do not invent cities.
- Geography is decided in **code**, not by a model. Tokyo today / Hokkaido tomorrow / Okinawa the next day = **travel days**. No full-day tour. Show a “too much to ask” / pace warning.
- Kyoto ↔ Osaka / Nara = local (full day OK). Tokyo ↔ Kyoto = near (half day only).
- Recommend **any published** tour, even if no guide is allocated. Label “Guide to be appointed”. Hiding unassigned published tours is a bug.
- Never invent a tour, price, or operator.
- Never add a tour silently. Advisor picks tour + day, then existing `POST /api/jobs`.
- If the job create fails, the UI must not show the tour as added.
- Flight notes are notes only. Do not auto-create Transferz.
- Do not show guide net rates to advisors. Use existing `pagoda-pricing` / `tour-price`.
- Empty city = say so. Do not substitute another region’s catalogue.
- A silent fallback that returns the wrong thing is a bug.

## Sensei API (already built — pagoda-ai-handover)

FastAPI on `http://127.0.0.1:8000` (`uvicorn app.main:app --reload --port 8000`).

**Use this for the panel:** `POST /sensei/recommend`  
Read-only. `wrote_jobs` is always `false`. Does not write itineraries or jobs.

Do **not** use `POST /create-itinerary` for the in-app panel (it auto-writes a full draft).

Local mock inventory (no live Supabase required):

```
SUPABASE_URL=https://mock.supabase.local
SUPABASE_ANON_KEY=mock-anon-key
USE_MOCK_TOURS=true
```

Tours: `app/data/mock_tours.json`. Switch `USE_MOCK_TOURS=false` and real Pro `SUPABASE_URL` / `SUPABASE_ANON_KEY` when going live.

Key API files:

- `app/models/intake.py` — intake contract + `destinations_for_sensei`
- `app/services/day_calendar.py` — overnight nights → dated days
- `app/services/travel_days.py` — Japan clusters, local / near / far
- `app/services/sensei_recommend.py` — rank 2–4 published tours
- `app/routes/sensei_recommend_route.py` — `POST /sensei/recommend`
- `tests/test_sensei_itinerary_builder.py` — fixtures (must keep passing)

### POST /sensei/recommend

Request (dates + `intake_data`, camelCase except dates):

```ts
{
  arrival_date: string;       // YYYY-MM-DD
  departure_date: string;
  destinationStays: { city: string; nights: number; hotelName?: string }[];
  interestDestinations?: string[];
  adults?: number; children?: number; infants?: number; totalTravelers?: number;
  japanExperiences?: string[]; chinaExperiences?: string[];
  travelerTypes?: string[]; travelStyles?: string[]; tourStyles?: string[];
  tripPace?: string; activityLevel?: string;
  topPriorities?: string[]; mustHaveExperiences?: string;
  experiencesToAvoid?: string[]; estimatedBudget?: string;
  additionalNotes?: string; flightDetails?: string;
  preferredSuppliers?: { destination: string; operatorId?: string; guideId?: string }[];
  query?: string;
  exclude_tour_ids?: string[];
}
```

Response:

```ts
{
  destinations_for_sensei: string[];
  pace_warning: string | null;
  wrote_jobs: false;
  ai_notes: string;
  days: Array<{
    day: number;
    date: string;
    overnight_city: string;
    next_overnight_city: string | null;
    band: "local" | "near" | "far";
    day_kind: "stay" | "travel_near" | "travel_far";
    max_tour_minutes: number;
    day_trip_cities: string[];
    pace_warning: string | null;
    suggested_tours: Array<{
      tour_id: string;
      tour_name: string;
      description: string;
      location: string;
      duration_minutes: number | null;
      duration_display: string;
      operator: { id: string | null; name: string; email: string | null };
      guide: { id: string | null; name: string | null; status: "appointed" | "to_be_appointed" };
      price: number | null;
      price_display: string;
      match_score: number;
      match_reasoning: string;
      needs_confirmation: true;
    }>;
    empty_reason: string | null;
  }>;
}
```

---

## If this workspace is Pagoda Pro (Next.js)

Integrate. Do not reimplement travel days or matching.

1. `.env.local`: `SENSEI_API_URL=http://127.0.0.1:8000`
2. Server route `app/api/sensei/recommend/route.ts` (advisor auth). Load itinerary `start_date` / `end_date` / `intake_data` / existing `tour_id`s. POST to Sensei. Return JSON or 422 `detail`.
3. Right-hand **Sensei** panel on **edit itinerary**, Planning tab.
4. Show `pace_warning`, per-day `day_kind`, cards or `empty_reason`.
5. **Add to this day** → confirm → existing `POST /api/jobs` (`tourId`, date, participants from intake). Same `pagoda-pricing`.
6. Failed job POST → error, itinerary unchanged. Drag/delete stay as today.
7. Pass `interestDestinations`. Keep `destinationsForSensei(intake)`.

Acceptance: Kyoto+Nara day trip; unassigned published tour addable; Tokyo–Sapporo–Naha is travel_far; Kyoto–Osaka still allows a day tour; empty city does not steal another region.

Find `lib/itinerary-intake.ts`, the edit-itinerary page, and `POST /api/jobs` first. Then proxy, then panel, then add-to-day. Stop when that works. No Q&A.

---

## If this workspace is the FastAPI Sensei API

Do not rebuild what exists. Next API work only if needed for Pro:

- Keep `/sensei/recommend` read-only
- Keep mock tours working with `USE_MOCK_TOURS=true`
- When Pro is ready: live `fetch_live_tours` (published only, no assignment filter, no unfiltered city fallback)
- Do not start `sensei_articles` / embeddings / help chat
- Run `pytest tests/test_sensei_itinerary_builder.py` after changes

If the user wants Pro UI and you are in the API repo, say so and implement only API gaps they ask for.

---

## Out of scope until asked

- Q&A knowledge base, advisor/guide help chat
- Auto Transferz
- Sensei sending emails or confirming bookings
- Replacing Pagoda-build humans
- B2C / client-facing Sensei
- Voice / WhatsApp

Start by stating which repo you are in and the first file you will open. Then implement only that track.
