# AI usage

> Reviewer note: this file describes the AI-assisted session that produced this
> repository. If you are the submitting candidate, edit the specifics to match your own
> session before submitting — the corrections below are the ones that actually happened
> here and are worth keeping only if they are also true for you.

## Tool and model

- **Claude Code** (CLI / IDE extension), model **Claude Opus 5**.
- Used for: the whole implementation, plus domain reading on FHIR R4 status semantics
  (`clinicalStatus` vs `verificationStatus`, `entered-in-error` semantics, US Core
  race/ethnicity extension shape, `criticality` value set).
- No other model provider was used.

## Where it accelerated me

- **Boilerplate with a lot of surface area.** The Pydantic resource models, the
  `CodeableConcept` / `Reference` / `Quantity` plumbing, the alias handling for
  `resourceType` / `postalCode` / `medicationCodeableConcept`. Mechanical, tedious,
  easy to get subtly wrong by hand.
- **Test scaffolding.** Turning each safety rule I had decided on into a named test was
  fast. The 77 tests exist because writing them was cheap, and they immediately paid
  for themselves — the `midnight_utc_padded` behaviour and the allergy-merge escalation
  were both nailed down by tests before I trusted the code.
- **Domain lookup.** Confirming which FHIR statuses mean "retracted" versus "not
  current" was much faster than reading the spec end to end. I verified the important
  ones against the published value sets rather than taking them on trust.
- **CSS and layout.** The visual hierarchy — allergies loudest, one meaning per colour,
  audit panels collapsed — I specified; the model wrote the CSS.

## Where I overrode, corrected or rejected its output

These are the substantive ones. Each is a case where the fluent first answer was wrong
in a way that mattered clinically.

### 1. It wanted to fill in missing code displays from its own knowledge — rejected

The first pass at handling codings with no `display` was, in effect, "the model knows
what these codes mean, so write the label." It confidently produced a name for RxNorm
`849574`.

I rejected this outright. An LLM-generated medication name in a clinical summary is a
medication error waiting to happen, and it is *invisible* — it looks exactly like a
label the sending system provided. What I built instead:

- a `LabelSource` enum (`source` / `local_table` / `code_only` / `absent`) that travels
  with every label to the UI;
- a small **hand-verified** table for concepts I could check against the published code
  system (12 entries);
- `849574` **deliberately left unresolved**, rendered as the monospaced code
  `RxNorm 849574` with an "unlabelled code" badge.

The gap is now visible. A clinician will look it up. That is the correct outcome.

### 2. It de-escalated a confirmed penicillin allergy during deduplication — corrected

The bundle records penicillin twice: `confirmed` / criticality **high**, and
`unconfirmed` / `unable-to-assess`. The first dedup implementation was a dictionary
keyed by allergen where the later record overwrote the earlier one. Correct-looking,
passed a naive "no duplicate rows" check — and it silently turned a confirmed
high-risk penicillin allergy into "risk not assessed."

I rewrote the merge to be **escalate-only**: take the maximum criticality, take the
strongest verification status, keep both source resource ids in the provenance, and
note on the row that a second weaker record exists. `rules.ALLERGY_CRITICALITY_RANK`
exists purely to make "never lower this" expressible. Two tests guard it.

This is the one I would flag hardest in review. The bug was not a crash or a type
error; it was a plausible implementation that lost safety-critical information, and
nothing but domain reasoning would have caught it.

### 3. It parsed FHIR partial dates into `datetime` — corrected

The initial models had `birth_date: date`. Pydantic happily coerces `"1958"`… and
`patient-002`'s year-only birth date became 1 January 1958, which produced a
confident, wrong age.

I replaced it with `PartialDateTime`, which keeps the raw string, the detected
precision, and a sort key used *only* for ordering — plus `AgeEstimate`, which returns
**67–68 y** from a year-only birth date instead of a single number it cannot justify.
I also added `midnight_utc_padded` detection (a `T00:00:00Z` value is a date a sender
padded, not a midnight event) and made unparseable dates sort to the far past so they
can never win a "most recent value" comparison. The model had not raised any of this.

### 4. It filtered `entered-in-error` records out with a list comprehension — corrected

Functionally right — those records must not appear as clinical fact — but it made the
suppression invisible. The voided creatinine of 14.7 mg/dL simply vanished.

I changed "filter" to "withhold, visibly": suppressed records produce a
`SuppressedItem` *and* a ledger entry, and the UI shows them in a collapsed panel with
the reason and the source status. "This record contains a voided critical result" is
itself clinically relevant. `test_nothing_is_silently_dropped` now asserts every
clinical resource in the bundle is either displayed or explicitly accounted for.

### 5. It merged the two Patient records without qualification — corrected

The first identity implementation matched the two Patient resources and merged them,
full stop. The MRNs are *different* (`MRN-48213` vs `MRN-48213-A`), and an **active**
medication order hangs off the second record.

I made the confidence level a first-class output and capped it: `CERTAIN` is reachable
only via an identical identifier in the same system, so this pair is permanently
`PROBABLE` at 0.89 no matter how many other fields agree. The active medication is
included — dropping it would hand a clinician an incomplete list with no warning — but
every item arriving via the link carries `via_linked_identity`, the row shows a "linked
record" badge, and the page opens with a banner stating the assumption and a table of
every field the two records disagreed about.

### 6. It offered to add reference ranges and high/low colouring — rejected

138/88 mmHg, and the suggestion was to flag it. Tempting, and it would have looked
impressive.

I declined. Abnormal-value flagging is clinical decision support: it needs validated
thresholds, age and sex context, and its own review process. Hand-rolling it in a
summary view is out of scope and unsafe. Values are shown in the source's own units,
unconverted, with interpretation left to the clinician — and a test asserts no
interpretive language leaks into the observation payload.

### 7. Smaller corrections

- **`condition-002` is both `inactive` and `entered-in-error`.** The draft bucketed it
  as resolved history. Listing it there asserts the patient once had asthma. Retraction
  outranks inactivity.
- **Unknown statuses were treated as active.** Changed to fail closed into
  not-current. An unrecognised status treated as "probably fine" is how a discontinued
  drug reaches an active medication list.
- **An allergy with no `clinicalStatus` was dropped.** Changed to fail *open* — treated
  as possibly active and flagged. Note the direction is opposite to the medication case,
  and deliberately so: over-warning about an allergy is the safe error, whereas
  over-listing a medication is not.
- **`system_mismatch_warning` was not suggested at all.** Noticing that SNOMED
  `7980-2` is not a SNOMED concept id was mine; the model wrote the regex table once I
  asked for shape validation per code system.
- **Empty allergy list rendered as "No known allergies."** Corrected to "Allergy status
  unknown", with `no_known_allergies_asserted` as a separate field so the UI cannot
  conflate absence of records with a documented negation.

## Honest summary of the split

The model wrote most of the lines. The decisions that make this submission defensible —
what counts as retracted, what may never be inferred, which direction each rule should
fail in, that suppression must stay visible, that an identity match can be probable
forever — were mine, and in four of the seven cases above they were corrections against
a fluent and plausible first draft. Fluency is not judgement, and on this dataset the
plausible answer was the dangerous one more often than not.
