/**
 * AI 模块公共组件和工具
 * 提供 AI 对话相关的通用组件，可在不同场景复用
 */

// ==================== 组件导出 ====================
export { default as AiSidebar } from './components/AiSidebar.vue';

// ==================== 类型导出 ====================
export type { SidebarGroup, SidebarItem } from './components/AiSidebar.vue';

// ==================== Composables 导出 ====================
export { useSseStream } from './composables/useSseStream';
export {
  referenceDisplayIndex,
  toChatMessage,
  type ConversationMessageLike,
  type UiChatMessage,
} from './composables/chat-message-mapper';
export {
  applySseMessage,
  createStreamState,
  type ChatReference,
  type StreamState,
} from './composables/stream-events';
export type {
  SseStreamOptions,
  SseStreamResult,
} from './composables/useSseStream';
