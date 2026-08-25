import type { ClinicalSnapshot } from '@/lib/types';
import { Badge, Collapsible, DateText } from './ui';

/**
 * Records deliberately kept out of the clinical view.
 *
 * This panel exists so that suppression is never invisible. The most important
 * entry in the sample data is a creatinine of 14.7 mg/dL that the source system
 * marked entered-in-error: it must not sit beside real results, but "this
 * record contains a voided critical value" is itself something a reader needs
 * to know. Noteworthy items are listed first and flagged.
 */
export function WithheldPanel({ snapshot }: { snapshot: ClinicalSnapshot }) {
  const items = snapshot.suppressed;
  if (items.length === 0) return null;

  const noteworthy = items.filter((item) => item.is_noteworthy).length;

  return (
    <Collapsible
      tone="danger"
      summary={
        <>
          <strong>{items.length} record(s) withheld from the clinical view</strong>
          {noteworthy > 0 && (
            <Badge tone="danger" title="Includes a retracted record whose content would be clinically significant if it were real.">
              {noteworthy} needs attention
            </Badge>
          )}
        </>
      }
    >
      <p className="muted">
        These records are present in the source bundle but were not presented as
        current clinical fact. They are listed here rather than dropped so the
        suppression is auditable.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Section</th>
            <th>Record</th>
            <th>Status in source</th>
            <th>Why it was withheld</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.resource} className={item.is_noteworthy ? 'tr--flag' : undefined}>
              <td>{item.section}</td>
              <td>
                <div>{item.label}</div>
                <code>{item.resource}</code>
                {item.recorded && (
                  <div className="muted">
                    <DateText value={item.recorded} prefix="dated" />
                  </div>
                )}
              </td>
              <td>{item.status ?? '—'}</td>
              <td>{item.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Collapsible>
  );
}
