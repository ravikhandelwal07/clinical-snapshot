import type { ClinicalSnapshot, Medication } from '@/lib/types';
import {
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
 * Current and past are separate lists with separate headings, never one list
 * distinguished only by a colour: a stopped drug read as current is a
 * re-prescription risk.
 */
export function MedicationPanel({ snapshot }: { snapshot: ClinicalSnapshot }) {
  const { current, past } = snapshot.medications;
  const coverage = snapshot.coverage.medications;

  return (
    <SectionCard
      title="Medications"
      count={
        <CoverageNote
          displayed={coverage?.displayed ?? current.length + past.length}
          suppressed={coverage?.suppressed ?? 0}
          noun="orders"
        />
      }
    >
      <h3 className="subhead">Current ({current.length})</h3>
      {current.length === 0 ? (
        <EmptyState>
          No active medication order in this extract. Absence of orders is not
          evidence the patient is on nothing.
        </EmptyState>
      ) : (
        current.map((medication) => (
          <MedicationRow key={medication.provenance.resource} medication={medication} />
        ))
      )}

      {past.length > 0 && (
        <>
          <h3 className="subhead">Not current ({past.length})</h3>
          {past.map((medication) => (
            <MedicationRow
              key={medication.provenance.resource}
              medication={medication}
              muted
            />
          ))}
        </>
      )}
    </SectionCard>
  );
}

function MedicationRow({
  medication,
  muted = false,
}: {
  medication: Medication;
  muted?: boolean;
}) {
  return (
    <article className={muted ? 'row row--muted' : 'row'}>
      <div className="row__main">
        <ConceptLabel concept={medication.concept} className="row__title" />
        <div className="row__badges">
          <StatusChip status={medication.status} />
        </div>
      </div>
      <div className="row__meta">
        {medication.dosage_text ?? <span className="muted">dose not recorded</span>}
        {' · '}
        <DateText
          value={medication.authored_on}
          prefix="ordered"
          fallback="order date not recorded"
        />
      </div>
      <NoteList notes={medication.notes} />
      <ProvenanceLine provenance={medication.provenance} />
    </article>
  );
}
