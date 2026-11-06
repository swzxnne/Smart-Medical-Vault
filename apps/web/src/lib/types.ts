export type Sex = "male" | "female" | "other";

export const BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"] as const;
export type BloodGroup = (typeof BLOOD_GROUPS)[number];

export interface Profile {
  id: string;
  name: string;
  dob: string | null;
  sex: Sex | null;
  blood_group: BloodGroup | null;
  relation: string | null;
  created_at: string;
  updated_at: string;
}

export type ProfileInput = Pick<Profile, "name" | "dob" | "sex" | "blood_group" | "relation">;

export type DocumentStatus = "queued" | "parsing" | "indexing" | "summarizing" | "ready" | "failed" | "uploaded";

export const IN_PROGRESS: ReadonlySet<string> = new Set(["queued", "parsing", "indexing", "summarizing"]);

export interface MedicalDocument {
  id: string;
  profile_id: string;
  original_name: string;
  mime: string;
  size_bytes: number;
  status: DocumentStatus;
  error: string | null;
  doc_type: string | null;
  doc_date: string | null;
  provider: string | null;
  patient_name: string | null;
  review_reason: string | null;
  page_count: number | null;
  processed_at: string | null;
  created_at: string;
}

export interface DocumentPage {
  page_no: number;
  text: string;
  engine: "docling" | "tesseract" | "gemini-vision";
  ocr_confidence: number | null;
}

export interface DocumentSummary {
  content_md: string;
  model: string;
  created_at: string;
}

export interface UploadUrlResponse {
  upload_url: string;
  storage_key: string;
  headers: Record<string, string>;
  expires_in: number;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_name: string;
  doc_type: string | null;
  doc_date: string | null;
  profile_id: string;
  profile_name: string;
  page_no: number;
  snippet: string;
  score: number;
  matched_by: ("semantic" | "keyword")[];
}

export interface SearchResponse {
  query: string;
  mode: "hybrid" | "keyword";
  results: SearchHit[];
}

export interface AskSource {
  number: number;
  kind: "measurements" | "passage";
  document_id: string | null;
  document_name: string;
  doc_type: string | null;
  doc_date: string | null;
  profile_name: string;
  page_no: number | null;
  snippet: string;
}

export interface AskObservation {
  code: string;
  name: string;
  value_num: number | null;
  value_text: string;
  unit: string | null;
  observed_at: string | null;
  flag: Flag | null;
  profile_name: string;
  source_number: number;
  verified: boolean;
}

export type Flag = "low" | "normal" | "high";

export interface Observation {
  id: string;
  profile_id: string;
  document_id: string | null;
  document_name: string | null;
  page_no: number | null;
  code: string;
  group: string | null;
  name: string;
  value_num: number | null;
  value_text: string;
  unit: string | null;
  ref_low: number | null;
  ref_high: number | null;
  ref_text: string | null;
  flag: Flag | null;
  observed_at: string | null;
  source: "ai" | "corrected" | "manual";
  verified: boolean;
  review_note: string | null;
}

export interface Medication {
  id: string;
  document_id: string;
  document_name: string | null;
  page_no: number | null;
  name: string;
  dose: string | null;
  frequency: string | null;
  duration: string | null;
  prescribed_on: string | null;
}

export interface Condition {
  id: string;
  document_id: string;
  document_name: string | null;
  page_no: number | null;
  name: string;
  kind: "diagnosis" | "allergy";
  noted_on: string | null;
}

export interface MetricSnapshot {
  code: string;
  name: string;
  unit: string;
  group: string;
  count: number;
  latest: Observation;
  previous: Observation | null;
}

export interface ProfileSummaryCitation {
  number: number;
  document_id: string;
  document_name: string;
  doc_date: string | null;
}

export interface HealthOverview {
  summary: { content_md: string; citations: ProfileSummaryCitation[]; model: string; created_at: string } | null;
  metrics: MetricSnapshot[];
  medications: Medication[];
  conditions: Condition[];
  outdated_documents: number;
  unverified_count: number;
  summary_pending: boolean;
}

export interface DocumentFacts {
  observations: Observation[];
  medications: Medication[];
  conditions: Condition[];
}

export interface CatalogMetric {
  code: string;
  name: string;
  unit: string;
  group: string;
  decimals: number;
}

export interface Catalog {
  groups: Record<string, string>;
  metrics: CatalogMetric[];
}
