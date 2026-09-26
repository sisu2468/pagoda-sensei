# Pagoda Travel — AI Matching & Itinerary Service

AI-powered tour matching and itinerary generation microservice for the Pagoda Travel marketplace. Built with FastAPI, Supabase, and Anthropic Claude.

---

## What It Does

The service exposes a REST API that takes a client travel intake form and produces structured, day-by-day itineraries populated with real tours from the live Pagoda Travel tour inventory (Supabase). The core pipeline is:

1. Accept a structured intake form — destinations, dates, travel styles, experience preferences, per-destination experience checkboxes, must-haves, avoidances.
2. Fetch published tours from Supabase filtered by requested destinations, joined with their assigned guide and profile data.
3. Send tour inventory + client preferences to Claude (Anthropic), which recommends 2–3 tours per day ranked by location fit and preference alignment.
4. Write the resulting itinerary and individual tour jobs into Supabase (`itineraries` + `jobs` tables).
5. Return the itinerary ID, job counts, AI notes, and processing metadata.

A legacy operator-matching endpoint (`/match`) is also included, which scores a static operator database against a client intake form and returns the top 3 matched operators with a preliminary itinerary.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI (Python 3.12+) |
| AI model | Anthropic Claude (`claude-sonnet-4-6`) |
| Database / storage | Supabase (PostgreSQL) |
| Validation | Pydantic v2 |
| Server | Uvicorn (ASGI) |

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <your-repo-url>
cd pagoda-ai
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp env.example .env
```

Edit `.env` and fill in the required values:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key — get one at console.anthropic.com |
| `SUPABASE_URL` | Yes | Your Supabase project URL |
| `SUPABASE_ANON_KEY` | Yes | Supabase service role key |
| `ALLOWED_ORIGINS` | No (default: `*`) | Comma-separated CORS origins |
| `PORT` | No (default: `8000`) | Port to listen on |
| `LOG_LEVEL` | No (default: `INFO`) | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `ENVIRONMENT` | No (default: `development`) | Environment label in logs |

---

## Running Locally

```bash
uvicorn app.main:app --reload --port 8000
```

Windows helper (mock inventory, port 8000):

```powershell
powershell -File scripts\start-sensei.ps1
```

Interactive API docs are available at `http://localhost:8000/docs` once the server is running.

---

## Endpoints

### `GET /health`

Health check. Returns service status and the number of operators loaded from the database.

**Response:**
```json
{
  "status": "ok",
  "service": "pagoda-ai-matching",
  "version": "1.0.0",
  "operators_loaded": 15
}
```

---

### `POST /match`

**Legacy operator-first matching.** Scores a static operator database against a client intake form and returns the top 3 matched operators with a preliminary day-by-day itinerary.

Accepts a `TravelIntakeForm` JSON body (advisor info, lead traveler name, group size, dates, destinations, budget, travel styles, special notes). Returns ranked operators with match scores, reasoning, suggested tours, and a preliminary itinerary.

**Processing time:** ~8–15 seconds (one Claude API call).

---

### `POST /sensei/recommend`

**Itinerary builder (read-only).** Intake → day calendar → travel-day bands → published tours per stay day.

- Overnight stays plus `interestDestinations` (Nara is kept when the client sleeps in Kyoto)
- Hotel names from intake are copied onto each day (not invented)
- Far hops (Tokyo → Hokkaido → Okinawa) are not full-tour days
- Published tours with no guide allocated are eligible (`guide.status: to_be_appointed`)
- Assigned guides are bundled on the tour card (`guide` + `guides[]`)
- Search-by-guide-name (`query` or `guide_name`) returns that guide’s tours — still tours
- Airport-transfer / Transferz products are excluded. Flight notes never create a transfer
- Host-agency exclusivity: `host_agency_id` hides another agency’s introduced guides; the published tour stays
- **Does not write jobs.** Advisor confirms tour + day in the marketplace, then `POST /api/jobs`

**Processing time:** typically under 2 seconds (no model call for geography).

### `GET /sensei/guide-tours`

Second entry: `guide_name` or `guide_id`. Returns that guide’s published tours. Not a guide-only list.

---

### `POST /match-tours`

**Tour-first day-by-day matching.** For each day of a trip, recommends 2–3 specific tours from the live Supabase inventory that best fit that day's city and the client's stated preferences.

**Request body:**
```json
{
  "destinations": ["Tokyo", "Kyoto"],
  "arrival_date": "2026-11-01",
  "departure_date": "2026-11-10",
  "travel_styles": ["cultural", "food"],
  "special_notes": "Prefer small groups, no long walking days"
}
```

**Response:** A `day_by_day` array (one entry per day), each containing 2–3 ranked tour objects with tour name, location, duration, price, guide/operator info, availability, and match reasoning. Also includes `ai_notes`, `data_gaps`, and `processing_metadata`.

**Processing time:** ~15–60 seconds depending on trip length.

---

### `POST /create-itinerary`

**Full pipeline — match tours and write to Supabase.** Accepts the complete structured intake form, runs Sensei AI tour matching, then writes the resulting itinerary and individual tour jobs directly into the production `itineraries` and `jobs` tables.

Supports the full structured intake: per-destination experience checkboxes (`japanExperiences`, `thailandExperiences`, etc.), `topPriorities`, `mustHaveExperiences`, `experiencesToAvoid`, `tourStyles`, `transportationPreferences`, and day-by-day city stay plan (`destinationStays`).

**Required fields:**
- `user_id` — UUID of the advisor (`users.id`)
- `profile_id` — UUID of the guide/advisor profile (`profiles.id`)
- `name` — Human-readable itinerary name
- `arrival_date` / `departure_date` — `YYYY-MM-DD`
- At least one destination via `destinationStays[].city` or `primaryDestination`

**Response:**
```json
{
  "itinerary_id": "uuid",
  "jobs_created": 28,
  "jobs_skipped": 2,
  "skip_reasons": ["No guide assignment for tour abc123"],
  "logistics_notes": ["Shinkansen Tokyo→Kyoto inserted on Day 6"],
  "ai_notes": "Strong inventory match for Tokyo and Kyoto...",
  "data_gaps": [],
  "processing_metadata": {
    "model": "claude-sonnet-4-6",
    "input_tokens": 12480,
    "output_tokens": 3920,
    "latency_seconds": 34.1
  }
}
```

**Processing time:** ~30–120 seconds. For long trips or serverless deployments, use the async variant below.

---

### `POST /create-itinerary-async` → `POST /create-itinerary-process/{job_id}` → `GET /create-itinerary-status/{job_id}`

**Async variant of `/create-itinerary`** for environments where synchronous long-running requests are not viable (e.g. Vercel serverless).

The flow is a three-step chain:

**Step 1 — Enqueue:**
```
POST /create-itinerary-async
```
Accepts the same body as `/create-itinerary`. Returns immediately with `202 Accepted` and a `job_id`. No AI calls are made here.

```json
{ "job_id": "uuid" }
```

**Step 2 — Process (fire-and-forget):**
```
POST /create-itinerary-process/{job_id}
```
Runs the full Sensei matching + itinerary write pipeline. The client should call this **without awaiting the response** — it will block for 30–120 seconds. The client does not need the response from this endpoint; it polls step 3 instead. Returns `409 Conflict` if the job is already processing, done, or failed.

**Step 3 — Poll for result:**
```
GET /create-itinerary-status/{job_id}
```
Returns the current job state. Poll every 3–5 seconds until `status` is `done` or `failed`.

| Status | Response shape |
|---|---|
| `pending` / `processing` | `{ job_id, status, created_at, updated_at }` |
| `done` | `{ job_id, status, result: { itinerary_id, jobs_created, ... }, created_at, updated_at }` |
| `failed` | `{ job_id, status, error: "...", created_at, updated_at }` |

> **Vercel Hobby plan note:** Functions are killed after 60 seconds. A 15+ day itinerary can take 90+ seconds. Treat a job stuck in `processing` for more than 90 seconds as timed out. Vercel Pro (300s max duration) handles most trips fine.

---

## Project Structure

```
pagoda-ai/
├── app/
│   ├── main.py                          # FastAPI app, middleware, /match + /webhook routes
│   ├── models/
│   │   └── schemas.py                   # All Pydantic models (input + output)
│   ├── routes/
│   │   ├── tour_match_route.py          # POST /match-tours
│   │   ├── itinerary_route.py           # POST /create-itinerary
│   │   └── itinerary_async_route.py     # POST /create-itinerary-async + process + status
│   ├── services/
│   │   ├── matching_engine.py           # Legacy operator-first Claude matching
│   │   ├── tour_matching_engine.py      # Tour-first Claude matching (Sensei)
│   │   ├── itinerary_writer.py          # Writes itinerary + jobs rows to Supabase
│   │   ├── supabase_tours_service_v2.py # Fetches live tours from Supabase
│   │   ├── supabase_service.py          # Fetches live operators from Supabase
│   │   └── webhook_parser.py            # JotForm payload → TravelIntakeForm
│   ├── data/
│   │   └── operators.json               # Static operator database (legacy /match fallback)
│   └── utils/
│       └── dependencies.py              # Dependency injection (engine singleton)
├── requirements.txt
├── env.example
└── README.md
```
