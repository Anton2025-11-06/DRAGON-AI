export interface ChatReference {
  chunkId?: string;
  documentId?: string;
  documentName?: string;
  icon?: string;
  itemId?: string;
  metadata?: Record<string, any>;
  score?: number;
  siteName?: string;
  snippet?: string;
  title?: string;
  type?: 'document' | 'graph' | 'web' | string;
  url?: string;
}

export interface StreamState {
  content: string;
  references: ChatReference[];
  thinking: string;
}

export function createStreamState(): StreamState {
  return {
    content: '',
    thinking: '',
    references: [],
  };
}

export function applySseMessage(rawData: string, state: StreamState) {
  if (!rawData) return;

  const processText = (text: string) => text.replaceAll(String.raw`\n`, '\n');

  try {
    const data = JSON.parse(rawData);
    if (typeof data.thinking === 'string') {
      state.thinking += processText(data.thinking);
    }
    if (typeof data.content === 'string') {
      state.content += processText(data.content);
    }
    if (Array.isArray(data.references)) {
      mergeReferences(state.references, data.references);
    }
  } catch {
    state.content += processText(rawData);
  }
}

export function mergeReferences(
  target: ChatReference[],
  incoming: ChatReference[],
) {
  const keys = new Set(target.map(referenceKey));
  incoming.forEach((reference) => {
    const normalized = normalizeReference(reference);
    const key = referenceKey(normalized);
    if (keys.has(key)) return;
    keys.add(key);
    target.push(normalized);
  });
}

function normalizeReference(reference: ChatReference): ChatReference {
  const title =
    reference.title ||
    reference.documentName ||
    reference.siteName ||
    reference.url ||
    '引用来源';
  return {
    ...reference,
    title,
  };
}

function referenceKey(reference: ChatReference) {
  if (reference.url) return `web:${reference.url}`;
  if (reference.chunkId) return `chunk:${reference.chunkId}`;
  if (reference.documentId) return `document:${reference.documentId}`;
  return `${reference.type || 'unknown'}:${reference.title || ''}:${reference.snippet || ''}`;
}
