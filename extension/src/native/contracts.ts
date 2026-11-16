export const PROTOCOL_VERSION = 1 as const;

export type ConnectionState =
  | "CONNECTING"
  | "CONNECTED"
  | "DISCONNECTED"
  | "HOST_NOT_REGISTERED"
  | "UNSUPPORTED_VERSION";

export type Phase =
  | "DRAFT"
  | "R0"
  | "S1"
  | "S1_REVIEW"
  | "S2"
  | "DONE";

export type SessionStatus =
  | "ACTIVE"
  | "PAUSED"
  | "BLOCKED"
  | "COMPLETED"
  | "COMPLETED_WITH_GAPS"
  | "CANCELLED";

export type TaskStatus =
  | "PENDING"
  | "PROMPT_READY"
  | "AWAITING_USER"
  | "RECEIVED"
  | "UNAVAILABLE"
  | "SKIPPED_WITH_REASON"
  | "STALE"
  | "FAILED";

export interface NativeRequest {
  v: 1;
  id: string;
  op: string;
  args: Record<string, unknown>;
}

export interface NativeSuccess {
  v: 1;
  id: string;
  ok: true;
  result: unknown;
}

export interface NativeErrorPayload {
  code: string;
  message: string;
  retryable: boolean;
}

export interface NativeError {
  v: 1;
  id: string | null;
  ok: false;
  error: NativeErrorPayload;
}

export type NativeResponse = NativeSuccess | NativeError;

export interface HelloResult {
  protocol_version: number;
  operations: string[];
}

export interface Assignment {
  provider_id: string;
  reported_model: string | null;
  role: string;
}

export interface ResearchSession {
  session_id: string;
  question: string;
  title: string | null;
  perplexity_prompt: string | null;
  intake_version: number;
  phase: Phase;
  status: SessionStatus;
  revision: number;
  gap_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionSummary {
  session_id: string;
  title: string | null;
  phase: Phase;
  status: SessionStatus;
  revision: number;
  updated_at: string;
}

export interface ResearchTask {
  task_id: string;
  session_id: string;
  round_id: string;
  phase: Phase;
  kind: string;
  provider_id: string | null;
  role: string;
  ordinal: number;
  status: TaskStatus;
  gap_reason: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface SessionListResult {
  items: SessionSummary[];
  next_cursor: number | null;
}

export interface TaskListResult {
  items: ResearchTask[];
}

export interface PromptMetadata {
  prompt_id: string;
  task_id: string;
  version: number;
  size_bytes: number;
  sha256: string;
}

export interface ChunkResult {
  chunk_b64: string;
  offset: number;
  size_bytes: number;
  total_bytes: number;
  done: boolean;
}

export interface UploadReservation {
  upload_id: string;
  task_id: string;
  mode: "create" | "revise";
  size_bytes: number;
  sha256: string;
  next_index: number;
  expires_at: string;
}

export interface ResponseMetadata {
  result_id: string;
  task_id: string;
  version: number;
  source_type: string;
  reported_model: string | null;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

export interface ResponseReceipt {
  response: ResponseMetadata;
  task: ResearchTask;
  session_revision: number;
}

export interface S1InputReference {
  ordinal: number;
  provider_id: string;
  reported_model: string | null;
  task_id: string;
  status: TaskStatus;
  result_id: string | null;
  result_version: number | null;
  sha256: string | null;
  gap_reason: string | null;
}

export interface S1PackagePartMetadata {
  part_index: number;
  prompt_id: string;
  size_bytes: number;
  sha256: string;
}

export interface S1PackageMetadata {
  package_id: string;
  session_id: string;
  task_id: string;
  template_version: number;
  input_fingerprint: string;
  package_sha256: string;
  total_bytes: number;
  part_count: number;
  r0_complete: boolean;
  stale: boolean;
  inputs: S1InputReference[];
  parts: S1PackagePartMetadata[];
  created_at: string;
}

export interface S1PrepareReceipt {
  package: S1PackageMetadata;
  session: ResearchSession;
}

export interface ReviewerPackageMetadata {
  package_id: string;
  session_id: string;
  task_id: string;
  provider_id: string;
  template_version: number;
  s1_result_id: string;
  s1_result_version: number;
  s1_package_id: string;
  s1_package_fingerprint: string;
  input_fingerprint: string;
  package_sha256: string;
  total_bytes: number;
  part_count: number;
  r0_complete: boolean;
  stale: boolean;
  inputs: S1InputReference[];
  parts: S1PackagePartMetadata[];
  created_at: string;
}

export interface ReviewerPrepareReceipt {
  packages: ReviewerPackageMetadata[];
  session: ResearchSession;
}

export interface S2ReviewReference {
  ordinal: number;
  provider_id: string;
  task_id: string;
  result_id: string;
  result_version: number;
  sha256: string;
  reviewer_package_id: string;
}

export interface S2PackageMetadata extends Omit<S1PackageMetadata, "inputs"> {
  s1_result_id: string;
  s1_result_version: number;
  s1_package_id: string;
  context_warning: boolean;
  reviews: S2ReviewReference[];
  inputs: S1InputReference[];
}

export interface S2PrepareReceipt {
  package: S2PackageMetadata;
  session: ResearchSession;
}

export interface BridgeStatusMessage {
  kind: "bridge.status";
}

export interface BridgeConnectMessage {
  kind: "bridge.connect";
}

export interface NativeRequestMessage {
  kind: "native.request";
  op: string;
  args: Record<string, unknown>;
}

export type UiMessage = BridgeStatusMessage | BridgeConnectMessage | NativeRequestMessage;

export interface BridgeReply<T = unknown> {
  ok: boolean;
  result?: T;
  error?: NativeErrorPayload;
  connectionState: ConnectionState;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export function parseNativeResponse(value: unknown): NativeResponse | null {
  if (!isRecord(value) || value.v !== 1 || typeof value.ok !== "boolean") return null;
  if (value.ok === true) {
    return typeof value.id === "string" && "result" in value
      ? value as unknown as NativeSuccess
      : null;
  }
  if ((typeof value.id !== "string" && value.id !== null) || !isRecord(value.error)) return null;
  const error = value.error;
  if (typeof error.code !== "string" || typeof error.message !== "string" || typeof error.retryable !== "boolean") {
    return null;
  }
  return value as unknown as NativeError;
}

export function isHelloResult(value: unknown): value is HelloResult {
  return isRecord(value)
    && value.protocol_version === PROTOCOL_VERSION
    && Array.isArray(value.operations)
    && value.operations.every((item) => typeof item === "string");
}

export function isUiMessage(value: unknown): value is UiMessage {
  if (!isRecord(value) || typeof value.kind !== "string") return false;
  if (value.kind === "bridge.status" || value.kind === "bridge.connect") return Object.keys(value).length === 1;
  return value.kind === "native.request"
    && typeof value.op === "string"
    && isRecord(value.args);
}

const phases = new Set<Phase>(["DRAFT", "R0", "S1", "S1_REVIEW", "S2", "DONE"]);
const sessionStatuses = new Set<SessionStatus>(["ACTIVE", "PAUSED", "BLOCKED", "COMPLETED", "COMPLETED_WITH_GAPS", "CANCELLED"]);
const taskStatuses = new Set<TaskStatus>(["PENDING", "PROMPT_READY", "AWAITING_USER", "RECEIVED", "UNAVAILABLE", "SKIPPED_WITH_REASON", "STALE", "FAILED"]);

export function isResearchSession(value: unknown): value is ResearchSession {
  if (!isRecord(value)) return false;
  return typeof value.session_id === "string"
    && typeof value.question === "string"
    && (typeof value.title === "string" || value.title === null)
    && (typeof value.perplexity_prompt === "string" || value.perplexity_prompt === null)
    && Number.isInteger(value.intake_version)
    && phases.has(value.phase as Phase)
    && sessionStatuses.has(value.status as SessionStatus)
    && Number.isInteger(value.revision)
    && (typeof value.gap_reason === "string" || value.gap_reason === null)
    && typeof value.created_at === "string"
    && typeof value.updated_at === "string";
}

function isSessionSummary(value: unknown): value is SessionSummary {
  if (!isRecord(value)) return false;
  return typeof value.session_id === "string"
    && (typeof value.title === "string" || value.title === null)
    && phases.has(value.phase as Phase)
    && sessionStatuses.has(value.status as SessionStatus)
    && Number.isInteger(value.revision)
    && typeof value.updated_at === "string";
}

export function isSessionListResult(value: unknown): value is SessionListResult {
  return isRecord(value)
    && Array.isArray(value.items)
    && value.items.every(isSessionSummary)
    && (value.next_cursor === null || Number.isInteger(value.next_cursor));
}

function isResearchTask(value: unknown): value is ResearchTask {
  if (!isRecord(value)) return false;
  return typeof value.task_id === "string"
    && typeof value.session_id === "string"
    && typeof value.round_id === "string"
    && phases.has(value.phase as Phase)
    && typeof value.kind === "string"
    && (typeof value.provider_id === "string" || value.provider_id === null)
    && typeof value.role === "string"
    && Number.isInteger(value.ordinal)
    && taskStatuses.has(value.status as TaskStatus)
    && (typeof value.gap_reason === "string" || value.gap_reason === null)
    && Number.isInteger(value.revision)
    && typeof value.created_at === "string"
    && typeof value.updated_at === "string";
}

export function isTaskListResult(value: unknown): value is TaskListResult {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isResearchTask);
}

export function isPromptMetadata(value: unknown): value is PromptMetadata {
  return isRecord(value) && typeof value.prompt_id === "string" && typeof value.task_id === "string"
    && Number.isInteger(value.version) && Number.isInteger(value.size_bytes) && typeof value.sha256 === "string";
}

export function isChunkResult(value: unknown): value is ChunkResult {
  return isRecord(value) && typeof value.chunk_b64 === "string" && Number.isInteger(value.offset)
    && Number.isInteger(value.size_bytes) && Number.isInteger(value.total_bytes) && typeof value.done === "boolean";
}

export function isUploadReservation(value: unknown): value is UploadReservation {
  return isRecord(value) && typeof value.upload_id === "string" && typeof value.task_id === "string"
    && (value.mode === "create" || value.mode === "revise") && Number.isInteger(value.size_bytes)
    && typeof value.sha256 === "string" && Number.isInteger(value.next_index) && typeof value.expires_at === "string";
}

function isResponseMetadata(value: unknown): value is ResponseMetadata {
  return isRecord(value) && typeof value.result_id === "string" && typeof value.task_id === "string"
    && Number.isInteger(value.version) && typeof value.source_type === "string"
    && (typeof value.reported_model === "string" || value.reported_model === null)
    && Number.isInteger(value.size_bytes) && typeof value.sha256 === "string" && typeof value.created_at === "string";
}

export function isResponseList(value: unknown): value is { items: ResponseMetadata[] } {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isResponseMetadata);
}

export function isResponseReceipt(value: unknown): value is ResponseReceipt {
  return isRecord(value) && isResponseMetadata(value.response) && isResearchTask(value.task)
    && Number.isInteger(value.session_revision);
}

export function isS1PackageMetadata(value: unknown): value is S1PackageMetadata {
  if (!isRecord(value)) return false;
  return typeof value.package_id === "string" && typeof value.session_id === "string"
    && typeof value.task_id === "string" && Number.isInteger(value.template_version)
    && typeof value.input_fingerprint === "string"
    && typeof value.package_sha256 === "string" && Number.isInteger(value.total_bytes)
    && Number.isInteger(value.part_count) && typeof value.r0_complete === "boolean"
    && typeof value.stale === "boolean" && typeof value.created_at === "string"
    && Array.isArray(value.inputs) && value.inputs.every((item) => isRecord(item)
      && Number.isInteger(item.ordinal) && typeof item.provider_id === "string"
      && (typeof item.reported_model === "string" || item.reported_model === null)
      && typeof item.task_id === "string" && taskStatuses.has(item.status as TaskStatus)
      && (typeof item.result_id === "string" || item.result_id === null)
      && (Number.isInteger(item.result_version) || item.result_version === null)
      && (typeof item.sha256 === "string" || item.sha256 === null)
      && (typeof item.gap_reason === "string" || item.gap_reason === null))
    && Array.isArray(value.parts) && value.parts.every((part) => isRecord(part)
      && Number.isInteger(part.part_index) && typeof part.prompt_id === "string"
      && Number.isInteger(part.size_bytes) && typeof part.sha256 === "string");
}

export function isS1PrepareReceipt(value: unknown): value is S1PrepareReceipt {
  return isRecord(value) && isS1PackageMetadata(value.package) && isResearchSession(value.session);
}

export function isReviewerPackageMetadata(value: unknown): value is ReviewerPackageMetadata {
  return isRecord(value) && isS1PackageMetadata({
    ...value,
    r0_complete: value.r0_complete,
  })
    && typeof value.provider_id === "string"
    && typeof value.s1_result_id === "string"
    && Number.isInteger(value.s1_result_version)
    && typeof value.s1_package_id === "string"
    && typeof value.s1_package_fingerprint === "string";
}

export function isReviewerPackageList(value: unknown): value is { items: ReviewerPackageMetadata[] } {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isReviewerPackageMetadata);
}

export function isReviewerPrepareReceipt(value: unknown): value is ReviewerPrepareReceipt {
  return isRecord(value) && Array.isArray(value.packages)
    && value.packages.every(isReviewerPackageMetadata) && isResearchSession(value.session);
}

export function isS2PackageMetadata(value: unknown): value is S2PackageMetadata {
  return isRecord(value) && isS1PackageMetadata(value)
    && typeof value.s1_result_id === "string"
    && Number.isInteger(value.s1_result_version)
    && typeof value.s1_package_id === "string"
    && typeof value.context_warning === "boolean"
    && Array.isArray(value.reviews) && value.reviews.every((review) => isRecord(review)
      && Number.isInteger(review.ordinal) && typeof review.provider_id === "string"
      && typeof review.task_id === "string" && typeof review.result_id === "string"
      && Number.isInteger(review.result_version) && typeof review.sha256 === "string"
      && typeof review.reviewer_package_id === "string");
}

export function isS2PrepareReceipt(value: unknown): value is S2PrepareReceipt {
  return isRecord(value) && isS2PackageMetadata(value.package) && isResearchSession(value.session);
}
