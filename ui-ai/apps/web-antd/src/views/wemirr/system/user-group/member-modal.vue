<script lang="ts" setup>
/**
 * 用户组成员维护弹窗。
 *
 * 整表提交（PUT /user-groups/{group_id}/members）：选择框里的就是最终成员集合，
 * 取消勾选即移出，与后端「先清后插」语义一一对应，不做逐条增删接口。
 *
 * 必须让操作者知道的一件事：成员变化不即时生效。登录载荷里的 group_ids 是登录时
 * 算好的（与角色/部门同口径），所以刚加进组的人要下次登录才拿到该组已授予的权限。
 * 这句话不写出来，运维就会被「我明明加进去了为什么看不到那个知识库」的工单淹没。
 *
 * 还有一件事不在这一层定：候选名单里看不到某个同事，不是接口坏了也不是分页丢了，
 * 而是后端对非 ADMIN 藏起了系统管理员账号（见 acl_service）；除此之外不做筛人，
 * 部门和数据范围都不参与，跨部门协作的人照常能搜到。
 * 这一层不自己拼 姓名/账号/部门，直接用后端下发的 label，免得两边口径分叉。
 */
import type { UserCandidate, UserGroupMemberItem } from './api';

import { computed, ref } from 'vue';

import { message } from 'ant-design-vue';

import * as api from './api';

const visible = ref(false);
const loading = ref(false);
const saving = ref(false);
const groupId = ref<number>(0);
const groupName = ref('');
/** 同名组现在允许存在（组名只在创建人自己名下唯一），标题要带上人才能分得清 */
const creatorName = ref('');
/** 在册成员（进弹窗时拉一次，作为展示基线） */
const members = ref<UserGroupMemberItem[]>([]);
/** 最终成员集合：选择框绑它，保存提交它 */
const pickedIds = ref<number[]>([]);
/** 搜索到的候选攒下来：否则翻页搜索后，之前选中的 id 会退回显示成裸数字 */
const cache = ref<UserCandidate[]>([]);

/** 拿不到创建人就退回只显示组名，不给标题里多带一对空括号 */
const modalTitle = computed(() =>
  creatorName.value
    ? `成员维护 - ${groupName.value}（${creatorName.value}）`
    : `成员维护 - ${groupName.value}`,
);

const columns = [
  { dataIndex: 'real_name', key: 'real_name', title: '姓名', width: 120 },
  { dataIndex: 'username', key: 'username', title: '账号', width: 140 },
  { dataIndex: 'dept_name', key: 'dept_name', title: '部门', width: 160 },
  { key: 'status', title: '状态', width: 140 },
];

const selectOptions = computed(() => {
  const map = new Map<number, string>();
  members.value.forEach((m) =>
    map.set(m.user_id, `${m.real_name || m.username}（${m.username}）`),
  );
  cache.value.forEach((c) => {
    if (!map.has(c.user_id)) map.set(c.user_id, c.label);
  });
  pickedIds.value.forEach((id) => {
    if (!map.has(id)) map.set(id, `用户(${id})`);
  });
  return [...map].map(([value, label]) => ({ label, value }));
});

/** 将被移出的人：整表提交前给个二次确认级别的可见性 */
const removing = computed(() =>
  members.value.filter((m) => !pickedIds.value.includes(m.user_id)),
);

async function fetchCandidates(keyword?: string) {
  loading.value = true;
  try {
    const list = await api.GetMemberCandidates(keyword);
    // 追加而非覆盖：覆盖会让上面 selectOptions 的兜底分支失效
    cache.value = dedupe([...cache.value, ...(list ?? [])]);
  } finally {
    loading.value = false;
  }
}

function dedupe(list: UserCandidate[]) {
  const seen = new Set<number>();
  return list.filter((item) => {
    if (seen.has(item.user_id)) return false;
    seen.add(item.user_id);
    return true;
  });
}

async function openModal(row: {
  creatorName?: string;
  groupId: number;
  groupName?: string;
}) {
  groupId.value = row.groupId;
  groupName.value = row.groupName ?? '';
  creatorName.value = row.creatorName ?? '';
  cache.value = [];
  visible.value = true;
  loading.value = true;
  try {
    members.value = (await api.GetMembers(row.groupId)) ?? [];
    pickedIds.value = members.value.map((m) => m.user_id);
  } catch {
    members.value = [];
    pickedIds.value = [];
  } finally {
    loading.value = false;
  }
  fetchCandidates();
}

async function handleSave() {
  saving.value = true;
  try {
    const ret = await api.AssignMembers(groupId.value, pickedIds.value);
    message.success(
      `已保存 ${ret?.member_count ?? pickedIds.value.length} 名成员，` +
        '成员变更在对方下次登录后生效',
    );
    visible.value = false;
  } finally {
    saving.value = false;
  }
}

defineExpose({ open: openModal });
</script>

<template>
  <a-modal
    v-model:open="visible"
    :confirm-loading="saving"
    :title="modalTitle"
    width="760px"
    @ok="handleSave"
  >
    <div class="pick-label">
      选择成员（默认列出 100 人，可按姓名/账号/部门模糊搜索；空集合即清空该组）
    </div>

    <a-select
      v-model:value="pickedIds"
      :filter-option="false"
      :loading="loading"
      :options="selectOptions"
      mode="multiple"
      placeholder="输入姓名、账号或部门搜索用户"
      style="width: 100%"
      @search="(kw: string) => fetchCandidates(kw)"
    />

    <a-alert
      v-if="removing.length > 0"
      :message="`本次将移出 ${removing.length} 人：${removing.map((m) => m.real_name || m.username).join('、')}`"
      show-icon
      style="margin-top: 12px"
      type="warning"
    />

    <a-table
      :columns="columns"
      :data-source="members"
      :loading="loading"
      :pagination="false"
      row-key="user_id"
      size="small"
      style="margin-top: 12px"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'real_name'">
          {{ record.real_name || '-' }}
        </template>
        <template v-else-if="column.key === 'status'">
          <a-space :size="4">
            <a-tag v-if="record.status !== 1" color="error">停用</a-tag>
            <a-tag v-if="!pickedIds.includes(record.user_id)" color="orange">
              将移除
            </a-tag>
            <a-tag
              v-if="record.status === 1 && pickedIds.includes(record.user_id)"
              color="success"
            >
              在册
            </a-tag>
          </a-space>
        </template>
      </template>
      <template #emptyText>
        <a-empty description="该组还没有成员" />
      </template>
    </a-table>
  </a-modal>
</template>

<style scoped>
.pick-label {
  margin-bottom: 6px;
  font-size: 12px;
  color: #8c8c8c;
}
</style>
