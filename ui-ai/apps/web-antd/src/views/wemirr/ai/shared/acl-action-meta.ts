/**
 * ACL 动作码的前端展示表：图标 + 悬停说明（+ 一份本地兜底分组）。
 *
 * 为什么放在前端而不是后端：后端 /acl/resources 下发的是「有哪些动作、叫什么名字、
 * 怎么分组」（RESOURCE_SPECS + ACTION_LABELS + groups，鉴权与文案的唯一来源），
 * 图标和解释句是纯展示层的东西，后端加字段只会让接口变胖。这里只维护展示，
 * **名称与分组一律用后端下发的**，不在这份表里写 label，
 * 否则后端改了文案页面上还是旧的（同名不同义比缺图标更难查）。
 *
 * 分组从后端下发（每个资源在 spec 里各写各的组，如知识库的 知识库操作/库内文件/知识库引用），
 * `group` 字段只在后端没带 groups 时兜底分两组（旧版接口/新资源没写分组）：
 * - action：这个资源的列表页上就有同名按钮/图标位。所以 ACTION_LABELS 必须与按钮文案逐字相同，
 *   数量也要对得上 —— 多出来的项等于「授了也没入口」，缺的项等于「页面上的按钮没人能授」；
 * - scope：页面上没有按钮，但它是真鉴权动作。view 决定这条资源在不在别人列表里出现，
 *   use 决定它能不能被检索/被引用。这类绝不能因为「页面上没按钮」就从后端清单里删掉，
 *   删了跨部门就永远授不进来看库、参与检索。
 *
 * 未登记的码不报错也不隐藏：退化成问号图标、归到兜底组照常渲染。
 * 这样后端新增一个动作，页面自动多一项能勾（可能图标不对），而不是悄悄授不了。
 */
import type { Component } from 'vue';

import type { AclResourceMeta } from '#/api/acl';

import {
  ApartmentOutlined,
  BlockOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  ExperimentOutlined,
  EyeOutlined,
  FileAddOutlined,
  FileSearchOutlined,
  FormOutlined,
  HistoryOutlined,
  KeyOutlined,
  MessageOutlined,
  PlayCircleOutlined,
  ProfileOutlined,
  QuestionCircleOutlined,
  ReadOutlined,
  ScissorOutlined,
  ShareAltOutlined,
  SwapOutlined,
  ToolOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';

/** 兜底分组（只在后端没下发 groups 时用来分档） */
export type AclActionGroup = 'action' | 'scope';

export interface AclActionDisplay {
  /** 兜底分组：后端下发 groups 时这个字段不参与渲染 */
  group?: AclActionGroup;
  /** @ant-design/icons-vue 的组件（不是 iconify 字符串） */
  icon: Component;
  /** ？号气泡里的说明：讲清「授出去之后对方能做什么」，不复述动作名 */
  tip: string;
}

/** key 是后端 resource_guard 的动作码字面量 */
export const ACL_ACTION_META: Record<string, AclActionDisplay> = {
  apikey: {
    group: 'action',
    icon: KeyOutlined,
    tip: '维护这条资源对外调用的 API Key',
  },
  chat: { group: 'action', icon: MessageOutlined, tip: '与它开始会话对话' },
  chunk: {
    group: 'action',
    icon: ProfileOutlined,
    tip: '查看/修改/停用单条切片，粒度比编辑文档更细',
  },
  content: {
    group: 'action',
    icon: ReadOutlined,
    tip: '读这篇文档解析出来的正文与切片（不是原件，原件走「预览原文件」）',
  },
  copy: {
    group: 'action',
    icon: CopyOutlined,
    tip: '以它为模板复制一份归你自己的副本',
  },
  delete: {
    group: 'action',
    icon: DeleteOutlined,
    tip: '删除这条资源（软删，底层数据异步清理）',
  },
  doc_delete: {
    group: 'action',
    icon: BlockOutlined,
    tip: '删掉这个库里的一篇文档（不动库本身，与「删除知识库」分码）',
  },
  edit: { group: 'action', icon: EditOutlined, tip: '修改它的配置与内容' },
  export: {
    group: 'action',
    icon: DownloadOutlined,
    tip: '下载它的定义文件（导入到别的环境用）',
  },
  graph: {
    group: 'action',
    icon: ApartmentOutlined,
    tip: '先清掉它已有的实体与关系，再重新抽取图谱',
  },
  history: {
    group: 'action',
    icon: HistoryOutlined,
    tip: '查看它的执行记录与每次运行的中间结果',
  },
  preview: {
    group: 'action',
    icon: FileSearchOutlined,
    tip: '取原件的只读地址，在页面外预览原始文件',
  },
  rename: {
    group: 'action',
    icon: FormOutlined,
    tip: '只改显示名称，不动内容与配置',
  },
  reparse: {
    group: 'action',
    icon: ScissorOutlined,
    tip: '先删已有向量，再按当前分块配置重新分块与向量化',
  },
  replace: {
    group: 'action',
    icon: SwapOutlined,
    tip: '整包替换它的内容文件（会覆盖上一版）',
  },
  share: {
    group: 'action',
    icon: ShareAltOutlined,
    tip: '把你对这条资源的权限继续授给别人（只有归属人和管理员能转授）',
  },
  template: {
    group: 'action',
    icon: FileAddOutlined,
    tip: '把它存成可复用的模板',
  },
  test: {
    group: 'action',
    icon: ExperimentOutlined,
    tip: '填参数试跑一次，不产生正式调用记录',
  },
  tools: {
    group: 'action',
    icon: ToolOutlined,
    tip: '列出它提供的工具清单（供节点下拉引用）',
  },
  upload: {
    group: 'action',
    icon: UploadOutlined,
    tip: '往这条知识库里新增资源文件',
  },
  use: {
    group: 'scope',
    icon: PlayCircleOutlined,
    tip: '让它可以被引用：知识库被检索命中、工具/工作流被节点调用',
  },
  view: {
    group: 'scope',
    icon: EyeOutlined,
    tip: '让它可以被看见：列表里出现、详情页打得开，但不含任何修改',
  },
};

/** 分组标题与一句话解释，渲染在勾选框上方的分组行 */
export const ACL_ACTION_GROUPS: Record<
  AclActionGroup,
  { hint: string; label: string }
> = {
  action: {
    label: '页面操作',
    hint: '与列表页上的按钮一一对应，勾了才会看到那个按钮',
  },
  scope: {
    label: '可见与引用',
    hint: '页面上没有对应按钮：查看决定能不能看见它，使用决定它能不能被检索/被引用',
  },
};

/**
 * 图片型/音视频型知识库没有「解析产物」这一层（不分块、不切片、不抽图谱）。
 * 这四个码对它们不适用：置灰而不是隐藏 —— 在册授权要还能看见，
 * 隐藏掉的项会在整表提交时被当成「取消勾选」默默撤掉。
 * 值与 kb_type 的 doc/image/audio_video 对齐（后端 KB_FILE_TO_DOC 同族）。
 */
export const ACL_MEDIA_INAPPLICABLE = ['content', 'chunk', 'reparse', 'graph'];

/** 按知识库类型算出该置灰的动作码；非知识库资源（不传 kbType）一律不适用为空 */
export function aclInapplicableActions(
  kbType?: '' | 'audio_video' | 'doc' | 'image',
): string[] {
  return kbType && kbType !== 'doc' ? ACL_MEDIA_INAPPLICABLE : [];
}

/** 未知动作码的退化展示：问号图标 + 提示去后端补这份表 */
const FALLBACK: AclActionDisplay = {
  icon: QuestionCircleOutlined,
  tip: '前端展示表里还没有这个动作码（后端新增的），名称以后端为准，图标与说明待补',
};

export function aclActionDisplay(code: string): AclActionDisplay {
  return ACL_ACTION_META[code] ?? FALLBACK;
}

export function aclActionIcon(code: string): Component {
  return aclActionDisplay(code).icon;
}

export function aclActionTip(code: string): string {
  return aclActionDisplay(code).tip;
}

/** 兜底分档：没写 group 的码（后端新增的）按 action 处理，绝不静默丢掉一个勾选项 */
export function splitAclActions(
  options: { code: string; name: string }[],
): Record<AclActionGroup, { code: string; name: string }[]> {
  const buckets: Record<AclActionGroup, { code: string; name: string }[]> = {
    action: [],
    scope: [],
  };
  for (const opt of options) {
    buckets[aclActionDisplay(opt.code).group ?? 'action'].push(opt);
  }
  return buckets;
}

/** 弹窗里一组「操作范围」的渲染入参 */
export interface AclRenderGroup {
  hint: string;
  key: string;
  label: string;
  options: { code: string; name: string }[];
}

/**
 * 算出这一行该渲染哪几组：后端下发的 groups 优先（分类是鉴权语义的一部分，写在自己
 * 的 spec 里），没下发才退回本地兜底的两组。保持后端给的组序与组内项序，不做二次排序。
 *
 * 后端漏把一个码归组时不丢项：单列一组「其他动作」并写明原因，
 * 否则「后端能授、页面上没得选」会变成静默失效（比图标不对严重得多）。
 */
export function resolveAclGroups(
  meta: AclResourceMeta | null,
): AclRenderGroup[] {
  const actions = meta?.actions ?? [];
  const groups = meta?.groups ?? [];
  if (groups.length === 0) {
    const buckets = splitAclActions(actions);
    return (['action', 'scope'] as const)
      .filter((key) => buckets[key].length > 0)
      .map((key) => ({
        key,
        hint: ACL_ACTION_GROUPS[key].hint,
        label: ACL_ACTION_GROUPS[key].label,
        options: buckets[key],
      }));
  }
  const listed = new Set<string>();
  const out: AclRenderGroup[] = groups.map((group) => {
    const options = group.actions.filter((a) => {
      if (listed.has(a.code)) return false;
      listed.add(a.code);
      return true;
    });
    return {
      key: group.key,
      hint: group.hint ?? '',
      label: group.name,
      options,
    };
  });
  const rest = actions.filter((a) => !listed.has(a.code));
  if (rest.length > 0) {
    out.push({
      key: 'other',
      hint: '后端 groups 还没把这些动作归组（仍可勾选，名称以后端为准）',
      label: '其他动作',
      options: rest,
    });
  }
  // 组里一项都没有就不渲染表头，免得上出现一行组名后面什么都没有
  return out.filter((group) => group.options.length > 0);
}
