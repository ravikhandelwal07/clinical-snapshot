import type { ClinicalSnapshot, Problem } from '@/lib/types';
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

export function ProblemPanel({ snapshot }: { snapshot: ClinicalSnapshot }) {
  const { active, inactive } = snapshot.problems;
  const coverage = snapshot.coverage.problems;

  return (
    <SectionCard
      title="Active problems"
      count={
        <CoverageNote
          displayed={coverage?.displayed ?? active.length + inactive.length}
          suppressed={coverage?.suppressed ?? 0}
          noun="conditions"
        />
      }
    >
      {active.length === 0 && (
        <EmptyState>
          No condition in this extract is recorded as active. The problem list may
          simply not have been sent — treat it as unknown, not empty.
        </EmptyState>
      )}

      {active.map((problem) => (
        <ProblemRow key={problem.provenance.resource} problem={problem} />
      ))}

      {inactive.length > 0 && (
        <>
          <h3 className="subhead">Resolved / inactive</h3>
          {inactive.map((problem) => (
            <ProblemRow key={problem.provenance.resource} problem={problem} muted />
          ))}
        </>
      )}
    </SectionCard>
  );
}

function ProblemRow({ problem, muted = false }: { problem: Problem; muted?: boolean }) {
  return (
    <article className={muted ? 'row row--muted' : 'row'}>
      <div className="row__main">
        <ConceptLabel concept={problem.concept} className="row__title" />
        <div className="row__badges">
          <StatusChip status={problem.clinical_status} />
          <StatusChip status={problem.verification_status} />
        </div>
      </div>
      <div className="row__meta">
        <DateText value={problem.onset} prefix="onset" fallback="onset not recorded" />
        {problem.recorded && (
          <>
            {' · '}
            <DateText value={problem.recorded} prefix="recorded" />
          </>
        )}
      </div>
      <NoteList notes={problem.notes} />
      <ProvenanceLine provenance={problem.provenance} />
    </article>
  );
}
