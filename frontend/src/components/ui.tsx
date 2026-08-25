/**
 * Shared display primitives.
 *
 * All presentational, no state. The collapsible panels use native `<details>`
 * rather than a state hook, which keeps them keyboard-accessible, printable and
 * expandable with no JavaScript of our own.
 */

import type { ReactNode } from 'react';

import type {
  CodedConcept,
  PartialDateTime,
  Provenance,
  StatusBadge,
  Tone,
} from '@/lib/types';

export function Badge({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode;
  tone?: Tone | 'info';
  title?: string;
}) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {children}
    </span>
  );
}

export function StatusChip({ status }: { status: StatusBadge }) {
  if (!status.label) return null;
  return (
    <Badge
      tone={status.tone}
      title={status.is_current ? undefined : 'Not current clinical fact'}
    >
      {status.label}
    </Badge>
  );
}

/**
 * Renders a concept and, critically, where its human-readable label came from.
 * An unresolved code is shown as a code -- never dressed up as a name.
 */
export function ConceptLabel({
  concept,
  className,
}: {
  concept: CodedConcept;
  className?: string;
}) {
  const unresolved = concept.label_is_unresolved;
  return (
    <span className={className}>
      <span className={unresolved ? 'concept concept--unresolved' : 'concept'}>
        {concept.text}
      </span>
      {unresolved && (
        <Badge tone="caution" title="The source provided no display text for this code and it is not in the application's curated code table. No label was inferred.">
          unlabelled code
        </Badge>
      )}
      {concept.label_source === 'local_table' && (
        <Badge tone="info" title="The source provided no display text. This label comes from the application's curated code table, not from the sending system.">
          label resolved locally
        </Badge>
      )}
      {concept.warnings.map((warning) => (
        <Badge key={warning} tone="caution" title={warning}>
          coding issue
        </Badge>
      ))}
      {concept.code && (
        <span className="code" title={concept.system_uri ?? undefined}>
          {concept.system_name ?? 'code'} {concept.code}
        </span>
      )}
    </span>
  );
}

/** A date shown at exactly the precision the source recorded. */
export function DateText({
  value,
  prefix,
  fallback = 'date not recorded',
}: {
  value: PartialDateTime | null;
  prefix?: string;
  fallback?: string;
}) {
  if (!value) {
    return <span className="muted">{fallback}</span>;
  }
  return (
    <span className="date">
      {prefix && <span className="muted">{prefix} </span>}
      {value.display}
      {value.is_imprecise && (
        <Badge tone="caution" title={value.precision_note ?? undefined}>
          {value.precision === 'unknown' ? 'unparseable' : `${value.precision} only`}
        </Badge>
      )}
    </span>
  );
}

export function NoteList({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return (
    <ul className="notes">
      {notes.map((note) => (
        <li key={note}>{note}</li>
      ))}
    </ul>
  );
}

export function ProvenanceLine({ provenance }: { provenance: Provenance }) {
  return (
    <div className="provenance">
      <span>{provenance.resource}</span>
      {provenance.merged_from.length > 0 && (
        <span> + {provenance.merged_from.join(', ')}</span>
      )}
      {provenance.via_linked_identity && (
        <Badge tone="caution" title="This record was attached to a different Patient resource that we linked to this patient with a probable — not certain — match.">
          linked record
        </Badge>
      )}
    </div>
  );
}

export function SectionCard({
  title,
  subtitle,
  tone = 'neutral',
  count,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  tone?: 'neutral' | 'danger';
  count?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`card card--${tone}`}>
      <header className="card__header">
        <h2>{title}</h2>
        {count !== undefined && <span className="card__count">{count}</span>}
      </header>
      {subtitle && <p className="card__subtitle">{subtitle}</p>}
      <div className="card__body">{children}</div>
    </section>
  );
}

/**
 * Empty states state what is unknown, never what is absent. "No records" and
 * "none" are different claims and the distinction can matter clinically.
 */
export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Collapsible({
  summary,
  children,
  open = false,
  tone = 'neutral',
}: {
  summary: ReactNode;
  children: ReactNode;
  open?: boolean;
  tone?: 'neutral' | 'danger';
}) {
  return (
    <details className={`collapsible collapsible--${tone}`} open={open}>
      <summary>{summary}</summary>
      <div className="collapsible__body">{children}</div>
    </details>
  );
}

export function CoverageNote({
  displayed,
  suppressed,
  noun,
}: {
  displayed: number;
  suppressed: number;
  noun: string;
}) {
  if (suppressed === 0) {
    return (
      <span className="coverage">
        {displayed} {noun} in source
      </span>
    );
  }
  return (
    <span className="coverage coverage--warn">
      {displayed} shown · {suppressed} withheld
    </span>
  );
}
