<script setup lang="ts">
import { computed, ref } from 'vue';

import { ProfileNotificationSetting } from '@vben/common-ui';

import {
  BellOutlined,
  LockOutlined,
  MessageOutlined,
  ScheduleOutlined,
} from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';

/** 通知设置状态 */
const settings = ref({
  systemMessage: true,
  todoTask: true,
  loginNotify: true,
  securityAlert: true,
});

/** 图标映射 */
const iconMap = {
  systemMessage: MessageOutlined,
  todoTask: ScheduleOutlined,
  loginNotify: BellOutlined,
  securityAlert: LockOutlined,
};

const formSchema = computed(() => {
  return [
    {
      value: settings.value.systemMessage,
      fieldName: 'systemMessage',
      label: '系统消息',
      description: '系统公告、版本更新等消息将以站内信形式通知',
      icon: iconMap.systemMessage,
    },
    {
      value: settings.value.todoTask,
      fieldName: 'todoTask',
      label: '待办任务',
      description: '待办任务提醒将以站内信形式通知',
      icon: iconMap.todoTask,
    },
    {
      value: settings.value.loginNotify,
      fieldName: 'loginNotify',
      label: '登录通知',
      description: '账号在新设备登录时发送通知提醒',
      icon: iconMap.loginNotify,
    },
    {
      value: settings.value.securityAlert,
      fieldName: 'securityAlert',
      label: '安全告警',
      description: '账号安全相关的告警信息通知',
      icon: iconMap.securityAlert,
    },
  ];
});

/** 切换通知设置：后端暂未提供通知设置接口，本地保存并提示 */
async function handleToggle(payload: Record<string, any>) {
  const { fieldName, value } = payload;
  settings.value[fieldName as keyof typeof settings.value] = value;
  message.success('设置已更新（后端存储开发中，暂存本地）');
}
</script>

<template>
  <div class="notification-setting">
    <ProfileNotificationSetting
      :form-schema="formSchema"
      @change="handleToggle"
    />
  </div>
</template>

<style scoped>
.notification-setting {
  max-width: 600px;
}

.notification-setting :deep(.vben-profile-notification-setting) {
  padding: 0;
}

.notification-setting :deep(.ant-list-item) {
  padding: 16px 20px;
  margin-bottom: 12px;
  background: #fff;
  border: 1px solid #f0f0f0 !important;
  border-radius: 12px;
  transition: all 0.3s ease;
}

.notification-setting :deep(.ant-list-item:hover) {
  border-color: #d9d9d9 !important;
  box-shadow: 0 4px 12px rgb(0 0 0 / 6%);
}

.notification-setting :deep(.ant-list-item-meta-title) {
  font-size: 15px;
  font-weight: 500;
  color: #1f2937;
}

.notification-setting :deep(.ant-list-item-meta-description) {
  font-size: 13px;
  color: #6b7280;
}

.notification-setting :deep(.ant-switch) {
  min-width: 44px;
}
</style>
