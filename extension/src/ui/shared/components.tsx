import { useState, type FormEvent } from "react";

import type { ConnectionState, Phase, ResearchSession, ResearchTask, SessionSummary } from "../../native/contracts";
import { ArrowIcon, HistoryIcon, PlusIcon, RefreshIcon, StatusIcon, WorkspaceIcon } from "./icons";

const PHASES: Array<{ phase: Phase; label: string }> = [
  { phase: "INDEPENDENT", label: "Независимые ответы" },
  { phase: "COMPARISON", label: "Сравнение" },
  { phase: "CRITIQUE", label: "Критика" },
  { phase: "FACT_CHECK", label: "Проверка фактов" },
  { phase: "ARBITRATION", label: "Арбитраж" },
  { phase: "REVIEW", label: "Итог" },
];

const phaseLabels: Record<Phase, string> = {
  DRAFT: "Подготовка",
  INDEPENDENT: "Независимые ответы",
  COMPARISON: "Сравнение",
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
  onCreate: (question: string, title?: string) => Promise<void>;
  disabled: boolean;
}) {
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [formError, setFormError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim()) {
      setFormError("Введите вопрос исследования");
      return;
    }
    setFormError("");
    try {
      await onCreate(question.trim(), title.trim() || undefined);
      setTitle("");
      setQuestion("");
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
    <form onSubmit={submit} noValidate>
      <label htmlFor="research-title">Название <span className="optional">необязательно</span></label>
      <input id="research-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} disabled={disabled} />
      <label htmlFor="research-question">Исследовательский вопрос</label>
      <textarea id="research-question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={7} required disabled={disabled} aria-describedby={formError ? "question-error" : undefined} />
      {formError && <p id="question-error" className="field-error" role="alert">{formError}</p>}
      <button className="primary-button" type="submit" disabled={disabled || !question.trim()}>
        Создать исследование <ArrowIcon />
      </button>
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

export function TaskList({ tasks, phase }: { tasks: ResearchTask[]; phase: Phase }) {
  const current = tasks.filter((task) => task.phase === phase);
  return <section className="task-section">
    <div className="section-heading compact">
      <p className="eyebrow">Задания этапа</p>
      <h3>{current.length ? `${current.length} задач` : "Задач пока нет"}</h3>
    </div>
    {current.length > 0 && <ul className="task-list">
      {current.map((task) => <li key={task.task_id}>
        <span className="task-index">{task.ordinal + 1}</span>
        <div><strong>{task.provider_id || "Локальная операция"}</strong><span>{task.role}</span></div>
        <span className={`status-badge status-${task.status.toLowerCase()}`}><span aria-hidden="true">●</span>{statusLabels[task.status]}</span>
      </li>)}
    </ul>}
  </section>;
}

export function SessionOverview({ session, tasks, syncing }: {
  session: ResearchSession;
  tasks: ResearchTask[];
  syncing: boolean;
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
    <section className="question-block" aria-labelledby="question-heading">
      <h3 id="question-heading">Исходный вопрос</h3>
      <p>{session.question}</p>
    </section>
    <PhaseTimeline phase={session.phase} />
    <TaskList tasks={tasks} phase={session.phase} />
    <div className="future-placeholder">
      <strong>Следующий этап</strong>
      <p>Импорт ответов будет добавлен в P1.0.4. Результаты сравнения появятся после P1.0.4/P1.0.5.</p>
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
  onAdvance: () => void;
}) {
  const currentTasks = tasks.filter((task) => task.phase === session.phase);
  const gateReady = currentTasks.length > 0 && currentTasks.every((task) => task.status === "RECEIVED");
  const canAdvance = session.status === "ACTIVE" && !["DRAFT", "DONE"].includes(session.phase) && gateReady;
  return <aside className="panel-card action-panel" aria-label="Действия сессии">
    <div className="section-heading compact"><p className="eyebrow">Управление</p><h2>Доступные действия</h2></div>
    {session.phase === "DRAFT" && session.status === "ACTIVE" &&
      <button className="primary-button full-width" type="button" onClick={onStart} disabled={busy}>Начать исследование <ArrowIcon /></button>}
    {session.status === "ACTIVE" && session.phase !== "DONE" &&
      <button className="secondary-button full-width" type="button" onClick={onPause} disabled={busy}>Пауза</button>}
    {["PAUSED", "BLOCKED"].includes(session.status) &&
      <button className="primary-button full-width" type="button" onClick={onResume} disabled={busy}>Возобновить</button>}
    <button className="secondary-button full-width" type="button" onClick={onRefresh} disabled={busy}><RefreshIcon />Обновить</button>
    {session.phase !== "DRAFT" && session.phase !== "DONE" && <>
      <button className="secondary-button full-width" type="button" onClick={onAdvance} disabled={busy || !canAdvance}>Следующий этап <ArrowIcon /></button>
      {!gateReady && <p className="action-help">Переход станет доступен после получения всех обязательных результатов текущего этапа.</p>}
    </>}
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
