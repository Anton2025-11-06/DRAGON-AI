<script lang="ts" setup name="AiSkillPage">
import type { Key } from 'ant-design-vue/es/table/interface';
import type { UploadFile } from 'ant-design-vue';

import type { SkillDetailResp, SkillPageResp, SkillUploadReq } from './api';

import { computed, nextTick, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  CodeOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  FileOutlined,
  FileMarkdownOutlined,
  FileTextOutlined,
  FolderOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  SaveOutlined,
  SettingOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue';
import {
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Switch,
  Tag,
  Tree,
  Upload,
} from 'ant-design-vue';
import { useFs } from '@fast-crud/fast-crud';

import * as api from './api';
import createCrudOptions from './crud';
import CodeEditor from './components/code-editor.vue';
import MarkdownRenderer from '../../workflow/components/debug/MarkdownRenderer.vue';

interface SkillFormState {
  category?: string;
  description?: string;
  icon?: string;
  name: string;
  skillPackage: UploadFile[];
  tagsInput?: string;
}

interface PreviewTreeNode {
  children?: PreviewTreeNode[];
  isLeaf: boolean;
  key: string;
  title: string;
  type: 'file' | 'folder';
}

const previewIconMap: Record<string, string> = {
  bash: 'code',
  cjs: 'code',
  css: 'style',
  html: 'style',
  ini: 'config',
  java: 'code',
  js: 'code',
  json: 'config',
  jsx: 'code',
  less: 'style',
  md: 'markdown',
  mjs: 'code',
  properties: 'config',
  psm1: 'code',
  ps1: 'code',
  py: 'code',
  sh: 'code',
  sql: 'database',
  ts: 'code',
  tsx: 'code',
  xml: 'config',
  yaml: 'config',
  yml: 'config',
};

const saving = ref(false);
const previewLoading = ref(false);
const previewSaving = ref(false);
const uploadDrawerVisible = ref(false);
const previewModalVisible = ref(false);
const editingSkill = ref<SkillPageResp | null>(null);
const previewSkill = ref<SkillDetailResp | null>(null);
const previewContentMap = ref<Record<string, string>>({});
const previewTreeData = ref<PreviewTreeNode[]>([]);
const expandedPreviewKeys = ref<string[]>([]);
const selectedPreviewKeys = ref<string[]>([]);
const selectedPreviewPath = ref('');

const formState = reactive<SkillFormState>({
  category: '',
  description: '',
  icon: '',
  name: '',
  skillPackage: [],
  tagsInput: '',
});

const selectedPreviewContent = computed({
  get: () =>
    selectedPreviewPath.value
      ? previewContentMap.value[selectedPreviewPath.value] || ''
      : '',
  set: (value: string) => {
    if (selectedPreviewPath.value) {
      previewContentMap.value[selectedPreviewPath.value] = value;
    }
  },
});

const isMarkdownPreview = computed(() =>
  ['markdown', 'md'].includes(getPreviewExtension(selectedPreviewPath.value)),
);

const { crudBinding, crudExpose, crudRef } = useFs({
  createCrudOptions,
  context: {
    onPreview: (item: SkillPageResp) => openPreview(item),
    onReplace: (item: SkillPageResp) => openReplaceDrawer(item),
    toggleStatus: (item: SkillPageResp, checked: boolean) =>
      toggleStatus(item, checked),
  },
});
void crudRef;

function normalizeArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string');
  }
  if (typeof value === 'string' && value) {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed)
        ? parsed.filter((item): item is string => typeof item === 'string')
        : [];
    } catch {
      return [];
    }
  }
  return [];
}

function normalizeRecord(value: unknown): Record<string, string> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, content]) => typeof content === 'string')
        .map(([path, content]) => [normalizePreviewPath(path), content as string]),
    );
  }
  if (typeof value === 'string' && value) {
    try {
      return normalizeRecord(JSON.parse(value));
    } catch {
      return {};
    }
  }
  return {};
}

function normalizePreviewPath(path: string) {
  return path.replaceAll('\\', '/').split('/').filter(Boolean).join('/');
}

function getPreviewExtension(path: string) {
  const filename = path.split('/').pop() || '';
  return filename.includes('.') ? filename.split('.').pop()?.toLowerCase() || '' : '';
}

function getPreviewIconType(path: string) {
  return previewIconMap[getPreviewExtension(path)] || 'file';
}

function getAllFolderKeys(nodes: PreviewTreeNode[]): string[] {
  return nodes.flatMap((node) => {
    if (node.type === 'file') {
      return [];
    }
    return [node.key, ...getAllFolderKeys(node.children || [])];
  });
}

function sortPreviewNodes(nodes: PreviewTreeNode[]) {
  nodes.sort((left, right) => {
    if (left.type !== right.type) {
      return left.type === 'folder' ? -1 : 1;
    }
    return left.title.localeCompare(right.title);
  });
  nodes.forEach((node) => sortPreviewNodes(node.children || []));
}

function buildPreviewTree(fileMap: Record<string, string>): PreviewTreeNode[] {
  const root: PreviewTreeNode[] = [];
  const nodeMap = new Map<string, PreviewTreeNode>();

  Object.keys(fileMap)
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right))
    .forEach((path) => {
      const parts = path.split('/').filter(Boolean);
      let level = root;
      parts.forEach((part, index) => {
        const fullPath = parts.slice(0, index + 1).join('/');
        const isFile = index === parts.length - 1;
        let node = nodeMap.get(fullPath);
        if (!node) {
          node = {
            children: isFile ? undefined : [],
            isLeaf: isFile,
            key: fullPath,
            title: part,
            type: isFile ? 'file' : 'folder',
          };
          nodeMap.set(fullPath, node);
          level.push(node);
        }
        if (!isFile) {
          node.children ||= [];
          level = node.children;
        }
      });
    });

  sortPreviewNodes(root);
  return root;
}

function buildPreviewFileMap(skill: SkillDetailResp) {
  const fileMap: Record<string, string> = {};
  fileMap['SKILL.md'] = skill.skillContent || '';
  const resourceContents = normalizeRecord(skill.resourceContents);
  const resourcePaths = normalizeArray(skill.resources)
    .map(normalizePreviewPath)
    .filter(Boolean);
  resourcePaths.forEach((path) => {
    fileMap[path] = resourceContents[path] || '';
  });
  Object.entries(resourceContents).forEach(([path, content]) => {
    if (path && path !== 'SKILL.md') {
      fileMap[path] = content;
    }
  });
  return fileMap;
}

function relativePathOf(file: File) {
  return (file as any).webkitRelativePath || (file as any).relativePath || file.name;
}

function normalizeSkillCode(value: string) {
  const normalized = value
    .trim()
    .replaceAll('\\', '/')
    .split('/')
    .filter(Boolean)[0]
    ?.replaceAll(/[^-\w]/g, '-')
    .replaceAll(/-+/g, '-')
    .replaceAll(/^-|-$/g, '');
  return normalized || 'skill';
}

function deriveSkillCode(relativePaths: string[]) {
  const skillFilePath =
    relativePaths.find((path) => path === 'SKILL.md' || path.endsWith('/SKILL.md')) ||
    relativePaths[0] ||
    '';
  return normalizeSkillCode(skillFilePath);
}

function parseTags(input?: string) {
  return (input || '')
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function resolveUploadFiles(fileList: UploadFile[]) {
  const files = fileList
    .map((item) => item.originFileObj || item)
    .filter(Boolean) as unknown as File[];
  return {
    files,
    relativePaths: files.map(relativePathOf),
  };
}

function resetForm() {
  Object.assign(formState, {
    category: '',
    description: '',
    icon: '',
    name: '',
    skillPackage: [],
    tagsInput: '',
  });
}

function openCreateDrawer() {
  editingSkill.value = null;
  resetForm();
  uploadDrawerVisible.value = true;
}

function openReplaceDrawer(item: SkillPageResp) {
  editingSkill.value = item;
  Object.assign(formState, {
    category: item.category || '',
    description: item.description || '',
    icon: item.icon || '',
    name: item.name,
    skillPackage: [],
    tagsInput: normalizeArray(item.tags).join(', '),
  });
  uploadDrawerVisible.value = true;
}

function beforeUpload() {
  return false;
}

function handleUploadChange({ fileList }: { fileList: UploadFile[] }) {
  formState.skillPackage = fileList;
}

function buildUploadReq(): SkillUploadReq {
  const { files, relativePaths } = resolveUploadFiles(formState.skillPackage);
  if (!formState.name.trim()) {
    throw new Error('请输入技能名称');
  }
  if (files.length === 0) {
    throw new Error('请选择技能文件夹');
  }
  if (!relativePaths.some((path) => path === 'SKILL.md' || path.endsWith('/SKILL.md'))) {
    throw new Error('技能文件夹必须包含 SKILL.md');
  }
  return {
    category: formState.category?.trim() || undefined,
    code: editingSkill.value?.code || deriveSkillCode(relativePaths),
    description: formState.description?.trim() || undefined,
    files,
    icon: formState.icon?.trim() || undefined,
    name: formState.name.trim(),
    relativePaths,
    tags: parseTags(formState.tagsInput),
  };
}

async function submitSkill() {
  let payload: SkillUploadReq;
  try {
    payload = buildUploadReq();
  } catch (error: any) {
    message.warning(error?.message || '请完善技能目录信息');
    return;
  }

  saving.value = true;
  try {
    if (editingSkill.value) {
      await api.UpdateObj(editingSkill.value.id, payload);
      message.success('技能目录已替换');
    } else {
      await api.AddObj(payload);
      message.success('技能目录已上传');
    }
    uploadDrawerVisible.value = false;
    await crudExpose.doRefresh();
  } finally {
    saving.value = false;
  }
}

async function openPreview(item: SkillPageResp) {
  previewModalVisible.value = true;
  previewSkill.value = null;
  previewContentMap.value = {};
  previewTreeData.value = [];
  expandedPreviewKeys.value = [];
  selectedPreviewKeys.value = [];
  selectedPreviewPath.value = '';
  previewLoading.value = true;
  try {
    const detail = await api.PreviewSkill(item.id);
    previewSkill.value = detail;
    previewContentMap.value = buildPreviewFileMap(detail);
    previewTreeData.value = buildPreviewTree(previewContentMap.value);
    expandedPreviewKeys.value = getAllFolderKeys(previewTreeData.value);
    const firstPath =
      previewContentMap.value['SKILL.md'] !== undefined
        ? 'SKILL.md'
        : Object.keys(previewContentMap.value)[0] || '';
    selectedPreviewPath.value = firstPath;
    selectedPreviewKeys.value = firstPath ? [firstPath] : [];
  } finally {
    previewLoading.value = false;
  }
}

async function downloadSkill(item: SkillPageResp) {
  message.info({ content: '开始下载技能目录', duration: 3 });
  await api.DownloadSkill(item);
}

function closePreview() {
  previewModalVisible.value = false;
}

function handlePreviewSelect(selectedKeys: Key[]) {
  const selectedKey = selectedKeys.length > 0 ? String(selectedKeys[0]) : '';
  if (!selectedKey || previewContentMap.value[selectedKey] === undefined) {
    return;
  }
  selectedPreviewPath.value = selectedKey;
  selectedPreviewKeys.value = [selectedKey];
}

async function savePreviewFile() {
  if (!previewSkill.value?.id || !selectedPreviewPath.value) {
    message.warning('请选择要保存的文件');
    return;
  }
  previewSaving.value = true;
  try {
    await api.UpdateSkillFile(previewSkill.value.id, {
      content: selectedPreviewContent.value,
      path: selectedPreviewPath.value,
    });
    message.success('技能文件已保存');
  } finally {
    previewSaving.value = false;
  }
}

function confirmRemove(item: SkillPageResp) {
  Modal.confirm({
    cancelText: '取消',
    content: `确定要删除技能目录「${item.name}」吗？`,
    okText: '删除',
    okType: 'danger',
    title: '确认删除',
    onOk: async () => {
      await crudExpose.doRemove({ row: item });
    },
  });
}

async function toggleStatus(item: SkillPageResp, checked: boolean) {
  const previous = item.status;
  item.status = checked;
  try {
    await api.ToggleStatus(item.id, checked);
    message.success(checked ? '已启用' : '已禁用');
  } catch (error: any) {
    item.status = previous;
    message.error(error?.message || '状态更新失败');
  }
}

onMounted(() => {
  nextTick(() => {
    crudExpose.doRefresh();
  });
});
</script>

<template>
  <Page content-class="skill-page-content">
    <Card class="skill-list-card" title="AI技能管理">
      <fs-crud ref="crudRef" v-bind="crudBinding">
        <template #actionbar-left>
          <a-button type="primary" @click="openCreateDrawer">
            <template #icon><UploadOutlined /></template>
            上传技能文件夹
          </a-button>
        </template>
        <template #default>
          <div v-if="crudBinding.data?.length" class="skill-card-grid">
            <div
              v-for="item in crudBinding.data"
              :key="item.id"
              class="skill-folder-card"
              data-testid="skill-folder-card"
            >
              <div class="skill-card-main" @click="openPreview(item)">
                <div class="skill-card-icon">
                  <FolderOpenOutlined />
                </div>
                <div class="skill-card-content">
                  <div class="skill-card-title-row">
                    <h3 class="skill-card-title">{{ item.name }}</h3>
                    <Tag :color="item.status ? 'success' : 'default'">
                      {{ item.status ? '启用' : '禁用' }}
                    </Tag>
                  </div>
                  <div class="skill-card-path">
                    {{ item.skillPath || `skills/${item.code}` }}
                  </div>
                  <p class="skill-card-desc">
                    {{ item.description || '暂无描述' }}
                  </p>
                </div>
              </div>

              <div class="skill-card-meta">
                <span>{{ item.skillFile || 'SKILL.md' }}</span>
                <span>{{ item.resourceCount || 0 }} 个资源</span>
              </div>

              <div class="skill-card-tags">
                <Tag v-if="item.category">{{ item.category }}</Tag>
                <Tag v-for="tag in normalizeArray(item.tags)" :key="tag">
                  {{ tag }}
                </Tag>
              </div>

              <div class="skill-card-footer">
                <Switch
                  :checked="item.status"
                  checked-children="启用"
                  size="small"
                  un-checked-children="禁用"
                  @change="(checked) => toggleStatus(item, Boolean(checked))"
                />
                <div class="skill-card-actions">
                  <a-tooltip title="预览">
                    <a-button type="text" size="small" @click="openPreview(item)">
                      <template #icon><EyeOutlined /></template>
                    </a-button>
                  </a-tooltip>
                  <a-tooltip title="下载">
                    <a-button type="text" size="small" @click="downloadSkill(item)">
                      <template #icon><DownloadOutlined /></template>
                    </a-button>
                  </a-tooltip>
                  <a-tooltip title="替换目录">
                    <a-button
                      type="text"
                      size="small"
                      @click="openReplaceDrawer(item)"
                    >
                      <template #icon><EditOutlined /></template>
                    </a-button>
                  </a-tooltip>
                  <a-tooltip title="删除">
                    <a-button
                      danger
                      type="text"
                      size="small"
                      @click="confirmRemove(item)"
                    >
                      <template #icon><DeleteOutlined /></template>
                    </a-button>
                  </a-tooltip>
                </div>
              </div>
            </div>
          </div>
          <Empty v-else class="skill-empty" description="还没有技能目录">
            <a-button type="primary" @click="openCreateDrawer">
              <template #icon><UploadOutlined /></template>
              上传技能文件夹
            </a-button>
          </Empty>
        </template>
      </fs-crud>
    </Card>

    <Drawer
      v-model:open="uploadDrawerVisible"
      :title="editingSkill ? '替换技能文件夹' : '上传技能文件夹'"
      width="560"
    >
      <Form layout="vertical">
        <a-form-item label="技能名称" required>
          <Input
            v-model:value="formState.name"
            :maxlength="100"
            placeholder="请输入技能名称"
            show-count
          />
        </a-form-item>
        <a-form-item label="技能描述">
          <Input.TextArea
            v-model:value="formState.description"
            :maxlength="500"
            :rows="3"
            placeholder="说明该技能适合处理的问题和边界"
            show-count
          />
        </a-form-item>
        <a-form-item label="分类">
          <Input
            v-model:value="formState.category"
            :maxlength="50"
            placeholder="例如: 数据查询"
            show-count
          />
        </a-form-item>
        <a-form-item label="标签">
          <Input
            v-model:value="formState.tagsInput"
            placeholder="多个标签用逗号分隔"
          />
        </a-form-item>
        <a-form-item label="技能文件夹" required>
          <Upload.Dragger
            :before-upload="beforeUpload"
            :directory="true"
            :file-list="formState.skillPackage"
            :multiple="true"
            @change="handleUploadChange"
          >
            <p class="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p class="ant-upload-text">选择或拖入技能文件夹</p>
            <p class="ant-upload-hint">
              文件夹内必须包含 SKILL.md，其他资源文件会随目录一起上传到 OSS。
            </p>
          </Upload.Dragger>
        </a-form-item>
      </Form>
      <template #extra>
        <a-space>
          <a-button @click="uploadDrawerVisible = false">取消</a-button>
          <a-button :loading="saving" type="primary" @click="submitSkill">
            保存
          </a-button>
        </a-space>
      </template>
    </Drawer>

    <Modal
      :open="previewModalVisible"
      :footer="null"
      title="技能预览"
      width="80%"
      @cancel="closePreview"
    >
      <a-spin :spinning="previewLoading">
        <div class="preview-container">
          <div class="tree-panel">
            <Tree
              v-model:expanded-keys="expandedPreviewKeys"
              v-model:selected-keys="selectedPreviewKeys"
              :tree-data="previewTreeData"
              show-icon
              @select="handlePreviewSelect"
            >
              <template #icon="{ expanded, isLeaf, data }">
                <template v-if="!isLeaf">
                  <FolderOpenOutlined v-if="expanded" style="color: #e8a838" />
                  <FolderOutlined v-else style="color: #e8a838" />
                </template>
                <template v-else>
                  <CodeOutlined
                    v-if="getPreviewIconType(data.title) === 'java'"
                    style="color: #e76f00"
                  />
                  <SettingOutlined
                    v-else-if="getPreviewIconType(data.title) === 'xml'"
                    style="color: #f16529"
                  />
                  <CodeOutlined
                    v-else-if="getPreviewIconType(data.title) === 'ts'"
                    style="color: #3178c6"
                  />
                  <CodeOutlined
                    v-else-if="getPreviewIconType(data.title) === 'js'"
                    style="color: #f7df1e"
                  />
                  <CodeOutlined
                    v-else-if="getPreviewIconType(data.title) === 'code'"
                    style="color: #722ed1"
                  />
                  <DatabaseOutlined
                    v-else-if="getPreviewIconType(data.title) === 'database'"
                    style="color: #336791"
                  />
                  <FileTextOutlined
                    v-else-if="getPreviewIconType(data.title) === 'style'"
                    style="color: #264de4"
                  />
                  <SettingOutlined
                    v-else-if="getPreviewIconType(data.title) === 'config'"
                    style="color: #6d6d6d"
                  />
                  <FileMarkdownOutlined
                    v-else-if="getPreviewIconType(data.title) === 'markdown'"
                    style="color: #083fa1"
                  />
                  <FileOutlined v-else style="color: #8c8c8c" />
                </template>
              </template>
            </Tree>
          </div>
          <div class="code-panel">
            <div class="preview-toolbar">
              <span class="preview-path">{{ selectedPreviewPath }}</span>
              <a-button
                :disabled="!selectedPreviewPath"
                :loading="previewSaving"
                type="primary"
                @click="savePreviewFile"
              >
                <template #icon><SaveOutlined /></template>
                保存
              </a-button>
            </div>
            <div v-if="isMarkdownPreview" class="markdown-edit-layout">
              <div class="markdown-editor">
                <CodeEditor
                  v-model:command="selectedPreviewContent"
                  :read-only="false"
                  height="100%"
                />
              </div>
              <div class="markdown-panel">
                <MarkdownRenderer :content="selectedPreviewContent" />
              </div>
            </div>
            <CodeEditor
              v-else
              v-model:command="selectedPreviewContent"
              :read-only="false"
              height="100%"
            />
          </div>
        </div>
      </a-spin>
    </Modal>
  </Page>
</template>

<style lang="less" scoped>
:deep(.skill-page-content) {
  height: calc(100vh - 120px);
  overflow: hidden;
}

.skill-list-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;

  :deep(.ant-card-body) {
    flex: 1;
    padding: 12px;
    overflow: hidden;
  }

  :deep(.fs-crud-container) {
    height: 100%;
  }
}

.skill-card-grid {
  display: grid;
  flex: 1;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  align-content: flex-start;
  padding: 16px;
  overflow-y: auto;
}

.skill-folder-card {
  display: flex;
  flex-direction: column;
  min-height: 220px;
  padding: 16px;
  background: #fff;
  border: 1px solid #edf0f5;
  border-radius: 8px;
  transition:
    border-color 0.2s,
    box-shadow 0.2s;

  &:hover {
    border-color: #1677ff;
    box-shadow: 0 8px 24px rgb(15 23 42 / 8%);
  }
}

.skill-card-main {
  display: flex;
  gap: 12px;
  cursor: pointer;
}

.skill-card-icon {
  display: flex;
  flex: 0 0 44px;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  font-size: 24px;
  color: #1677ff;
  background: #e8f1ff;
  border-radius: 8px;
}

.skill-card-content {
  min-width: 0;
}

.skill-card-title-row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.skill-card-title {
  max-width: 180px;
  margin: 0;
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-card-path {
  overflow: hidden;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  color: #667085;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-card-desc {
  display: -webkit-box;
  margin: 8px 0 0;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.5;
  color: #4b5563;
  text-overflow: ellipsis;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.skill-card-meta {
  display: flex;
  gap: 8px;
  justify-content: space-between;
  margin-top: 16px;
  font-size: 12px;
  color: #667085;
}

.skill-card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 28px;
  margin-top: 12px;
}

.skill-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  margin-top: auto;
  border-top: 1px solid #f0f0f0;
}

.skill-card-actions {
  display: flex;
  gap: 4px;
}

.skill-empty {
  padding: 80px 0;
  background: #fff;
}

.preview-container {
  display: flex;
  height: 75vh;
  overflow: hidden;
}

.tree-panel {
  width: 30%;
  padding: 8px;
  overflow-y: auto;
  border-right: 1px solid #f0f0f0;

  :deep(.ant-tree-node-content-wrapper) {
    min-width: 0;
  }

  :deep(.ant-tree-title) {
    display: inline-block;
    max-width: 260px;
    overflow: hidden;
    font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
    font-size: 12px;
    text-overflow: ellipsis;
    vertical-align: middle;
    white-space: nowrap;
  }
}

.code-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  padding: 8px;
  overflow: hidden;
}

.preview-toolbar {
  display: flex;
  flex: 0 0 auto;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 0 0 8px;
}

.preview-path {
  min-width: 0;
  overflow: hidden;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  color: #475467;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.markdown-edit-layout {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(320px, 1fr) minmax(320px, 1fr);
  gap: 12px;
  min-height: 0;
  overflow: hidden;
}

.markdown-editor {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-right: 1px solid #f0f0f0;
}

.markdown-panel {
  height: 100%;
  padding: 16px 20px;
  overflow: auto;
  background: #fff;

  :deep(.markdown-renderer) {
    max-width: 960px;
  }
}

@media (max-width: 900px) {
  .preview-container {
    flex-direction: column;
  }

  .tree-panel {
    width: 100%;
    max-height: 180px;
    border-right: 0;
    border-bottom: 1px solid #f0f0f0;
  }

  .markdown-edit-layout {
    grid-template-columns: 1fr;
  }

  .markdown-editor {
    min-height: 260px;
    border-right: 0;
    border-bottom: 1px solid #f0f0f0;
  }
}
</style>
