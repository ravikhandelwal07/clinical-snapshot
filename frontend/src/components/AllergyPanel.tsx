import type { Allergy, ClinicalSnapshot } from '@/lib/types';
import {
  Badge,
  ConceptLabel,
  CoverageNote,
  DateText,
  EmptyState,
  NoteList,
  ProvenanceLine,
  SectionCard,
  StatusChip,
} from './ui';

/**
 * Allergies come first and are visually loudest -- it is the one section where
 * a miss can be immediately fatal.
 *
 * Note the empty state: it says allergy status is *unknown*, not "none". The
 * backend tracks those separately (`no_known_allergies_asserted`) and only an
 * explicit negation in the source licenses "no known allergies".
 */
export function AllergyPanel({ snapshot }: { snapshot: ClinicalSnapshot }) {
  const { active, inactive, no_known_allergies_asserted } = snapshot.allergies;
  const coverage = snapshot.coverage.allergies;

  return (
    <SectionCard
      title="Allergies & intolerances"
      tone="danger"
      count={
        <CoverageNote
          displayed={coverage?.displayed ?? active.length + inactive.length}
          suppressed={coverage?.suppressed ?? 0}
          noun="records"
        />
      }
    >
      {active.length === 0 && (
        no_known_allergies_asserted ? (
          <p className="assert-none">
            Source explicitly documents <strong>no known allergies</strong>.
          </p>
        ) : (
          <EmptyState>
            <strong>Allergy status unknown.</strong> No current allergy record was
            found in this extract. That is not the same as “no known allergies” —
            nothing in the source asserts the absence of allergies.
          </EmptyState>
        )
      )}

      {active.map((allergy) => (
        <AllergyRow key={allergy.provenance.resource} allergy={allergy} />
      ))}

      {inactive.length > 0 && (
        <>
          <h3 className="subhead">Resolved / inactive</h3>
          {inactive.map((allergy) => (
            <AllergyRow key={allergy.provenance.resource} allergy={allergy} muted />
          ))}
        </>
      )}
    </SectionCard>
  );
}

function AllergyRow({ allergy, muted = false }: { allergy: Allergy; muted?: boolean }) {
  const criticalityTone =
    allergy.criticality === 'high'
      ? 'danger'
      : allergy.criticality === 'low'
        ? 'neutral'
        : 'caution';

  return (
    <article className={muted ? 'row row--muted' : 'row row--alert'}>
      <div className="row__main">
        <ConceptLabel concept={allergy.concept} className="row__title" />
        <div className="row__badges">
          <Badge tone={criticalityTone}>{allergy.criticality_label}</Badge>
          <StatusChip status={allergy.clinical_status} />
          <StatusChip status={allergy.verification_status} />
        </div>
      </div>
      <div className="row__meta">
        <DateText value={allergy.recorded} prefix="recorded" fallback="date not recorded" />
        {allergy.reactions.length > 0 && (
          <span> · reaction: {allergy.reactions.join('; ')}</span>
        )}
      </div>
      <NoteList notes={allergy.notes} />
      <ProvenanceLine provenance={allergy.provenance} />
    </article>
  );
}
