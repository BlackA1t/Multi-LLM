import { beforeEach, describe, expect, it, vi } from "vitest";

import { INDEPENDENT_ASSIGNMENTS, SessionController } from "../src/state/session-controller";
import type { ResearchSession } from "../src/native/contracts";

const session: ResearchSession = {
  session_id: "123e4567-e89b-42d3-a456-426614174000",
  question: "Синтетический вопрос",
  title: "Тест",
  phase: "DRAFT",
  status: "ACTIVE",
  revision: 4,
  gap_reason: null,
  created_at: "2026-11-14T00:00:00Z",
  updated_at: "2026-11-14T00:00:00Z",
};

describe("SessionController", () => {
  const sendMessage = chrome.runtime.sendMessage as unknown as ReturnType<typeof vi.fn>;

  beforeEach(() => sendMessage.mockReset());

  it("builds four distinct fixed assignments and preserves a logical action key", () => {
    const controller = new SessionController();
    const command = controller.startCommand(session, "logical-action-key");
    expect(command.args.idempotency_key).toBe("logical-action-key");
    expect(command.args.expected_revision).toBe(4);
    expect(command.args.assignments).toEqual(INDEPENDENT_ASSIGNMENTS);
    expect(new Set(INDEPENDENT_ASSIGNMENTS.map((item) => item.provider_id)).size).toBe(4);
    expect(new Set(INDEPENDENT_ASSIGNMENTS.map((item) => item.role)).size).toBe(4);
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
});
