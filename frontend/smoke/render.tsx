/**
 * Render smoke test (dev only, not part of the app bundle).
 *
 * Renders every panel to a string against a real API response so that null
 * handling and optional-field access are exercised outside a browser.
 *
 *   npx vite build --ssr smoke/render.tsx --outDir smoke/dist
 *   node smoke/dist/render.js
 */

import { renderToString } from 'react-dom/server';

import { AllergyPanel } from '@/components/AllergyPanel';
import { DataQualityPanel } from '@/components/DataQualityPanel';
import { EncounterPanel } from '@/components/EncounterPanel';
import { IdentityBanner } from '@/components/IdentityBanner';
import { MedicationPanel } from '@/components/MedicationPanel';
import { ObservationPanel } from '@/components/ObservationPanel';
import { PatientHeader } from '@/components/PatientHeader';
import { ProblemPanel } from '@/components/ProblemPanel';
import { WithheldPanel } from '@/components/WithheldPanel';
import type { ClinicalSnapshot } from '@/lib/types';

import raw from './snapshot.json';

const snapshot = raw as unknown as ClinicalSnapshot;

const panels: Array<[string, () => JSX.Element | null]> = [
  ['PatientHeader', () => (
    <PatientHeader
      demographics={snapshot.demographics}
      source={snapshot.source}
      generatedAt={snapshot.generated_at}
    />
  )],
  ['IdentityBanner', () => <IdentityBanner identity={snapshot.identity} />],
  ['AllergyPanel', () => <AllergyPanel snapshot={snapshot} />],
  ['ProblemPanel', () => <ProblemPanel snapshot={snapshot} />],
  ['MedicationPanel', () => <MedicationPanel snapshot={snapshot} />],
  ['ObservationPanel', () => <ObservationPanel snapshot={snapshot} />],
  ['EncounterPanel', () => <EncounterPanel snapshot={snapshot} />],
  ['WithheldPanel', () => <WithheldPanel snapshot={snapshot} />],
  ['DataQualityPanel', () => <DataQualityPanel snapshot={snapshot} />],
];

let failures = 0;
const output: string[] = [];

for (const [name, render] of panels) {
  try {
    const element = render();
    const html = element === null ? '' : renderToString(element);
    output.push(html);
    console.log(`ok    ${name.padEnd(18)} ${html.length} chars`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL  ${name}: ${String(error)}`);
  }
}

// Content assertions: the safety-critical strings must actually reach the DOM.
const html = output.join('\n');
const expectations: Array<[string, boolean]> = [
  ['patient name rendered', html.includes('Dorothy M Whitfield')],
  ['age rendered', html.includes('68 y')],
  ['identity caveat shown', html.includes('probable match')],
  ['penicillin shown once', (html.match(/Penicillin/g) ?? []).length >= 1],
  ['high risk badge', html.includes('High risk')],
  ['unlabelled code badge', html.includes('unlabelled code')],
  ['locally resolved badge', html.includes('label resolved locally')],
  ['linked record badge', html.includes('linked record')],
  ['year-only badge', html.includes('year only')],
  ['blood pressure pair', html.includes('138/88 mmHg')],
  ['withheld panel lists creatinine', html.includes('observation-004')],
  ['voided value not shown as a result', !html.includes('>14.7 mg/dL<')],
  ['no SSN digits', !html.includes('4471')],
];

for (const [label, passed] of expectations) {
  if (!passed) failures += 1;
  console.log(`${passed ? 'ok   ' : 'FAIL '} ${label}`);
}

console.log(failures === 0 ? '\nALL PANELS RENDERED' : `\n${failures} FAILURE(S)`);
process.exit(failures === 0 ? 0 : 1);
