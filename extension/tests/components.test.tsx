import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConnectionHelp, CreateSessionForm, SessionOverview, TaskList } from "../src/ui/shared/components";
import type { ResearchSession, ResearchTask, ResponseMetadata } from "../src/native/contracts";

const session: ResearchSession = {
  session_id: "123e4567-e89b-42d3-a456-426614174000",
  question: "<img src=x onerror=alert('unsafe')>",
  title: "Безопасный рендер",
  perplexity_prompt: null,
  intake_version: 1,
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
  responses: [] as ResponseMetadata[], busy: false,
  onLoadPrompt: vi.fn().mockResolvedValue({ text: "stored prompt", version: 1, sha256: "a".repeat(64) }),
  onCopyPrompt: vi.fn().mockResolvedValue(undefined),
  onAwaiting: vi.fn().mockResolvedValue(undefined),
  onImport,
  onLoadResponse: vi.fn().mockResolvedValue("stored response"),
  onUnavailable: vi.fn().mockResolvedValue(undefined),
  onSkip: vi.fn().mockResolvedValue(undefined),
  onReassign: vi.fn().mockResolvedValue(undefined),
  onPrepareS1: vi.fn().mockResolvedValue(undefined),
  onLoadS1Package: vi.fn().mockRejectedValue(new Error("not used")),
  onCopyS1Part: vi.fn().mockResolvedValue(undefined),
  onLoadS1Part: vi.fn().mockResolvedValue("stored S1 part"),
  onPrepareReviewers: vi.fn().mockResolvedValue(undefined),
  onLoadReviewerPackages: vi.fn().mockResolvedValue([]),
  onCopyReviewerPart: vi.fn().mockResolvedValue(undefined),
  onLoadReviewerPart: vi.fn().mockResolvedValue("stored reviewer part"),
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
    const question = screen.getByLabelText("Основной промпт для восьми R0");
    await user.type(question, "Синтетический вопрос");
    await user.click(screen.getByRole("button", { name: /Создать и начать/ }));
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
    const submit = screen.getByRole("button", { name: /Создать и начать/ });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Основной промпт для восьми R0"), { target: { value: " " } });
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

  it("shows numbered S1 package parts and copy progress", async () => {
    const user = userEvent.setup();
    const s1Task: ResearchTask = {
      ...task, phase: "COMPARISON", kind: "S1_SYNTHESIS", provider_id: "chatgpt",
      role: "s1_synthesizer",
    };
    const tools = taskTools();
    tools.onLoadS1Package.mockResolvedValue({
      package_id: "423e4567-e89b-42d3-a456-426614174000",
      session_id: session.session_id,
      task_id: s1Task.task_id,
      template_version: 1,
      input_fingerprint: "a".repeat(64),
      package_sha256: "b".repeat(64),
      total_bytes: 900_000,
      part_count: 2,
      r0_complete: true,
      stale: false,
      inputs: [],
      parts: [
        { part_index: 0, prompt_id: "p0", size_bytes: 500_000, sha256: "c".repeat(64) },
        { part_index: 1, prompt_id: "p1", size_bytes: 400_000, sha256: "d".repeat(64) },
      ],
      created_at: session.created_at,
    });
    render(<TaskList tasks={[s1Task]} phase="COMPARISON" tools={tools} />);
    await user.click(screen.getByRole("button", { name: "Показать пакет S1" }));
    expect(screen.getByRole("status")).toHaveTextContent("0/2");
    expect(screen.getByText(/Часть 1\/2/)).toBeVisible();
    const copyButtons = screen.getAllByRole("button", { name: "Копировать" });
    await user.click(copyButtons[0]);
    expect(tools.onCopyS1Part).toHaveBeenCalledWith("423e4567-e89b-42d3-a456-426614174000", 0);
    expect(screen.getByRole("status")).toHaveTextContent("1/2");
  });

  it("shows independent reviewer progress and sequential package copy", async () => {
    const user = userEvent.setup();
    const reviewer: ResearchTask = {
      ...task, phase: "CRITIQUE", kind: "S1_REVIEW", provider_id: "deepseek",
      role: "s1_reviewer",
    };
    const tools = taskTools();
    tools.onLoadReviewerPackages.mockResolvedValue([{
      package_id: "523e4567-e89b-42d3-a456-426614174000",
      session_id: session.session_id,
      task_id: reviewer.task_id,
      provider_id: "deepseek",
      template_version: 1,
      s1_result_id: "623e4567-e89b-42d3-a456-426614174000",
      s1_result_version: 1,
      s1_package_id: "723e4567-e89b-42d3-a456-426614174000",
      s1_package_fingerprint: "a".repeat(64),
      input_fingerprint: "b".repeat(64),
      package_sha256: "c".repeat(64),
      total_bytes: 800_000,
      part_count: 2,
      r0_complete: true,
      stale: false,
      inputs: [],
      parts: [
        { part_index: 0, prompt_id: "r0", size_bytes: 500_000, sha256: "d".repeat(64) },
        { part_index: 1, prompt_id: "r1", size_bytes: 300_000, sha256: "e".repeat(64) },
      ],
      created_at: session.created_at,
    }]);
    render(<TaskList tasks={[reviewer]} phase="CRITIQUE" tools={tools} />);
    expect(screen.getByText("Независимая проверка S1: 0/3")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Показать пакеты" }));
    expect(screen.getByRole("status")).toHaveTextContent("0/2");
    await user.click(screen.getAllByRole("button", { name: "Копировать" })[0]);
    expect(tools.onCopyReviewerPart).toHaveBeenCalledWith(
      "523e4567-e89b-42d3-a456-426614174000", 0,
    );
    expect(screen.getByRole("status")).toHaveTextContent("1/2");
  });

  it("shows zero current reviews immediately when completed reviewer tasks are stale", () => {
    const staleTasks: ResearchTask[] = ["deepseek", "grok", "gemini"].map((providerId, index) => ({
      ...task,
      task_id: `${index + 7}23e4567-e89b-42d3-a456-426614174000`,
      phase: "CRITIQUE",
      kind: "S1_REVIEW",
      provider_id: providerId,
      role: "s1_reviewer",
      ordinal: index,
      status: "STALE",
    }));
    const tools = taskTools();
    tools.responses = staleTasks.map((item, index) => ({
      result_id: `${index + 1}23e4567-e89b-42d3-a456-426614174999`,
      task_id: item.task_id,
      version: 1,
      source_type: "web_manual",
      reported_model: item.provider_id,
      size_bytes: 20,
      sha256: "f".repeat(64),
      created_at: session.created_at,
    }));
    render(<TaskList tasks={staleTasks} phase="CRITIQUE" tools={tools} />);
    expect(screen.getByText("Независимая проверка S1: 0/3")).toBeVisible();
    expect(screen.queryByText("Независимая проверка S1: 3/3")).not.toBeInTheDocument();
  });
});
