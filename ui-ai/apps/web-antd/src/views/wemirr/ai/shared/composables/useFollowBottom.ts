import type { Ref } from 'vue';

import { onMounted, onUnmounted, ref } from 'vue';

interface FollowBottomOptions {
  /** 内容元素（被滚动容器撑高的那一个）；不传就观察容器自身的子元素 */
  content?: Ref<HTMLElement | null | undefined>;
  /** 距底多少像素以内算「贴着底部」，超出即认为用户在往上翻，暂停跟随 */
  threshold?: number;
}

/** 贴底之后多久之内的 scroll 事件仍算「我们自己触发的」 */
const PROGRAMMATIC_WINDOW_MS = 200;

/**
 * 聊天/执行面板的「输出时自动跟随到底部」。
 *
 * 三件事合在这里，缺一条观感就是「每出一点新内容都得自己往下拖一下」：
 *
 * 1. **认得出真正在滚的元素**。以前写死「组件根节点就是滚动容器」，一旦某层祖先
 *    才是滚的那个（或被 flex/height 链改掉），`scrollTop = scrollHeight` 就是空写。
 *    这里每次贴底都从容器往上找最近的可滚祖先，找不到才兜到文档。
 * 2. **绕开继承来的平滑滚动**。全局 `html { scroll-behavior: smooth }`，而
 *    scroll-behavior 是**继承**属性，滚动容器会跟着变平滑：贴底成了一段动画，
 *    动画途中的 scroll 事件量到的位置离底部还远，于是「用户是不是在底部」被误判成
 *    false，跟随从此死掉。贴底一律 `behavior: 'instant'`，并把自己触发的那批 scroll
 *    事件排除在判断之外。
 * 3. **盯内容尺寸而不是盯数据**。markdown/代码块的渲染比最后一个 token 晚落地，
 *    watch props 时量到的 scrollHeight 还是旧的，所以用 ResizeObserver 盯容器 +
 *    内容元素 + 每一行（列表未必有单一包裹元素），只要还在变高就继续贴。
 */
export function useFollowBottom(
  container: Ref<HTMLElement | null | undefined>,
  options: FollowBottomOptions = {},
) {
  const { content, threshold = 80 } = options;

  /** 用户是否贴近底部：false 时暂停自动跟随，不打断他往上回看 */
  const isNearBottom = ref(true);

  let observer: null | ResizeObserver = null;
  let pinFrame = 0;
  let lastPinAt = 0;

  /** 往上找真正的滚动元素：容器自己滚不动就换祖先，最后兜到文档 */
  function scroller(): HTMLElement | null {
    let node: HTMLElement | null = container.value ?? null;
    while (node) {
      if (distanceToBottom(node) > 0) {
        const overflowY = getComputedStyle(node).overflowY;
        if (overflowY === 'auto' || overflowY === 'scroll') return node;
      }
      node = node.parentElement;
    }
    // 一路都没找到可滚元素：可能是整页在滚（html/body 的 overflow 是 visible，
    // 按 overflowY 判不出来），这时文档本身就是滚动元素
    const doc = document.scrollingElement as HTMLElement | null;
    return doc && distanceToBottom(doc) > 0 ? doc : null;
  }

  function distanceToBottom(el: HTMLElement): number {
    return el.scrollHeight - el.scrollTop - el.clientHeight;
  }

  /** 观察集合：容器 + 内容元素 + 它们的直接子元素（新增行靠每次贴底前重挂补上） */
  function observe() {
    if (!observer) return;
    const roots = [container.value, content?.value].filter(
      (item): item is HTMLElement => !!item,
    );
    for (const root of roots) {
      observer.observe(root);
      for (const child of root.children) observer.observe(child);
    }
  }

  function pin() {
    const el = scroller();
    if (!el) return;
    lastPinAt = performance.now();
    // 'instant' 压掉继承来的 smooth：滚动是同步落底的，紧随其后的 scroll 事件
    // 量到的就是底部本身，跟随状态不会被动画中间态带偏
    el.scrollTo({
      behavior: 'instant' as ScrollBehavior,
      top: el.scrollHeight,
    });
  }

  /** @param force 强制贴底并恢复跟随（刚发新消息/切会话时用，覆盖「用户在往上翻」） */
  function scrollToBottom(force = false) {
    if (force) isNearBottom.value = true;
    if (pinFrame) return;
    pinFrame = requestAnimationFrame(() => {
      pinFrame = 0;
      observe();
      if (isNearBottom.value) pin();
    });
  }

  // scroll 不冒泡，只能挂在 window 上用捕获阶段拦（这样滚动元素是谁都收得到）
  function handleScroll(event: Event) {
    const el = scroller();
    if (!el || event.target !== el) return;
    const near = distanceToBottom(el) <= threshold;
    // 刚贴过底还没到底：那是贴底动画/内容继续变高的中间态，不是用户在往上翻
    if (!near && performance.now() - lastPinAt < PROGRAMMATIC_WINDOW_MS) return;
    isNearBottom.value = near;
  }

  /** 用户一动滚轮/触摸/滚动条就结束「程序触发」窗口，他的意图立刻生效 */
  function handleUserIntent(event: Event) {
    const el = scroller();
    if (!el || !el.contains(event.target as Node | null)) return;
    const wheel = event as WheelEvent;
    // 只在往上翻时收口：往下推本来就是贴底方向
    if (event.type === 'wheel' && wheel.deltaY >= 0) return;
    lastPinAt = 0;
  }

  onMounted(() => {
    observer = new ResizeObserver(() => scrollToBottom());
    observe();
    window.addEventListener('scroll', handleScroll, true);
    window.addEventListener('wheel', handleUserIntent, { passive: true });
    window.addEventListener('pointerdown', handleUserIntent, { passive: true });
    window.addEventListener('touchmove', handleUserIntent, { passive: true });
  });

  onUnmounted(() => {
    observer?.disconnect();
    observer = null;
    if (pinFrame) cancelAnimationFrame(pinFrame);
    pinFrame = 0;
    window.removeEventListener('scroll', handleScroll, true);
    window.removeEventListener('wheel', handleUserIntent);
    window.removeEventListener('pointerdown', handleUserIntent);
    window.removeEventListener('touchmove', handleUserIntent);
  });

  return { isNearBottom, scrollToBottom };
}
