/**
 * G6 图谱画布的交互底座：平移、命中判定、hover 回调。
 *
 * 图谱检索页的子图与知识检索页的关系小图共用这一份。两页原先各写一套命中半径，改一处的
 * 容差另一处就漂，而「实体难点中」这种反馈恰恰只在规模大、对视倍数低时才暴露，漂了也没人
 * 发现 —— 所以口径只留一处。
 *
 * 为什么不交给 G6 自己的行为层（实测源码，g6-pc 0.8.25 / g6-core 0.8.24）：
 *
 * 1. `drag-canvas` 的 `allowDragOnItem` 默认 false（g6-pc/lib/behavior/drag-canvas.js:25），
 *    按下落在节点/边/标签上时 `validate` 直接 return false（同文件 :291）+ 图谱页布满节点，
 *    能起手平移的只剩几点空白 = 页面上就是「拖着不动」。
 * 2. `node:mouseenter` 只认形状自己的包围盒：对视到 0.2 倍后一个 40 模型像素的圆在屏幕上
 *    只剩 8 像素，鼠标要停得极准才亮 —— 而它认的范围和「点哪儿算点中这个实体」是两套判定，
 *    屏幕上会出现「亮圈在别处、点中的又是另一个」。
 *
 * 改听 DOM 后只依赖 clientX/Y 与视口矩阵：平移走 `graph.translate`（画布像素，与鼠标 1:1，
 * 且它读的是 group 上现装的矩阵，页面自己写对视矩阵不会被它当成没缩放过），命中走
 * `getPointByClient` 换回绘制坐标再按缩放比放大容差比距离。
 */

export interface GraphHit {
  /** 命中点到节点中心的模型坐标距离（多个候选取最近的那个） */
  dist: number;
  id: string;
}

interface PickOptions {
  /** 节点下方那行标签在模型坐标里占的高度（字号 + offset，G6 里它们随缩放一起缩） */
  labelBand?: number;
  /** 屏幕上至少留给光标的容差（CSS 像素） */
  minScreenPad?: number;
}

interface CanvasInteractionOptions extends PickOptions {
  /** 画布实例每次现取：建图/拆图换过实例也不用重挂监听 */
  getGraph: () => any;
  /** 命中光标类名，挂在容器本身上（命中带比形状宽，光标得跟着提前变 pointer） */
  hoverClass?: string;
  /** 停在实体上时回调，从命中带里出去回调一次 null；不传就不做 hover 判定 */
  onHover?: (hit: GraphHit | null, event: MouseEvent | null) => void;
  /** 每次平移之后回调：钉在节点上的详情卡得跟着重算位置 */
  onPan?: () => void;
  /** 位移在容差内的一次按下 = 点实体；不传就只做平移与 hover（关系小图没有点选） */
  onPick?: (hit: GraphHit, event: MouseEvent) => void;
  /** 平移中光标类名，挂在容器本身上 */
  panClass?: string;
  /** Shift 是否留给 G6 的 drag-node 摆单个节点；留着的页面 Shift+按下就不平移 */
  reserveShiftForNodeDrag?: boolean;
}

/** 判定「点一下」的位移容差（px）：免得一次手抖既平移了画布又弹出详情 */
const CLICK_MOVE_TOLERANCE = 8;
/**
 * 命中容差的最小屏幕尺寸（CSS 像素），约等于一枚小按钮的半径。
 *
 * 节点半径与标签带宽都是**模型坐标**里的长度，对视到 0.2 倍之后一个 40 模型像素的圆落到
 * 屏幕上只剩 8 像素，再按模型单位给容差等于要求光标停在针尖上 —— 规模一大、对视倍数一低
 * 就完全点不中。容差按屏幕像素给、再除以当前缩放比换回模型坐标，缩放到多少倍都一样好点。
 */
const HIT_MIN_SCREEN = 12;
/** 默认标签带高度：字号 12 + offset 5，再留点余量（各页字号不同可单独传） */
const LABEL_BAND = 20;

/**
 * 按屏幕位置找实体：圆内与它下方的名字都算命中。
 *
 * 只命中圆的话，对视后名字比圆还显眼，用户「指哪儿点哪儿」反而落在判定圈外。
 */
export function pickGraphNode(
  graph: any,
  clientX: number,
  clientY: number,
  options: PickOptions = {},
): GraphHit | null {
  if (!graph || graph.destroyed) return null;
  const { labelBand = LABEL_BAND, minScreenPad = HIT_MIN_SCREEN } = options;
  const p = graph.getPointByClient(clientX, clientY);
  const zoom = Number(graph.getZoom?.() || 1) || 1;
  const pad = minScreenPad / zoom;
  let best: GraphHit | null = null;
  for (const item of graph.getNodes() || []) {
    const model = item.getModel();
    if (!Number.isFinite(model?.x) || !Number.isFinite(model?.y)) continue;
    const size = Array.isArray(model.size)
      ? model.size
      : [model.size, model.size];
    const r = (Number(size[0]) || 40) / 2;
    const dx = Number(p.x) - Number(model.x);
    const dy = Number(p.y) - Number(model.y);
    const dist = Math.hypot(dx, dy);
    const inCircle = dist <= r + pad;
    // 下方那条标签的命中带（画在 bottom，宽度按字数估，与 G6 画出来的差半个字无所谓）
    const labelHalfW =
      Math.max(r, String(model.label || '').length * 6) / 2 + pad;
    const inLabel =
      Math.abs(dx) <= labelHalfW && dy > r - pad && dy < r + labelBand + pad;
    if ((inCircle || inLabel) && (!best || dist < best.dist)) {
      best = { dist, id: String(model.id) };
    }
  }
  return best;
}

/**
 * 把「按住任意处拖动 = 平移整张图」「停在实体上 = hover」「点一下 = 选中」挂到容器 DOM 上。
 *
 * 返回拆监听的函数：监听得在建图**之前**就挂上，建图中间任何一步抛异常（悬空边、重复节点
 * id 都能让 G6 直接报错）都不会再把它们带走 —— 挂在建图之后，就会出现「图照样画得出来、
 * 却拖不动也点不中」，而且页面上一处痕迹都不留。
 *
 * move/up 听到 window 上：往画布外拖出再松手也要收尾，不然图卡在拖动态一直跟着鼠标跑。
 */
export function attachCanvasInteractions(
  el: HTMLElement,
  options: CanvasInteractionOptions,
): () => void {
  const {
    getGraph,
    hoverClass = 'pointing',
    labelBand,
    minScreenPad,
    onHover,
    onPan,
    onPick,
    panClass = 'grabbing',
    reserveShiftForNodeDrag = true,
  } = options;
  let dragging = false;
  let moved = 0;
  let lastX = 0;
  let lastY = 0;

  const pick = (ev: MouseEvent) =>
    pickGraphNode(getGraph(), ev.clientX, ev.clientY, {
      labelBand,
      minScreenPad,
    });

  const reportHover = (hit: GraphHit | null, ev: MouseEvent | null) => {
    el.classList.toggle(hoverClass, !!hit);
    onHover?.(hit, ev);
  };

  const onDown = (ev: MouseEvent) => {
    // 只认左键
    if (ev.button !== 0) return;
    // Shift 留给 drag-node 摆节点：不然一次按下既摆节点又挪图
    if (reserveShiftForNodeDrag && ev.shiftKey) return;
    dragging = true;
    moved = 0;
    lastX = ev.clientX;
    lastY = ev.clientY;
    el.classList.add(panClass);
  };

  const onMove = (ev: MouseEvent) => {
    if (!dragging) return;
    const dx = ev.clientX - lastX;
    const dy = ev.clientY - lastY;
    lastX = ev.clientX;
    lastY = ev.clientY;
    moved += Math.abs(dx) + Math.abs(dy);
    if (!dx && !dy) return;
    const g = getGraph();
    if (!g || g.destroyed) return;
    g.translate(dx, dy);
    onPan?.();
  };

  const onUp = (ev: MouseEvent) => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove(panClass);
    // 拖过容差 = 这一趟是平移画布，不是点实体
    if (!onPick || moved > CLICK_MOVE_TOLERANCE) return;
    const hit = pick(ev);
    // 一个都没命中时什么都不做：点在空白处不该把刚打开的详情抹掉
    if (hit) onPick(hit, ev);
  };

  const onHoverMove = (ev: MouseEvent) => {
    // 拖图途中不打扰：光标已经变成 grabbing 了
    if (!onHover || dragging) return;
    reportHover(pick(ev), ev);
  };

  const onLeave = () => {
    if (onHover && !dragging) reportHover(null, null);
  };

  el.addEventListener('mousedown', onDown);
  el.addEventListener('mousemove', onHoverMove);
  el.addEventListener('mouseleave', onLeave);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);

  return () => {
    el.removeEventListener('mousedown', onDown);
    el.removeEventListener('mousemove', onHoverMove);
    el.removeEventListener('mouseleave', onLeave);
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
    el.classList.remove(hoverClass, panClass);
  };
}
