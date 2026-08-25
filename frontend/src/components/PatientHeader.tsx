import type { Demographics, SourceInfo } from '@/lib/types';
import { Badge, DateText } from './ui';

/**
 * The banner a clinician reads first. Age is the one derived value here, and it
 * is rendered from the backend's AgeEstimate -- so a year-only birth date shows
 * "67–68 y", not a confidently wrong single number.
 */
export function PatientHeader({
  demographics,
  source,
  generatedAt,
}: {
  demographics: Demographics;
  source: SourceInfo;
  generatedAt: string;
}) {
  const {
    full_name,
    gender,
    birth_date,
    age,
    mrn,
    other_identifiers,
    phones,
    address,
    alternate_addresses,
    race,
    ethnicity,
    name_note,
    withheld_identifier_systems,
  } = demographics;

  return (
    <header className="banner">
      <div className="banner__identity">
        <h1>{full_name}</h1>
        <div className="banner__facts">
          <span>
            <strong>{age.display}</strong>
            {age.is_approximate && (
              <Badge tone="caution" title={age.note ?? undefined}>
                approximate
              </Badge>
            )}
          </span>
          <span>{gender ? capitalize(gender) : <em className="muted">sex not recorded</em>}</span>
          <span>
            <span className="muted">DOB </span>
            <DateText value={birth_date} fallback="not recorded" />
          </span>
          <span>
            <span className="muted">MRN </span>
            {mrn ?? <em className="muted">not recorded</em>}
          </span>
        </div>
        {name_note && <p className="banner__note">{name_note}</p>}
      </div>

      <dl className="banner__grid">
        <div>
          <dt>Contact</dt>
          <dd>
            {phones.length > 0 ? phones.join(' · ') : <span className="muted">none recorded</span>}
          </dd>
        </div>
        <div>
          <dt>Address</dt>
          <dd>
            {address ?? <span className="muted">none recorded</span>}
            {alternate_addresses.map((alternate) => (
              <div key={alternate} className="banner__alt">
                also recorded as {alternate}
              </div>
            ))}
          </dd>
        </div>
        <div>
          <dt>Race / ethnicity</dt>
          <dd>
            {race || ethnicity ? (
              <>
                {race?.text ?? 'not recorded'}
                {' · '}
                {ethnicity?.text ?? 'not recorded'}
                <span className="muted"> (US Core)</span>
              </>
            ) : (
              <span className="muted">not recorded</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Other identifiers</dt>
          <dd>
            {other_identifiers.length > 0 ? (
              other_identifiers.map((identifier) => (
                <div key={identifier}>{identifier}</div>
              ))
            ) : (
              <span className="muted">none</span>
            )}
            {withheld_identifier_systems.map((system) => (
              <div key={system} className="muted">
                {system} present in source — withheld from this view
              </div>
            ))}
          </dd>
        </div>
      </dl>

      <p className="banner__source">
        {source.currency_note ??
          'Source extract has no timestamp, so its age is unknown.'}{' '}
        Rendered {new Date(generatedAt).toUTCString()}. Bundle{' '}
        <code>{source.bundle_id ?? 'unidentified'}</code> · {source.entry_count} entries.
      </p>
    </header>
  );
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
