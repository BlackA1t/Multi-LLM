import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ConnectionState, ResearchSession, ResearchTask, SessionSummary } from "../../native/contracts";
import {
  type MutationCommand,
  SessionController,
  UiBridgeError,
} from "../../state/session-controller";

export type ViewName = "new" | "history" | "session";

interface UiError {
  code: string;
  message: string;
  retryable: boolean;
}

export function useResearch() {
  const controller = useMemo(() => new SessionController(), []);
  const [connection, setConnection] = useState<ConnectionState>("CONNECTING");
  const [view, setView] = useState<ViewName>("new");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [session, setSession] = useState<ResearchSession | null>(null);
  const [tasks, setTasks] = useState<ResearchTask[]>([]);
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<UiError | null>(null);
  const [retryCommand, setRetryCommand] = useState<MutationCommand | null>(null);
  const selectedId = useRef<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingCreate = useRef<{ fingerprint: string; key: string } | null>(null);

  const describeError = useCallback((caught: unknown): UiError => {
    if (caught instanceof UiBridgeError) {
      setConnection(caught.connectionState);
      return { code: caught.detail.code, message: caught.detail.message, retryable: caught.detail.retryable };
    }
    return { code: "INTERNAL_ERROR", message: "Не удалось выполнить действие", retryable: false };
  }, []);

  const loadHistory = useCallback(async (cursor = 0) => {
    const result = await controller.list(cursor);
    setSessions((current) => cursor === 0 ? result.items : [...current, ...result.items]);
    setNextCursor(result.next_cursor);
    setConnection("CONNECTED");
  }, [controller]);

  const refreshSession = useCallback(async (
    sessionId = selectedId.current,
    preserveError = false,
  ) => {
    if (!sessionId) return null;
    setSyncing(true);
    try {
      const [freshSession, freshTasks] = await Promise.all([
        controller.get(sessionId),
        controller.tasks(sessionId),
      ]);
      selectedId.current = sessionId;
      setSession(freshSession);
      setTasks(freshTasks);
      setConnection("CONNECTED");
      if (!preserveError) setError(null);
      return freshSession;
    } catch (caught) {
      setError(describeError(caught));
      return null;
    } finally {
      setSyncing(false);
    }
  }, [controller, describeError]);

  const selectSession = useCallback(async (sessionId: string) => {
    selectedId.current = sessionId;
    setView("session");
    await chrome.storage.local.set({ activeSessionId: sessionId });
    await refreshSession(sessionId);
  }, [refreshSession]);

  const connect = useCallback(async () => {
    setBusy(true);
    setError(null);
    setConnection("CONNECTING");
    try {
      const state = await controller.reconnect();
      setConnection(state);
      await loadHistory(0);
      const stored = await chrome.storage.local.get("activeSessionId");
      if (typeof stored.activeSessionId === "string") await selectSession(stored.activeSessionId);
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }, [controller, describeError, loadHistory, selectSession]);

  useEffect(() => {
    void connect();
  }, [connect]);

  useEffect(() => {
    const scheduleRefresh = () => {
      if (!selectedId.current) return;
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(() => void refreshSession(), 150);
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") scheduleRefresh();
    };
    const onMessage = (message: unknown) => {
      if (typeof message === "object" && message !== null && "kind" in message
        && message.kind === "session.changed" && "sessionId" in message
        && message.sessionId === selectedId.current) scheduleRefresh();
    };
    window.addEventListener("focus", scheduleRefresh);
    document.addEventListener("visibilitychange", onVisibility);
    chrome.runtime.onMessage.addListener(onMessage);
    return () => {
      window.removeEventListener("focus", scheduleRefresh);
      document.removeEventListener("visibilitychange", onVisibility);
      chrome.runtime.onMessage.removeListener(onMessage);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [refreshSession]);

  const createSession = useCallback(async (question: string, title?: string) => {
    setBusy(true);
    setError(null);
    try {
      const fingerprint = JSON.stringify([question, title ?? null]);
      if (pendingCreate.current?.fingerprint !== fingerprint) {
        pendingCreate.current = { fingerprint, key: crypto.randomUUID() };
      }
      const created = await controller.create(question, title, pendingCreate.current.key);
      pendingCreate.current = null;
      await loadHistory(0);
      await selectSession(created.session_id);
    } catch (caught) {
      const described = describeError(caught);
      setError(described);
      if (!["HOST_UNAVAILABLE", "DISCONNECTED"].includes(described.code)) pendingCreate.current = null;
      throw caught;
    } finally {
      setBusy(false);
    }
  }, [controller, describeError, loadHistory, selectSession]);

  const runMutation = useCallback(async (command: MutationCommand) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await controller.mutate(command);
      setRetryCommand(null);
      await refreshSession(updated.session_id);
      await loadHistory(0);
    } catch (caught) {
      const described = describeError(caught);
      setError(described);
      if (["HOST_UNAVAILABLE", "DISCONNECTED"].includes(described.code)) setRetryCommand(command);
      if (described.code === "STALE_REVISION") {
        const fresh = await refreshSession(undefined, true);
        if (fresh) {
          setRetryCommand({
            ...command,
            args: { ...command.args, expected_revision: fresh.revision },
          });
        }
      }
    } finally {
      setBusy(false);
    }
  }, [controller, describeError, loadHistory, refreshSession]);

  const clearActive = useCallback(async () => {
    selectedId.current = null;
    setSession(null);
    setTasks([]);
    setView("new");
    await chrome.storage.local.remove("activeSessionId");
  }, []);

  return {
    connection,
    view,
    setView,
    sessions,
    nextCursor,
    session,
    tasks,
    busy,
    syncing,
    error,
    retryCommand,
    connect,
    loadHistory,
    refreshSession,
    selectSession,
    createSession,
    runMutation,
    retryMutation: retryCommand ? () => runMutation(retryCommand) : null,
    start: session ? () => runMutation(controller.startCommand(session)) : null,
    pause: session ? () => runMutation(controller.pauseCommand(session)) : null,
    resume: session ? () => runMutation(controller.resumeCommand(session)) : null,
    advance: session ? () => runMutation(controller.advanceCommand(session)) : null,
    clearActive,
  };
}
