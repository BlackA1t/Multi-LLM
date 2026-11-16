import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ConnectionState, ResearchSession, ResearchTask, ResponseMetadata, SessionSummary } from "../../native/contracts";
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
  const [responses, setResponses] = useState<ResponseMetadata[]>([]);
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<UiError | null>(null);
  const [retryCommand, setRetryCommand] = useState<MutationCommand | null>(null);
  const selectedId = useRef<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingCreate = useRef<{ fingerprint: string; key: string } | null>(null);
  const pendingImport = useRef<{ fingerprint: string; key: string } | null>(null);

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
      const [freshSession, freshTasks, freshResponses] = await Promise.all([
        controller.get(sessionId),
        controller.tasks(sessionId),
        controller.responses(sessionId),
      ]);
      selectedId.current = sessionId;
      setSession(freshSession);
      setTasks(freshTasks);
      setResponses(freshResponses);
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

  const createSession = useCallback(async (
    question: string,
    title?: string,
    perplexityPrompt?: string,
    startNow = true,
  ) => {
    setBusy(true);
    setError(null);
    let startCommand: MutationCommand | null = null;
    try {
      const fingerprint = JSON.stringify([question, title ?? null, perplexityPrompt ?? null, startNow]);
      if (pendingCreate.current?.fingerprint !== fingerprint) {
        pendingCreate.current = { fingerprint, key: crypto.randomUUID() };
      }
      const created = await controller.create(question, title, perplexityPrompt, pendingCreate.current.key);
      pendingCreate.current = null;
      await loadHistory(0);
      await selectSession(created.session_id);
      if (startNow) {
        startCommand = controller.startCommand(created);
        await controller.mutate(startCommand);
        await refreshSession(created.session_id);
        await loadHistory(0);
      }
    } catch (caught) {
      const described = describeError(caught);
      setError(described);
      if (startCommand && ["HOST_UNAVAILABLE", "DISCONNECTED"].includes(described.code)) {
        setRetryCommand(startCommand);
      }
      if (!["HOST_UNAVAILABLE", "DISCONNECTED"].includes(described.code)) pendingCreate.current = null;
      throw caught;
    } finally {
      setBusy(false);
    }
  }, [controller, describeError, loadHistory, refreshSession, selectSession]);

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
    setResponses([]);
    setView("new");
    await chrome.storage.local.remove("activeSessionId");
  }, []);

  const taskAction = useCallback(async <T,>(action: () => Promise<T>): Promise<T> => {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      await refreshSession();
      return result;
    } catch (caught) {
      setError(describeError(caught));
      throw caught;
    } finally {
      setBusy(false);
    }
  }, [describeError, refreshSession]);

  const importResponse = useCallback(async (
    task: ResearchTask,
    text: string,
    mode: "create" | "revise",
    expectedResultVersion?: number,
    reportedModel?: string,
  ) => {
    if (!session) throw new Error("No active session");
    const fingerprint = JSON.stringify([session.session_id, task.task_id, text, mode, expectedResultVersion, reportedModel]);
    if (pendingImport.current?.fingerprint !== fingerprint) {
      pendingImport.current = { fingerprint, key: crypto.randomUUID() };
    }
    try {
      const receipt = await taskAction(() => controller.importResponse(
        session, task, text, mode, pendingImport.current!.key, expectedResultVersion, reportedModel,
      ));
      pendingImport.current = null;
      return receipt;
    } catch (caught) {
      const code = caught instanceof UiBridgeError ? caught.detail.code : "INTERNAL_ERROR";
      if (!["HOST_UNAVAILABLE", "DISCONNECTED"].includes(code)) pendingImport.current = null;
      throw caught;
    }
  }, [controller, session, taskAction]);

  return {
    connection,
    view,
    setView,
    sessions,
    nextCursor,
    session,
    tasks,
    responses,
    busy,
    syncing,
    error,
    retryCommand,
    connect,
    loadHistory,
    refreshSession,
    selectSession,
    createSession,
    updateIntake: session ? (
      question: string, title?: string, perplexityPrompt?: string,
    ) => runMutation(controller.updateIntakeCommand(session, question, title, perplexityPrompt)) : null,
    runMutation,
    retryMutation: retryCommand ? () => runMutation(retryCommand) : null,
    start: session ? () => runMutation(controller.startCommand(session)) : null,
    pause: session ? () => runMutation(controller.pauseCommand(session)) : null,
    resume: session ? () => runMutation(controller.resumeCommand(session)) : null,
    advance: session ? (gapReason?: string) => runMutation(controller.advanceCommand(session, crypto.randomUUID(), gapReason)) : null,
    loadPrompt: (taskId: string) => session ? controller.prompt(session.session_id, taskId) : Promise.reject(new Error("No session")),
    copyPrompt: (taskId: string) => session ? taskAction(() => controller.copyPrompt(session.session_id, taskId)) : Promise.reject(new Error("No session")),
    markAwaiting: (taskId: string) => session ? taskAction(() => controller.markAwaiting(session, taskId)) : Promise.reject(new Error("No session")),
    importResponse,
    loadResponse: (response: ResponseMetadata) => session ? controller.responseText(session.session_id, response) : Promise.reject(new Error("No session")),
    markUnavailable: (taskId: string, reason: string) => session ? taskAction(() => controller.markUnavailable(session, taskId, reason)) : Promise.reject(new Error("No session")),
    skipTask: (taskId: string, reason: string) => session ? taskAction(() => controller.skip(session, taskId, reason)) : Promise.reject(new Error("No session")),
    reassignTask: (taskId: string, providerId: string) => session ? taskAction(() => controller.reassign(session, taskId, providerId)) : Promise.reject(new Error("No session")),
    prepareS1: () => session ? taskAction(() => controller.prepareS1(session)) : Promise.reject(new Error("No session")),
    loadS1Package: () => session ? controller.s1Package(session.session_id) : Promise.reject(new Error("No session")),
    copyS1Part: (packageId: string, partIndex: number) => session
      ? taskAction(() => controller.copyS1Part(session.session_id, packageId, partIndex))
      : Promise.reject(new Error("No session")),
    loadS1Part: (packageId: string, partIndex: number, sizeBytes: number) => session
      ? controller.s1PartText(session.session_id, packageId, partIndex, sizeBytes)
      : Promise.reject(new Error("No session")),
    prepareReviewers: () => session
      ? taskAction(() => controller.prepareReviewers(session))
      : Promise.reject(new Error("No session")),
    loadReviewerPackages: () => session
      ? controller.reviewerPackages(session.session_id)
      : Promise.reject(new Error("No session")),
    copyReviewerPart: (packageId: string, partIndex: number) => session
      ? taskAction(() => controller.copyReviewerPart(session.session_id, packageId, partIndex))
      : Promise.reject(new Error("No session")),
    loadReviewerPart: (packageId: string, partIndex: number, sizeBytes: number) => session
      ? controller.reviewerPartText(session.session_id, packageId, partIndex, sizeBytes)
      : Promise.reject(new Error("No session")),
    prepareS2: () => session
      ? taskAction(() => controller.prepareS2(session))
      : Promise.reject(new Error("No session")),
    loadS2Package: () => session
      ? controller.s2Package(session.session_id)
      : Promise.reject(new Error("No session")),
    copyS2Part: (packageId: string, partIndex: number) => session
      ? taskAction(() => controller.copyS2Part(session.session_id, packageId, partIndex))
      : Promise.reject(new Error("No session")),
    loadS2Part: (packageId: string, partIndex: number, sizeBytes: number) => session
      ? controller.s2PartText(session.session_id, packageId, partIndex, sizeBytes)
      : Promise.reject(new Error("No session")),
    clearActive,
  };
}
