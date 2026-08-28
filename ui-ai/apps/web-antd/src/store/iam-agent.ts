import { ref } from 'vue';

import { defineStore } from 'pinia';

/**
 * 全局平台助手只负责抽屉可见性。
 *
 * 会话、消息和流式响应沿用 AI 聊天模块的能力，由抽屉组件管理，避免再次构造 Run 状态机。
 */
export const useIamAgentStore = defineStore(
  'iam-agent',
  () => {
    const drawerOpen = ref(false);

    function openDrawer() {
      drawerOpen.value = true;
    }

    function closeDrawer() {
      drawerOpen.value = false;
    }

    function dispose() {
      // 抽屉关闭后保留当前会话，重新打开即可继续交流。
    }

    function $reset() {
      drawerOpen.value = false;
    }

    return { $reset, closeDrawer, dispose, drawerOpen, openDrawer };
  },
  {
    persist: {
      pick: ['drawerOpen'],
    },
  },
);
