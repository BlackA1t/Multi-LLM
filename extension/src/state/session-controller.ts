import type {
  Assignment,
  BridgeReply,
  BridgeStatusMessage,
  ConnectionState,
  NativeErrorPayload,
  NativeRequestMessage,
  ResearchSession,
  ResearchTask,
  SessionListResult,
  TaskListResult,
} from "../native/contracts";
import { isResearchSession, isSessionListResult, isTaskListResult } from "../native/contracts";

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

  advanceCommand(session: ResearchSession, idempotencyKey: string = crypto.randomUUID()): MutationCommand {
    return this.simpleCommand("session.advance", session, idempotencyKey);
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
}
