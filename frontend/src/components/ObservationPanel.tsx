import type { ClinicalSnapshot, ObservationSummary } from '@/lib/types';
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
 * Vitals and results.
 *
 * Deliberately no high/low colouring and no reference ranges. Flagging a value
 * as abnormal is clinical decision support: it needs validated thresholds and
 * patient context, and inventing it in a summary view would be both out of
 * scope and unsafe. Values are shown in the unit the source sent, unconverted.
 */
export function ObservationPanel({ snapshot }: { snapshot: ClinicalSnapshot }) {
  const { vitals, labs, other } = snapshot.observations;
  const coverage = snapshot.coverage.observations;
  const total = vitals.length + labs.length + other.length;

  return (
    <SectionCard
      title="Vitals & results"
      subtitle="Values are shown exactly as recorded. No reference ranges or high/low flags are applied — interpretation is left to the clinician."
      count={
        <CoverageNote
          displayed={coverage?.displayed ?? total}
          suppressed={coverage?.suppressed ?? 0}
          noun="observations"
        />
      }
    >
      {total === 0 && <EmptyState>No observations in this extract.</EmptyState>}

      {vitals.length > 0 && (
        <>
          <h3 className="subhead">Vital signs</h3>
          {vitals.map((observation) => (
            <ObservationRow
              key={observation.provenance.resource}
              observation={observation}
            />
          ))}
        </>
      )}

      {labs.length > 0 && (
        <>
          <h3 className="subhead">Results</h3>
          {labs.map((observation) => (
            <ObservationRow
              key={observation.provenance.resource}
              observation={observation}
            />
          ))}
        </>
      )}

      {other.length > 0 && (
        <>
          <h3 className="subhead">Other observations</h3>
          {other.map((observation) => (
            <ObservationRow
              key={observation.provenance.resource}
              observation={observation}
            />
          ))}
        </>
      )}
    </SectionCard>
  );
}

function ObservationRow({ observation }: { observation: ObservationSummary }) {
  return (
    <article className={observation.is_historical ? 'row row--muted' : 'row'}>
      <div className="row__main">
        <ConceptLabel concept={observation.concept} className="row__title" />
        <div className="row__badges">
          <span className="value">
            {observation.value ?? <span className="muted">no value recorded</span>}
          </span>
          {observation.is_historical && (
            <Badge tone="caution" title="Older than 12 months — not a current measurement.">
              historical
            </Badge>
          )}
          {!observation.status.is_current && <StatusChip status={observation.status} />}
        </div>
      </div>

      {observation.components.length > 0 && observation.value === null && (
        <ul className="components">
          {observation.components.map((component) => (
            <li key={`${component.code}-${component.text}`}>
              {component.label ?? <span className="muted">unlabelled component</span>}:{' '}
              <strong>{component.text}</strong>
            </li>
          ))}
        </ul>
      )}

      <div className="row__meta">
        <DateText value={observation.effective} fallback="no date recorded" />
        {observation.age_text && <span className="muted"> · {observation.age_text}</span>}
      </div>
      <NoteList notes={observation.notes} />
      <ProvenanceLine provenance={observation.provenance} />
    </article>
  );
}
