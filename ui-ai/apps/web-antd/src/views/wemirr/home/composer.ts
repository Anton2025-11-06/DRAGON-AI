/**
 * 深度探索输入框的「资源芯片」建模。
 *
 * 输入框里可以穿插技能/MCP/工具/知识库/智能体的图标芯片，芯片在 DOM 上是一段
 * 带 data-* 的不可编辑片段，在状态上就是这里这个三元组：source + key + name。
 * key 对挂载资源是资源 id 字符串，对智能体是 `${mode}:${id}`（不同编排模式的 id 会重号）。
 */
export type ComposerChipSource = 'agent' | 'kb' | 'mcp' | 'skill' | 'tool';

export interface ComposerChip {
  key: string;
  name: string;
  source: ComposerChipSource;
}
