import {
  type ConnectionState,
  type HelloResult,
  isHelloResult,
  type NativeRequest,
  type NativeResponse,
  parseNativeResponse,
  PROTOCOL_VERSION,
} from "./contracts";

const HOST_NAME = "com.blackalt.multi_llm";
const REQUIRED_OPERATIONS = [
  "system.hello",
  "session.create",
  "session.update_intake",
  "session.get",
  "session.list",
  "session.start",
  "session.pause",
  "session.resume",
  "session.advance",
  "task.list",
  "task.prompt.meta",
  "task.prompt.chunk",
  "task.prompt.copy",
  "task.mark_awaiting",
  "response.upload.begin",
  "response.upload.part",
  "response.upload.commit",
  "response.upload.abort",
  "response.list",
  "response.chunk",
  "task.mark_unavailable",
  "task.skip",
  "task.reassign",
  "s1.package.prepare",
  "s1.package.get",
  "s1.package.part.chunk",
  "s1.package.part.copy",
  "review.package.prepare",
  "review.package.list",
  "review.package.part.chunk",
  "review.package.part.copy",
  "s2.package.prepare",
  "s2.package.get",
  "s2.package.part.chunk",
  "s2.package.part.copy",
];

export class BridgeFailure extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly retryable = false,
  ) {
    super(message);
    this.name = "BridgeFailure";
  }
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: BridgeFailure) => void;
  timeout: ReturnType<typeof setTimeout>;
}

export interface NativePortLike {
  postMessage(message: unknown): void;
  disconnect(): void;
  onMessage: { addListener(callback: (message: unknown) => void): void };
  onDisconnect: { addListener(callback: () => void): void };
}

export interface BridgeRuntime {
  connectNative(hostName: string): NativePortLike;
  lastError?: { message?: string };
}

export class NativeBridge {
  private port: NativePortLike | null = null;
  private pending = new Map<string, PendingRequest>();
  private connectPromise: Promise<HelloResult> | null = null;
  private state: ConnectionState = "DISCONNECTED";

  constructor(
    private readonly runtime: BridgeRuntime = chrome.runtime,
    private readonly timeoutMs = 15_000,
    private readonly maxPending = 32,
  ) {}

  get connectionState(): ConnectionState {
    return this.state;
  }

  async connect(): Promise<HelloResult> {
    if (this.connectPromise) return this.connectPromise;
    this.state = "CONNECTING";
    this.connectPromise = this.openAndHandshake();
    try {
      return await this.connectPromise;
    } finally {
      this.connectPromise = null;
    }
  }

  async reconnect(): Promise<HelloResult> {
    this.port?.disconnect();
    this.port = null;
    this.rejectAll(new BridgeFailure("DISCONNECTED", "Соединение с Python было перезапущено", true));
    return this.connect();
  }

  async request(operation: string, args: Record<string, unknown>): Promise<unknown> {
    if (!this.port || this.state !== "CONNECTED") await this.connect();
    return this.send(operation, args);
  }

  private async openAndHandshake(): Promise<HelloResult> {
    try {
      const port = this.runtime.connectNative(HOST_NAME);
      this.port = port;
      port.onMessage.addListener((message) => this.handleMessage(message));
      port.onDisconnect.addListener(() => this.handleDisconnect());
      const result = await this.send("system.hello", {});
      if (!isHelloResult(result)) {
        this.state = "UNSUPPORTED_VERSION";
        throw new BridgeFailure("UNSUPPORTED_VERSION", "Python Host использует неподдерживаемый протокол");
      }
      const missing = REQUIRED_OPERATIONS.filter((operation) => !result.operations.includes(operation));
      if (missing.length > 0) {
        this.state = "UNSUPPORTED_VERSION";
        throw new BridgeFailure("UNSUPPORTED_VERSION", "Python Host не поддерживает обязательные операции");
      }
      this.state = "CONNECTED";
      return result;
    } catch (error) {
      if (error instanceof BridgeFailure && error.code === "UNSUPPORTED_VERSION") {
        const port = this.port;
        this.port = null;
        port?.disconnect();
        this.state = "UNSUPPORTED_VERSION";
        throw error;
      }
      const message = this.runtime.lastError?.message ?? "";
      const hostNotRegistered = this.state === "HOST_NOT_REGISTERED"
        || message.toLowerCase().includes("native messaging host");
      this.state = hostNotRegistered
        ? "HOST_NOT_REGISTERED"
        : "DISCONNECTED";
      throw new BridgeFailure(
        this.state === "HOST_NOT_REGISTERED" ? "HOST_UNAVAILABLE" : "DISCONNECTED",
        this.state === "HOST_NOT_REGISTERED"
          ? "Python Host не зарегистрирован"
          : "Соединение с Python недоступно",
        true,
      );
    }
  }

  private send(operation: string, args: Record<string, unknown>): Promise<unknown> {
    if (!this.port) return Promise.reject(new BridgeFailure("HOST_UNAVAILABLE", "Python Host недоступен", true));
    if (this.pending.size >= this.maxPending) {
      return Promise.reject(new BridgeFailure("TOO_MANY_REQUESTS", "Слишком много ожидающих операций", true));
    }
    const id = crypto.randomUUID();
    const request: NativeRequest = { v: PROTOCOL_VERSION, id, op: operation, args };
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new BridgeFailure("HOST_UNAVAILABLE", "Python Host не ответил вовремя", true));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timeout });
      try {
        this.port?.postMessage(request);
      } catch {
        clearTimeout(timeout);
        this.pending.delete(id);
        reject(new BridgeFailure("DISCONNECTED", "Соединение с Python разорвано", true));
      }
    });
  }

  private handleMessage(raw: unknown): void {
    const response = parseNativeResponse(raw);
    if (!response || response.v !== PROTOCOL_VERSION || typeof response.id !== "string") return;
    const pending = this.pending.get(response.id);
    if (!pending) return;
    clearTimeout(pending.timeout);
    this.pending.delete(response.id);
    this.resolveResponse(response, pending);
  }

  private resolveResponse(response: NativeResponse, pending: PendingRequest): void {
    if (response.ok) pending.resolve(response.result);
    else pending.reject(new BridgeFailure(response.error.code, response.error.message, response.error.retryable));
  }

  private handleDisconnect(): void {
    const message = this.runtime.lastError?.message ?? "";
    this.state = message.toLowerCase().includes("native messaging host")
      ? "HOST_NOT_REGISTERED"
      : "DISCONNECTED";
    this.port = null;
    this.rejectAll(new BridgeFailure(
      this.state === "HOST_NOT_REGISTERED" ? "HOST_UNAVAILABLE" : "DISCONNECTED",
      this.state === "HOST_NOT_REGISTERED" ? "Python Host не зарегистрирован" : "Соединение с Python разорвано",
      true,
    ));
  }

  private rejectAll(error: BridgeFailure): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timeout);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
