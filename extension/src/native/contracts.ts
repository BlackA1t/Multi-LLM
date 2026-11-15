export const PROTOCOL_VERSION = 1 as const;

export type ConnectionState =
  | "CONNECTING"
  | "CONNECTED"
  | "DISCONNECTED"
  | "HOST_NOT_REGISTERED"
  | "UNSUPPORTED_VERSION";

export type Phase =
  | "DRAFT"
  | "INDEPENDENT"
  | "COMPARISON"
  | "CRITIQUE"
  | "FACT_CHECK"
  | "ARBITRATION"
  | "REVIEW"
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
  role: string;
}

export interface ResearchSession {
  session_id: string;
  question: string;
  title: string | null;
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

const phases = new Set<Phase>(["DRAFT", "INDEPENDENT", "COMPARISON", "CRITIQUE", "FACT_CHECK", "ARBITRATION", "REVIEW", "DONE"]);
const sessionStatuses = new Set<SessionStatus>(["ACTIVE", "PAUSED", "BLOCKED", "COMPLETED", "COMPLETED_WITH_GAPS", "CANCELLED"]);
const taskStatuses = new Set<TaskStatus>(["PENDING", "PROMPT_READY", "AWAITING_USER", "RECEIVED", "UNAVAILABLE", "SKIPPED_WITH_REASON", "STALE", "FAILED"]);

export function isResearchSession(value: unknown): value is ResearchSession {
  if (!isRecord(value)) return false;
  return typeof value.session_id === "string"
    && typeof value.question === "string"
    && (typeof value.title === "string" || value.title === null)
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
