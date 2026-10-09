<script lang="ts" setup name="RagGraphRetrieve">
/**
 * 图谱检索页（需求 4：从「知识库列表」行内按钮升级为独立菜单页）。
 *
 * 三栏布局对应图谱浏览的三个真实动作：
 * - 左：定范围（选库）+ 找入口（实体检索 / 核心实体榜单，点一下就把它当子图中心）；
 * - 中：看关系（G6 力导向子图，关键词只决定起点，起点之外按库展开全部）；
 * - 右：追原文（点节点看摘要、别名，并回溯到出自哪些文档切片）。
 *
 * 后端 rag/graph/** 一律先过 kb_scope 白名单，并且只留 doc 型且开了图谱的库：
 * 所以「查出来是空」可能是没图，也可能是这些库都没开图谱，statistics 会把两者分开回话。
 *
 * 画布交互：按住左键拖 = 平移整张图，滚轮 = 缩放，Shift+拖 = 摆单个节点，
 * 点一下实体（圆或它下方的名字）= 右侧详情 + 溯源，hover 亮蓝圈的就是能点开的同一个范围。
 * 平移、命中、hover 都不交给 G6 的行为层，走 shared/graph-canvas 那套 DOM 监听（页面只
 * 管「命中之后做什么」），而且监听在建图之前就挂上：建图中间抛一下就把交互摘了，页面会
 * 变成「图看得见但拖不动」。
 *
 * API 复用 ../doc/api.ts（RAG 模块契约唯一来源）。
 */
import type { GraphNodeResp, KbOption } from '../doc/api';

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { useAccess } from '@vben/access';

import {
  AimOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
  SearchOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons-vue';
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  InputNumber,
  message,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  // 实体类型取自后端 rag_constant.KG_ENTITY_TYPES（中文），按原值着色，未命中落 DEFAULT
  // 与检索页的「图谱命中」共用一份表，两处各写一份只会各漂一半
  kgEntityColor as colorOf,
  GraphEntitySearch,
  GraphEntitySources,
  GraphQuery,
  GraphStatistics,
  KbOptions,
} from '../doc/api';
import { attachCanvasInteractions } from '../shared/graph-canvas';

const route = useRoute();
const { hasPermission } = useAccess();
// 页面准入跟后端 @has_permission("ai:kb:graph") 同一位；没这个权限就不发请求，
// 免得一进页面就连着吃三个 403 红条
const allowed = computed(() => hasPermission('ai:kb:graph'));

const options = ref<KbOption[]>([]);
const kbIds = ref<number[]>([]);

const keyword = ref('');
const entityType = ref<string | undefined>();
// 默认只看一跳、20 个节点：力导图一上来就给 2 跳 120 点，首屏要在画布上摆一百多次
// 节点，对视后被压到 MIN_FIT_ZOOM 以下，看上去就是一片糊 —— 先给一个看得清的邻域，
// 要更大范围由人往上加（上限仍与后端 depth≤3 / InputNumber max=500 对齐）。
const depth = ref(1);
const limit = ref(20);

const nodes = ref<GraphNodeResp[]>([]);
const edges = ref<any[]>([]);
const totalNodes = ref(0);
const totalEdges = ref(0);
const truncated = ref(false);
/** 后端给的空白原因（关键词与类型筛空起点时用），空态拿它替掉「库里没图」那句通用文案 */
const emptyMessage = ref('');
/**
 * 建图失败的原因（留在页面上，不只弹 toast）。
 *
 * 以前 render() 中途抛了只在调用点弹一条 toast，画布上什么痕迹都不留、图仍然停在屏幕上，
 * 读者无从知道「拖不动、点不中」是这一抛造成的。
 */
const buildError = ref('');

const stats = ref<Record<string, any>>({});
const loading = ref(false);

// 实体检索（左栏）
const searchKeyword = ref('');
const searchResult = ref<Record<string, any>[]>([]);
const searching = ref(false);

const selectedNode = ref<null | GraphNodeResp>(null);
const sourceDocs = ref<Record<string, any>[]>([]);
const sourceLoading = ref(false);

/**
 * 点中的实体在画布上浮一张小卡。
 *
 * 详情栏在最右栏，点上实体之后画布上没有任何当场反馈，读者就读成「点了没反应」——
 * 尤其窄窗口下右栏要被横向滚动推出去时更是如此。
 */
const cardNode = ref<null | GraphNodeResp>(null);
const cardPos = ref({ left: 0, top: 0 });
const nodeCardStyle = computed(() => ({
  left: `${cardPos.value.left}px`,
  top: `${cardPos.value.top}px`,
}));

const container = ref<HTMLDivElement | null>(null);
let graph: any = null;
/** 画布 DOM 监听的收口句柄：render 会反复重建图，容器却是同一个 DOM，不清会叠一层监听 */
let detachCanvasEvents: (() => void) | null = null;

// 实体类型着色走 doc/api 的 kgEntityColor（图谱检索页与检索页共用一份表）

/** 实体类型下拉优先用统计接口给的类型（图上没出现的类型也能筛），拿不到再退回当前子图里出现过的 */
const entityTypeOptions = computed(() => {
  const fromStats: Record<string, any>[] = stats.value?.types || [];
  if (fromStats.length) {
    return fromStats.map((t) => ({
      label: `${t.type}（${t.count}）`,
      value: String(t.type),
    }));
  }
  const set = new Set<string>();
  nodes.value.forEach((n) => n.entityType && set.add(n.entityType));
  return [...set].map((t) => ({ label: t, value: t }));
});

const kbSelectOptions = computed(() =>
  options.value.map((o) => ({
    label: `${o.name}（${o.kbTypeLabel}）`,
    value: o.id,
  })),
);

const topEntities = computed<Record<string, any>[]>(
  () => stats.value?.topEntities || [],
);

/**
 * 空态文案：后端给了原因就用它。
 *
 * 「没匹配到起点」与「这个范围内根本没图」在画布上是一块同样的白，不把两者分开，
 * 用户只会反复换关键词、或怀疑上面两个过滤条件没生效。
 */
const emptyText = computed(
  () =>
    emptyMessage.value ||
    '当前范围内没有图谱数据：确认所选知识库已开启图谱，且文档已完成「构建图谱」',
);

async function loadOptions() {
  try {
    options.value = await KbOptions();
    // 从知识维护页带 kbId 过来时预选中那个库
    const preset = Number(route.query.kbId || 0);
    if (preset && options.value.some((o) => o.id === preset)) {
      kbIds.value = [preset];
    }
  } catch {
    options.value = [];
  }
}

async function loadStats() {
  if (!allowed.value) return;
  try {
    stats.value = await GraphStatistics({
      kbIds: kbIds.value.join(','),
    });
  } catch {
    stats.value = {};
  }
}

async function load() {
  if (!allowed.value) return;
  loading.value = true;
  try {
    const res = await GraphQuery({
      kbIds: kbIds.value,
      keyword: keyword.value.trim() || undefined,
      entityType: entityType.value,
      depth: depth.value,
      limit: limit.value,
    });
    nodes.value = res.nodes || [];
    edges.value = res.edges || [];
    totalNodes.value = res.totalNodes ?? nodes.value.length;
    totalEdges.value = res.totalEdges ?? edges.value.length;
    truncated.value = !!res.truncated;
    emptyMessage.value = String(res.message || '');
    selectedNode.value = null;
    cardNode.value = null;
    sourceDocs.value = [];
    await nextTick();
    await render();
  } catch (e: any) {
    message.error(e?.message || '加载图谱失败');
    emptyMessage.value = '';
  } finally {
    loading.value = false;
  }
}

/** 对视四周留白；算缩放比用的是 (画布尺寸 - 2×此值) / 内容包围盒 */
const FIT_VIEW_PADDING = 24;
/** 容器小于这个尺寸就继续等排版：留白已占掉 48px，再小必然算出负缩放比 */
const MIN_CANVAS = 60;
/**
 * 对视结果的可读区间：G6 的 minZoom/maxZoom 是 0.02/10，落在两端都等于看不见。
 *
 * 下限原先卡在 0.2：上百个节点的力导图被 linkDistance 撑到几千像素见方，对视老实
 * 算出 0.1 左右，卡在 0.2 等于把整图又放大一倍 —— 只有中心那一小块在视口里，
 * 其余全在屏幕外，就是反馈的「有渲染但特别远/看不到」。宁可小到要手动放大，
 * 也要保证「一整张图都在画面里」，真看不清还有「适应画布」与两个缩放按钮兜底。
 */
const MIN_FIT_ZOOM = 0.08;
const MAX_FIT_ZOOM = 2;

/** 浮在节点下方的详情小卡：宽度、与节点的间距、给卡片留出的竖向高度 */
const CARD_WIDTH = 260;
const CARD_GAP = 10;
const CARD_MAX_HEIGHT = 132;

/** 页面当前缩放（百分比）：下次再出现「有数据但没画面」，一眼能看出是被顶到了哪一端 */
const zoomPct = ref(100);

function nextFrame() {
  return new Promise<void>((resolve) => {
    requestAnimationFrame(() => resolve());
  });
}

function canvasSize(el: HTMLElement) {
  const box = el.getBoundingClientRect();
  return {
    width: Math.max(0, Math.round(box.width)),
    height: Math.max(0, Math.round(box.height)),
  };
}

/**
 * 逐帧等容器被 flex 排版出可用尺寸（最多 20 帧）。
 *
 * 返回的一定是容器实测尺寸，**绝不返回比容器大的兜底值**：G6 的定心与对视都按它自己
 * 记录的 width/height 计算，一旦用 800×520 建图而容器只有 172 宽，整张图会被定心到
 * x≈400 处，而外层 .center-col 是 overflow:hidden 只露出 0~172 —— 图正好落在裁剪区外，
 * 表现就是「接口有数据、子图一块白」（探针实测：内容只有 6% 落在画布内）。
 * 量不到也按实测的来，尺寸后续变化交给 ResizeObserver 校正。
 */
async function waitForCanvasSize(el: HTMLElement) {
  let size = canvasSize(el);
  for (
    let i = 0;
    i < 20 && (size.width < MIN_CANVAS || size.height < MIN_CANVAS);
    i += 1
  ) {
    await nextFrame();
    size = canvasSize(el);
  }
  return size;
}

/**
 * 用节点模型坐标算内容包围盒（含节点半径与标签余量）。
 *
 * 为什么不能直接用 G6 的 fitView/getCanvasBBox：力导布局是异步的（实测 render 之后
 * 约 1.4s 才 afterlayout），而形状的位置比模型坐标更晚落地 —— afterlayout 那一刻模型
 * 已铺到 1318×963，形状包围盒还停在 36×48。拿这个滞后的盒去 fitView 会算出 5 倍
 * **放大**，被 MAX_FIT_ZOOM 钳到 200% 后屏幕上只剩三颗巨无霸节点 = 「子图看不到内容」。
 * 模型坐标是布局的直接产物、永远最新，用它算出来的比例才可信。
 */
function modelBBox(g: any) {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  g.getNodes().forEach((item: any) => {
    const model = item.getModel();
    if (!Number.isFinite(model?.x) || !Number.isFinite(model?.y)) return;
    const size = Array.isArray(model.size) ? model.size : [model.size, model.size];
    // 标签画在节点下方，左右也会外溢，各留 30px 余量
    const halfW = (Number(size[0]) || 40) / 2 + 30;
    const halfH = (Number(size[1]) || 40) / 2 + 30;
    minX = Math.min(minX, model.x - halfW);
    maxX = Math.max(maxX, model.x + halfW);
    minY = Math.min(minY, model.y - halfH);
    maxY = Math.max(maxY, model.y + halfH);
  });
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  return {
    width: maxX - minX,
    height: maxY - minY,
    cx: (minX + maxX) / 2,
    cy: (minY + maxY) / 2,
  };
}

/**
 * 对视的唯一入口：任何时候都只走这里。
 *
 * 比例按模型包围盒与画布可用区算，然后把矩阵直接落到位
 * （canvas = ratio × point + t，让内容盒中心对齐画布中心）。不走 graph.fitView /
 * zoomTo / translate / fitCenter —— 它们内部都要读那个滞后的形状包围盒。
 * fitToken 让「连续多次对视」退化成「最后一次生效」，ResizeObserver 抖动不会叠加。
 */
let fitToken = 0;

async function fitToView(g: any) {
  fitToken += 1;
  const mine = fitToken;
  await nextFrame();
  if (mine !== fitToken || !graph || g !== graph || g.destroyed) return;
  const width = Number(g.get('width')) || 0;
  const height = Number(g.get('height')) || 0;
  const box = modelBBox(g);
  if (!box || width <= 0 || height <= 0 || box.width <= 0 || box.height <= 0) {
    return;
  }
  const ratio = Math.min(
    MAX_FIT_ZOOM,
    Math.max(
      MIN_FIT_ZOOM,
      Math.min(
        (width - 2 * FIT_VIEW_PADDING) / box.width,
        (height - 2 * FIT_VIEW_PADDING) / box.height,
      ),
    ),
  );
  g.get('group').setMatrix([
    ratio,
    0,
    0,
    0,
    ratio,
    0,
    width / 2 - ratio * box.cx,
    height / 2 - ratio * box.cy,
    0,
    0,
    1,
  ]);
  g.autoPaint();
  zoomPct.value = Math.round(ratio * 100);
}

/**
 * 让「按在任意形状上」都能起手拖拽（Shift 摆节点用得上）。
 *
 * 平移已经改由页面听 DOM 自己做，但 G6 的 drag-node 仍要走事件层那道闸：g-base 只在
 * 按下的那个形状 `draggable` 为真时才把这次操作判成拖拽并派发 drag 事件
 * （@antv/g-base/lib/event/event-contoller.js:361）。而 G6 只给节点的 keyShape 补了
 * draggable（g6-core/item/item.js:158），标签文字、边的路径与边标签一个都没有 ——
 * Shift+按住名字想摆节点时按在标签上就不出 drag，表现为「只能拖着圆走」。
 * 这里把节点/边容器里的每个子形状都补上 draggable，让拖拽从哪儿都能起手。
 */
function enableDragOnAllShapes(g: any) {
  const mark = (shape: any) => {
    if (!shape?.set) return;
    shape.set('draggable', true);
    const children = shape.get?.('children');
    if (children) children.forEach((child: any) => mark(child));
  };
  [...(g.getNodes() || []), ...(g.getEdges() || [])].forEach((item: any) =>
    mark(item?.getContainer?.()),
  );
}

/**
 * 把详情浮卡贴到它那个节点下方。
 *
 * 用画布包围盒而不是模型坐标：平移缩放之后只有前者和容器内的 CSS 坐标一一对应，
 * 卡片才跟得住节点。左右各夹住半个卡宽、下方留出卡片的高度，别让卡片探出画布。
 */
function placeCard() {
  const g = graph;
  const el = container.value;
  const node = cardNode.value;
  if (!g || g.destroyed || !el || !node) return;
  const item = g.findById(node.id);
  if (!item) return;
  const box = item.getContainer().getCanvasBBox();
  if (!box || !Number.isFinite(box.minX) || !Number.isFinite(box.maxY)) return;
  const size = canvasSize(el);
  const half = CARD_WIDTH / 2;
  const cx = (box.minX + box.maxX) / 2;
  const left = Math.min(
    Math.max(cx, half + 4),
    Math.max(size.width - half - 4, half + 4),
  );
  const top = Math.min(
    box.maxY + CARD_GAP,
    Math.max(size.height - CARD_MAX_HEIGHT, 4),
  );
  cardPos.value = { left: Math.round(left), top: Math.round(top) };
}

/**
 * 把画布交互接到本页面：平移与命中的通用那一段在 shared/graph-canvas（关系小图共用
 * 同一份口径），这里只给「命中之后做什么」：平移完让浮卡跟上、点开的实体走右侧详情。
 *
 * 监听得在建图之前就挂上：render() 中途任何一步抛异常（悬空边、重复节点 id 都能让 G6 直接
 * 报错），挂在函数尾部的这一句就永远执行不到，于是图照样画得出来、却拖不动也点不中，
 * 只剩一条一闪而过的红 toast，谁也说不清是哪一步废了（失败原因现在会留在页面上，见 buildError）。
 */
function attachCanvasEvents(el: HTMLElement) {
  detachCanvasEvents?.();
  detachCanvasEvents = attachCanvasInteractions(el, {
    getGraph: () => graph,
    // 平移不再回经 zoom-canvas 那条路径，卡片得自己跟上（钉在原地会被当成另一个实体）
    onPan: placeCard,
    // hover 与点击共用一份命中判定：亮蓝圈的范围就是能点开的那个范围
    onHover: (hit) => setHover(hit?.id ?? null),
    onPick: (hit) => {
      void selectNode(nodes.value.find((n) => String(n.id) === hit.id) || null);
    },
  });
}

/** 当前被 hover 高亮的节点 id：只在变化时改状态，不给每次 mousemove 都上一遍全图 */
let hoverId: null | string = null;

/**
 * 把蓝圈（hover 态）打在判定命中的那个节点上。
 *
 * 原先靠 G6 的 node:mouseenter/leave，而它只认形状自己的包围盒 —— 对视之后那盒子
 * 在屏幕上就是几个像素，鼠标要很准才亮，反馈便是「挪开二三十像素才出现蓝圈」。
 * 现在由 shared/graph-canvas 的 hover 回调驱动（光标类名也归它），蓝圈与点击命中是同一个范围。
 */
function setHover(id: null | string) {
  if (id === hoverId) return;
  const g = graph;
  if (!g || g.destroyed) {
    hoverId = null;
    return;
  }
  if (hoverId) {
    g.findAll('node', (m: any) => String(m.id) === hoverId).forEach(
      (it: any) => {
        g.setItemState(it, 'hover', false);
      },
    );
  }
  hoverId = id;
  if (id) {
    g.findAll('node', (m: any) => String(m.id) === id).forEach((it: any) => {
      g.setItemState(it, 'hover', true);
    });
  }
}

/**
 * 建图前的数据自检：G6 对「边指向不存在的节点」是直接抛异常的（render 里取不到端点
 * 就报错退出），重复节点 id 同理，而这两个坑在图谱页是常态 —— 后端按规模截断时
 * 会给回一半端点已被裁掉的边。一抛，render() 就断在建图那一半，留下「图看得见但拖不动」。
 * 这里先把画不出来的边剔掉，剔了多少交给 cutNote 报到页面上，不悄悄丢数据。
 */
const graphData = computed(() => {
  const ids = new Set<string>();
  const nodeRows: GraphNodeResp[] = [];
  nodes.value.forEach((n) => {
    const id = String(n.id);
    if (ids.has(id)) return;
    ids.add(id);
    nodeRows.push(n);
  });
  const edgeRows = edges.value.filter(
    (e) => ids.has(String(e.source)) && ids.has(String(e.target)),
  );
  return {
    droppedEdges: edges.value.length - edgeRows.length,
    droppedNodes: nodes.value.length - nodeRows.length,
    edgeRows,
    nodeRows,
  };
});

/** 截断说明：后端截了一刀、这里又剔了多少，都说清楚，否则用户只看到「图比预期稀」 */
const cutNote = computed(() => {
  const d = graphData.value;
  if (!truncated.value && d.droppedEdges === 0 && d.droppedNodes === 0) {
    return '';
  }
  const parts = [
    `已画进 ${d.nodeRows.length} 个节点 / ${d.edgeRows.length} 条关系`,
  ];
  if (truncated.value) parts.push('后端按规模截断了结果');
  if (d.droppedEdges > 0) parts.push(`${d.droppedEdges} 条关系的端点没进图`);
  if (d.droppedNodes > 0) parts.push(`${d.droppedNodes} 个重复节点已合并`);
  return parts.join('，');
});

/**
 * 截断与剔除合并成一句提示。
 *
 * 不开两条 Alert：两条叠着会把画布顶矮一截，而画布高度一变又得重对视。
 */
const cutAlertMessage = computed(() => {
  if (!cutNote.value) return '';
  if (truncated.value) {
    return `结果过大已截断，请缩小深度 / 规模或按关键词过滤后再查看（${cutNote.value}）`;
  }
  return `有数据没能画进图里：${cutNote.value}`;
});

/**
 * 真正建图的那一段（从 render() 拆出来：拆了才能在外层 try/catch 它）。
 */
async function buildGraph(el: HTMLElement) {
  const G6 = await import('@antv/g6');
  const { width, height } = await waitForCanvasSize(el);
  // 边长与斥力按规模收一档：几十个节点时 150 的边长好看，上百个会把整张图撑到
  // 几千像素见方，fitView 只能缩成芝麻粒
  const largeGraph = graphData.value.nodeRows.length > 80;

  graph = new G6.Graph({
    container: el,
    width,
    height,
    // 交给 G6 自己在 render 里对视是不可控的（那时还没 autoPaint），统一走 fitToView
    fitView: false,
    fitViewPadding: FIT_VIEW_PADDING,
    animate: true,
    // 交互分工：只把滚轮缩放与 Shift 摆节点交给 G6，平移、点实体、hover 都自己听 DOM。
    // 平移原先挂的是 drag-canvas：它的 allowDragOnItem 默认 false（g6-pc/behavior/drag-canvas.js）
    // 把按在节点/边/标签上的平移请求一律 return false，打开后事件层还要求按下的形状带
    // draggable —— 两道闸门都在库里，页面上只能看到「拖不动」。详情原先走 node:mouseup /
    // node:dragend 记账，而 g-base 把「按下超 120ms」一律判成拖拽、click 被吞——就是
    // 「点了实体什都不出」。换见 attachCanvasEvents：只依赖 clientX/Y 与视口矩阵。
    modes: {
      default: [
        'zoom-canvas',
        {
          type: 'drag-node',
          // 普通拖动已收给 DOM 平移，这里只留 Shift+拖动摆单个节点
          shouldBegin: (ev: any) => !!ev?.originalEvent?.shiftKey,
        },
      ],
    } as any,
    layout: {
      type: 'force',
      // 力导默认逐帧迭代：render() 那一帧的位置只是起点，之后图还在继续扩张，
      // 任何在此之前做的对视都会对不上（节点整批飞出视口 = 「特别远」）。
      // 关掉布局动画让它在 render 里跑收敛，位置定下来再对视；万一这个开关在当前
      // 版本没生效，下面还挂了一道 afterlayout 再对视一次兜底。
      animate: false,
      preventOverlap: true,
      nodeSize: 60,
      linkDistance: largeGraph ? 90 : 150,
      nodeStrength: largeGraph ? -120 : -300,
      edgeStrength: 0.1,
    },
    defaultNode: {
      type: 'circle',
      size: 40,
      labelCfg: {
        position: 'bottom',
        offset: 5,
        style: { fontSize: 12, fill: '#333' },
      },
      // 手上给个可点的暗示：节点唯一的用途就是点开右侧详情与溯源
      style: { lineWidth: 2, cursor: 'pointer' },
    },
    defaultEdge: {
      type: 'quadratic',
      style: {
        stroke: '#c9c9c9',
        lineWidth: 1.5,
        endArrow: { path: G6.Arrow.triangle(6, 8, 0), fill: '#c9c9c9' },
      },
      labelCfg: {
        autoRotate: true,
        style: {
          fontSize: 10,
          fill: '#999',
          background: { fill: '#fff', padding: [2, 4, 2, 4], radius: 2 },
        },
      },
    },
    nodeStateStyles: {
      selected: { stroke: '#1890ff', lineWidth: 3, shadowColor: '#1890ff', shadowBlur: 10 },
      hover: { stroke: '#1890ff', lineWidth: 2 },
    },
  });

  graph.data({
    nodes: graphData.value.nodeRows.map((n) => ({
      id: n.id,
      label: n.name || n.label,
      size: 30 + Math.min(30, (n.weight || 0) * 2),
      style: { fill: colorOf(n.entityType), stroke: colorOf(n.entityType) },
    })),
    edges: graphData.value.edgeRows.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      // 具体关系描述在 name（「曾就职于」），relation 是边类型 RELATED_TO——
      // 先取 relation 会让每条边的标签都变成同一句没信息量的 RELATED_TO
      label: e.name || e.relation || '',
    })),
  });
  // 监听必须在 render 之前挂：布局同步收敛时 afterlayout 就在 render 里面发出去，
  // 挂晚了听不到。fitToken 保证多次对视只有最后一次生效（不会回到同帧二次对视那个坑）
  graph.once('afterlayout', () => {
    if (graph && !graph.destroyed) void fitToView(graph);
  });
  graph.render();
  // 子形状是 render 里才建出来的，draggable 只能补在这一步之后
  enableDragOnAllShapes(graph);

  // 上面可能拿还没长开的实测尺寸建了图：建完再量一次，只要与建图尺寸不一致就改画布，
  // 让 G6 记录的尺寸与 DOM 始终一致（不一致 = 定心点落在裁剪区外）
  const real = canvasSize(el);
  if (
    real.width > 0 &&
    real.height > 0 &&
    (real.width !== width || real.height !== height)
  ) {
    graph.changeSize(real.width, real.height);
  }
  await fitToView(graph);

  graph.on('viewportchange', () => {
    if (graph && !graph.destroyed) {
      zoomPct.value = Math.round(Number(graph.getZoom() || 1) * 100);
      placeCard();
    }
  });
  // hover 不再听 G6 的 node:mouseenter/leave：它只认形状的包围盒，对视后就是几个像素，
  // 鼠标要停得很准才亮圈；改由 attachCanvasEvents 挂的那套 DOM mousemove 驱动，
  // 与点击共用 shared/graph-canvas 里那份命中判定（见 setHover）
}

async function render() {
  let el = container.value;
  if (!el) {
    // 容器可能比数据晚挂载一帧（首屏 bootstrap 里请求先回来了）：静默 return 会永远空白
    await nextFrame();
    el = container.value;
  }
  if (!el) return;
  // 旧图的监听先拆干净：容器 DOM 是同一个，不拆就会叠上第二套按下/松手
  detachCanvasEvents?.();
  detachCanvasEvents = null;
  hoverId = null;
  // 光标类名也得清：拖到一半就重新查询时，mouseup 的监听已经被拆了，没人再把 grabbing
  // 摘掉，整块画布从此顶着一个「正在拖图」的手型
  el.classList.remove('grabbing', 'pointing');
  buildError.value = '';
  if (graph) {
    try {
      graph.destroy();
    } catch {
      /* 旧图销不干净也不拦着建新图：残留画布由下面那句统一摘掉 */
    }
    graph = null;
  }
  // 容器里只要还留着 canvas 就一律摘掉：叠了两层时，上面那张不再更新的旧图会把新图整个
  // 盖住，表现正是「拖着不动、点不中、蓝圈还在老位置上」
  el.querySelectorAll('canvas').forEach((c) => c.remove());
  if (nodes.value.length === 0) {
    // 空图这次不建监听：上一张图的已经在函数开头拆了，没有画布可听
    return;
  }
  // 监听先挂上再建图（理由见 attachCanvasEvents 末尾那段）
  attachCanvasEvents(el);
  try {
    await buildGraph(el);
  } catch (error: any) {
    // 建图失败的原因得留在页面上：只弹一条 toast，下一秒就没人知道刚刚发生过什么
    const reason = String(error?.message || error || '未知错误');
    buildError.value = reason.slice(0, 200);
    // 半张图僵在画布上冒充「能交互」比一块空白更难解释：清掉，只留下那句原因
    try {
      graph?.destroy();
    } catch {
      /* 已经坏了，销不掉就只摘 DOM */
    }
    graph = null;
    el.querySelectorAll('canvas').forEach((c) => c.remove());
  }
}

/** 选中一个实体：右侧详情 + 溯源一起换，图上把它描边高亮 */
async function selectNode(node: null | GraphNodeResp) {
  selectedNode.value = node;
  sourceDocs.value = [];
  if (!node) {
    cardNode.value = null;
    return;
  }
  // 浮卡只贴在画布上真有的节点旁边：左栏搜来的实体可能被 depth 截断而没进图，
  // 那种情况右侧详情照常打开，画布上不浮卡（贴在旧位置反而误导）
  cardNode.value =
    graph && !graph.destroyed && graph.findById(node.id) ? node : null;
  placeCard();
  if (graph) {
    graph.findAll('node', (m: any) => m.id === node.id).forEach((it: any) => {
      graph.setItemState(it, 'selected', true);
    });
    graph.findAll('node', (m: any) => m.id !== node.id).forEach((it: any) => {
      graph.setItemState(it, 'selected', false);
    });
  }
  sourceLoading.value = true;
  try {
    sourceDocs.value = await GraphEntitySources({
      name: node.name,
      kbIds: kbIds.value.join(','),
      entityType: node.entityType,
      limit: 20,
    });
  } catch {
    /* 溯源失败不阻断详情 */
  } finally {
    sourceLoading.value = false;
  }
}

/** 左栏点了某个实体：把它当起点重查一次子图，并选中之 */
async function focusEntity(row: Record<string, any>) {
  keyword.value = String(row.name || '');
  await load();
  const hit = nodes.value.find((n) => n.name === keyword.value) || null;
  // 榜单行只有 type（实体列表行是 entityType），归一再当节点用；
  // 子图里找不到同名节点时（起点被 depth 截断），至少右侧详情能打开
  await selectNode(
    hit ||
      (({ ...row, entityType: row.entityType || row.type }) as unknown as GraphNodeResp),
  );
}

async function doEntitySearch() {
  if (!allowed.value) return;
  const kw = searchKeyword.value.trim();
  if (!kw) {
    searchResult.value = [];
    return;
  }
  searching.value = true;
  try {
    searchResult.value = await GraphEntitySearch({
      keyword: kw,
      kbIds: kbIds.value.join(','),
      entityType: entityType.value,
      limit: 50,
    });
  } catch (e: any) {
    message.error(e?.message || '实体检索失败');
    searchResult.value = [];
  } finally {
    searching.value = false;
  }
}

function zoom(delta: number) {
  if (!graph) return;
  const cur = graph.getZoom();
  graph.zoomTo(Math.max(MIN_FIT_ZOOM, Math.min(3, cur + delta)));
  zoomPct.value = Math.round(Number(graph.getZoom() || 1) * 100);
}

/** 「适应画布」：手动放大缩小之后一键回到整图可见（仍走那个唯一的对视入口） */
function fitCanvas() {
  if (graph && !graph.destroyed) void fitToView(graph);
}

const resizeObserver = ref<ResizeObserver | null>(null);
watch(container, (el) => {
  if (!el) return;
  resizeObserver.value = new ResizeObserver(() => {
    const size = canvasSize(el);
    if (!graph || size.width < MIN_CANVAS || size.height < MIN_CANVAS) return;
    // 差不到 4px 一律当量取误差（getBoundingClientRect 取小数、这里四舍五入）：
    // 跟着这种抖动重画布 + 重对视，等于用户刚拖完就被拽回默认视口
    if (
      Math.abs(size.width - Math.round(graph.get('width'))) < 4 &&
      Math.abs(size.height - Math.round(graph.get('height'))) < 4
    ) {
      return;
    }
    graph.changeSize(size.width, size.height);
    // 力导图的定心点用的是建图那一刻的尺寸，尺寸一变就必须重新对视口；
    // 对视统一走 fitToView（下一帧生效 + 结果校验），别在这里直接调 graph.fitView
    void fitToView(graph);
  });
  resizeObserver.value.observe(el);
});

watch(kbIds, () => {
  loadStats();
});

onBeforeUnmount(() => {
  detachCanvasEvents?.();
  detachCanvasEvents = null;
  try {
    graph?.destroy();
  } catch {
    /* 离开页面时旧图销不掉也不能拦住卸载 */
  }
  graph = null;
  resizeObserver.value?.disconnect();
});

async function bootstrap() {
  await loadOptions();
  if (!allowed.value) return;
  await Promise.all([loadStats(), load()]);
}

bootstrap();
</script>

<template>
  <div class="page-container">
    <Alert
      v-if="!allowed"
      type="warning"
      show-icon
      class="mb-2"
      message="没有「图谱检索」的菜单权限（ai:kb:graph），本页不可用。"
    />

    <!-- 规模概览：先让人知道这张图有多大，再决定要不要往下点 -->
    <Card :bordered="false" size="small" class="mb-2">
      <Space wrap :size="12">
        <Select
          v-model:value="kbIds"
          mode="multiple"
          :options="kbSelectOptions"
          placeholder="知识库（留空 = 全部可用库）"
          style="min-width: 300px"
          :max-tag-count="2"
          option-filter-prop="label"
          allow-clear
        />
        <Tag color="blue">实体 {{ stats.entityCount ?? 0 }}</Tag>
        <Tag color="geekblue">关系 {{ stats.relationCount ?? 0 }}</Tag>
        <Tag color="cyan">切片 {{ stats.chunkCount ?? 0 }}</Tag>
        <Tag color="purple">文档 {{ stats.documentCount ?? 0 }}</Tag>
        <Tag v-if="stats.ready === false" color="orange">
          {{ stats.message || '图谱服务未就绪' }}
        </Tag>
        <Tag v-else-if="stats.enabled === false" color="orange">
          所选知识库未开启图谱
        </Tag>
      </Space>
    </Card>

    <div class="graph-layout">
      <!-- 左：找入口 -->
      <Card :bordered="false" size="small" class="side-col">
        <template #title>实体检索</template>
        <Space direction="vertical" :size="10" style="width: 100%">
          <Input
            v-model:value="searchKeyword"
            placeholder="按实体名搜索"
            allow-clear
            @press-enter="doEntitySearch"
          >
            <template #prefix><SearchOutlined /></template>
          </Input>
          <Button block :loading="searching" @click="doEntitySearch">搜索实体</Button>

          <div class="entity-list">
            <Spin :spinning="searching">
              <template v-if="searchResult.length">
                <div
                  v-for="(row, i) in searchResult"
                  :key="`s${i}`"
                  class="entity-row"
                  @click="focusEntity(row)"
                >
                  <span
                    class="dot"
                    :style="{ background: colorOf(row.entityType) }"
                  ></span>
                  <span class="entity-name ellipsis">{{ row.name }}</span>
                  <Tag v-if="row.entityType" class="ml-1">{{ row.entityType }}</Tag>
                </div>
              </template>
              <Empty
                v-else-if="searchKeyword.trim()"
                :image="Empty.PRESENTED_IMAGE_SIMPLE"
                description="没有匹配实体"
              />
              <template v-else>
                <div class="block-title">核心实体（按连接数）</div>
                <div
                  v-for="(row, i) in topEntities"
                  :key="`t${i}`"
                  class="entity-row"
                  @click="focusEntity(row)"
                >
                  <span class="dot" :style="{ background: colorOf(row.type) }"></span>
                  <span class="entity-name ellipsis">{{ row.name }}</span>
                  <span class="entity-degree">{{ row.degree }}</span>
                </div>
                <div v-if="!topEntities.length" class="detail-tip">
                  暂无数据，先给文档「构建图谱」
                </div>
              </template>
            </Spin>
          </div>
        </Space>
      </Card>

      <!-- 中：看关系 -->
      <Card :bordered="false" size="small" class="flex-1 center-col">
        <template #title>子图</template>
        <template #extra>
          <Space :size="4">
            <Tooltip title="放大">
              <Button size="small" @click="zoom(0.2)">
                <template #icon><ZoomInOutlined /></template>
              </Button>
            </Tooltip>
            <Tooltip title="缩小">
              <Button size="small" @click="zoom(-0.2)">
                <template #icon><ZoomOutOutlined /></template>
              </Button>
            </Tooltip>
            <Tooltip title="适应画布（把整张图缩放到刚好填满中间这块）">
              <Button size="small" @click="fitCanvas">
                <template #icon><AimOutlined /></template>
              </Button>
            </Tooltip>
            <Tooltip title="重新查询">
              <Button size="small" @click="load">
                <template #icon><ReloadOutlined /></template>
              </Button>
            </Tooltip>
          </Space>
        </template>

        <!-- 工具栏分两行且都不许被压扁：卡片体是 flex 列、画布那块 flex:1，原先提示文字和
             控件挤在同一个 Space 里，一换行工具栏就被撑高、而 Space 默认又能缩，缩掉的
             那一截正好被画布盖住——这就是「这句话被画布挡住」的来处。把会变宽度的
             「缩放 xx%」与计数挪到第二行（行高固定）还顺带断了另一个反馈环：以前每次滚轮
             缩放都改工具栏宽度→换行→画布高度变→ResizeObserver 重对视，把用户刚调好的
             视口拽回默认。 -->
        <Space class="toolbar-main mb-2" wrap :size="8">
          <Input
            v-model:value="keyword"
            placeholder="起点实体 / 关键词"
            allow-clear
            style="width: 200px"
            @press-enter="load"
          />
          <Select
            v-model:value="entityType"
            :options="entityTypeOptions"
            placeholder="实体类型"
            allow-clear
            style="width: 150px"
          />
          <Tooltip
            title="这两个条件是叠加过滤（AND）：都填了就以「名字命中关键词且类型相符」的实体当起点，其中一个把起点筛空就是空图（空态会写明原因，不会悄悄丢掉关键词只按类型给图）。两者都只决定起点，展开出来的邻居不按类型筛——只留同类型的节点会把关系链凭空切断。"
          >
            <QuestionCircleOutlined class="param-label" />
          </Tooltip>
          <span class="param-label">深度</span>
          <!-- 上限 3 与后端 GraphQueryReq.depth(le=3) 对齐，填 4 会被 422 打回 -->
          <InputNumber v-model:value="depth" :min="1" :max="3" style="width: 70px" />
          <span class="param-label">规模</span>
          <InputNumber v-model:value="limit" :min="10" :max="500" :step="10" style="width: 88px" />
          <Button type="primary" :loading="loading" @click="load">查询</Button>
        </Space>

        <div v-if="nodes.length > 0" class="toolbar-meta mb-2">
          <Tag color="blue">节点 {{ totalNodes }}</Tag>
          <Tag color="green">关系 {{ totalEdges }}</Tag>
          <Tag color="default">缩放 {{ zoomPct }}%</Tag>
          <!-- 摆节点被收进了 Shift，不写在脸上没人知道还能这么用；但这一句不再挤进
               控件那一行：定高一行、超长就省略，悬停看全文 -->
          <Tooltip
            title="任意处按住拖动即可平移 · 滚轮缩放 · 点实体出详情与溯源（亮蓝圈的就是能点开的）· Shift+拖动摆单个节点；点不中多半是规模太大被截断，把深度或规模调低一档再试"
          >
            <span class="param-label toolbar-hint">
              任意处按住拖动即可平移 · 滚轮缩放 · 点实体出详情 ·
              Shift+拖动摆节点
            </span>
          </Tooltip>
        </div>

        <Alert
          v-if="buildError"
          type="error"
          show-icon
          class="mb-2"
          :message="`子图渲染失败：${buildError}`"
          description="把上面的深度调小、规模调低一档再查一次；还是失败就是这批数据里有画不出来的关系。"
        />
        <Alert
          v-else-if="cutAlertMessage"
          type="warning"
          show-icon
          class="mb-2"
          :message="cutAlertMessage"
        />

        <Spin :spinning="loading" wrapper-class-name="graph-canvas-spin">
          <div ref="container" class="graph-canvas"></div>
          <!-- 点实体的当场反馈：名字+类型+摘要贴在节点下方，原文溯源仍在右栏 -->
          <div v-if="cardNode" class="node-card" :style="nodeCardStyle">
            <div class="node-card-head">
              <span class="node-card-name ellipsis">{{ cardNode.name }}</span>
              <Tag
                v-if="cardNode.entityType"
                :color="colorOf(cardNode.entityType)"
              >
                {{ cardNode.entityType }}
              </Tag>
            </div>
            <div v-if="cardNode.summary" class="node-card-summary">
              {{ cardNode.summary }}
            </div>
            <div class="node-card-tip">
              权重 {{ cardNode.weight }} · 原文溯源见右侧「实体详情」
            </div>
          </div>
          <Empty
            v-if="!loading && nodes.length === 0"
            class="graph-empty"
            :description="emptyText"
          />
        </Spin>
      </Card>

      <!-- 右：追原文 -->
      <Card :bordered="false" size="small" class="side-col detail-col">
        <template #title>实体详情</template>
        <Spin :spinning="sourceLoading">
          <template v-if="selectedNode">
            <div class="detail-title">{{ selectedNode.name }}</div>
            <Space wrap class="mb-2">
              <Tag v-if="selectedNode.entityType" :color="colorOf(selectedNode.entityType)">
                {{ selectedNode.entityType }}
              </Tag>
              <Tag>权重 {{ selectedNode.weight }}</Tag>
              <Tag v-if="selectedNode.kbId">库 {{ selectedNode.kbId }}</Tag>
            </Space>
            <div v-if="selectedNode.aliases?.length" class="detail-block">
              <div class="detail-label">别名</div>
              <Space wrap>
                <Tag v-for="(a, i) in selectedNode.aliases" :key="i">{{ a }}</Tag>
              </Space>
            </div>
            <div v-if="selectedNode.summary" class="detail-block">
              <div class="detail-label">摘要</div>
              <div class="detail-text">{{ selectedNode.summary }}</div>
            </div>
            <div class="detail-block">
              <div class="detail-label">溯源切片</div>
              <div v-if="sourceDocs.length">
                <div v-for="(s, i) in sourceDocs" :key="i" class="source-item">
                  <div class="source-head">
                    文档 {{ s.docId ?? i }}
                    <span v-if="s.chunkIndex !== undefined" class="source-loc">
                      #{{ s.chunkIndex }}
                    </span>
                  </div>
                  <div v-if="s.titlePath" class="source-path">{{ s.titlePath }}</div>
                  <div class="source-content">{{ (s.content || '').slice(0, 120) }}</div>
                </div>
              </div>
              <div v-else class="detail-text">（无溯源信息）</div>
            </div>
          </template>
          <div v-else class="detail-tip">点击左侧节点或实体搜索结果查看详情与原文溯源</div>
        </Spin>
      </Card>
    </div>
  </div>
</template>

<style scoped>
/* 定高不能用 height:100%：布局链的根是 min-h-full（不是 h-full），<main> 的高度由内容
   决定，内容一长过视口整条百分比高度链就塌陷、改由整页滚动 —— 表现就是内部滚动条
   永远不出现、子图被顶到屏幕外。这里直接用视口高减去头部容器与页脚（两个值框架
   已经以 CSS 变量写在 :root 上），拿到的是一个不依赖自测量的确定高度。 */
.page-container {
  display: flex;
  flex-direction: column;
  height: calc(
    100vh - var(--vben-header-height, 90px) - var(--vben-footer-height, 32px)
  );
  padding: 8px;
  overflow: hidden;
}

/* 窄窗口下宁可整行横向滚动，也不能把中间的子图挤成一粒沙：实测两侧栏各 280/300 时
   子图只剩 172px 宽，120 个节点的力导图缩到 0.09 倍，等于看不到内容 */
.graph-layout {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;

  /* 纵向交给每一栏自己滚，横向留给「窗口太窄」这个兜底 */
  overflow: hidden auto;
}

.side-col {
  flex: 0 0 240px;
  overflow: auto;
}

.detail-col {
  flex-basis: 260px;
}

.center-col {
  display: flex;
  flex: 1 0 460px;
  flex-direction: column;
  min-width: 460px;
  overflow: hidden;
}

:deep(.center-col .ant-card-body) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

/* 卡片体里除了画布那块，谁都不许被 flex 压扁：工具栏一被压，换行的那一截就落到画布
   底下；Alert 一被压，失败原因与截断说明正好被裁掉半句。 */
:deep(.center-col .ant-card-body > .ant-alert) {
  flex: none;
}

.toolbar-main,
.toolbar-meta {
  flex: none;
}

/* 第二行行高固定：计数与操作提示都只占一行，超长就地省略，不许把画布顶高顶矮 */
.toolbar-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  overflow: hidden;
  white-space: nowrap;
}

.toolbar-hint {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.param-label {
  font-size: 12px;
  color: var(--text-color-secondary, #888);
}

.entity-list {
  max-height: 46vh;
  overflow-y: auto;
}

.entity-row {
  display: flex;
  align-items: center;
  padding: 4px 6px;
  cursor: pointer;
  border-radius: 4px;
}

.entity-row:hover {
  background: hsl(var(--primary) / 10%);
}

.dot {
  flex: none;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  border-radius: 50%;
}

.entity-name {
  flex: 1;
  min-width: 0;
}

.entity-degree {
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

.block-title {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

/* Spin 的 wrapper-class-name 落在 ant 内部渲染的 .ant-spin-nested-loading 上，
   scoped 样式带不上 data-v 属性命中不了它；而 .ant-spin-container 默认不带高度，
   .graph-canvas 撑不起来便是画在一块 0×0 画布上（接口有数据也看不见）。
   用 :deep 穿透 ant 的结构把「高度链 + 弹性宽度」补齐。

   高度只能靠 flex 一级级传，**一级都不能写 height:100%**：卡片体是 flex 列，第一个
   子项是两行工具栏，spin 容器再按 100% 算就等于「卡片体全高 + 工具栏高」，多出来那
   84px 被 .center-col 的 overflow:hidden 直接裁掉 —— 子图下半截永远看不到也滚不出来
   （实测 可视533 / 内容617 / 纵溢84）。min-height:0 是必需的：flex 子项默认
   min-height:auto，不加就会被内容顶高、又变成溢出。 */
:deep(.graph-canvas-spin) {
  /* 浮卡与空态都按这块画布区域定位，得先把它变成定位上下文 */
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

:deep(.graph-canvas-spin .ant-spin-container) {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

.graph-canvas {
  position: relative;
  flex: 1;
  width: 100%;
  min-height: 0;
  overflow: hidden;
  cursor: grab;
  background: var(--component-background-light, #fafcff);
  border: 1px solid var(--border-color, #eee);
  border-radius: 6px;
}

/* 命中带比节点形状本身宽（容差见 shared/graph-canvas）：光标也得跟着提前变 pointer，否则
   「明明是点得中的位置，光标仍是个十字」，读者照样不敢点。 */
.graph-canvas.pointing,
.graph-canvas.pointing :deep(canvas) {
  cursor: pointer !important;
}

/* 拖动过程中给个「正在推图」的手势反馈：g-base  hover 到带 cursor 属性的形状时会把
   cursor 直接写到 canvas 元素的行内样式上（event-contoller.js:235），盖不过行内只能
   用 !important。而 G6 自身不设 canvas 的 cursor（取到 undefined，等于把行内清掉），
   所以静止时的 grab 能从 .graph-canvas 继承下去，只有节点上显示 pointer。 */
.graph-canvas.grabbing,
.graph-canvas.grabbing :deep(canvas) {
  cursor: grabbing !important;
}

.graph-empty {
  position: absolute;
  top: 40%;
  right: 0;
  left: 0;
}

/* 点中实体浮在节点下方的小卡。pointer-events:none 是必需的：卡片压在画布上，
   留着它就会挡住下面 canvas 的按下，而平移与命中都听的是容器 DOM，等于自己把
   「拖图 / 点节点」的起手挡掉一半。 */
.node-card {
  position: absolute;
  z-index: 2;
  box-sizing: border-box;
  width: max-content;
  max-width: 260px;
  padding: 6px 8px;
  font-size: 12px;
  line-height: 1.5;
  pointer-events: none;
  background: var(--component-background, #fff);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 12%);
  transform: translateX(-50%);
}

.node-card-head {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-bottom: 2px;
}

.node-card-name {
  max-width: 150px;
  font-size: 13px;
  font-weight: 600;
}

.node-card-summary {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  color: #555;
}

.node-card-tip {
  color: var(--text-color-secondary, #999);
}

.detail-title {
  margin-bottom: 8px;
  font-size: 15px;
  font-weight: 600;
}

.detail-block {
  margin-bottom: 12px;
}

.detail-label {
  margin-bottom: 4px;
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

.detail-text {
  font-size: 13px;
  line-height: 1.6;
}

.detail-tip {
  margin-top: 40px;
  color: var(--text-color-secondary, #999);
  text-align: center;
}

.source-item {
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px dashed var(--border-color, #eee);
}

.source-head {
  font-weight: 500;
}

.source-loc {
  margin-left: 4px;
  color: var(--text-color-secondary, #999);
}

.source-path {
  font-size: 12px;
  color: var(--text-color-secondary, #999);
}

.source-content {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.5;
  color: #555;
}

.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
