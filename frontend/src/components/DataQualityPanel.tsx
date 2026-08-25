import type { ClinicalSnapshot, DataIssue, IssueSeverity } from '@/lib/types';
import { Badge, Collapsible } from './ui';

const SEVERITY_TONE: Record<IssueSeverity, 'danger' | 'caution' | 'info'> = {
  critical: 'danger',
  warning: 'caution',
  info: 'info',
};

const CATEGORY_LABELS: Record<string, string> = {
  identity: 'Patient identity',
  suppressed_status: 'Retracted by source',
  unresolved_code: 'Unresolved coding',
  code_system_mismatch: 'Coding system mismatch',
  dangling_reference: 'Missing referenced record',
  date_precision: 'Date precision',
  duplicate: 'Duplicate record',
  stale_data: 'Aged data',
  phi_minimization: 'Data minimisation',
  missing_data: 'Missing data',
  parse_failure: 'Could not parse',
  bundle_integrity: 'Bundle integrity',
  unsupported_resource: 'Not summarised',
  orphaned_resource: 'Unattributed record',
};

export function DataQualityPanel({ snapshot }: { snapshot: ClinicalSnapshot }) {
  const issues = snapshot.data_quality;
  const counts = issues.reduce<Record<string, number>>((accumulator, issue) => {
    accumulator[issue.severity] = (accumulator[issue.severity] ?? 0) + 1;
    return accumulator;
  }, {});

  const grouped = issues.reduce<Record<string, DataIssue[]>>((accumulator, issue) => {
    (accumulator[issue.category] ??= []).push(issue);
    return accumulator;
  }, {});

  return (
    <Collapsible
      summary={
        <>
          <strong>Data quality — {issues.length} finding(s)</strong>
          {(['critical', 'warning', 'info'] as IssueSeverity[]).map((severity) =>
            counts[severity] ? (
              <Badge key={severity} tone={SEVERITY_TONE[severity]}>
                {counts[severity]} {severity}
              </Badge>
            ) : null,
          )}
        </>
      }
    >
      <p className="muted">
        Everything the normalization pass changed, withheld, merged or could not
        resolve. Each finding names the source record and what was done about it.
      </p>
      {Object.entries(grouped).map(([category, categoryIssues]) => (
        <div key={category} className="issuegroup">
          <h4>
            {CATEGORY_LABELS[category] ?? category} ({categoryIssues.length})
          </h4>
          <ul className="issues">
            {categoryIssues.map((issue, index) => (
              <li key={`${issue.resource}-${index}`}>
                <Badge tone={SEVERITY_TONE[issue.severity]}>{issue.severity}</Badge>{' '}
                {issue.message}
                {issue.action && (
                  <div className="issues__action">
                    <span className="muted">Action taken: </span>
                    {issue.action}
                  </div>
                )}
                {(issue.resource || issue.field) && (
                  <div className="provenance">
                    {issue.resource}
                    {issue.field && <span> · {issue.field}</span>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Collapsible>
  );
}
