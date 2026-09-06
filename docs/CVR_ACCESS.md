# Getting CVR system-to-system access

**Why**: cvrapi.dk allows 50 lookups per day per IP range, which is days of budget for a
single run across Sjælland. Erhvervsstyrelsen's own distribution endpoint is free, has no
comparable cap, and — the bigger win — **can enumerate by industry code and postal code**,
which cvrapi.dk cannot. That turns CVR into the discovery mechanism the original brief
assumed it was, and likely removes the need for a Brave Search API key.

**Send this today.** Access is free but takes **a couple of weeks** to come through, so it's
the long pole. Everything else is either done or a five-minute job.

---

## The email

**To**: `cvrselvbetjening@erst.dk`
**Subject**: Anmodning om system-til-system adgang til CVR-data

```
Kære Erhvervsstyrelsen

Jeg vil gerne anmode om system-til-system adgang til CVR-data via
distribution.virk.dk (Elasticsearch-løsningen).

Om mig:
Jeg er privatperson og henvender mig som privat bruger — ikke på vegne
af en virksomhed.

Navn:    Kasper Groes Ludvigsen
E-mail:  groes.ludvigsen@gmail.com
Telefon: [dit telefonnummer]
Adresse: [din adresse]

Formål:
Jeg udvikler et privat, ikke-kommercielt hobbyprojekt til eget brug: et
værktøj der hjælper mig med at finde et sommerhus på Sjælland, Lolland,
Falster og Møn. Værktøjet filtrerer boligannoncer efter afstand til
åbent vand samt til dagligvarebutikker og spisesteder.

Jeg har brug for CVR-data til at slå op, hvilke dagligvarebutikker,
restauranter og caféer der ligger i et givet postnummer, og på hvilken
adresse. Konkret har jeg brug for at kunne søge på produktionsenheder
filtreret på branchekode og postnummer.

Data anvendes udelukkende lokalt til eget brug. De bliver ikke
videresolgt, offentliggjort eller brugt til markedsføring eller
henvendelser af nogen art.

Jeg er indforstået med at underskrive erklæringen om vilkårene for
modtagelse af data om reklamebeskyttede enheder, og beder om at få den
tilsendt.

Sig endelig til, hvis I har brug for yderligere oplysninger.

Med venlig hilsen
Kasper Groes Ludvigsen
```

**Fill in before sending**: phone and address. Check the name is how you want it.

Want it in English instead? Say the word — they handle English fine, but Danish to a Danish
authority is the path of least friction.

---

## Context you may want

### Is a private individual eligible?

CVR data is public, and system-to-system access is free and not restricted to companies. The
email says plainly that you're a private person on a non-commercial project, which is the
honest framing and the one least likely to generate follow-up questions. If they come back
asking for a CVR number, just reply that you're a private individual — don't register a
company for this.

### What the declaration actually commits you to

Some entities in CVR are marked *reklamebeskyttet* — they've opted out of being contacted
for marketing. The declaration is your undertaking not to use their data for marketing or
unsolicited approaches.

For this project that's trivially satisfied: it reads names and addresses of shops to
compute distances, and contacts nobody. Sign it without concern. Just don't later reuse the
extract for a mailing list.

### Security: the endpoint appears to be HTTP-only

`http://distribution.virk.dk` answers (401, as expected without credentials);
`https://` gets a connection reset. The published documentation also describes it as HTTP
only — it's a legacy Elasticsearch 1.7.4 deployment from 2015.

If that holds, **your Basic-auth credentials travel in clear text on every request**. So:

- Use a password unique to this service. Never reuse one you use elsewhere.
- Treat the credentials as low-trust: they guard public data, so the exposure is small, but
  a reused password would not be.
- Worth one line in your reply asking whether an HTTPS endpoint is available — if there is
  one, we should use it.

### Don't send me the credentials

I don't need them and shouldn't have them. When they arrive:

```bash
export CVR_USERNAME='...'
export CVR_PASSWORD='...'
```

The client will read them from the environment. **Don't commit them** — not to
`config/thresholds.yaml`, not to a `.env` that's tracked. Tell me they've arrived and I'll
build against them; you run the first live query yourself.

### A privacy note on the data itself

CVR contains personal data where a business is a sole proprietorship — the owner's name, and
sometimes a home address used as the business address. This project only needs shop names
and addresses for distance maths, so keep the extract local, don't republish it, and don't
push a bulk dump to the repo. Fine for personal use; just don't turn it into a public
dataset.

### What I'll build once it lands

The swap sits behind the existing `resolve/cvr_match.py` seam — the same shape of change as
the address validator turned out to be, so `resolve/pipeline.py` shouldn't need touching.

Confirmed so far, without credentials:

| Thing | Value |
|---|---|
| Endpoint | `http://distribution.virk.dk/cvr-permanent` (answers 401, so host and index are right) |
| Auth | HTTP Basic |
| Index to use | `produktionsenhed` — physical sites, 2.79M records |
| Industry code | `VrproduktionsEnhed.hovedbranche.branchekode` |
| Postal code | `VrproduktionsEnhed.beliggenhedsadresse.postnummer` |
| Bulk paging | scroll API, above 3,000 results |

Use `produktionsenhed`, not `virksomhed`: a production unit (P-number) is a physical shop
with its own address, while a company (CVR number) is a legal entity. A supermarket chain is
one company and hundreds of shops, and this project cares about shops.

Industry codes to confirm against real data once access exists — these are DB07 codes I
expect to want, not yet verified:

| Code | Category |
|---|---|
| 471110 | Købmænd og døgnkiosker |
| 471120 | Supermarkeder |
| 471130 | Discountforretninger |
| 472200 | Slagter- og viktualieforretninger |
| 472300 | Fiskeforretninger |
| 472500 | Vinforretninger |
| 561010 | Restauranter |
| 563000 | Caféer, værtshuse, diskoteker |

### If it's refused or drags on

Two fallbacks, in order:

1. **Ask cvrapi.dk for a token.** They grant higher limits on request —
   <https://cvrapi.dk/contact>. Smaller change: the existing client already works, it just
   needs the cap lifted. Worth sending in parallel as a hedge.
2. **Datafordeleren** (<https://datafordeler.dk>) also distributes CVR, but needs a user
   account and service agreement — more setup, which is why it wasn't the first choice.

The project still runs without any of this. It just falls back to OSM POIs plus web search
for discovering candidate business names, which is what the plan originally assumed.
