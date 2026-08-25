/**
 * Mirror of the backend's Pydantic response models.
 *
 * Hand-written rather than generated, so that reading this file tells you the
 * whole contract. Every "is this safe / is this certain" decision is a field
 * here -- the UI renders flags, it never re-derives them. Keep in step with
 * `backend/app/models/summary.py`.
 */

export type LabelSource = 'source' | 'local_table' | 'code_only' | 'absent';
export type Confidence = 'certain' | 'probable' | 'possible' | 'unresolved';
export type IssueSeverity = 'info' | 'warning' | 'critical';
export type Tone = 'neutral' | 'positive' | 'caution' | 'danger';

export interface PartialDateTime {
  raw: string;
  precision: 'year' | 'month' | 'day' | 'instant' | 'unknown';
  sort_key: string;
  midnight_utc_padded: boolean;
  display: string;
  is_imprecise: boolean;
  precision_note: string | null;
}

export interface AgeEstimate {
  years: number | null;
  is_approximate: boolean;
  low: number | null;
  high: number | null;
  display: string;
  note: string | null;
}

export interface CodedConcept {
  text: string;
  code: string | null;
  system_uri: string | null;
  system_name: string | null;
  label_source: LabelSource;
  warnings: string[];
  label_is_from_source: boolean;
  label_is_unresolved: boolean;
}

export interface Provenance {
  resource: string;
  subject_reference: string | null;
  via_linked_identity: boolean;
  merged_from: string[];
}

export interface StatusBadge {
  code: string | null;
  label: string | null;
  is_current: boolean;
  tone: Tone;
}

export interface DataIssue {
  severity: IssueSeverity;
  category: string;
  message: string;
  resource: string | null;
  field: string | null;
  action: string | null;
}

export interface SuppressedItem {
  resource: string;
  section: string;
  label: string;
  reason: string;
  status: string | null;
  recorded: PartialDateTime | null;
  is_noteworthy: boolean;
}

export interface ConflictValue {
  value: string;
  source: string;
}

export interface FieldConflict {
  field: string;
  values: ConflictValue[];
  chosen: string | null;
  rationale: string;
}

export interface IdentityResolution {
  primary_resource: string | null;
  linked_resources: string[];
  unlinked_resources: string[];
  confidence: Confidence;
  score: number;
  matched_on: string[];
  differed_on: string[];
  conflicts: FieldConflict[];
  narrative: string;
  requires_review: boolean;
}

export interface Demographics {
  full_name: string;
  family_name: string | null;
  given_names: string[];
  name_note: string | null;
  gender: string | null;
  birth_date: PartialDateTime | null;
  age: AgeEstimate;
  deceased: boolean | null;
  mrn: string | null;
  other_identifiers: string[];
  withheld_identifier_systems: string[];
  phones: string[];
  address: string | null;
  alternate_addresses: string[];
  race: CodedConcept | null;
  ethnicity: CodedConcept | null;
  us_core_profiles: string[];
}

export interface Problem {
  concept: CodedConcept;
  clinical_status: StatusBadge;
  verification_status: StatusBadge;
  onset: PartialDateTime | null;
  recorded: PartialDateTime | null;
  provenance: Provenance;
  notes: string[];
}

export interface Medication {
  concept: CodedConcept;
  status: StatusBadge;
  intent: string | null;
  dosage_text: string | null;
  authored_on: PartialDateTime | null;
  provenance: Provenance;
  notes: string[];
}

export interface Allergy {
  concept: CodedConcept;
  clinical_status: StatusBadge;
  verification_status: StatusBadge;
  criticality: string | null;
  criticality_label: string;
  criticality_rank: number;
  reactions: string[];
  recorded: PartialDateTime | null;
  provenance: Provenance;
  notes: string[];
}

export interface EncounterSummary {
  type: CodedConcept | null;
  encounter_class: string | null;
  status: StatusBadge;
  start: PartialDateTime | null;
  end: PartialDateTime | null;
  duration_minutes: number | null;
  provenance: Provenance;
  notes: string[];
}

export interface ObservationValue {
  label: string | null;
  text: string;
  code: string | null;
  label_source: LabelSource | null;
}

export interface ObservationSummary {
  concept: CodedConcept;
  value: string | null;
  components: ObservationValue[];
  unit: string | null;
  effective: PartialDateTime | null;
  status: StatusBadge;
  is_vital_sign: boolean;
  is_historical: boolean;
  age_text: string | null;
  provenance: Provenance;
  notes: string[];
}

export interface SectionCoverage {
  displayed: number;
  suppressed: number;
  qualified: number;
}

export interface SourceInfo {
  bundle_id: string | null;
  bundle_type: string | null;
  bundle_timestamp: PartialDateTime | null;
  declared_total: number | null;
  entry_count: number;
  resource_counts: Record<string, number>;
  currency_note: string | null;
}

export interface ClinicalSnapshot {
  generated_at: string;
  as_of: string;
  source: SourceInfo;
  identity: IdentityResolution;
  demographics: Demographics;
  allergies: {
    active: Allergy[];
    inactive: Allergy[];
    no_known_allergies_asserted: boolean;
  };
  problems: { active: Problem[]; inactive: Problem[] };
  medications: { current: Medication[]; past: Medication[] };
  encounters: EncounterSummary[];
  observations: {
    vitals: ObservationSummary[];
    labs: ObservationSummary[];
    other: ObservationSummary[];
  };
  suppressed: SuppressedItem[];
  data_quality: DataIssue[];
  coverage: Record<string, SectionCoverage>;
}
