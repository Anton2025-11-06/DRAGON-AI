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
  GraphEntitySearch,
  GraphEntitySources,
  GraphQuery,
  GraphStatistics,
  KbOptions,
} from '../doc/api';

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
/** 一次「按下 → 松手」的记账（详情不能挂 G6 的 click，原因见 finishGesture） */
let gesture: null | { id?: string; x: number; y: number } = null;

// 实体类型取自后端 rag_constant.KG_ENTITY_TYPES（中文），按原值着色，未命中落 DEFAULT
const ENTITY_COLORS: Record<string, string> = {
  人物: '#5B8FF9',
  组织: '#5AD8A6',
  地点: '#5D7092',
  时间: '#9270CA',
  产品: '#6DC8EC',
  技术: '#13C2C2',
  事件: '#F6BD16',
  指标: '#E8684A',
  其他: '#269A99',
  DEFAULT: '#8C8C8C',
};
function colorOf(type?: string) {
  return ENTITY_COLORS[type || ''] || ENTITY_COLORS.DEFAULT;
}

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

/**
 * 判定「点一下」的位移容差（px）。drag-canvas 自己累计到 10px 才认平移
 * （g6-pc/lib/behavior/drag-canvas.js 的 DRAG_OFFSET），这里留出 2px 余量，
 * 免得一次手抖既平移了画布又弹出详情。
 */
const CLICK_MOVE_TOLERANCE = 8;
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
 * 让「按在任意形状上」都能起手平移。
 *
 * allowDragOnItem 只打开了行为层，事件层还有一道闸：g-base 只在按下的那个形状
 * `draggable` 为真时才把这次操作判成拖拽并派发 drag 事件
 * （@antv/g-base/lib/event/event-contoller.js:361），另一条分支要求按下的是**纯空白**
 * （:372）。而 G6 只给节点的 keyShape 补了 draggable（g6-core/item/item.js:158），
 * 标签文字、边的路径与边标签一个都没有。结果就是：按在圆上能拖、按在名字上整张图纹丝
 * 不动，而对视之后圆和名字几乎连成一片铺满画布 —— 用户看到的就是「子图拖不动」。
 * 这里把节点/边容器里的每个子形状都补上 draggable，让平移从哪儿都能起手。
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
 * 松手（mouseup）与拖拽结束（dragend）的统一收口：位移没超容差就算这次是「点」。
 *
 * 为什么不用 node:click：g-base 的 _onmouseup 只在「这次没进拖拽」时才派发 mouseup/click
 * （event-contoller.js:310-322），而它进拖拽的门槛是**按下超过 120ms 且动过一下**
 * （:360 的 `timeWindow > 120 || dist > CLICK_OFFSET`，CLICK_OFFSET=40 只在 120ms 内生效）。
 * 人正常点一下鼠标常常就是 150ms 上下、还夹着一两个像素的抖动，于是 click 被吞成
 * dragend —— 表现就是「点了实体什么详情都不出」。按下与松手自己记账才不受这套判定影响。
 */
function finishGesture(ev: any) {
  const rec = gesture;
  gesture = null;
  if (!rec?.id) return;
  if (
    Math.abs(Number(ev.clientX) - rec.x) > CLICK_MOVE_TOLERANCE ||
    Math.abs(Number(ev.clientY) - rec.y) > CLICK_MOVE_TOLERANCE
  ) {
    return; // 拖过容差 = 平移画布 / Shift 摆节点，不弹详情
  }
  void selectNode(nodes.value.find((n) => n.id === rec.id) || null);
}

async function render() {
  let el = container.value;
  if (!el) {
    // 容器可能比数据晚挂载一帧（首屏 bootstrap 里请求先回来了）：静默 return 会永远空白
    await nextFrame();
    el = container.value;
  }
  if (!el) return;
  if (graph) {
    graph.destroy();
    graph = null;
  }
  // 旧图的按下记账对新图没有意义（节点集已经换了一批），带过来只会认错实体
  gesture = null;
  if (nodes.value.length === 0) return;

  const G6 = await import('@antv/g6');
  const { width, height } = await waitForCanvasSize(el);
  // 边长与斥力按规模收一档：几十个节点时 150 的边长好看，上百个会把整张图撑到
  // 几千像素见方，fitView 只能缩成芝麻粒
  const largeGraph = nodes.value.length > 80;

  graph = new G6.Graph({
    container: el,
    width,
    height,
    // 交给 G6 自己在 render 里对视是不可控的（那时还没 autoPaint），统一走 fitToView
    fitView: false,
    fitViewPadding: FIT_VIEW_PADDING,
    animate: true,
    // 交互分工。原先写成 ['drag-canvas', 'zoom-canvas', 'drag-node'] 全用默认值，三处不合手：
    // ① drag-canvas 的 allowDragOnItem 默认 false（g6-pc/lib/behavior/drag-canvas.js:25），
    //    allowDrag() 里鼠标按在节点/边/标签上就直接 return false 不平移。而这张图对视后
    //    节点圆加底部标签几乎铺满画布，很难落到纯空白 —— 反馈就是「子图拖不动」。
    //    打开它只过了行为层这一道闸，事件层还得按在可拖的形状上（见 enableDragOnAllShapes）。
    // ② ①一开，节点上按下就同时「平移整张画布」+「drag-node 拖走这个节点」，图跟着乱跑。
    //    所以把 drag-node 收起来：普通拖动只看视图，按住 Shift 才摆单个节点。
    // ③ 详情不挂 node:click，改走 render 末尾的按下/松手记账自判（见 finishGesture）。
    modes: {
      default: [
        {
          type: 'drag-canvas',
          allowDragOnItem: true,
          direction: 'both',
          // 默认 0 = 最多只能拖一屏，拖到边界会被 updateViewport 判成「要整块推出视口」
          // 而把 dx/dy 置 0（同样是「拖到某个方向就死住」）。图被对视成一团时余量很容易吃紧，
          // 这里给足，让它能一直拖过去把左侧内容拉进画面。
          scalableRange: 2000,
        },
        'zoom-canvas',
        {
          type: 'drag-node',
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
    nodes: nodes.value.map((n) => ({
      id: n.id,
      label: n.name || n.label,
      size: 30 + Math.min(30, (n.weight || 0) * 2),
      style: { fill: colorOf(n.entityType), stroke: colorOf(n.entityType) },
    })),
    edges: edges.value.map((e, i) => ({
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
  // 平移是 drag-canvas 逐帧 translate 出来的，不一定回 viewportchange；听它自己发出去的
  // canvas:drag，让浮卡跟着节点走（卡片钉在原地会被当成另一个实体）
  graph.on('canvas:drag', () => placeCard());
  graph.on('node:mouseenter', (ev: any) => graph?.setItemState(ev.item, 'hover', true));
  graph.on('node:mouseleave', (ev: any) => graph?.setItemState(ev.item, 'hover', false));
  graph.on('node:mousedown', (ev: any) => {
    gesture = {
      id: ev.item?.getModel?.().id,
      x: Number(ev.clientX),
      y: Number(ev.clientY),
    };
  });
  // 同一次操作里 g-base 只走 mouseup 与 dragend 其中一条，两个都接上、收口只有一个
  graph.on('node:mouseup', finishGesture);
  graph.on('node:dragend', finishGesture);
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
    if (
      size.width === Math.round(graph.get('width')) &&
      size.height === Math.round(graph.get('height'))
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
  graph?.destroy();
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

        <Space class="mb-2" wrap :size="8">
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
          <Tag color="blue">节点 {{ totalNodes }}</Tag>
          <Tag color="green">关系 {{ totalEdges }}</Tag>
          <Tag v-if="nodes.length > 0" color="default">缩放 {{ zoomPct }}%</Tag>
          <!-- 摆节点被收进了 Shift，不写在脸上没人知道还能这么用 -->
          <span v-if="nodes.length > 0" class="param-label">
            任意处按住拖动即可平移 · 滚轮缩放 · 点实体出详情 · Shift+拖动摆节点
          </span>
        </Space>

        <Alert
          v-if="truncated"
          type="warning"
          show-icon
          class="mb-2"
          message="结果过大已截断，请缩小深度 / 规模或按关键词过滤后再查看"
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
  background: var(--component-background-light, #fafcff);
  border: 1px solid var(--border-color, #eee);
  border-radius: 6px;
}

.graph-empty {
  position: absolute;
  top: 40%;
  right: 0;
  left: 0;
}

/* 点中实体浮在节点下方的小卡。pointer-events:none 是必需的：卡片压在画布上，
   留着它就会挡住下面节点的按下记账，等于自己把「点节点」的判定吃掉一半。 */
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
