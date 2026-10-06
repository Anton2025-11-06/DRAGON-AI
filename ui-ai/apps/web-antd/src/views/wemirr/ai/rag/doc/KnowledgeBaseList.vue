<script lang="ts" setup>
/**
 * 知识库列表组件（对齐新后端契约，SPEC §11.1）。
 *
 * 不再走 fast-crud：三类知识库的卡片信息、按类型区分的入口用轻量列表比列声明更直观。
 * 增删改统一走 KnowledgeBaseFormModal + /knowledge-bases 端点。
 *
 * 两处口径变更：
 * - 启用/停用已取消（需求 5）：库可用不再有状态位，能不能看见、能不能改只看
 *   「归属人 + 数据范围 + ACL 显式授权」三层并集，后端把求值结果放进每条记录的
 *   actions 下发，前端不复制第二份判定规则（否则会按钮亮着点了却 403）；
 * - 「检索」「图谱」不再是行内按钮（需求 4），已升级为「知识检索」「图谱检索」两个独立菜单页。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { useAccess } from '@vben/access';

import {
  ApartmentOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  SearchOutlined,
  ShareAltOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import {
  Button,
  Card,
  Input,
  message,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { canAction } from '#/api/acl';

import { aclInapplicableActions } from '../../shared/acl-action-meta';
import AclGrantModal from '../../shared/components/AclGrantModal.vue';
import {
  DeleteKb,
  KB_TYPE_DOC,
  KB_TYPE_LABELS,
  KB_TYPES_ALL,
  KbPageList,
} from './api';
import type { KnowledgeBaseResp, KbType } from './api';
import KnowledgeBaseFormModal from './KnowledgeBaseFormModal.vue';

const emit = defineEmits<{
  (e: 'select', kb: KnowledgeBaseResp | null): void;
}>();

const route = useRoute();
const { hasPermission } = useAccess();

/**
 * 两层权限各管一头（需求 6）：
 * - 「列表查询」「新增」是全局入口，归 RBAC 功能权限（系统管理-菜单管理的按钮权限点）；
 * - 行内「编辑」「删除」只看这一条知识库的 ACL，「授权」多叠一层 grant 入口位。
 *   行级动作不再另开 ai:kb:edit / ai:kb:delete 这类功能权限点（与 v2_init.sql §8.9 同口径），
 *   否则会两层判据打架：菜单勾了 edit 但这条库没授给他，照样该灰。
 */
const canQueryKb = computed(() => hasPermission('ai:kb:list'));
const canAddKb = computed(() => hasPermission('ai:kb:add'));
const canEditKb = (kb: KnowledgeBaseResp) => canAction(kb, 'edit');
const canDeleteKb = (kb: KnowledgeBaseResp) => canAction(kb, 'delete');
const canGrantKb = (kb: KnowledgeBaseResp) =>
  hasPermission('ai:kb:grant') && canAction(kb, 'share');

/** 'all' 是一个真选项（需求 12）：选中它 = 不按类型筛选，而不是清空后再也点不回来 */
const TYPE_ALL = 'all';
const searchText = ref('');
const typeFilter = ref<KbType | typeof TYPE_ALL>(TYPE_ALL);
const selectedId = ref<number | null>(null);
const loading = ref(false);
const list = ref<KnowledgeBaseResp[]>([]);

// 增改弹窗状态
const formOpen = ref(false);
const editId = ref<null | number>(null);
const defaultKbType = ref<KbType>('doc');

// 授权弹窗状态：grantKb 为空即不挂载，:key 绑 id 让换库时整组件重建
const grantKb = ref<null | KnowledgeBaseResp>(null);
const grantOpen = ref(false);

/**
 * 图片/音视频库置灰那几项的原因文案（与 acl-action-meta 的置灰码同口径）。
 * 只置灰不隐藏：弹窗是整表提交的，隐藏的项会被当成「取消勾选」默默撤掉已有授权。
 */
const MEDIA_DISABLED_REASON =
  '这个知识库是图片/音视频型：资源不切分，没有解析产物与切片，这几项用不上';

const typeSelectOptions = computed(() => [
  { label: '全部类型', value: TYPE_ALL },
  ...KB_TYPES_ALL.map((t) => ({ label: KB_TYPE_LABELS[t], value: t })),
]);

/**
 * 卡片上的计数文案：文档库报「文档 + 切片」两个数，图片/音视频库只报「资源」
 * （它们不讲切片概念，一个资源就是一片，摆上去对它们只是噪声）。
 */
function kbCountTexts(kb: KnowledgeBaseResp): string[] {
  const unit = kb.kbType === KB_TYPE_DOC ? '文档' : '资源';
  const texts = [`${unit} ${kb.docCount || 0}`];
  if (kb.kbType === KB_TYPE_DOC) {
    texts.push(`切片 ${kb.chunkCount || 0}`);
  }
  return texts;
}

async function load() {
  // 没有查询按钮位就不发请求：后端那关也会 403，但提前拦住可以少一次红色报错
  if (!canQueryKb.value) {
    list.value = [];
    emit('select', null);
    return;
  }
  loading.value = true;
  try {
    const res = await KbPageList({
      current: 1,
      size: 200,
      name: searchText.value.trim() || undefined,
      kbType: typeFilter.value === TYPE_ALL ? undefined : typeFilter.value,
    });
    list.value = res.records || [];
    ensureSelection();
  } catch (e: any) {
    message.error(e?.message || '加载知识库失败');
  } finally {
    loading.value = false;
  }
}

/** 保持/初始化选中：优先路由 kbId，其次当前选中，最后第一个 */
function ensureSelection() {
  if (!list.value.length) {
    selectedId.value = null;
    emit('select', null);
    return;
  }
  const kbIdFromQuery = route.query.kbId;
  let target: KnowledgeBaseResp | undefined;
  if (kbIdFromQuery) {
    target = list.value.find((kb) => kb.id === Number(kbIdFromQuery));
  }
  if (!target && selectedId.value) {
    target = list.value.find((kb) => kb.id === selectedId.value);
  }
  target = target || list.value[0];
  if (target) handleSelect(target);
}

function handleSelect(kb: KnowledgeBaseResp) {
  selectedId.value = kb.id;
  emit('select', kb);
}

function handleAdd() {
  if (!canAddKb.value) return message.warning('没有新建知识库的权限');
  editId.value = null;
  defaultKbType.value = 'doc';
  formOpen.value = true;
}

function handleGrant(kb: KnowledgeBaseResp, event: Event) {
  event.stopPropagation();
  grantKb.value = kb;
  grantOpen.value = true;
}

function handleEdit(kb: KnowledgeBaseResp, event: Event) {
  event.stopPropagation();
  editId.value = kb.id;
  defaultKbType.value = kb.kbType;
  formOpen.value = true;
}

function handleDelete(kb: KnowledgeBaseResp, event: Event) {
  event.stopPropagation();
  Modal.confirm({
    content: `删除知识库「${kb.name}」将级联清理其文档、切片与图谱实体，且不可恢复。`,
    title: '确认删除',
    okType: 'danger',
    async onOk() {
      await DeleteKb(kb.id);
      message.success('删除成功');
      if (selectedId.value === kb.id) {
        selectedId.value = null;
      }
      load();
    },
  });
}

function onFormSuccess() {
  load();
}

watch(typeFilter, load);

defineExpose({ refresh: load });

onMounted(load);
</script>

<template>
  <div class="kb-wrapper">
    <Card class="kb-card" :bordered="false">
      <template #title>
        <div class="flex items-center">
          <DatabaseOutlined class="mr-2 text-blue-500" />
          知识库列表
        </div>
      </template>
      <template #extra>
        <Tooltip v-if="canAddKb" title="新增知识库">
          <Button type="primary" size="small" @click="handleAdd">
            <template #icon><PlusOutlined /></template>
          </Button>
        </Tooltip>
      </template>

      <div class="kb-content">
        <Space class="mb-3" :size="8" style="width: 100%">
          <Input
            v-model:value="searchText"
            placeholder="搜索名称 / 描述"
            allow-clear
            style="flex: 1"
            @press-enter="load"
          >
            <template #prefix><SearchOutlined /></template>
          </Input>
          <Button size="small" :disabled="!canQueryKb" @click="load">查询</Button>
        </Space>

        <!-- 需求 12：类型下拉给上提示文案，与搜索框拼成一句「搜索名称/描述，知识库类型」；
             「全部类型」是个可选中的真选项，选中后不参与筛选 -->
        <Tooltip title="按知识库类型筛选，选「全部类型」则不按类型筛选">
          <Select
            v-model:value="typeFilter"
            :options="typeSelectOptions"
            placeholder="知识库类型"
            class="kb-type-select"
            size="small"
          />
        </Tooltip>

        <div class="kb-list">
          <Spin :spinning="loading">
            <div
              v-for="kb in list"
              :key="kb.id"
              class="kb-item"
              :class="{ selected: selectedId === kb.id }"
              @click="handleSelect(kb)"
            >
              <div class="kb-header">
                <div class="flex flex-1 items-center">
                  <span class="font-medium ellipsis">{{ kb.name }}</span>
                  <Tag class="ml-2" color="geekblue">
                    {{ kb.kbTypeLabel || KB_TYPE_LABELS[kb.kbType] }}
                  </Tag>
                </div>
                <Space :size="0" class="kb-actions">
                  <Tooltip v-if="canEditKb(kb)" title="编辑">
                    <Button
                      type="text"
                      size="small"
                      @click="(e) => handleEdit(kb, e)"
                    >
                      <template #icon><EditOutlined /></template>
                    </Button>
                  </Tooltip>
                  <Tooltip v-if="canGrantKb(kb)" title="授权">
                    <Button
                      type="text"
                      size="small"
                      @click="(e) => handleGrant(kb, e)"
                    >
                      <template #icon><ShareAltOutlined /></template>
                    </Button>
                  </Tooltip>
                  <Tooltip v-if="canDeleteKb(kb)" title="删除">
                    <Button
                      type="text"
                      size="small"
                      danger
                      @click="(e) => handleDelete(kb, e)"
                    >
                      <template #icon><DeleteOutlined /></template>
                    </Button>
                  </Tooltip>
                </Space>
              </div>

              <div v-if="kb.description" class="kb-description">
                {{ kb.description }}
              </div>

              <div class="kb-meta">
                <span v-for="m in kbCountTexts(kb)" :key="m">{{ m }}</span>
                <Tag v-if="kb.enableGraph" color="purple" size="small">图谱</Tag>
              </div>

              <!-- 需求 2：创建人与归属部门。名字由后端 attach_creator / attach_dept 补，
                   前端不拿 created_by 这种数字 id 给人看；查不到部门时退化成不展示这一行 -->
              <div class="kb-owner">
                <span class="kb-owner-item">
                  <UserOutlined class="mr-1" />{{ kb.creatorName || '未知创建人' }}
                </span>
                <span v-if="kb.deptName" class="kb-owner-item">
                  <ApartmentOutlined class="mr-1" />{{ kb.deptName }}
                </span>
              </div>

              <div class="kb-footer">
                <span class="kb-time">{{ kb.createTime }}</span>
              </div>
            </div>

            <div v-if="list.length === 0 && !loading" class="empty-state">
              <DatabaseOutlined class="empty-icon" />
              <p v-if="!canQueryKb">没有「知识库查询」按钮权限，列表为空</p>
              <p v-else-if="searchText.trim() || typeFilter !== TYPE_ALL">
                未找到匹配的知识库
              </p>
              <div v-else>
                <p>暂无知识库</p>
                <Button v-if="canAddKb" type="link" @click="handleAdd">立即创建</Button>
              </div>
            </div>
          </Spin>
        </div>
      </div>
    </Card>

    <KnowledgeBaseFormModal
      v-model:open="formOpen"
      :edit-id="editId"
      :default-kb-type="defaultKbType"
      @success="onFormSuccess"
    />

    <AclGrantModal
      v-if="grantKb"
      v-model:open="grantOpen"
      :key="grantKb.id"
      resource-code="knowledge_base"
      :resource-id="grantKb.id"
      :resource-name="grantKb.name"
      :disabled-actions="aclInapplicableActions(grantKb.kbType)"
      :disabled-reason="MEDIA_DISABLED_REASON"
    />
  </div>
</template>

<style lang="less" scoped>
.kb-wrapper {
  position: relative;
  height: 100%;
}

.kb-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;

  :deep(.ant-card-body) {
    display: flex;
    flex: 1;
    flex-direction: column;
    padding: 16px;
    min-height: 0;
    overflow: hidden;
  }
}

.kb-content {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* 整列表域只占卡片剩下的那一截，库多了就在这块里上下滚（flex 项没有 min-height: 0
   会被内容顶高，滚动条根本不出现，下面的库直接被裁掉看不见） */
.kb-list {
  flex: 1;
  padding-right: 4px;
  min-height: 0;
  overflow-y: auto;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-thumb {
    background: #ccc;
    border-radius: 4px;
  }
}

.kb-item {
  padding: 12px;
  margin-bottom: 8px;
  cursor: pointer;
  background: var(--component-background, #fff);
  border: 1px solid var(--border-color, #e8e8e8);
  border-radius: 6px;
  transition: all 0.3s;

  &:hover {
    border-color: hsl(var(--primary));
    box-shadow: 0 2px 8px rgb(0 0 0 / 10%);
  }

  &.selected {
    background: hsl(var(--primary) / 10%);
    border-color: hsl(var(--primary));
  }
}

.kb-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

/* 行操作图标常显：以前藏在 hover 里，但这一列已经是图标（不占文字宽度），
   藏起来只会让人以为这些知识库不能改不能删；没权限的行压根不渲染按钮，不会多出一排灰图标 */
.kb-actions {
  flex-shrink: 0;
}

.kb-description {
  display: -webkit-box;
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  font-size: 12px;
  line-height: 1.4;
  color: var(--text-color-secondary, #666);
  -webkit-box-orient: vertical;
}

.kb-meta {
  display: flex;
  gap: 12px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--text-color-secondary, #888);
  align-items: center;
}

.kb-type-select {
  width: 120px;
  margin-bottom: 12px;
}

.kb-owner {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--text-color-secondary, #666);
}

.kb-owner-item {
  display: inline-flex;
  align-items: center;
}

.kb-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kb-time {
  font-size: 11px;
  color: var(--text-color-secondary, #999);
}

.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-state {
  padding: 40px 20px;
  color: var(--text-color-secondary, #999);
  text-align: center;

  .empty-icon {
    margin-bottom: 16px;
    font-size: 48px;
    opacity: 0.3;
  }
}
</style>
