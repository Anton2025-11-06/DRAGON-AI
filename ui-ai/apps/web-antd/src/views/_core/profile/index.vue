<script setup lang="ts">
import { computed, ref } from 'vue';

import { Page } from '@vben/common-ui';
import { preferences } from '@vben/preferences';
import { useUserStore } from '@vben/stores';

import {
  ApartmentOutlined,
  MailOutlined,
  MobileOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import { Avatar, Card, Tabs } from 'ant-design-vue';

import BaseSetting from './base-setting.vue';
import PasswordSetting from './password-setting.vue';

const userStore = useUserStore();
const activeTab = ref('base');

/** 用户信息 */
const userInfo = computed(() => userStore.userInfo);

/** 头像地址 */
const avatarSrc = computed(
  () => userInfo.value?.avatar || preferences.app.defaultAvatar,
);
</script>

<template>
  <Page auto-content-height>
    <div class="profile-container">
      <!-- 左侧个人信息卡片 -->
      <Card class="profile-sidebar" :bordered="false">
        <div class="user-header">
          <Avatar :src="avatarSrc" :size="80" class="user-avatar">
            {{ userInfo?.nickname?.charAt(0) || 'U' }}
          </Avatar>
          <h3 class="user-name">
            {{ userInfo?.realName || userInfo?.nickname || '-' }}
          </h3>
          <p class="user-account">{{ userInfo?.username || '-' }}</p>
        </div>

        <div class="user-info-list">
          <div class="info-item">
            <UserOutlined class="info-icon" />
            <span class="info-label">真实姓名</span>
            <span class="info-value">{{
              userInfo?.realName || userInfo?.nickname || '-'
            }}</span>
          </div>
          <div class="info-item">
            <ApartmentOutlined class="info-icon" />
            <span class="info-label">所属部门</span>
            <span class="info-value">{{ userInfo?.deptName || '-' }}</span>
          </div>
          <div class="info-item">
            <MobileOutlined class="info-icon" />
            <span class="info-label">手机号码</span>
            <span class="info-value">{{ userInfo?.mobile || '-' }}</span>
          </div>
          <div class="info-item">
            <MailOutlined class="info-icon" />
            <span class="info-label">用户邮箱</span>
            <span class="info-value">{{ userInfo?.email || '-' }}</span>
          </div>
        </div>
      </Card>

      <!-- 右侧表单区域 -->
      <Card class="profile-content" :bordered="false">
        <Tabs v-model:active-key="activeTab" class="profile-tabs">
          <Tabs.TabPane key="base" tab="基本资料">
            <BaseSetting />
          </Tabs.TabPane>
          <Tabs.TabPane key="password" tab="修改密码">
            <PasswordSetting />
          </Tabs.TabPane>
        </Tabs>
      </Card>
    </div>
  </Page>
</template>

<style scoped>
.profile-container {
  display: flex;
  gap: 16px;
  height: 100%;
}

.profile-sidebar {
  flex-shrink: 0;
  width: 380px;
}

.profile-sidebar :deep(.ant-card-body) {
  padding: 24px;
}

.user-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-bottom: 24px;
  margin-bottom: 20px;
  border-bottom: 1px solid hsl(var(--border));
}

.user-avatar {
  font-size: 32px;
  font-weight: 600;
  background: linear-gradient(
    135deg,
    hsl(var(--primary)) 0%,
    hsl(var(--primary)) 100%
  );
}

.user-name {
  margin: 16px 0 4px;
  font-size: 18px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.user-account {
  margin: 0;
  font-size: 14px;
  color: hsl(var(--muted-foreground));
}

.user-info-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.info-item {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 14px;
}

.info-icon {
  flex-shrink: 0;
  width: 16px;
  color: hsl(var(--muted-foreground));
}

.info-label {
  flex-shrink: 0;
  width: 70px;
  color: hsl(var(--muted-foreground));
}

.info-value {
  flex: 1;
  color: hsl(var(--foreground));
  text-align: right;
  word-break: break-all;
}

.profile-content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.profile-content :deep(.ant-card-body) {
  height: 100%;
  padding: 0;
}

.profile-tabs {
  height: 100%;
}

.profile-tabs :deep(.ant-tabs-nav) {
  padding: 0 24px;
  margin-bottom: 0;
  border-bottom: 1px solid hsl(var(--border));
}

.profile-tabs :deep(.ant-tabs-tab) {
  padding: 16px 0;
  font-size: 15px;
}

.profile-tabs :deep(.ant-tabs-content-holder) {
  padding: 24px;
  overflow: auto;
}

.profile-tabs :deep(.ant-tabs-content) {
  height: 100%;
}
</style>
