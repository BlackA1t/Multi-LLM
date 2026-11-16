import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionController } from "../src/state/session-controller";
import type { ResearchSession, ResearchTask } from "../src/native/contracts";

const session: ResearchSession = {
  session_id: "123e4567-e89b-42d3-a456-426614174000",
  question: "Синтетический вопрос",
  title: "Тест",
  perplexity_prompt: null,
  intake_version: 1,
  phase: "DRAFT",
  status: "ACTIVE",
  revision: 4,
  gap_reason: null,
  created_at: "2026-11-14T00:00:00Z",
  updated_at: "2026-11-14T00:00:00Z",
};

const task: ResearchTask = {
  task_id: "223e4567-e89b-42d3-a456-426614174000",
  session_id: session.session_id,
  round_id: "323e4567-e89b-42d3-a456-426614174000",
  phase: "R0",
  kind: "R0_RESPONSE",
  provider_id: "gemini",
  role: "independent_gemini",
  ordinal: 0,
  status: "PROMPT_READY",
  gap_reason: null,
  revision: 0,
  created_at: session.created_at,
  updated_at: session.updated_at,
};

describe("SessionController", () => {
  const sendMessage = chrome.runtime.sendMessage as unknown as ReturnType<typeof vi.fn>;

  beforeEach(() => sendMessage.mockReset());

  it("lets the server select the fixed R0 assignments and preserves a logical action key", () => {
    const controller = new SessionController();
    const command = controller.startCommand(session, "logical-action-key");
    expect(command.args.idempotency_key).toBe("logical-action-key");
    expect(command.args.expected_revision).toBe(4);
    expect(command.args).not.toHaveProperty("assignments");
    expect(controller.advanceCommand(session, "gap-key", "  explicit gap  ").args).toMatchObject({
      idempotency_key: "gap-key", gap_reason: "explicit gap",
    });
  });

  it("sends result-based IPC and surfaces typed errors", async () => {
    sendMessage.mockResolvedValueOnce({
      ok: true, result: session, connectionState: "CONNECTED",
    });
    await expect(new SessionController().get(session.session_id)).resolves.toEqual(session);

    sendMessage.mockResolvedValueOnce({
      ok: false,
      error: { code: "STALE_REVISION", message: "changed", retryable: true },
      connectionState: "CONNECTED",
    });
    await expect(new SessionController().get(session.session_id)).rejects.toMatchObject({
      detail: { code: "STALE_REVISION" },
    });
  });

  it("chunks a long response and retries a lost commit ACK with the same logical mutation", async () => {
    const active = { ...session, phase: "R0" as const };
    const text = "🚀".repeat(9_000);
    const controller = new SessionController();
    sendMessage
      .mockResolvedValueOnce({
        ok: true,
        result: {
          upload_id: "423e4567-e89b-42d3-a456-426614174000",
          task_id: task.task_id,
          mode: "create",
          size_bytes: new TextEncoder().encode(text).byteLength,
          sha256: "a".repeat(64),
          next_index: 0,
          expires_at: session.updated_at,
        },
        connectionState: "CONNECTED",
      })
      .mockResolvedValueOnce({ ok: true, result: { next_index: 1 }, connectionState: "CONNECTED" })
      .mockResolvedValueOnce({ ok: true, result: { next_index: 2 }, connectionState: "CONNECTED" })
      .mockResolvedValueOnce({
        ok: false,
        error: { code: "DISCONNECTED", message: "lost ACK", retryable: true },
        connectionState: "DISCONNECTED",
      });
    await expect(controller.importResponse(active, task, text, "create", "same-key")).rejects.toMatchObject({
      detail: { code: "DISCONNECTED" },
    });

    const receivedTask = { ...task, status: "RECEIVED" as const };
    const receipt = {
      response: {
        result_id: "523e4567-e89b-42d3-a456-426614174000",
        task_id: task.task_id,
        version: 1,
        source_type: "web_manual",
        reported_model: null,
        size_bytes: new TextEncoder().encode(text).byteLength,
        sha256: "b".repeat(64),
        created_at: session.updated_at,
      },
      task: receivedTask,
      session_revision: 5,
    };
    sendMessage.mockResolvedValueOnce({ ok: true, result: receipt, connectionState: "CONNECTED" });
    await expect(controller.importResponse(active, task, text, "create", "same-key")).resolves.toEqual(receipt);

    const operations = sendMessage.mock.calls.map(([message]) => message.op);
    expect(operations).toEqual([
      "response.upload.begin", "response.upload.part", "response.upload.part",
      "response.upload.commit", "response.upload.commit",
    ]);
    const commits = sendMessage.mock.calls.filter(([message]) => message.op === "response.upload.commit");
    expect(commits[0][0].args.idempotency_key).toBe("same-key");
    expect(commits[1][0].args.idempotency_key).toBe("same-key");
  });
});
