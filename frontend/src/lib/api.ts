import type { ClinicalSnapshot } from './types';

/**
 * API base URL. Override with VITE_API_BASE_URL in `.env.local` if the backend
 * is not on the default port.
 */
export const API_BASE = (
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  'http://127.0.0.1:8000'
).replace(/\/+$/, '');

export type SnapshotResult =
  | { ok: true; snapshot: ClinicalSnapshot }
  | { ok: false; error: string; hint: string };

/**
 * Fetches the snapshot. Failure is a first-class return value rather than a
 * thrown error: a clinical view must say "the data could not be loaded" loudly,
 * never render a blank page that could be misread as "this patient has no
 * problems, medications or allergies".
 */
export async function fetchSnapshot(): Promise<SnapshotResult> {
  const url = `${API_BASE}/api/patient-summary`;
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      const body = await response.text();
      return {
        ok: false,
        error: `The API returned ${response.status} ${response.statusText}.`,
        hint: body.slice(0, 400) || `Requested ${url}`,
      };
    }
    return { ok: true, snapshot: (await response.json()) as ClinicalSnapshot };
  } catch (error) {
    return {
      ok: false,
      error: 'Could not reach the snapshot API.',
      hint:
        `Tried ${url}. Start the backend with ` +
        `"uvicorn app.main:app --reload --port 8000" from the backend directory, ` +
        `or set VITE_API_BASE_URL if it runs elsewhere. (${String(error)})`,
    };
  }
}
