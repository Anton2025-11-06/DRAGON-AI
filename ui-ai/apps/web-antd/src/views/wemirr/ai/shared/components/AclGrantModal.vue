<script lang="ts" setup>
/**
 * 资源实例授权弹窗（ACL）。
 *
 * 挂在各资源列表页上（知识库/文档/工作流/模板/工具/技能/MCP），props 契约由调用方给定：
 *   <AclGrantModal v-if="grantItem" v-model:open="grantModalVisible" :key="grantItem.id"
 *     resource-code="tool" :resource-id="grantItem.id" :resource-name="grantItem.name" />
 * :key 绑资源 id，切换条目时整组件重建，避免上一行的授权行残留成「看起来还在」。
 *
 * 三条设计约束（都是踩过坑的）：
 * 1. 在册授权由弹窗自己拉（GET /acl/grants），调用方只给 id：列表接口下发的是按钮位
 *    求值结果，没有别人拿到过什么，让页面多查一次反而要把敏感数据摊到列表上。
 * 2. 保存走整表提交（PUT）：弹窗里看到的就是最终全集，取消勾选即撤销，
 *    语义与后端 AclService.save_grants 的「差集即撤销」一致，不做逐条增删（中途失败会留半态）。
 * 3. 动作清单/主体类型都从 /acl/resources 取，前端不硬编码动作码：
 *    后端加一个动作，这里自动多一个勾选项，不会出现「后端能授、页面上没得选」。
 * 4. 动作项按后端下发的 groups 分组渲染（每个资源的分类写在自己的 spec 里），每项一个图标
 *    + 后端下发的名称 + 一个？号气泡：组名与组说明跟着后端走，前端只负图标与排版。
 *    后端没下发 groups 时退成本地兜底的两组（页面操作 / 可见与引用），不丢项。
 * 5. 不适用的动作码（如图片/音视频知识库的切片管理、构建图谱）置灰不给勾但不隐藏：
 *    隐藏会在整表提交时被当成「取消勾选」默默撤掉已有的授权。调用方按资源形态传进来。
 */
import type {
  AclGrantItem,
  AclGrantPayload,
  AclGranteeOption,
  AclResourceMeta,
} from '#/api/acl';

import { computed, ref, watch } from 'vue';

import { useUserStore } from '@vben/stores';

import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  GetGranteeOptions,
  GetResourceMeta,
  GRANTEE_TYPE,
  ListGrants,
  SaveGrants,
} from '#/api/acl';

import {
  aclActionIcon,
  aclActionTip,
  resolveAclGroups,
} from '../acl-action-meta';

interface Props {
  open: boolean;
  resourceCode: string;
  resourceId: number | string;
  resourceName?: string;
  /**
   * 这一行资源形态上用不上的动作码（图片/音视频库的文件族动作），由调用方算好传入。
   * 只置灰不隐藏：隐藏掉的项不在渲染里，整表提交会把别人已有的同名授权默默撤掉。
   */
  disabledActions?: string[];
  /** 置灰项悬停上去的原因（不说清就没人知道为什么不给勾） */
  disabledReason?: string;
}

/** 弹窗内的一行待提交授权 */
interface GrantRow {
  actions: string[];
  deptIncludeSub: number;
  fetching: boolean;
  granteeId: number | undefined;
  granteeType: number;
  key: number;
  options: AclGranteeOption[];
  /** 过期时间：与后端约定的 naive 字符串（YYYY-MM-DDTHH:mm:ss），空 = 永久 */
  expireTime: null | string;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
}>();

const userStore = useUserStore();

const loading = ref(false);
const saving = ref(false);
/** 拉不到在册授权的原因（多为「没有这一条的授权权」）：非空即进只读提示态 */
const denied = ref('');
const meta = ref<AclResourceMeta | null>(null);
const ownerId = ref(0);
const resourceTitle = ref('');
const rows = ref<GrantRow[]>([]);
let rowSeq = 0;

/** 全员主体不需要选人（后端固定 grantee_id=0），其余类型都要选出一个具体主体 */
const needGrantee = (type: number) => type !== GRANTEE_TYPE.ALL;

const canDelegate = computed(
  () =>
    (userStore.userInfo?.roles ?? []).includes('ADMIN') ||
    Number(userStore.userInfo?.userId ?? 0) === ownerId.value,
);

/** 动作勾选项直接用后端元信息（code 作值、name 作标签），不另拼一层 label/value */
const actionOptions = computed(() => meta.value?.actions ?? []);

/**
 * 分组直接拿后端下发的 groups（没下发则本地兜底两组）。
 * 空组不渲染表头，免得上出现一行组名后面什么都没有。
 */
const actionGroups = computed(() => resolveAclGroups(meta.value));

const granteeTypeOptions = computed(() =>
  (meta.value?.grantee_types ?? []).map((t) => ({ label: t.name, value: t.code })),
);

/** 同部门自动能做的动作：提示出来，免得有人对着部门再授一遍 view */
const scopeHint = computed(() =>
  (meta.value?.scope_actions ?? []).map((a) => a.name).join('、'),
);

const columns = [
  {
    dataIndex: 'granteeType',
    key: 'granteeType',
    title: '主体类型',
    width: 100,
  },
  { dataIndex: 'granteeId', key: 'granteeId', title: '授权主体', width: 200 },
  // 操作范围：剩下的宽度全部给它（图标 + 名称 + ？号比纯文字宽，不够会折行）
  { dataIndex: 'actions', key: 'actions', title: '操作范围' },
  { dataIndex: 'expireTime', key: 'expireTime', title: '过期时间', width: 170 },
  { key: 'opt', title: '操作', width: 56 },
];

function newRow(seed?: Partial<GrantRow>): GrantRow {
  rowSeq += 1;
  return {
    actions: [],
    deptIncludeSub: 0,
    fetching: false,
    granteeId: undefined,
    granteeType: GRANTEE_TYPE.USER,
    key: rowSeq,
    options: [],
    expireTime: null,
    ...seed,
  };
}

async function loadOptions(row: GrantRow, keyword?: string) {
  if (!needGrantee(row.granteeType)) return;
  row.fetching = true;
  try {
    const list = await GetGranteeOptions({
      grantee_type: row.granteeType,
      keyword: keyword || undefined,
      limit: 30,
    });
    row.options = list ?? [];
  } catch {
    // 错误提示已由请求层统一弹出，这里只保证不把 undefined 塞进选择器
    row.options = [];
  } finally {
    row.fetching = false;
  }
}

/** 换主体类型：原来选中的 id 属于上一类主体，留着会授给一个不相干的同号主体 */
function onTypeChange(row: GrantRow) {
  row.granteeId = undefined;
  row.deptIncludeSub = 0;
  row.options = [];
  loadOptions(row);
}

/**
 * 只保留后端当前清单里还认的动作码。
 * 在册行里出现退役码（比如旧版给文档授过 export）时，整表提交会被 check_actions 判成
 * 非法动作，结果是这条资源的授权再也保存不了；不显示则差集会把它自动撤销。
 * 元信息没拉到（size=0）时原样透传：宁可交给后端报错，不能把别人的授权默默抹掉。
 */
function keepKnownCodes(actions: string[]): string[] {
  const known = new Set(actionOptions.value.map((a) => a.code));
  return known.size === 0 ? actions : actions.filter((code) => known.has(code));
}

function toRows(items: AclGrantItem[]): GrantRow[] {
  const out: GrantRow[] = [];
  for (const item of items) {
    const actions = keepKnownCodes(item.actions.map((a) => a.action));
    // 整条授权只剩退役码就不回显：留一行空勾选框会让人以为还没授过，也提交不了
    if (actions.length === 0) continue;
    out.push(
      newRow({
        actions,
        deptIncludeSub: item.dept_include_sub,
        expireTime: item.expire_time ? normalizeExpire(item.expire_time) : null,
        granteeId: item.grantee_id,
        granteeType: item.grantee_type,
        options: needGrantee(item.grantee_type)
          ? [
              {
                grantee_id: item.grantee_id,
                grantee_name: item.grantee_name,
                label: item.grantee_name || `${item.grantee_id}`,
              },
            ]
          : [],
      }),
    );
  }
  return out;
}

/** 后端可能回 "2026-10-01 12:00:00" 或带 T 的 ISO，统一成 a-date-picker 认的格式 */
function normalizeExpire(value: string): null | string {
  const d = dayjs(value);
  return d.isValid() ? d.format('YYYY-MM-DDTHH:mm:ss') : null;
}

async function loadAll() {
  loading.value = true;
  denied.value = '';
  rows.value = [];
  try {
    const [metaList, grants] = await Promise.all([
      GetResourceMeta(props.resourceCode),
      ListGrants(props.resourceCode, props.resourceId),
    ]);
    meta.value =
      (metaList ?? []).find((m) => m.resource_code === props.resourceCode) ??
      (metaList ?? [])[0] ??
      null;
    ownerId.value = grants?.resource?.owner_id ?? 0;
    resourceTitle.value = grants?.resource?.title || props.resourceName || '';
    rows.value = toRows(grants?.items ?? []);
    if (rows.value.length === 0) {
      rows.value = [newRow()];
    }
  } catch (error: any) {
    // 403/400 都进提示态：没有 share 的人本来就不该看到别人拿到过什么，
    // 把表单渲染成可编辑只会让人以为改成功了
    denied.value =
      error?.response?.data?.message ||
      error?.message ||
      '获取授权信息失败，请确认你对该资源有「授权」权限';
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  (visible) => {
    if (visible) loadAll();
  },
  { immediate: true },
);

/** 整表提交的载荷：完全没填的行丢掉，填了一半的要拦下来（后端会按缺字段报错，文案不友好） */
function buildPayload(): AclGrantPayload[] | null {
  const grants: AclGrantPayload[] = [];
  for (const row of rows.value) {
    const empty = !row.granteeId && row.actions.length === 0;
    if (empty) continue;
    if (needGrantee(row.granteeType) && !row.granteeId) {
      message.warning('请选择授权主体');
      return null;
    }
    if (row.actions.length === 0) {
      message.warning('请为每个授权主体至少勾选一个动作');
      return null;
    }
    if (row.expireTime && !dayjs(row.expireTime).isAfter(dayjs())) {
      message.warning('过期时间必须晚于当前时间');
      return null;
    }
    grants.push({
      actions: row.actions,
      dept_include_sub:
        row.granteeType === GRANTEE_TYPE.DEPT ? row.deptIncludeSub : 0,
      expire_time: row.expireTime || null,
      grantee_id: needGrantee(row.granteeType) ? (row.granteeId as number) : 0,
      grantee_type: row.granteeType,
    });
  }
  return grants;
}

async function handleSave() {
  const grants = buildPayload();
  if (grants === null) return;
  saving.value = true;
  try {
    const ret = await SaveGrants({
      grants,
      resource_code: props.resourceCode,
      resource_id: props.resourceId,
    });
    const skipped = ret?.skipped ?? [];
    if (skipped.length > 0) {
      message.warning(
        `已跳过 ${skipped.length} 条：${skipped[0]?.reason ?? ''}（同主体的重复项已自动合并）`,
      );
    }
    message.success(
      `授权已更新：生效 ${ret?.saved ?? 0} 条，撤销 ${ret?.revoked ?? 0} 条`,
    );
    emit('update:open', false);
  } finally {
    saving.value = false;
  }
}

/** 「授权」动作只有归属人和 ADMIN 能转授（后端同规则），提前置灰，省一次 403 */
function shareDisabled(code: string) {
  return code === 'share' && !canDelegate.value;
}

/** 这一行资源形态用不上（如图片库的切片管理）：置灰，原因进悬停 */
function inapplicable(code: string) {
  return (props.disabledActions ?? []).includes(code);
}

function actionDisabled(code: string) {
  return shareDisabled(code) || inapplicable(code);
}

/** ？号气泡的文案：置灰项先说为什么不适用，其余说「授出去之后对方能做什么」 */
function actionTip(code: string) {
  if (inapplicable(code)) {
    return props.disabledReason || '这种资源形态用不上这个动作';
  }
  return aclActionTip(code);
}

/**
 * 单个动作勾选：不再用 a-checkbox-group（它一次 change 只会回传自己组内的选中项，
 * 拆成两组后两组绑同一个数组会互相覆盖），改成逐项受控写入。
 */
function toggleAction(row: GrantRow, code: string, checked: boolean) {
  const next = new Set(row.actions);
  if (checked) next.add(code);
  else next.delete(code);
  row.actions = [...next];
}
</script>

<template>
  <a-modal
    cancel-text="关闭"
    :confirm-loading="saving"
    :ok-button-props="{ disabled: !!denied || loading }"
    :open="open"
    :title="`授权 - ${resourceName || resourceTitle || resourceCode}`"
    width="1080px"
    @cancel="emit('update:open', false)"
    @ok="handleSave"
  >
    <a-alert
      v-if="denied"
      :message="denied"
      show-icon
      style="margin-bottom: 12px"
      type="warning"
    />

    <a-space :size="12" style="margin-bottom: 12px" wrap>
      <a-button
        :disabled="!!denied"
        type="primary"
        @click="rows.push(newRow())"
      >
        添加主体
      </a-button>
      <span v-if="scopeHint" class="hint">
        同部门成员自动可：{{ scopeHint }}（无需重复授权）
      </span>
      <span v-else-if="!canDelegate" class="hint">
        「授权」动作只有归属人或管理员能转授
      </span>
    </a-space>

    <a-table
      :columns="columns"
      :data-source="rows"
      :loading="loading"
      :pagination="false"
      :scroll="{ y: 360 }"
      row-key="key"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'granteeType'">
          <a-select
            :options="granteeTypeOptions"
            :value="record.granteeType"
            style="width: 100%"
            @change="(val: any) => { record.granteeType = val; onTypeChange(record); }"
          />
        </template>

        <template v-else-if="column.key === 'granteeId'">
          <span v-if="!needGrantee(record.granteeType)" class="hint">所有登录用户</span>
          <a-select
            v-else
            :disabled="!!denied"
            :filter-option="false"
            :loading="record.fetching"
            :options="
              record.options.map((o: any) => ({ label: o.label, value: o.grantee_id }))
            "
            show-search
            style="width: 100%"
            v-model:value="record.granteeId"
            @search="(kw: string) => loadOptions(record, kw)"
          />
          <a-checkbox
            v-if="record.granteeType === GRANTEE_TYPE.DEPT"
            :checked="record.deptIncludeSub === 1"
            :disabled="!!denied"
            style="margin-top: 4px"
            @change="
              (e: any) => {
                record.deptIncludeSub = e.target.checked ? 1 : 0;
              }
            "
          >
            含下级部门
          </a-checkbox>
        </template>

        <template v-else-if="column.key === 'actions'">
          <div class="action-groups">
            <div
              v-for="group in actionGroups"
              :key="group.key"
              class="action-group"
            >
              <a-tooltip :title="group.hint">
                <span class="action-group-label">{{ group.label }}</span>
              </a-tooltip>
              <div class="action-items">
                <div
                  v-for="opt in group.options"
                  :key="opt.code"
                  class="action-opt"
                  :class="{ muted: inapplicable(opt.code) }"
                >
                  <a-checkbox
                    :checked="record.actions.includes(opt.code)"
                    :disabled="!!denied || actionDisabled(opt.code)"
                    @change="
                      (e: any) =>
                        toggleAction(record, opt.code, e.target.checked)
                    "
                  >
                    <span class="action-label">
                      <component
                        :is="aclActionIcon(opt.code)"
                        class="action-icon"
                      />
                      {{ opt.name }}
                    </span>
                  </a-checkbox>
                  <a-tooltip :title="actionTip(opt.code)">
                    <QuestionCircleOutlined class="action-help" />
                  </a-tooltip>
                </div>
              </div>
            </div>
            <span v-if="actionOptions.length === 0" class="hint">
              没有可授权的动作
            </span>
          </div>
        </template>

        <template v-else-if="column.key === 'expireTime'">
          <a-date-picker
            v-model:value="record.expireTime"
            :disabled="!!denied"
            format="YYYY-MM-DD HH:mm:ss"
            placeholder="留空=长期"
            show-time
            style="width: 100%"
            value-format="YYYY-MM-DDTHH:mm:ss"
          />
        </template>

        <template v-else-if="column.key === 'opt'">
          <a @click="rows = rows.filter((r) => r.key !== record.key)">移除</a>
        </template>
      </template>

      <template #emptyText>
        <a-empty description="暂无授权，点「添加主体」" />
      </template>
    </a-table>
  </a-modal>
</template>

<style scoped>
.hint {
  font-size: 12px;
  color: #8c8c8c;
}

.action-groups {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.action-group-label {
  display: inline-flex;
  align-items: center;
  padding: 0 6px;
  font-size: 12px;
  color: #595959;
  cursor: help;
  background: #fafafa;
  border-left: 2px solid #d9d9d9;
  border-radius: 2px;
}

.action-items {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 14px;
  margin-top: 2px;
}

.action-opt {
  display: inline-flex;
  gap: 2px;
  align-items: center;
}

/* 不适用的项只压颜度：勾与不勾仍看得见（已授的不能规掉），但一眼知道这一项现在不能动 */
.action-opt.muted {
  opacity: 55%;
}

.action-label {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.action-icon {
  font-size: 13px;
  color: #595959;
}

.action-help {
  font-size: 12px;
  color: #bfbfbf;
  cursor: help;
}

.action-help:hover {
  color: #1677ff;
}
</style>
