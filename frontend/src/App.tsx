import { useCallback, useEffect, useState } from 'react';

import { AllergyPanel } from '@/components/AllergyPanel';
import { DataQualityPanel } from '@/components/DataQualityPanel';
import { EncounterPanel } from '@/components/EncounterPanel';
import { IdentityBanner } from '@/components/IdentityBanner';
import { MedicationPanel } from '@/components/MedicationPanel';
import { ObservationPanel } from '@/components/ObservationPanel';
import { PatientHeader } from '@/components/PatientHeader';
import { ProblemPanel } from '@/components/ProblemPanel';
import { WithheldPanel } from '@/components/WithheldPanel';
import { fetchSnapshot, type SnapshotResult } from '@/lib/api';

type State = { status: 'loading' } | { status: 'done'; result: SnapshotResult };

export default function App() {
  const [state, setState] = useState<State>({ status: 'loading' });

  const load = useCallback(async () => {
    setState({ status: 'loading' });
    setState({ status: 'done', result: await fetchSnapshot() });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <div className="topbar">
        <span className="topbar__brand">Centauri Health Solutions</span>
        <span className="topbar__title">Clinical Snapshot</span>
        <button className="topbar__reload" onClick={() => void load()} type="button">
          Reload
        </button>
        <span className="topbar__warning">Synthetic data · not for clinical use</span>
      </div>
      <main className="page">
        <Body state={state} />
      </main>
    </>
  );
}

function Body({ state }: { state: State }) {
  if (state.status === 'loading') {
    return (
      <div className="alert alert--warn" role="status">
        <div className="alert__title">Loading clinical snapshot…</div>
        <p className="muted">
          Nothing below has been verified yet. Do not read this screen as a
          patient record until it has finished loading.
        </p>
      </div>
    );
  }

  if (!state.result.ok) {
    return (
      <div className="alert alert--danger" role="alert">
        <div className="alert__title">Clinical snapshot unavailable</div>
        <p>{state.result.error}</p>
        <p className="muted">{state.result.hint}</p>
        <p>
          <strong>No patient data is being shown.</strong> Do not treat this
          screen as evidence that the patient has no problems, medications or
          allergies.
        </p>
      </div>
    );
  }

  const snapshot = state.result.snapshot;

  return (
    <>
      <PatientHeader
        demographics={snapshot.demographics}
        source={snapshot.source}
        generatedAt={snapshot.generated_at}
      />

      <IdentityBanner identity={snapshot.identity} />

      <div className="grid">
        <div className="grid__column">
          <AllergyPanel snapshot={snapshot} />
          <ProblemPanel snapshot={snapshot} />
          <MedicationPanel snapshot={snapshot} />
        </div>
        <div className="grid__column">
          <ObservationPanel snapshot={snapshot} />
          <EncounterPanel snapshot={snapshot} />
        </div>
      </div>

      <div className="audit">
        <WithheldPanel snapshot={snapshot} />
        <DataQualityPanel snapshot={snapshot} />
      </div>

      <footer className="footer">
        Synthetic data. Not for clinical use. Snapshot generated{' '}
        {new Date(snapshot.generated_at).toISOString()} from bundle{' '}
        <code>{snapshot.source.bundle_id}</code>.
      </footer>
    </>
  );
}
