import { useState } from "react";

import { useResearch } from "./use-research";
import {
  AppHeader,
  ConnectionHelp,
  CreateSessionForm,
  ErrorBanner,
  PrimaryNav,
  SessionActions,
  SessionHistory,
  SessionOverview,
} from "./components";

export function ResearchApp({ mode }: { mode: "sidepanel" | "workspace" }) {
  const research = useResearch();
  const [dismissedError, setDismissedError] = useState<string | null>(null);
  const visibleError = research.error && research.error.code !== dismissedError ? research.error : null;
  const showNew = research.view === "new";
  const showHistory = research.view === "history";
  const showSession = research.view === "session" && research.session;
  const taskTools = {
    responses: research.responses,
    busy: research.busy,
    onLoadPrompt: research.loadPrompt,
    onCopyPrompt: research.copyPrompt,
    onAwaiting: research.markAwaiting,
    onImport: research.importResponse,
    onLoadResponse: research.loadResponse,
    onUnavailable: research.markUnavailable,
    onSkip: research.skipTask,
    onReassign: research.reassignTask,
    onPrepareS1: research.prepareS1,
    onLoadS1Package: research.loadS1Package,
    onCopyS1Part: research.copyS1Part,
    onLoadS1Part: research.loadS1Part,
    onPrepareReviewers: research.prepareReviewers,
    onLoadReviewerPackages: research.loadReviewerPackages,
    onCopyReviewerPart: research.copyReviewerPart,
    onLoadReviewerPart: research.loadReviewerPart,
    onPrepareS2: research.prepareS2,
    onLoadS2Package: research.loadS2Package,
    onCopyS2Part: research.copyS2Part,
    onLoadS2Part: research.loadS2Part,
  };
  const actions = research.session ? <SessionActions
    session={research.session}
    tasks={research.tasks}
    busy={research.busy}
    onStart={() => research.start?.()}
    onPause={() => research.pause?.()}
    onResume={() => research.resume?.()}
    onRefresh={() => void research.refreshSession()}
    onAdvance={(gapReason) => research.advance?.(gapReason)}
  /> : null;

  return <div className={`app-shell ${mode}`}>
    <AppHeader connection={research.connection} onReconnect={() => void research.connect()} busy={research.busy} />
    <PrimaryNav
      view={research.view}
      hasSession={Boolean(research.session)}
      onNew={() => research.setView("new")}
      onHistory={() => research.setView("history")}
      onSession={() => research.setView("session")}
    />
    <ConnectionHelp state={research.connection} onReconnect={() => void research.connect()} busy={research.busy} />
    {visibleError && <ErrorBanner
      code={visibleError.code}
      message={visibleError.message}
      retry={Boolean(research.retryMutation)}
      onRetry={research.retryMutation ?? undefined}
      onDismiss={() => setDismissedError(visibleError.code)}
    />}

    {mode === "sidepanel" ? <main className="sidepanel-content">
      {showNew && <CreateSessionForm onCreate={research.createSession} disabled={research.busy || research.connection !== "CONNECTED"} />}
      {showHistory && <SessionHistory sessions={research.sessions} activeId={research.session?.session_id} onSelect={(id) => void research.selectSession(id)} nextCursor={research.nextCursor} onMore={() => research.nextCursor !== null && void research.loadHistory(research.nextCursor)} loading={research.busy} />}
      {showSession && <><SessionOverview session={research.session!} tasks={research.tasks} syncing={research.syncing} tools={taskTools} onUpdateIntake={research.updateIntake ?? undefined} />{actions}</>}
    </main> : <main className="workspace-grid">
      <aside className="workspace-sidebar">
        <CreateSessionForm onCreate={research.createSession} disabled={research.busy || research.connection !== "CONNECTED"} />
        <SessionHistory sessions={research.sessions} activeId={research.session?.session_id} onSelect={(id) => void research.selectSession(id)} nextCursor={research.nextCursor} onMore={() => research.nextCursor !== null && void research.loadHistory(research.nextCursor)} loading={research.busy} />
      </aside>
      <section className="workspace-main">
        {research.session ? <SessionOverview session={research.session} tasks={research.tasks} syncing={research.syncing} tools={taskTools} onUpdateIntake={research.updateIntake ?? undefined} /> : <div className="panel-card empty-state large"><WorkspaceIconFallback /><h2>Выберите или создайте сессию</h2><p>Данные загружаются напрямую из локального Python Host.</p></div>}
      </section>
      <section className="workspace-actions">{actions}</section>
    </main>}
  </div>;
}

function WorkspaceIconFallback() {
  return <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M9 10h12"/></svg>;
}
