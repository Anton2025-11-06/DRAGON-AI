<script lang="ts" setup name="ModelComingSoon">
/**
 * 业务占位页（后端服务未开发时的统一风格页面）
 * 保留 ui-ai 的卡片式页面风格，开发完成后可直接替换为真实业务组件
 */
interface FeatureItem {
  icon: string;
  title: string;
  description: string;
}

withDefaults(
  defineProps<{
    /** 页面标题 */
    title: string;
    /** 对应后端服务标识（展示用） */
    service: string;
    /** 服务描述 */
    description?: string;
    /** 规划中的功能点 */
    features?: FeatureItem[];
  }>(),
  {
    description: '',
    features: () => [],
  },
);
</script>

<template>
  <div class="coming-soon-page">
    <div class="page-toolbar">
      <div class="page-title">
        <slot name="icon" />
        <span>{{ title }}</span>
        <a-tag color="blue">{{ service }}</a-tag>
      </div>
    </div>

    <div class="content-card">
      <div class="content-header">
        <h3 class="content-title">功能规划中</h3>
        <p class="content-desc">
          {{ description || '该模块对应的后端微服务尚未开发完成，敬请期待。' }}
        </p>
        <a-tag class="service-tag" color="orange">后端服务开发中</a-tag>
      </div>

      <div v-if="features.length" class="feature-grid">
        <div
          v-for="feature in features"
          :key="feature.title"
          class="feature-item"
        >
          <span class="feature-icon">
            <slot :name="`feature-icon-${feature.title}`">
              <template>{{ feature.icon }}</template>
            </slot>
          </span>
          <span class="feature-title">{{ feature.title }}</span>
          <span class="feature-desc">{{ feature.description }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.coming-soon-page {
  min-height: 100%;
  padding: 20px;
}

.page-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  font-size: 18px;
  font-weight: 600;
}

.content-card {
  padding: 40px 32px;
  background: var(--component-background, #fff);
  border-radius: 8px;
  box-shadow: 0 1px 2px rgb(0 0 0 / 3%);
}

.content-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 0 8px;
  text-align: center;

  .content-title {
    margin: 0 0 8px;
    font-size: 20px;
    font-weight: 600;
    color: #333;
  }

  .content-desc {
    max-width: 640px;
    margin: 0 0 12px;
    font-size: 14px;
    color: #999;
    line-height: 1.8;
  }
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px;
  margin-top: 24px;
}

.feature-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 20px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  transition: all 0.2s;

  &:hover {
    background: #fff;
    border-color: #d9d9d9;
    box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
    transform: translateY(-2px);
  }

  .feature-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 48px;
    height: 48px;
    font-size: 22px;
    color: #1890ff;
    background: rgb(24 144 255 / 8%);
    border-radius: 12px;
  }

  .feature-title {
    font-size: 15px;
    font-weight: 600;
    color: #333;
  }

  .feature-desc {
    font-size: 13px;
    color: #999;
    line-height: 1.6;
  }
}
</style>