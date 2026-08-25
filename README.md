# Clinical Snapshot

A full-stack clinical snapshot built on a deliberately messy synthetic FHIR R4 bundle.

**Backend** — Python / FastAPI / Pydantic v2. Loads the bundle, models the resources,
runs a normalization pass, exposes a patient-summary endpoint.
**Frontend** — TypeScript / React / Vite. One scannable page that shows uncertainty
rather than hiding it.

The guiding rule throughout: **when the data is bad, say so — never guess, and never
silently drop.** Every record in the bundle ends up either displayed, or listed in a
"withheld" panel with a reason. Every human-readable label carries the provenance of
where that label came from.

---

## Running it

Two terminals. Python 3.10+ and Node 18+.

### 1. Backend

```bash
cd backend
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Verify: <http://127.0.0.1:8000/health> → `{"status":"ok", ..., "detail":"21 data-quality findings, 5 critical."}`
Interactive API docs: <http://127.0.0.1:8000/docs>

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>.

If the backend is not on port 8000, copy `frontend/.env.example` to
`frontend/.env.local` and set `VITE_API_BASE_URL`.

### Tests

```bash
cd backend  && python -m pytest       # 77 tests
cd frontend && npm run typecheck      # strict tsc, no emit
cd frontend && npm run smoke          # renders every panel and asserts the safety strings
```

The backend suite is the real specification: `tests/test_clinical_safety.py` encodes
each safety rule as a test named after the harm it prevents.

`npm run smoke` renders all nine panels to HTML against a captured API response
(`frontend/smoke/snapshot.json`) and asserts that the safety-critical output is
actually present — the identity caveat, the "unlabelled code" badge, the year-only
date flag, and that the voided creatinine value is *not* rendered as a result.
Regenerate the fixture with
`curl -s http://127.0.0.1:8000/api/patient-summary > frontend/smoke/snapshot.json`.

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/patient-summary` | The whole snapshot. What the UI consumes. |
| `GET /api/data-quality?severity=critical` | Just the issue ledger, filterable. |
| `GET /api/withheld` | Just the records kept out of the clinical view. |
| `GET /health` | Liveness + whether the bundle parsed. |

Add `?refresh=true` to re-read the bundle from disk. Point at a different bundle with
the `FHIR_BUNDLE_PATH` environment variable.

---

## What was wrong with the data, and what I did about it

The bundle has 17 entries. Here is every problem I found and the decision taken. The
running application reports all of this itself, in the "Data quality" panel.

### 1. Two Patient resources, and an active medication hanging off the second

`patient-001` and `patient-002` are almost certainly the same person: same family and
first given name, same administrative gender, compatible birth dates, the same street
address written two ways (`482 Larkspur Lane` / `482 Larkspur Ln`). But the MRNs
differ — `MRN-48213` vs `MRN-48213-A`.

`medicationrequest-003` is an **active** order attached to `patient-002`. Both easy
answers are unsafe:

- Show only `patient-001` → a clinician gets an incomplete medication list with no
  indication that anything is missing.
- Blindly merge → you may attribute another person's prescription to this patient.

**Decision:** score the match on weighted field agreement, link at ≥ 0.75, and mark
every item that arrived via the link. This pair scores 0.89 → `probable`.
`Confidence.CERTAIN` is reachable *only* via an identical identifier in the same
system, so two records that merely look alike stay `probable` forever no matter how
high the field agreement. The banner at the top of the page states the assumption; the
medication row carries a `linked record` badge; the conflict table shows every field
the two records disagreed about, with the value chosen and why.

Where a candidate record cannot be linked (e.g. incompatible birth dates), its clinical
records are listed as **withheld**, not merged and not silently dropped.

### 2. Retracted records presented as clinical fact

Four resources are marked `entered-in-error`. The most dangerous is
`observation-004`: **creatinine 14.7 mg/dL**, a value that if real means dialysis
today. The source system voided it.

**Decision:** retracted records never appear as clinical content — not greyed out
beside real results, where they could still be acted on. They go to a "withheld"
panel with the reason and the source status. Suppression is *visible*, because "this
record contains a voided critical result" is itself something a reader needs to know.
That one is flagged `needs attention`.

`condition-002` is both `inactive` **and** `entered-in-error`. Retraction wins:
listing it under "resolved problems" would assert the patient once had asthma.
Not-current (a `stopped` prescription, a `resolved` problem) is different — that is
true history, and it gets its own clearly-labelled section.

Unrecognised statuses **fail closed** into not-current. Treating an unknown status as
"probably fine" is how a discontinued drug ends up on an active medication list.

### 3. Codings with no display text

Three codes arrive with no `display`: ICD-10-CM `E11.9`, LOINC `4548-4`, SNOMED
`91936005`; RxNorm `849574` likewise.

There is one dishonest way to fill that gap, which is to let a language model write a
plausible label. So:

- Source `display` → used, marked `source`.
- Otherwise a **small, hand-verified table** in `normalize/terminology.py` → marked
  `local_table`, and the UI shows a "label resolved locally" badge. Adding a row there
  is a clinical-safety change, not a convenience.
- Otherwise the code is rendered *as a code*: `RxNorm 849574`, monospaced, with an
  `unlabelled code` badge.

I deliberately left `849574` unresolved rather than guess it. An unlabelled medication
row is a visible gap a clinician will chase. A *wrongly* labelled one is a medication
error.

### 4. A code filed under the wrong system

Not in the brief's list. `allergyintolerance-001` declares `http://snomed.info/sct`
but carries the code `7980-2`. SNOMED concept ids are 6–18 digits with no punctuation;
`7980-2` has the shape of a LOINC code (and `7980` is the RxNorm ingredient code for
penicillin G). Something upstream crossed wires.

**Decision:** validate code shape against its declared system, flag the mismatch on
the row (`coding issue` badge, with the detail in the tooltip), keep the sender's own
display text, and record it in the ledger. Silently trusting the coding would corrupt
anything downstream that keys on codes.

### 5. The same allergy recorded twice, with conflicting severity

`allergyintolerance-001` — "Penicillin", `confirmed`, criticality **high**.
`allergyintolerance-003` — SNOMED `91936005` (allergy to penicillin), `unconfirmed`,
criticality `unable-to-assess`, no display text.

Same allergen, two records, opposite confidence. A naive "last record wins" dedup
would downgrade a confirmed high-risk penicillin allergy to "risk not assessed" —
a genuine safety defect.

**Decision:** merge into one row, and **escalate only**: keep the highest criticality
and the strongest verification status, note that a second weaker record exists, and
credit both source resources in the provenance line. Deduplication never discards a
source record and never lowers a criticality.

Note that this dedup *depends* on resolving `91936005`, which is exactly why the
curated table exists. A production system needs a real terminology service here.

### 6. Inconsistent date precision

`birthDate` is `1958-03-12` on one record and `1958` on the other; onsets and
`authoredOn` range from full instants to bare years.

Parsing `"1958"` into a `datetime` invents 1 January, which then produces a
confidently wrong age. So dates are modelled as `PartialDateTime`: the raw string, the
detected precision, a sort key used *only* for ordering, and a display string that
never implies more precision than the source had. `AgeEstimate` returns **67–68 y**
from a year-only birth date, and an exact age only from a day-precision one.

A related subtlety, also unprompted: `2021-06-02T00:00:00Z` is flagged
`midnight_utc_padded`. Midnight UTC is almost always a date that a sending system
padded into an instant, so it renders as `02 Jun 2021` rather than implying a
midnight event — and encounter durations are never computed from a padded timestamp.

Unparseable dates are kept verbatim, flagged, and sorted to the far past so they can
never win a "most recent value" comparison.

### 7. References to resources that are not in the bundle

`condition-003` → `Encounter/encounter-099`; `observation-003` →
`Practitioner/practitioner-999`. Both are reported on the row itself ("Linked
encounter Encounter/encounter-099 is not in this extract"), not just in a log.

### 8. Aged data shown as if current

The HbA1c of 6.1% is dated `2020` — around seven years before this view. It is real
data and worth seeing, so it is displayed, muted, badged `historical`, and captioned
with its age.

### 9. PHI that a snapshot has no need for

`patient-001` carries a US SSN identifier. It is **not returned by the API at all** —
the demographics section reports only that an SSN was present in the source and
withheld. A test asserts the digits never appear in the serialized response.

### 10. US Core extensions

`us-core-race` and `us-core-ethnicity` are parsed from the nested `ombCategory` /
`text` sub-extensions and shown, labelled as US Core. Conformance profiles from
`meta.profile` are carried through.

---

## Key design decisions

**One place for the safety policy.** `normalize/rules.py` holds every "is this current
clinical fact?" decision as a named set. The section builders reference it; they do not
re-implement status checks. That makes the policy reviewable in a single screen and
testable without constructing a whole snapshot.

**The frontend needs zero clinical knowledge.** Every judgement — is this current, is
this label trustworthy, is this date precise, did this arrive via a probabilistic
identity link — is already made in the backend and carried as an explicit field. The UI
renders flags; it never re-derives them. The safety logic lives in one tested place
instead of being duplicated across two languages.

**Nothing is silently dropped.** Suppression produces a `SuppressedItem` *and* a ledger
entry. `test_nothing_is_silently_dropped` asserts every clinical resource in the bundle
is either displayed or explicitly accounted for.

**The loader is tolerant, the policy is strict.** One malformed entry must not cost the
other sixteen, so each entry validates independently and failures become data-quality
issues. But a resource that parses is still held to the full safety policy.

**No clinical interpretation.** Values are shown in the unit the source sent, with no
conversion, no reference ranges and no high/low flags. Deciding that 138/88 is "high"
is clinical decision support: it needs validated thresholds, patient context, and its
own review process. Inventing it in a summary view would be out of scope and unsafe.
A test enforces this.

**Absence is not negation.** An empty allergy list renders as "**Allergy status
unknown** — no current allergy record was found; that is not the same as 'no known
allergies'". Only an explicit SNOMED negation concept licenses the positive statement,
and the API tracks the two states as separate fields so the UI cannot conflate them.

**No-JS-first UI.** The collapsible audit panels are native `<details>` elements, which
keeps them keyboard-accessible and printable. Colour carries exactly one meaning —
severity — so a coloured element always signals that something matters.

---

## Tradeoffs accepted under the time cap

- **The curated code table is tiny and hand-maintained.** It resolves 12 concepts. The
  right answer is a terminology service (or a bundled subset of RxNorm / LOINC / SNOMED
  with version pinning). The `LabelSource` enum already models where a label came from,
  so swapping in a real resolver is a change behind one function.
- **Identity matching is a hand-tuned weighted score.** The weights are defensible but
  not calibrated against a labelled dataset, and there is no blocking strategy — it is
  O(n²) over Patient resources, which is fine for one bundle and wrong for a population.
- **Single-patient, single-bundle, read-only.** The bundle is parsed once and cached in
  process. No persistence, no auth, no multi-tenancy, no pagination.
- **Allergen deduplication uses a text heuristic** ("Penicillin" ≈ "Allergy to
  penicillin") alongside the code alias table. It is documented as a heuristic, and it
  is safe by construction — grouping never discards a record and never lowers a
  criticality — but it would mis-group in edge cases a real terminology hierarchy would
  handle correctly.
- **LOINC display text is used verbatim**, so the blood pressure panel shows "Blood
  pressure panel with all children optional". Ugly for a clinician, but substituting my
  own short name would mean overriding the sender's own words, which is the thing this
  application is otherwise careful not to do. A curated *display-name* layer (distinct
  from the label-resolution layer) would be the clean fix.
- **No frontend component tests.** Type-checking plus the backend suite; the UI was
  verified by running it. React Testing Library coverage of the uncertainty badges is
  the first thing I would add.
- **Staleness threshold is a flat 365 days** for every observation type. Clinically it
  should be per-concept — a 6-month-old HbA1c and a 6-month-old blood pressure are not
  equally stale.

## What I would do next

1. **A real terminology service** behind the `resolve_label` interface, with the
   `local_table` path kept as an offline fallback and version-stamped in the response.
2. **Reference ranges and abnormal-value flagging**, done properly: validated
   thresholds, age/sex context, and displayed as "outside reference range per <source>"
   rather than as a bare colour.
3. **Deterministic, auditable identity decisions.** Persist the match score and the
   fields compared, expose an endpoint to override a link, and never re-derive a
   previously human-reviewed decision silently.
4. **Per-concept recency policy** driven by a config table rather than one constant.
5. **Frontend tests** for each uncertainty state, plus an accessibility pass (the audit
   tables need proper caption/scope markup and the colour tokens need a contrast audit
   in high-contrast mode).
6. **`Bundle.total` mismatch handling.** Currently reported as a warning; a paged
   bundle should be followed via `Bundle.link.next` rather than silently truncated.
7. **Provenance and `meta.security` support**, so restricted records can be withheld
   for confidentiality reasons with the same visible-suppression mechanism.

---

## Layout

```
backend/
  app/
    fhir/          primitives.py (precision-aware dates), resources.py, bundle.py (tolerant loader)
    models/        issues.py (the data-quality ledger), summary.py (the API contract)
    normalize/     rules.py      <- the entire clinical-safety policy
                   terminology.py <- label resolution + provenance + code-shape validation
                   identity.py    <- patient matching and demographic merge
                   problems.py medications.py allergies.py observations.py encounters.py
                   pipeline.py    <- orchestration
    main.py services.py config.py
  tests/           test_clinical_safety.py, test_units.py, test_api.py
frontend/
  src/lib/         types.ts (mirror of the API contract), api.ts
  src/components/  one panel per section + ui.tsx primitives
  src/App.tsx styles.css
data/              scenario1_fhir_bundle.json
AI_USAGE.md
```

**Synthetic data. Not for clinical use.**
