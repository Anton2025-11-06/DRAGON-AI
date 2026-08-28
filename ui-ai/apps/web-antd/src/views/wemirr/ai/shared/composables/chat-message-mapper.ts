import type { ChatReference } from './stream-events';

export interface ConversationMessageLike {
  displayContent?: null | string;
  id: string;
  references?: ChatReference[];
  role: string;
  thinkingContent?: null | string;
  variables?: {
    references?: ChatReference[];
    [key: string]: any;
  } | null;
}

export interface UiChatMessage {
  content: string;
  key: string;
  references?: ChatReference[];
  role: 'ai' | 'user';
  thinking?: string;
}

export function toChatMessage(message: ConversationMessageLike): UiChatMessage {
  return {
    key: message.id,
    role: message.role === 'ASSISTANT' ? 'ai' : 'user',
    content: message.displayContent || '',
    thinking: message.thinkingContent || '',
    references: persistedReferences(message),
  };
}

export function referenceDisplayIndex(
  reference: ChatReference,
  fallbackIndex: number,
) {
  const sourceIndex = Number(reference.metadata?.index);
  return reference.type === 'web' && Number.isInteger(sourceIndex) && sourceIndex > 0
    ? sourceIndex
    : fallbackIndex + 1;
}

function persistedReferences(message: ConversationMessageLike) {
  if (message.references?.length) {
    return message.references;
  }
  return message.variables?.references || [];
}
