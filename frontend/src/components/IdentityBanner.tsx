import type { IdentityResolution } from '@/lib/types';
import { Collapsible } from './ui';

/**
 * The identity caveat sits above the clinical content, not buried in a footer.
 *
 * If the snapshot is a composite of two Patient records that we matched
 * probabilistically, the reader needs to know that before they read the
 * medication list -- because one of those medications only appears because of
 * the match.
 */
export function IdentityBanner({ identity }: { identity: IdentityResolution }) {
  if (!identity.requires_review) {
    return null;
  }

  const tone = identity.unlinked_resources.length > 0 ? 'danger' : 'warn';

  return (
    <div className={`alert alert--${tone}`} role="status">
      <div className="alert__title">
        {identity.unlinked_resources.length > 0
          ? 'Patient identity unresolved'
          : 'Patient identity is a probable match, not a certainty'}
      </div>
      <p>{identity.narrative}</p>

      <Collapsible
        summary={`Match detail — ${Math.round(identity.score * 100)}% field agreement, ${identity.conflicts.length} conflicting field(s)`}
      >
        <div className="matchgrid">
          <div>
            <h4>Agreed on</h4>
            <ul>
              {identity.matched_on.length > 0 ? (
                identity.matched_on.map((item) => <li key={item}>{item}</li>)
              ) : (
                <li className="muted">nothing</li>
              )}
            </ul>
          </div>
          <div>
            <h4>Differed on</h4>
            <ul>
              {identity.differed_on.length > 0 ? (
                identity.differed_on.map((item) => <li key={item}>{item}</li>)
              ) : (
                <li className="muted">nothing</li>
              )}
            </ul>
          </div>
        </div>

        {identity.conflicts.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Values in source</th>
                <th>Shown</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {identity.conflicts.map((conflict) => (
                <tr key={conflict.field}>
                  <th scope="row">{conflict.field}</th>
                  <td>
                    {conflict.values.map((value) => (
                      <div key={`${value.source}-${value.value}`}>
                        <code>{value.value}</code>{' '}
                        <span className="muted">{value.source}</span>
                      </div>
                    ))}
                  </td>
                  <td>
                    <code>{conflict.chosen ?? '—'}</code>
                  </td>
                  <td className="muted">{conflict.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <p className="muted">
          Primary record <code>{identity.primary_resource}</code>
          {identity.linked_resources.length > 0 && (
            <> · linked <code>{identity.linked_resources.join(', ')}</code></>
          )}
          {identity.unlinked_resources.length > 0 && (
            <> · not linked <code>{identity.unlinked_resources.join(', ')}</code></>
          )}
        </p>
      </Collapsible>
    </div>
  );
}
