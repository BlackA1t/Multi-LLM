import { useEffect, useRef, useState, type RefObject } from "react";

import type { ConnectionState, Phase, ResearchSession, ResearchTask, ResponseMetadata, S1PackageMetadata, SessionSummary } from "../../native/contracts";
import { openOfficialProvider, PROVIDERS, providerById } from "../../state/provider-catalog";
import { ArrowIcon, HistoryIcon, PlusIcon, RefreshIcon, StatusIcon, WorkspaceIcon } from "./icons";

const PHASES: Array<{ phase: Phase; label: string }> = [
  { phase: "INDEPENDENT", label: "Независимые ответы" },
  { phase: "COMPARISON", label: "Синтез S1" },
];

const phaseLabels: Record<Phase, string> = {
  DRAFT: "Подготовка",
  INDEPENDENT: "Независимые ответы",
  COMPARISON: "ChatGPT S1",
  CRITIQUE: "Критика",
  FACT_CHECK: "Проверка фактов",
  ARBITRATION: "Арбитраж",
  REVIEW: "Итоговая проверка",
  DONE: "Завершено",
};

const statusLabels: Record<string, string> = {
  ACTIVE: "Активна",
  PAUSED: "На паузе",
  BLOCKED: "Заблокирована",
  COMPLETED: "Завершена",
  COMPLETED_WITH_GAPS: "Завершена с пропусками",
  CANCELLED: "Отменена",
  PENDING: "Ожидает",
  PROMPT_READY: "Готово задание",
  AWAITING_USER: "Ожидает пользователя",
  RECEIVED: "Получено",
  UNAVAILABLE: "Недоступно",
  SKIPPED_WITH_REASON: "Пропущено с причиной",
  STALE: "Устарело",
  FAILED: "Ошибка",
};

function useDialogFocus(onClose: () => void): RefObject<HTMLElement | null> {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); previous?.focus(); };
  }, []);
  return ref;
}

export function AppHeader({ connection, onReconnect, busy }: {
  connection: ConnectionState;
  onReconnect: () => void;
  busy: boolean;
}) {
  const connected = connection === "CONNECTED";
  const connecting = connection === "CONNECTING";
  const label = connected ? "Python подключён" : connecting ? "Подключение…" : "Python не подключён";
  const openWorkspace = () => chrome.tabs.create({ url: chrome.runtime.getURL("workspace.html") });
  return <header className="app-header">
    <div>
      <p className="eyebrow">Deep research workspace</p>
      <h1>Multi-LLM</h1>
    </div>
    <div className="header-actions">
      <button className="connection-pill" type="button" onClick={onReconnect} disabled={busy || connecting} aria-label={`${label}. Переподключить`}>
        <StatusIcon state={connected ? "ok" : connecting ? "warn" : "error"} />
        <span>{label}</span>
      </button>
      <button className="icon-button" type="button" onClick={openWorkspace} aria-label="Открыть полный Workspace" title="Открыть Workspace">
        <WorkspaceIcon />
      </button>
    </div>
  </header>;
}

export function PrimaryNav({ view, hasSession, onNew, onHistory, onSession }: {
  view: string;
  hasSession: boolean;
  onNew: () => void;
  onHistory: () => void;
  onSession: () => void;
}) {
  return <nav className="primary-nav" aria-label="Разделы">
    <button type="button" className={view === "new" ? "active" : ""} onClick={onNew}><PlusIcon />Новая</button>
    <button type="button" className={view === "history" ? "active" : ""} onClick={onHistory}><HistoryIcon />История</button>
    {hasSession && <button type="button" className={view === "session" ? "active" : ""} onClick={onSession}><WorkspaceIcon />Сессия</button>}
  </nav>;
}

export function ConnectionHelp({ state, onReconnect, busy }: {
  state: ConnectionState;
  onReconnect: () => void;
  busy: boolean;
}) {
  if (state === "CONNECTED" || state === "CONNECTING") return null;
  const unsupported = state === "UNSUPPORTED_VERSION";
  return <section className="notice error-notice" role="alert">
    <StatusIcon state="error" />
    <div>
      <strong>{unsupported ? "Версия Python Host не поддерживается" : "Python Host не подключён"}</strong>
      <p>{unsupported
        ? "Обновите Multi-LLM до совместимой версии протокола."
        : "Соберите расширение, зарегистрируйте Native Host с фактическим ID расширения и повторите подключение."}</p>
      <button type="button" className="secondary-button" onClick={onReconnect} disabled={busy}>Повторить подключение</button>
    </div>
  </section>;
}

export function CreateSessionForm({ onCreate, disabled }: {
  onCreate: (question: string, title?: string, perplexityPrompt?: string, startNow?: boolean) => Promise<void>;
  disabled: boolean;
}) {
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [perplexityPrompt, setPerplexityPrompt] = useState("");
  const [formError, setFormError] = useState("");
  const submit = async (startNow: boolean) => {
    if (!question.trim()) {
      setFormError("Вставьте основной исследовательский промпт");
      return;
    }
    setFormError("");
    try {
      await onCreate(question, title.trim() || undefined, perplexityPrompt.trim() ? perplexityPrompt : undefined, startNow);
      setTitle("");
      setQuestion("");
      setPerplexityPrompt("");
    } catch {
      // Fields intentionally remain until a confirmed ACK.
    }
  };
  return <section className="panel-card create-card">
    <div className="section-heading">
      <p className="eyebrow">Новая сессия</p>
      <h2>Сформулируйте исследование</h2>
      <p>Сессия появится в истории только после подтверждения Python Host.</p>
    </div>
    <form onSubmit={(event) => { event.preventDefault(); void submit(true); }} noValidate>
      <label htmlFor="research-title">Название <span className="optional">необязательно</span></label>
      <input id="research-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} disabled={disabled} />
      <label htmlFor="research-question">Основной промпт для восьми R0</label>
      <textarea id="research-question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={10} required disabled={disabled} aria-describedby={formError ? "question-error" : "main-prompt-help"} />
      <p id="main-prompt-help" className="field-help">Текст без изменений будет передан в восемь независимых заданий.</p>
      {formError && <p id="question-error" className="field-error" role="alert">{formError}</p>}
      <label htmlFor="perplexity-prompt">Поисковый промпт Perplexity <span className="optional">необязательно</span></label>
      <textarea id="perplexity-prompt" value={perplexityPrompt} onChange={(event) => setPerplexityPrompt(event.target.value)} rows={5} disabled={disabled} />
      <div className="dialog-actions">
        <button className="secondary-button" type="button" disabled={disabled || !question.trim()} onClick={() => void submit(false)}>Сохранить черновик</button>
        <button className="primary-button" type="submit" disabled={disabled || !question.trim()}>
          Создать и начать <ArrowIcon />
        </button>
      </div>
    </form>
  </section>;
}

export function SessionHistory({ sessions, activeId, onSelect, nextCursor, onMore, loading }: {
  sessions: SessionSummary[];
  activeId?: string;
  onSelect: (id: string) => void;
  nextCursor: number | null;
  onMore: () => void;
  loading: boolean;
}) {
  return <section className="panel-card history-card">
    <div className="section-heading">
      <p className="eyebrow">История</p>
      <h2>Исследовательские сессии</h2>
    </div>
    {sessions.length === 0 ? <div className="empty-state"><HistoryIcon /><p>Сессий пока нет.</p></div> :
      <ul className="session-list">
        {sessions.map((item) => <li key={item.session_id}>
          <button type="button" className={item.session_id === activeId ? "selected" : ""} onClick={() => onSelect(item.session_id)}>
            <span className="session-list-title">{item.title || "Исследование без названия"}</span>
            <span>{phaseLabels[item.phase]} · {statusLabels[item.status]}</span>
          </button>
        </li>)}
      </ul>}
    {nextCursor !== null && <button type="button" className="secondary-button full-width" onClick={onMore} disabled={loading}>Показать ещё</button>}
  </section>;
}

export function PhaseTimeline({ phase }: { phase: Phase }) {
  const currentIndex = phase === "DRAFT" ? -1 : phase === "DONE" ? PHASES.length : PHASES.findIndex((item) => item.phase === phase);
  return <ol className="phase-timeline" aria-label="Этапы исследования">
    {PHASES.map((item, index) => {
      const state = index < currentIndex ? "complete" : index === currentIndex ? "current" : "future";
      return <li key={item.phase} className={state} aria-current={state === "current" ? "step" : undefined}>
        <span className="step-marker" aria-hidden="true">{index + 1}</span>
        <span>{item.label}</span>
      </li>;
    })}
  </ol>;
}

export interface TaskTools {
  responses: ResponseMetadata[];
  busy: boolean;
  onLoadPrompt: (taskId: string) => Promise<{ text: string; version: number; sha256: string }>;
  onCopyPrompt: (taskId: string) => Promise<unknown>;
  onAwaiting: (taskId: string) => Promise<unknown>;
  onImport: (task: ResearchTask, text: string, mode: "create" | "revise", version?: number, model?: string) => Promise<unknown>;
  onLoadResponse: (response: ResponseMetadata) => Promise<string>;
  onUnavailable: (taskId: string, reason: string) => Promise<unknown>;
  onSkip: (taskId: string, reason: string) => Promise<unknown>;
  onReassign: (taskId: string, providerId: string) => Promise<unknown>;
  onPrepareS1: () => Promise<unknown>;
  onLoadS1Package: () => Promise<S1PackageMetadata>;
  onCopyS1Part: (packageId: string, partIndex: number) => Promise<unknown>;
  onLoadS1Part: (packageId: string, partIndex: number, sizeBytes: number) => Promise<string>;
}

export function TaskList({ tasks, phase, tools }: { tasks: ResearchTask[]; phase: Phase; tools?: TaskTools }) {
  const phaseTasks = tasks.filter((task) => task.phase === phase);
  const current = phase === "COMPARISON"
    ? phaseTasks.filter((task) => task.kind === "S1_SYNTHESIS")
    : phaseTasks;
  const mandatoryR0 = current.filter((task) => task.kind === "INDEPENDENT_RESPONSE");
  const receivedR0 = mandatoryR0.filter((task) => task.status === "RECEIVED").length;
  const [prompt, setPrompt] = useState<{ task: ResearchTask; text: string; version: number; sha256: string } | null>(null);
  const [importTask, setImportTask] = useState<ResearchTask | null>(null);
  const [viewing, setViewing] = useState<{ response: ResponseMetadata; text: string } | null>(null);
  const [actionTask, setActionTask] = useState<ResearchTask | null>(null);
  const [s1Package, setS1Package] = useState<S1PackageMetadata | null>(null);
  const [copiedParts, setCopiedParts] = useState<Set<number>>(() => new Set());
  const [s1PartView, setS1PartView] = useState<{ index: number; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [localError, setLocalError] = useState("");
  const openPrompt = async (task: ResearchTask) => {
    if (!tools) return;
    setLoading(true); setLocalError("");
    try { setPrompt({ task, ...(await tools.onLoadPrompt(task.task_id)) }); }
    catch { setLocalError("Не удалось загрузить задание. Повторите после восстановления соединения."); }
    finally { setLoading(false); }
  };
  const copyPrompt = async (task: ResearchTask) => {
    if (!tools) return;
    setLoading(true); setLocalError("");
    try { await tools.onCopyPrompt(task.task_id); }
    catch { setLocalError("Не удалось записать задание в буфер обмена Windows."); }
    finally { setLoading(false); }
  };
  const openProvider = async (task: ResearchTask) => {
    if (!tools || !task.provider_id || !providerById(task.provider_id)) return;
    openOfficialProvider(task.provider_id);
    if (task.status === "PROMPT_READY") {
      try { await tools.onAwaiting(task.task_id); } catch { /* Global error remains visible. */ }
    }
  };
  const viewResponse = async (response: ResponseMetadata) => {
    if (!tools) return;
    setLoading(true); setLocalError("");
    try { setViewing({ response, text: await tools.onLoadResponse(response) }); }
    catch { setLocalError("Не удалось проверить и загрузить сохранённый ответ."); }
    finally { setLoading(false); }
  };
  const loadS1Package = async () => {
    if (!tools) return;
    setLoading(true); setLocalError("");
    try {
      const loaded = await tools.onLoadS1Package();
      setS1Package(loaded); setCopiedParts(new Set());
    } catch { setLocalError("Не удалось загрузить манифест пакета S1."); }
    finally { setLoading(false); }
  };
  const copyS1Part = async (partIndex: number) => {
    if (!tools || !s1Package) return;
    setLoading(true); setLocalError("");
    try {
      await tools.onCopyS1Part(s1Package.package_id, partIndex);
      setCopiedParts((currentParts) => new Set(currentParts).add(partIndex));
    } catch { setLocalError("Не удалось скопировать часть пакета S1."); }
    finally { setLoading(false); }
  };
  const viewS1Part = async (partIndex: number, sizeBytes: number) => {
    if (!tools || !s1Package) return;
    setLoading(true); setLocalError("");
    try {
      setS1PartView({ index: partIndex, text: await tools.onLoadS1Part(s1Package.package_id, partIndex, sizeBytes) });
    } catch { setLocalError("Не удалось загрузить часть пакета S1."); }
    finally { setLoading(false); }
  };
  return <section className="task-section">
    <div className="section-heading compact">
      <p className="eyebrow">Задания этапа</p>
      <h3>{phase === "INDEPENDENT" ? `Прогресс R0: ${receivedR0}/8` : phase === "COMPARISON" ? "ChatGPT Plus — первый синтез S1" : current.length ? `${current.length} задач` : "Задач пока нет"}</h3>
    </div>
    {phase === "COMPARISON" && !current.some((task) => task.kind === "S1_SYNTHESIS") && tools &&
      <button type="button" className="primary-button" disabled={tools.busy || loading} onClick={() => void tools.onPrepareS1()}>Подготовить пакет S1</button>}
    {current.length > 0 && <ul className="task-list">
      {current.map((task) => {
        const response = tools?.responses.find((item) => item.task_id === task.task_id);
        return <li key={task.task_id} className="task-card">
          <span className="task-index">{task.ordinal + 1}</span>
          <div className="task-identity"><strong>{providerById(task.provider_id)?.label || task.provider_id || "Локальная операция"}</strong><span>{task.kind === "SEARCH_EVIDENCE" ? "Отдельный поиск · не входит в R0" : task.kind === "S1_SYNTHESIS" ? "Первый аналитический синтез" : task.role}</span></div>
          <span className={`status-badge status-${task.status.toLowerCase()}`}><span aria-hidden="true">●</span>{statusLabels[task.status]}</span>
          {tools && task.phase === "INDEPENDENT" && <div className="task-actions">
            <button type="button" className="text-button" disabled={loading || tools.busy} onClick={() => void openPrompt(task)}>Задание</button>
            <button type="button" className="text-button" disabled={loading || tools.busy} onClick={() => void copyPrompt(task)}>Копировать</button>
            {task.provider_id && providerById(task.provider_id) && <button type="button" className="secondary-button" disabled={tools.busy} onClick={() => void openProvider(task)}>Открыть сайт</button>}
            {!response && !["SKIPPED_WITH_REASON"].includes(task.status) && <button type="button" className="primary-button compact-button" disabled={tools.busy} onClick={() => setImportTask(task)}>Импорт ответа</button>}
            {response && <><button type="button" className="secondary-button" disabled={loading} onClick={() => void viewResponse(response)}>Просмотреть</button><button type="button" className="text-button" disabled={tools.busy} onClick={() => setImportTask(task)}>Новая редакция</button></>}
            {!response && <button type="button" className="text-button danger-text" disabled={tools.busy} onClick={() => setActionTask(task)}>Недоступно / замена</button>}
          </div>}
          {tools && task.kind === "S1_SYNTHESIS" && <>
            <div className="task-actions">
              <button type="button" className="secondary-button" disabled={loading || tools.busy} onClick={() => void loadS1Package()}>{s1Package ? "Обновить манифест" : "Показать пакет S1"}</button>
              <button type="button" className="secondary-button" disabled={tools.busy} onClick={() => void openProvider(task)}>Открыть новый чат ChatGPT</button>
              {!response && <button type="button" className="primary-button compact-button" disabled={tools.busy} onClick={() => setImportTask(task)}>Импортировать S1</button>}
              {response && <><button type="button" className="secondary-button" disabled={loading} onClick={() => void viewResponse(response)}>Просмотреть S1</button><button type="button" className="text-button" disabled={tools.busy} onClick={() => setImportTask(task)}>Новая редакция S1</button></>}
            </div>
            {s1Package && <section className="s1-package" aria-label="Части пакета S1">
              <p role="status">Скопировано частей: {copiedParts.size}/{s1Package.part_count}</p>
              <p className="response-meta">{s1Package.r0_complete ? "Полный R0 8/8" : "Частичный R0 с зафиксированным пропуском"} · {(s1Package.total_bytes).toLocaleString("ru-RU")} байт · SHA-256 {s1Package.package_sha256.slice(0, 12)}…</p>
              {s1Package.stale && <p className="field-error" role="alert">Пакет устарел: версии входных материалов изменились. Не используйте его как актуальный.</p>}
              <ol className="s1-part-list">
                {s1Package.parts.map((part) => <li key={part.part_index}>
                  <span>Часть {part.part_index + 1}/{s1Package.part_count} · {part.size_bytes.toLocaleString("ru-RU")} байт</span>
                  <button type="button" className="text-button" disabled={loading || tools.busy || s1Package.stale} onClick={() => void viewS1Part(part.part_index, part.size_bytes)}>Просмотреть</button>
                  <button type="button" className="secondary-button" disabled={loading || tools.busy || s1Package.stale} onClick={() => void copyS1Part(part.part_index)}>{copiedParts.has(part.part_index) ? "Скопировано" : "Копировать"}</button>
                </li>)}
              </ol>
              <p className="action-help">Передайте части по порядку в один новый чат. Синтез запускается инструкцией в последней части.</p>
            </section>}
          </>}
          {task.gap_reason && <p className="task-reason">Причина: {task.gap_reason}</p>}
          {response && <p className="response-meta">Версия {response.version} · {response.size_bytes.toLocaleString("ru-RU")} байт · SHA-256 {response.sha256.slice(0, 12)}… · {new Date(response.created_at).toLocaleString("ru-RU")}</p>}
        </li>;
      })}
    </ul>}
    {localError && <p className="field-error" role="alert">{localError}</p>}
    {prompt && <TextDialog title={`Задание · ${providerById(prompt.task.provider_id)?.label ?? prompt.task.provider_id} · v${prompt.version} · SHA-256 ${prompt.sha256.slice(0, 12)}…`} text={prompt.text} onClose={() => setPrompt(null)} />}
    {viewing && <TextDialog title={`Сохранённый ответ · версия ${viewing.response.version}`} text={viewing.text} onClose={() => setViewing(null)} />}
    {s1PartView && <TextDialog title={`Пакет S1 · часть ${s1PartView.index + 1}/${s1Package?.part_count ?? "?"}`} text={s1PartView.text} onClose={() => setS1PartView(null)} />}
    {importTask && tools && <ResponseImportDialog
      task={importTask}
      current={tools.responses.find((item) => item.task_id === importTask.task_id)}
      onSave={tools.onImport}
      onClose={() => setImportTask(null)}
    />}
    {actionTask && tools && <TaskRecoveryDialog task={actionTask} tasks={current} tools={tools} onClose={() => setActionTask(null)} />}
  </section>;
}

function TextDialog({ title, text, onClose }: { title: string; text: string; onClose: () => void }) {
  const dialogRef = useDialogFocus(onClose);
  return <div className="dialog-backdrop" role="presentation"><section ref={dialogRef} tabIndex={-1} className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="text-dialog-title">
    <div className="dialog-heading"><h2 id="text-dialog-title">{title}</h2><button type="button" className="icon-button" onClick={onClose} aria-label="Закрыть">×</button></div>
    <pre className="text-viewer">{text}</pre>
  </section></div>;
}

function ResponseImportDialog({ task, current, onSave, onClose }: {
  task: ResearchTask;
  current?: ResponseMetadata;
  onSave: TaskTools["onImport"];
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [model, setModel] = useState("");
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [error, setError] = useState("");
  const bytes = new TextEncoder().encode(text).byteLength;
  const valid = Boolean(text.trim()) && bytes <= 8 * 1024 * 1024;
  const providerLabel = providerById(task.provider_id)?.label ?? task.provider_id ?? "Не указан";
  const requestClose = () => {
    if (!savingRef.current && (!text || window.confirm("Неподтверждённый текст не сохранён. Закрыть форму?"))) onClose();
  };
  const dialogRef = useDialogFocus(requestClose);
  const save = async () => {
    if (!valid || savingRef.current) return;
    savingRef.current = true;
    setSaving(true); setError("");
    try {
      await onSave(task, text, current ? "revise" : "create", current?.version, model);
      onClose();
    } catch { setError("Ответ не сохранён. Текст оставлен в форме; восстановите соединение и повторите подтверждение."); }
    finally { savingRef.current = false; setSaving(false); }
  };
  return <div className="dialog-backdrop" role="presentation"><section ref={dialogRef} tabIndex={-1} className="dialog-card import-dialog" role="dialog" aria-modal="true" aria-labelledby="import-title">
    <div className="dialog-heading"><div><p className="eyebrow">Шаг {preview ? "2 из 2" : "1 из 2"}</p><h2 id="import-title">{current ? "Новая редакция ответа" : "Импорт ответа"} · {providerLabel}</h2></div><button type="button" className="icon-button" onClick={requestClose} aria-label="Закрыть">×</button></div>
    <p className="import-metadata">Источник: <strong>web_manual</strong> · Модель: <strong>{model.trim() || "не указана"}</strong></p>
    {!preview ? <>
      <label htmlFor="response-text">Текст из официального веб-чата</label>
      <textarea id="response-text" rows={14} value={text} onChange={(event) => setText(event.target.value)} disabled={saving} />
      <label htmlFor="reported-model">Указанная модель <span className="optional">необязательно</span></label>
      <input id="reported-model" value={model} onChange={(event) => setModel(event.target.value)} maxLength={200} disabled={saving} />
      <p className="byte-count">{bytes.toLocaleString("ru-RU")} / {(8 * 1024 * 1024).toLocaleString("ru-RU")} байт</p>
      {!text.trim() && <p className="field-error">Пустой ответ сохранить нельзя.</p>}
      <button type="button" className="primary-button" disabled={!valid} onClick={() => setPreview(true)}>Перейти к проверке</button>
    </> : <>
      <p>Проверьте начало и конец: неполное копирование из веб-чата нельзя обнаружить автоматически. HTML и Markdown не исполняются.</p>
      <div className="response-fragments"><div><strong>Начало</strong><pre className="text-viewer preview">{text.slice(0, 1000)}</pre></div><div><strong>Конец</strong><pre className="text-viewer preview">{text.slice(-1000)}</pre></div></div>
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="dialog-actions"><button type="button" className="secondary-button" disabled={saving} onClick={() => setPreview(false)}>Назад</button><button type="button" className="primary-button" disabled={saving} onClick={() => void save()}>{saving ? "Сохранение…" : "Подтвердить и сохранить"}</button></div>
    </>}
  </section></div>;
}

function TaskRecoveryDialog({ task, tasks, tools, onClose }: { task: ResearchTask; tasks: ResearchTask[]; tools: TaskTools; onClose: () => void }) {
  const [reason, setReason] = useState(task.gap_reason ?? "");
  const [provider, setProvider] = useState("");
  const [error, setError] = useState("");
  const used = new Set(tasks.filter((item) => item.task_id !== task.task_id).map((item) => item.provider_id));
  const run = async (action: () => Promise<unknown>) => { try { setError(""); await action(); onClose(); } catch { setError("Изменение не сохранено. Проверьте состояние сессии и повторите."); } };
  const dialogRef = useDialogFocus(onClose);
  return <div className="dialog-backdrop" role="presentation"><section ref={dialogRef} tabIndex={-1} className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="recovery-title">
    <div className="dialog-heading"><h2 id="recovery-title">Недоступный источник</h2><button type="button" className="icon-button" onClick={onClose} aria-label="Закрыть">×</button></div>
    <label htmlFor="gap-reason">Содержательная причина</label><textarea id="gap-reason" rows={4} value={reason} onChange={(event) => setReason(event.target.value)} />
    <div className="dialog-actions"><button type="button" className="secondary-button" disabled={!reason.trim()} onClick={() => void run(() => task.status === "UNAVAILABLE" ? tools.onSkip(task.task_id, reason) : tools.onUnavailable(task.task_id, reason))}>{task.status === "UNAVAILABLE" ? "Пропустить с причиной" : "Отметить недоступным"}</button></div>
    <label htmlFor="replacement-provider">Или назначить другой официальный сервис</label>
    <select id="replacement-provider" value={provider} onChange={(event) => setProvider(event.target.value)}><option value="">Выберите сервис</option>{PROVIDERS.filter((item) => !used.has(item.id)).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
    <button type="button" className="primary-button" disabled={!provider} onClick={() => void run(() => tools.onReassign(task.task_id, provider))}>Переназначить</button>
    {error && <p className="field-error" role="alert">{error}</p>}
  </section></div>;
}

function DraftIntakeEditor({ session, busy, onSave }: {
  session: ResearchSession;
  busy: boolean;
  onSave: (question: string, title?: string, perplexityPrompt?: string) => Promise<void> | void;
}) {
  const [title, setTitle] = useState(session.title ?? "");
  const [question, setQuestion] = useState(session.question);
  const [perplexityPrompt, setPerplexityPrompt] = useState(session.perplexity_prompt ?? "");
  const [error, setError] = useState("");
  useEffect(() => {
    setTitle(session.title ?? "");
    setQuestion(session.question);
    setPerplexityPrompt(session.perplexity_prompt ?? "");
  }, [session.intake_version, session.question, session.title, session.perplexity_prompt]);
  const save = async () => {
    if (!question.trim()) { setError("Основной промпт не может быть пустым."); return; }
    setError("");
    await onSave(question, title.trim() || undefined, perplexityPrompt.trim() ? perplexityPrompt : undefined);
  };
  return <section className="question-block" aria-labelledby="draft-intake-heading">
    <h3 id="draft-intake-heading">Постановка исследования · версия {session.intake_version}</h3>
    <label htmlFor="draft-title">Название <span className="optional">необязательно</span></label>
    <input id="draft-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} disabled={busy} />
    <label htmlFor="draft-main-prompt">Основной промпт для восьми R0</label>
    <textarea id="draft-main-prompt" rows={10} value={question} onChange={(event) => setQuestion(event.target.value)} disabled={busy} aria-describedby={error ? "draft-prompt-error" : undefined} />
    {error && <p id="draft-prompt-error" className="field-error" role="alert">{error}</p>}
    <label htmlFor="draft-perplexity-prompt">Поисковый промпт Perplexity <span className="optional">необязательно</span></label>
    <textarea id="draft-perplexity-prompt" rows={5} value={perplexityPrompt} onChange={(event) => setPerplexityPrompt(event.target.value)} disabled={busy} />
    <button type="button" className="secondary-button" disabled={busy || !question.trim()} onClick={() => void save()}>Сохранить изменения</button>
  </section>;
}

export function SessionOverview({ session, tasks, syncing, tools, onUpdateIntake }: {
  session: ResearchSession;
  tasks: ResearchTask[];
  syncing: boolean;
  tools?: TaskTools;
  onUpdateIntake?: (question: string, title?: string, perplexityPrompt?: string) => Promise<void> | void;
}) {
  return <article className="panel-card session-overview">
    <div className="session-title-row">
      <div>
        <p className="eyebrow">{phaseLabels[session.phase]} · rev. {session.revision}</p>
        <h2>{session.title || "Исследование без названия"}</h2>
      </div>
      <span className="status-badge session-status"><span aria-hidden="true">●</span>{statusLabels[session.status]}</span>
    </div>
    {syncing && <p className="sync-label" role="status">Синхронизация…</p>}
    {session.phase === "DRAFT" && onUpdateIntake
      ? <DraftIntakeEditor session={session} busy={tools?.busy ?? false} onSave={onUpdateIntake} />
      : <section className="question-block" aria-labelledby="question-heading">
          <h3 id="question-heading">Основной промпт R0</h3>
          <pre className="text-viewer">{session.question}</pre>
          {session.perplexity_prompt && <><h3>Поисковый промпт Perplexity</h3><pre className="text-viewer">{session.perplexity_prompt}</pre></>}
        </section>}
    <PhaseTimeline phase={session.phase} />
    <TaskList tasks={tasks} phase={session.phase} tools={tools} />
    <div className="future-placeholder">
      <strong>Следующий этап</strong>
      <p>{session.phase === "COMPARISON" ? "После получения S1 три независимых рецензента будут подключены в P1.0.5C." : "Переход выполняется только после серверной проверки полноты текущего этапа."}</p>
    </div>
  </article>;
}

export function SessionActions({ session, tasks, busy, onStart, onPause, onResume, onRefresh, onAdvance }: {
  session: ResearchSession;
  tasks: ResearchTask[];
  busy: boolean;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onRefresh: () => void;
  onAdvance: (gapReason?: string) => void;
}) {
  const currentTasks = tasks.filter((task) => task.phase === session.phase);
  const gateTasks = session.phase === "INDEPENDENT"
    ? currentTasks.filter((task) => task.kind === "INDEPENDENT_RESPONSE")
    : currentTasks;
  const received = gateTasks.filter((task) => task.status === "RECEIVED").length;
  const skipped = gateTasks.filter((task) => task.status === "SKIPPED_WITH_REASON").length;
  const needsGapReason = session.phase === "INDEPENDENT" && received === 7 && skipped === 1;
  const [transitionReason, setTransitionReason] = useState("");
  const canAdvance = session.status === "ACTIVE" && !["DRAFT", "COMPARISON", "DONE"].includes(session.phase);
  return <aside className="panel-card action-panel" aria-label="Действия сессии">
    <div className="section-heading compact"><p className="eyebrow">Управление</p><h2>Доступные действия</h2></div>
    {session.phase === "DRAFT" && session.status === "ACTIVE" &&
      <button className="primary-button full-width" type="button" onClick={onStart} disabled={busy}>Начать исследование <ArrowIcon /></button>}
    {session.status === "ACTIVE" && session.phase !== "DONE" &&
      <button className="secondary-button full-width" type="button" onClick={onPause} disabled={busy}>Пауза</button>}
    {["PAUSED", "BLOCKED"].includes(session.status) &&
      <button className="primary-button full-width" type="button" onClick={onResume} disabled={busy}>Возобновить</button>}
    <button className="secondary-button full-width" type="button" onClick={onRefresh} disabled={busy}><RefreshIcon />Обновить</button>
    {session.phase !== "DRAFT" && session.phase !== "COMPARISON" && session.phase !== "DONE" && <>
      {needsGapReason && <><label htmlFor="transition-gap-reason">Причина перехода с пропуском</label><textarea id="transition-gap-reason" rows={3} value={transitionReason} onChange={(event) => setTransitionReason(event.target.value)} disabled={busy} /></>}
      <button className="secondary-button full-width" type="button" onClick={() => onAdvance(transitionReason)} disabled={busy || !canAdvance || (needsGapReason && !transitionReason.trim())}>Следующий этап <ArrowIcon /></button>
      <p className="action-help">Получено: {received} из {gateTasks.length}. Итоговое решение о переходе принимает Python Host.</p>
    </>}
    {session.phase === "COMPARISON" && <p className="action-help">Переход к рецензированию будет доступен после реализации P1.0.5C. Старый конвейер отключён.</p>}
  </aside>;
}

export function ErrorBanner({ code, message, retry, onRetry, onDismiss }: {
  code: string;
  message: string;
  retry: boolean;
  onRetry?: () => void;
  onDismiss: () => void;
}) {
  return <div className="notice error-notice" role="alert">
    <StatusIcon state="error" />
    <div><strong>{code === "STALE_REVISION" ? "Сессия была изменена в другом окне" : "Операция не выполнена"}</strong><p>{message}</p>
      <div className="inline-actions">{retry && onRetry && <button type="button" className="secondary-button" onClick={onRetry}>Повторить с тем же запросом</button>}<button type="button" className="text-button" onClick={onDismiss}>Закрыть</button></div>
    </div>
  </div>;
}
