import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConnectionHelp, CreateSessionForm, SessionOverview, TaskList } from "../src/ui/shared/components";
import type { ResearchSession, ResearchTask } from "../src/native/contracts";

const session: ResearchSession = {
  session_id: "123e4567-e89b-42d3-a456-426614174000",
  question: "<img src=x onerror=alert('unsafe')>",
  title: "Безопасный рендер",
  phase: "DRAFT",
  status: "ACTIVE",
  revision: 0,
  gap_reason: null,
  created_at: "2026-11-14T00:00:00Z",
  updated_at: "2026-11-14T00:00:00Z",
};

const task: ResearchTask = {
  task_id: "223e4567-e89b-42d3-a456-426614174000",
  session_id: session.session_id,
  round_id: "323e4567-e89b-42d3-a456-426614174000",
  phase: "INDEPENDENT",
  kind: "INDEPENDENT_RESPONSE",
  provider_id: "gemini",
  role: "independent_gemini",
  ordinal: 0,
  status: "PROMPT_READY",
  gap_reason: null,
  revision: 0,
  created_at: session.created_at,
  updated_at: session.updated_at,
};

const taskTools = (onImport = vi.fn().mockResolvedValue(undefined)) => ({
  responses: [], busy: false,
  onLoadPrompt: vi.fn().mockResolvedValue({ text: "stored prompt", version: 1, sha256: "a".repeat(64) }),
  onCopyPrompt: vi.fn().mockResolvedValue(undefined),
  onAwaiting: vi.fn().mockResolvedValue(undefined),
  onImport,
  onLoadResponse: vi.fn().mockResolvedValue("stored response"),
  onUnavailable: vi.fn().mockResolvedValue(undefined),
  onSkip: vi.fn().mockResolvedValue(undefined),
  onReassign: vi.fn().mockResolvedValue(undefined),
});

describe("UI safety and feedback", () => {
  it("renders research text as text rather than HTML", () => {
    const { container } = render(<SessionOverview session={session} tasks={[]} syncing={false} />);
    expect(screen.getByText(session.question)).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
  });

  it("keeps question fields when creation has no ACK", async () => {
    const user = userEvent.setup();
    const create = vi.fn().mockRejectedValue(new Error("disconnected"));
    render(<CreateSessionForm onCreate={create} disabled={false} />);
    const question = screen.getByLabelText("Исследовательский вопрос");
    await user.type(question, "Синтетический вопрос");
    await user.click(screen.getByRole("button", { name: /Создать исследование/ }));
    expect(question).toHaveValue("Синтетический вопрос");
  });

  it("shows actionable host registration guidance", () => {
    render(<ConnectionHelp state="HOST_NOT_REGISTERED" onReconnect={vi.fn()} busy={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Python Host не подключён");
    expect(screen.getByRole("button", { name: "Повторить подключение" })).toBeEnabled();
  });

  it("requires a non-empty question", () => {
    const create = vi.fn();
    render(<CreateSessionForm onCreate={create} disabled={false} />);
    const submit = screen.getByRole("button", { name: /Создать исследование/ });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Исследовательский вопрос"), { target: { value: " " } });
    expect(submit).toBeDisabled();
  });

  it("keeps pasted response volatile until explicit preview and confirmation", async () => {
    const user = userEvent.setup();
    const onImport = vi.fn().mockResolvedValue(undefined);
    render(<TaskList tasks={[task]} phase="INDEPENDENT" tools={taskTools(onImport)} />);
    await user.click(screen.getByRole("button", { name: "Импорт ответа" }));
    const textarea = screen.getByLabelText("Текст из официального веб-чата");
    await user.type(textarea, "Синтетический ответ");
    expect(onImport).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toHaveTextContent("Gemini");
    expect(screen.getByRole("dialog")).toHaveTextContent("web_manual");
    await user.click(screen.getByRole("button", { name: "Перейти к проверке" }));
    expect(onImport).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Подтвердить и сохранить" }));
    expect(onImport).toHaveBeenCalledOnce();
    expect(onImport).toHaveBeenCalledWith(task, "Синтетический ответ", "create", undefined, "");
  });

  it("blocks an empty manual response", async () => {
    const user = userEvent.setup();
    const onImport = vi.fn();
    render(<TaskList tasks={[task]} phase="INDEPENDENT" tools={taskTools(onImport)} />);
    await user.click(screen.getByRole("button", { name: "Импорт ответа" }));
    expect(screen.getByRole("button", { name: "Перейти к проверке" })).toBeDisabled();
    expect(onImport).not.toHaveBeenCalled();
  });
});
