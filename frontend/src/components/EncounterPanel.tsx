import type { ClinicalSnapshot } from '@/lib/types';
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

export function EncounterPanel({ snapshot }: { snapshot: ClinicalSnapshot }) {
  const encounters = snapshot.encounters;
  const coverage = snapshot.coverage.encounters;

  return (
    <SectionCard
      title="Recent encounters"
      count={
        <CoverageNote
          displayed={coverage?.displayed ?? encounters.length}
          suppressed={coverage?.suppressed ?? 0}
          noun="visits"
        />
      }
    >
      {encounters.length === 0 && (
        <EmptyState>No valid encounter records in this extract.</EmptyState>
      )}
      {encounters.map((encounter) => (
        <article key={encounter.provenance.resource} className="row">
          <div className="row__main">
            {encounter.type ? (
              <ConceptLabel concept={encounter.type} className="row__title" />
            ) : (
              <span className="row__title muted">Visit type not recorded</span>
            )}
            <div className="row__badges">
              {encounter.encounter_class && (
                <span className="muted">{encounter.encounter_class}</span>
              )}
              <StatusChip status={encounter.status} />
            </div>
          </div>
          <div className="row__meta">
            <DateText value={encounter.start} fallback="start not recorded" />
            {encounter.end && (
              <>
                {' → '}
                <DateText value={encounter.end} />
              </>
            )}
            {encounter.duration_minutes !== null && (
              <span className="muted"> · {encounter.duration_minutes} min</span>
            )}
          </div>
          <NoteList notes={encounter.notes} />
          <ProvenanceLine provenance={encounter.provenance} />
        </article>
      ))}
    </SectionCard>
  );
}
