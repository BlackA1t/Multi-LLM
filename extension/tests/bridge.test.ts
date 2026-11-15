import { afterEach, describe, expect, it, vi } from "vitest";

import { BridgeFailure, NativeBridge, type BridgeRuntime, type NativePortLike } from "../src/native/bridge";

class EventSource<T extends (...args: never[]) => void> {
  listeners: T[] = [];
  addListener = (callback: T) => this.listeners.push(callback);
  emit(...args: Parameters<T>) { this.listeners.forEach((listener) => listener(...args)); }
}

class FakePort implements NativePortLike {
  messages: Array<Record<string, unknown>> = [];
  disconnected = false;
  messageEvent = new EventSource<(message: unknown) => void>();
  disconnectEvent = new EventSource<() => void>();
  onMessage = { addListener: this.messageEvent.addListener };
  onDisconnect = { addListener: this.disconnectEvent.addListener };
  postMessage(message: unknown) { this.messages.push(message as Record<string, unknown>); }
  disconnect() { this.disconnected = true; this.disconnectEvent.emit(); }
}

const operations = [
  "system.hello", "session.create", "session.update_intake", "session.get", "session.list", "session.start",
  "session.pause", "session.resume", "session.advance", "task.list",
  "task.prompt.meta", "task.prompt.chunk", "task.prompt.copy", "task.mark_awaiting",
  "response.upload.begin", "response.upload.part", "response.upload.commit",
  "response.upload.abort", "response.list", "response.chunk", "task.mark_unavailable",
  "task.skip", "task.reassign",
];

async function connectedBridge(timeout = 1000) {
  const port = new FakePort();
  const runtime: BridgeRuntime = { connectNative: vi.fn(() => port) };
  const bridge = new NativeBridge(runtime, timeout);
  const connecting = bridge.connect();
  await Promise.resolve();
  const hello = port.messages[0];
  port.messageEvent.emit({ v: 1, id: hello.id, ok: true, result: { protocol_version: 1, operations } });
  await connecting;
  return { bridge, port, runtime };
}

afterEach(() => vi.useRealTimers());

describe("NativeBridge", () => {
  it("handshakes, correlates response IDs, and resolves result", async () => {
    const { bridge, port } = await connectedBridge();
    const pending = bridge.request("session.list", { limit: 50, cursor: 0 });
    await Promise.resolve();
    const request = port.messages[1];
    port.messageEvent.emit({ v: 1, id: "unknown", ok: true, result: "ignored" });
    port.messageEvent.emit({ v: 1, id: request.id, ok: true, result: { items: [], next_cursor: null } });
    await expect(pending).resolves.toEqual({ items: [], next_cursor: null });
    expect(request).toMatchObject({ v: 1, op: "session.list" });
  });

  it("rejects typed native errors", async () => {
    const { bridge, port } = await connectedBridge();
    const pending = bridge.request("session.advance", {});
    await Promise.resolve();
    const request = port.messages[1];
    port.messageEvent.emit({
      v: 1, id: request.id, ok: false,
      error: { code: "INVALID_PHASE", message: "Gate is incomplete", retryable: false },
    });
    await expect(pending).rejects.toMatchObject({ code: "INVALID_PHASE", retryable: false });
  });

  it("uses a hard timeout and clears a disconnected request", async () => {
    vi.useFakeTimers();
    const { bridge, port } = await connectedBridge(25);
    const timedOut = bridge.request("session.get", {});
    const timeoutAssertion = expect(timedOut).rejects.toMatchObject({ code: "HOST_UNAVAILABLE" });
    await vi.advanceTimersByTimeAsync(26);
    await timeoutAssertion;

    const disconnected = bridge.request("session.get", {});
    port.disconnectEvent.emit();
    await expect(disconnected).rejects.toMatchObject({ code: "DISCONNECTED" });
  });

  it("does not claim connection when required operations are missing", async () => {
    const port = new FakePort();
    const bridge = new NativeBridge({ connectNative: () => port }, 100);
    const connecting = bridge.connect();
    await Promise.resolve();
    port.messageEvent.emit({
      v: 1,
      id: port.messages[0].id,
      ok: true,
      result: { protocol_version: 1, operations: ["system.hello"] },
    });
    await expect(connecting).rejects.toBeInstanceOf(BridgeFailure);
    expect(bridge.connectionState).toBe("UNSUPPORTED_VERSION");
  });
});
