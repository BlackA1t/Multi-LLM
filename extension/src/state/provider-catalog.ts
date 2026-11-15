export interface ProviderDefinition {
  id: string;
  label: string;
  officialUrl: string;
}

export const PROVIDERS: readonly ProviderDefinition[] = Object.freeze([
  { id: "chatgpt", label: "ChatGPT", officialUrl: "https://chatgpt.com/" },
  { id: "perplexity", label: "Perplexity", officialUrl: "https://www.perplexity.ai/" },
  { id: "deepseek", label: "DeepSeek", officialUrl: "https://chat.deepseek.com/" },
  { id: "qwen", label: "Qwen", officialUrl: "https://chat.qwen.ai/" },
  { id: "gigachat", label: "GigaChat", officialUrl: "https://giga.chat/" },
  { id: "alice", label: "Алиса", officialUrl: "https://alice.yandex.ru/" },
  { id: "grok", label: "Grok", officialUrl: "https://grok.com/" },
  { id: "gemini", label: "Gemini", officialUrl: "https://gemini.google.com/" },
  { id: "copilot", label: "Copilot", officialUrl: "https://copilot.microsoft.com/" },
]);

export function providerById(id: string | null): ProviderDefinition | undefined {
  return PROVIDERS.find((provider) => provider.id === id);
}

export function openOfficialProvider(id: string): void {
  const provider = providerById(id);
  if (!provider) throw new Error("Unknown provider");
  void chrome.tabs.create({ url: provider.officialUrl });
}
