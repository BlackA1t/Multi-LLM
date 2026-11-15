import type {
  Assignment,
  BridgeReply,
  BridgeStatusMessage,
  ConnectionState,
  ChunkResult,
  NativeErrorPayload,
  NativeRequestMessage,
  ResearchSession,
  ResearchTask,
  ResponseMetadata,
  ResponseReceipt,
  SessionListResult,
  TaskListResult,
} from "../native/contracts";
import {
  isChunkResult, isPromptMetadata, isResearchSession, isResponseList, isResponseReceipt,
  isSessionListResult, isTaskListResult, isUploadReservation,
} from "../native/contracts";

const CHUNK_BYTES = 32_768;

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64ToBytes(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export const INDEPENDENT_ASSIGNMENTS: Assignment[] = [
  { provider_id: "gemini", role: "independent_gemini" },
  { provider_id: "deepseek", role: "independent_deepseek" },
  { provider_id: "qwen", role: "independent_qwen" },
  { provider_id: "grok", role: "independent_grok" },
];

export class UiBridgeError extends Error {
  constructor(
    public readonly detail: NativeErrorPayload,
    public readonly connectionState: ConnectionState,
  ) {
    super(detail.message);
    this.name = "UiBridgeError";
  }
}

export class NativeClient {
  async status(): Promise<ConnectionState> {
    const message: BridgeStatusMessage = { kind: "bridge.status" };
    const reply = await chrome.runtime.sendMessage(message) as BridgeReply;
    return reply.connectionState;
  }

  async connect(): Promise<ConnectionState> {
    const reply = await chrome.runtime.sendMessage({ kind: "bridge.connect" }) as BridgeReply;
    if (!reply.ok || reply.error) throw new UiBridgeError(
      reply.error ?? { code: "HOST_UNAVAILABLE", message: "Python Host недоступен", retryable: true },
      reply.connectionState,
    );
    return reply.connectionState;
  }

  async request<T>(
    op: string,
    args: Record<string, unknown>,
    validate?: (value: unknown) => value is T,
  ): Promise<T> {
    const message: NativeRequestMessage = { kind: "native.request", op, args };
    const reply = await chrome.runtime.sendMessage(message) as BridgeReply<T>;
    if (!reply.ok || reply.error || reply.result === undefined) {
      throw new UiBridgeError(
        reply.error ?? { code: "BAD_RESPONSE", message: "Python Host вернул некорректный ответ", retryable: false },
        reply.connectionState,
      );
    }
    if (validate && !validate(reply.result)) {
      throw new UiBridgeError(
        { code: "BAD_RESPONSE", message: "Python Host вернул некорректную структуру данных", retryable: false },
        reply.connectionState,
      );
    }
    return reply.result;
  }
}

export interface MutationCommand {
  op: "session.start" | "session.pause" | "session.resume" | "session.advance";
  args: Record<string, unknown>;
}

export class SessionController {
  private uploads = new Map<string, { uploadId: string; phase: "parts" | "commit" }>();
  constructor(private readonly client = new NativeClient()) {}

  connectionStatus(): Promise<ConnectionState> {
    return this.client.status();
  }

  reconnect(): Promise<ConnectionState> {
    return this.client.connect();
  }

  create(question: string, title: string | undefined, idempotencyKey: string): Promise<ResearchSession> {
    return this.client.request("session.create", {
      question,
      ...(title?.trim() ? { title: title.trim() } : {}),
      idempotency_key: idempotencyKey,
    }, isResearchSession);
  }

  list(cursor = 0): Promise<SessionListResult> {
    return this.client.request("session.list", { limit: 50, cursor }, isSessionListResult);
  }

  get(sessionId: string): Promise<ResearchSession> {
    return this.client.request("session.get", { session_id: sessionId }, isResearchSession);
  }

  tasks(sessionId: string): Promise<ResearchTask[]> {
    return this.client.request<TaskListResult>("task.list", { session_id: sessionId }, isTaskListResult)
      .then((result) => result.items);
  }

  async prompt(sessionId: string, taskId: string): Promise<{ text: string; version: number; sha256: string }> {
    const metadata = await this.client.request("task.prompt.meta", {
      session_id: sessionId, task_id: taskId,
    }, isPromptMetadata);
    const bytes = await this.readChunks("task.prompt.chunk", {
      session_id: sessionId, task_id: taskId,
    }, metadata.size_bytes);
    if (await sha256(bytes) !== metadata.sha256) throw new UiBridgeError(
      { code: "BAD_RESPONSE", message: "Контрольная сумма задания не совпала", retryable: true },
      "CONNECTED",
    );
    return {
      text: new TextDecoder("utf-8", { fatal: true }).decode(bytes),
      version: metadata.version,
      sha256: metadata.sha256,
    };
  }

  copyPrompt(sessionId: string, taskId: string): Promise<unknown> {
    return this.client.request("task.prompt.copy", { session_id: sessionId, task_id: taskId }, isPromptMetadata);
  }

  markAwaiting(session: ResearchSession, taskId: string, key = crypto.randomUUID()): Promise<ResearchTask> {
    return this.client.request("task.mark_awaiting", {
      session_id: session.session_id, task_id: taskId, expected_revision: session.revision,
      idempotency_key: key,
    });
  }

  async importResponse(
    session: ResearchSession,
    task: ResearchTask,
    text: string,
    mode: "create" | "revise",
    idempotencyKey: string,
    expectedResultVersion?: number,
    reportedModel?: string,
  ): Promise<ResponseReceipt> {
    const bytes = new TextEncoder().encode(text);
    const digest = await sha256(bytes);
    let state = this.uploads.get(idempotencyKey);
    if (!state || state.phase === "parts") {
      const reservation = await this.client.request("response.upload.begin", {
        session_id: session.session_id, task_id: task.task_id, mode,
        size_bytes: bytes.byteLength, sha256: digest, expected_revision: session.revision,
        ...(expectedResultVersion ? { expected_result_version: expectedResultVersion } : {}),
        idempotency_key: idempotencyKey,
      }, isUploadReservation);
      state = { uploadId: reservation.upload_id, phase: "parts" };
      this.uploads.set(idempotencyKey, state);
      for (let index = reservation.next_index; index * CHUNK_BYTES < bytes.byteLength; index += 1) {
        const chunk = bytes.slice(index * CHUNK_BYTES, (index + 1) * CHUNK_BYTES);
        await this.client.request("response.upload.part", {
          upload_id: state.uploadId, part_index: index, chunk_b64: bytesToBase64(chunk),
          sha256: await sha256(chunk),
        });
      }
      state.phase = "commit";
    }
    const receipt = await this.client.request("response.upload.commit", {
      upload_id: state.uploadId, session_id: session.session_id, task_id: task.task_id,
      expected_revision: session.revision, idempotency_key: idempotencyKey,
      source_type: "web_manual", ...(reportedModel?.trim() ? { reported_model: reportedModel.trim() } : {}),
    }, isResponseReceipt);
    this.uploads.delete(idempotencyKey);
    return receipt;
  }

  responses(sessionId: string, taskId?: string): Promise<ResponseMetadata[]> {
    return this.client.request("response.list", {
      session_id: sessionId, ...(taskId ? { task_id: taskId } : {}),
    }, isResponseList).then((result) => result.items);
  }

  async responseText(sessionId: string, response: ResponseMetadata): Promise<string> {
    const bytes = await this.readChunks("response.chunk", {
      session_id: sessionId, result_id: response.result_id, version: response.version,
    }, response.size_bytes);
    if (await sha256(bytes) !== response.sha256) throw new UiBridgeError(
      { code: "BAD_RESPONSE", message: "Контрольная сумма ответа не совпала", retryable: true }, "CONNECTED",
    );
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  }

  markUnavailable(session: ResearchSession, taskId: string, reason: string, key = crypto.randomUUID()): Promise<ResearchTask> {
    return this.taskReason("task.mark_unavailable", session, taskId, reason, key);
  }

  skip(session: ResearchSession, taskId: string, reason: string, key = crypto.randomUUID()): Promise<ResearchTask> {
    return this.taskReason("task.skip", session, taskId, reason, key);
  }

  reassign(session: ResearchSession, taskId: string, providerId: string, key = crypto.randomUUID()): Promise<ResearchTask> {
    return this.client.request("task.reassign", {
      session_id: session.session_id, task_id: taskId, provider_id: providerId,
      expected_revision: session.revision, idempotency_key: key,
    });
  }

  startCommand(session: ResearchSession, idempotencyKey: string = crypto.randomUUID()): MutationCommand {
    return {
      op: "session.start",
      args: {
        session_id: session.session_id,
        expected_revision: session.revision,
        idempotency_key: idempotencyKey,
        assignments: INDEPENDENT_ASSIGNMENTS,
      },
    };
  }

  pauseCommand(session: ResearchSession, idempotencyKey: string = crypto.randomUUID()): MutationCommand {
    return this.simpleCommand("session.pause", session, idempotencyKey);
  }

  resumeCommand(session: ResearchSession, idempotencyKey: string = crypto.randomUUID()): MutationCommand {
    return this.simpleCommand("session.resume", session, idempotencyKey);
  }

  advanceCommand(
    session: ResearchSession,
    idempotencyKey: string = crypto.randomUUID(),
    gapReason?: string,
  ): MutationCommand {
    const command = this.simpleCommand("session.advance", session, idempotencyKey);
    if (gapReason?.trim()) command.args.gap_reason = gapReason.trim();
    return command;
  }

  mutate(command: MutationCommand): Promise<ResearchSession> {
    return this.client.request(command.op, command.args, isResearchSession);
  }

  private simpleCommand(
    op: MutationCommand["op"],
    session: ResearchSession,
    idempotencyKey: string,
  ): MutationCommand {
    return {
      op,
      args: {
        session_id: session.session_id,
        expected_revision: session.revision,
        idempotency_key: idempotencyKey,
      },
    };
  }

  private taskReason(
    op: "task.mark_unavailable" | "task.skip",
    session: ResearchSession,
    taskId: string,
    reason: string,
    key: string,
  ): Promise<ResearchTask> {
    return this.client.request(op, {
      session_id: session.session_id, task_id: taskId, reason,
      expected_revision: session.revision, idempotency_key: key,
    });
  }

  private async readChunks(
    op: "task.prompt.chunk" | "response.chunk",
    baseArgs: Record<string, unknown>,
    expectedBytes: number,
  ): Promise<Uint8Array> {
    const output = new Uint8Array(expectedBytes);
    let offset = 0;
    while (offset < expectedBytes) {
      const part = await this.client.request<ChunkResult>(op, {
        ...baseArgs, offset, limit: CHUNK_BYTES,
      }, isChunkResult);
      const bytes = base64ToBytes(part.chunk_b64);
      if (part.offset !== offset || part.size_bytes !== bytes.byteLength || part.total_bytes !== expectedBytes) {
        throw new UiBridgeError(
          { code: "BAD_RESPONSE", message: "Получен некорректный фрагмент данных", retryable: true }, "CONNECTED",
        );
      }
      output.set(bytes, offset);
      offset += bytes.byteLength;
      if (bytes.byteLength === 0 || part.done !== (offset >= expectedBytes)) {
        throw new UiBridgeError(
          { code: "BAD_RESPONSE", message: "Передача данных завершилась некорректно", retryable: true }, "CONNECTED",
        );
      }
    }
    return output;
  }
}
