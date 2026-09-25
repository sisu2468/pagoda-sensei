# Sensei — what John wants (from client chat)

A filter of the Pagoda chat history for **Sensei / AI itinerary** only. Transferz ops, LINE/Slack, pay, and login issues are out of scope here.

---

## 1. Role of Sensei

- Sensei is **key** to Pagoda Pro (and later Foundation + Explorer). Same AI idea across products; Pro first.
- Advisors must be able to **search or build** what they want. Give **as many real options as possible**, from **Pagoda’s own database only** — not the open internet.
- Hiroki’s direction (RAG over Pagoda data) matches John: “only select data from our database.”
- Trial / prep first, then full in-product use. Humans still review until quality is good.

---

## 2. The intake form is the most important part

Advisors (and Pagoda-build) start here. Sensei must **read the intake**, not a flattened city list.

John’s intake / draft-itinerary intent:

- Dates, overnight cities (and hotel name when given)
- Day-trip / interest cities
- Budget, travel style, tour types, traveler count, special interests
- Mobility / dietary notes
- **Build mode:** (1) I will build this myself from the library, or (2) Pagoda team builds it for me → email ops with the intake (today). Sensei later **assists** that draft; it does not replace John on day one.

When they sleep in Kyoto and care about Nara, Sensei must not drop Nara.

---

## 3. What Sensei must return (this is where Hiroki’s demo failed)

John tested a live demo and said it was **wrong**:

> I entered stay dates and destinations (and hotels). I expected **one or multiple Tours for each day**, so the advisor and client can pick. Instead it selected **guides / operators**.

**Correct product**

- Primary unit = **published tour** for that day  
- Bundle the **allocated guide + profile** on the card when one exists  
- Advisor (+ client) **chooses** which tour(s) they like  
- Search-by-guide-name is a **second** entry: show that guide’s tours — still tours, not a guide-only list pretending to be an itinerary  

**Wrong**

- Operator-first / guide-first matching as the main itinerary result  
- Invented tours  
- Filling every day with an 8-hour tour to look complete  

---

## 4. Travel days (John, in his own words)

> If you are in Tokyo today and you want to be in Hokkaido tomorrow and then in Okinawa the next day, you cannot do a full day tour. I know that it is too much to ask.

Sensei must mark those hops as **travel days** in code and say so. Do not put a full-day Tokyo walk on a Hokkaido flight morning.

---

## 5. Guides on tours (feeds Sensei, not a separate AI)

- Operator assigns **their** guides to **their** tours (`guide_tour_assignments`).  
- Agent viewing a **guide** sees every tour that guide can lead.  
- Agent viewing a **tour** sees which guides are on it (tier, rating, availability).  
- Search by **guide name** → that guide’s tours.  
- A booking needs: assigned to the tour **and** available on the date.  
- Sensei may still **recommend a published tour with no guide yet** (“Guide to be appointed”). Assignment is ops, not a catalog filter.

Host-agency **exclusivity** (e.g. Fora only sees Fora’s introduced guides) belongs in retrieval from month one if that commercial rule is live. Do not retrofit later.

---

## 6. Airport transfers vs Sensei

- Menu label: **Airport Transfers** = **Transferz** only.  
- Partners must **not** offer airport transfers as their own product.  
- Old Pagoda-managed airport-transfer tours are a different thing; do not mix them.  
- Sensei must **not** auto-book Transferz from intake flight notes. Flight text is notes only.

---

## 7. After a tour is chosen (Sensei stops; Pro continues)

- **Lock the price** on the itinerary line when the tour is added/booked. Changing the commission calculator later must not rewrite booked lines.  
- Advisor confirms add to a **day**; existing job create / drag / delete.  
- Later (not Sensei v1): booking-confirmed button, invoice folder, archive unused itineraries.

---

## 8. What John already rejected

| He saw | He wants |
|---|---|
| Demo picked operators/guides | Tours per stay day + optional guide on the card |
| Full days on island hops | Travel-day warning, no full-day tour |
| Library-only busywork for some advisors | Intake + Pagoda-build / Sensei draft, then advisor + guide refine |
| Scraping the web | Pagoda Tour Library + assigned guides only |

---

## 9. One-sentence brief for Hiroki / Pro

**Sensei reads the intake, builds a day calendar (including travel days), and offers real published tours for each stay day — with the assigned guide when there is one — so the advisor can choose and add. It does not invent inventory, does not treat Transferz as a library tour, and does not replace human Pagoda-build until the picks are good enough.**
