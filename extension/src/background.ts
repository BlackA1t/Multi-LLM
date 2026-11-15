import { BridgeFailure, NativeBridge } from "./native/bridge";
import { isUiMessage, type BridgeReply, type UiMessage } from "./native/contracts";

const bridge = new NativeBridge();
const MUTATIONS = new Set([
  "session.create", "session.update_intake", "session.start", "session.pause", "session.resume", "session.advance",
  "task.mark_awaiting", "response.upload.commit", "task.mark_unavailable", "task.skip", "task.reassign",
]);

chrome.runtime.onInstalled.addListener(() => {
  if (chrome.sidePanel?.setPanelBehavior) {
    void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => undefined);
  }
});

function trustedSender(sender: chrome.runtime.MessageSender): boolean {
  if (sender.id !== chrome.runtime.id || !sender.url) return false;
  return sender.url.startsWith(`chrome-extension://${chrome.runtime.id}/`);
}

async function handleMessage(message: UiMessage): Promise<BridgeReply> {
  try {
    if (message.kind === "bridge.status") {
      return { ok: true, connectionState: bridge.connectionState };
    }
    if (message.kind === "bridge.connect") {
      const result = await bridge.reconnect();
      return { ok: true, result, connectionState: bridge.connectionState };
    }
    const result = await bridge.request(message.op, message.args);
    if (MUTATIONS.has(message.op)) {
      const sessionId = typeof message.args.session_id === "string"
        ? message.args.session_id
        : typeof result === "object" && result && "session_id" in result
          ? String(result.session_id)
          : null;
      if (sessionId) {
        void chrome.runtime.sendMessage({ kind: "session.changed", sessionId }).catch(() => undefined);
      }
    }
    return { ok: true, result, connectionState: bridge.connectionState };
  } catch (error) {
    const failure = error instanceof BridgeFailure
      ? error
      : new BridgeFailure("INTERNAL_ERROR", "Внутренняя ошибка расширения");
    return {
      ok: false,
      error: { code: failure.code, message: failure.message, retryable: failure.retryable },
      connectionState: bridge.connectionState,
    };
  }
}

chrome.runtime.onMessage.addListener((raw, sender, sendResponse) => {
  if (!trustedSender(sender) || !isUiMessage(raw)) return false;
  void handleMessage(raw).then(sendResponse);
  return true;
});
