<script setup lang="ts">
import type { FormInstance, Rule } from 'ant-design-vue/es/form';

import { computed, reactive, ref } from 'vue';

import { useUserStore } from '@vben/stores';

import {
  CheckCircleFilled,
  ExclamationCircleFilled,
  KeyOutlined,
  LockOutlined,
  MailOutlined,
  MobileOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons-vue';
import { Button, Form, Input, message, Modal } from 'ant-design-vue';

import { defHttp } from '#/api/request';
import { useAuthStore } from '#/store/auth';

const userStore = useUserStore();
const authStore = useAuthStore();
const formRef = ref<FormInstance>();

/** 手机号脱敏 */
function maskPhone(phone?: string) {
  if (!phone || phone.length < 7) return '未绑定';
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`;
}

/** 邮箱脱敏 */
function maskEmail(email?: string) {
  if (!email || !email.includes('@')) return '未绑定';
  const [name, domain] = email.split('@');
  const masked = name!.length > 2 ? `${name!.slice(0, 2)}***` : `${name}***`;
  return `${masked}@${domain}`;
}

/** 弹窗状态 */
const modalVisible = ref(false);
const modalType = ref<'email' | 'mfa' | 'password' | 'phone'>('password');
const modalLoading = ref(false);

/** 表单数据 */
const formState = reactive({
  oldPassword: '',
  newPassword: '',
  confirmPassword: '',
  phone: '',
  phoneCode: '',
  email: '',
  emailCode: '',
});

/** 密码表单校验规则 */
const passwordRules: Record<string, Rule[]> = {
  oldPassword: [{ required: true, message: '请输入当前密码' }],
  newPassword: [
    { required: true, message: '请输入新密码' },
    { min: 6, message: '密码长度不能少于6位' },
  ],
  confirmPassword: [
    { required: true, message: '请确认新密码' },
    {
      validator: async (_rule, value) => {
        if (value && value !== formState.newPassword) {
          throw new Error('两次密码输入不一致');
        }
      },
    },
  ],
};

/** 手机表单校验规则 */
const phoneRules: Record<string, Rule[]> = {
  phone: [
    { required: true, message: '请输入手机号' },
    { pattern: /^1\d{10}$/, message: '手机号格式不正确' },
  ],
  phoneCode: [{ required: true, message: '请输入验证码' }],
};

/** 邮箱表单校验规则 */
const emailRules: Record<string, Rule[]> = {
  email: [
    { required: true, message: '请输入邮箱' },
    { type: 'email', message: '邮箱格式不正确' },
  ],
  emailCode: [{ required: true, message: '请输入验证码' }],
};

/** 验证码倒计时 */
const countdown = ref(0);
let timer: null | ReturnType<typeof setInterval> = null;

/** 图标映射 */
const iconMap = {
  password: LockOutlined,
  phone: MobileOutlined,
  email: MailOutlined,
  mfa: SafetyCertificateOutlined,
};

/** 安全项列表 */
const securityItems = computed(() => {
  const info = userStore.userInfo;
  return [
    {
      key: 'password' as const,
      title: '账户密码',
      description: '定期修改密码可以提高账户安全性',
      action: '修改',
      status: true,
      color: '#1890ff',
    },
    {
      key: 'phone' as const,
      title: '密保手机',
      description: info?.mobile
        ? `已绑定：${maskPhone(info.mobile)}`
        : '未绑定，绑定后可用于找回密码',
      action: info?.mobile ? '更换' : '绑定',
      status: !!info?.mobile,
      color: '#52c41a',
    },
    {
      key: 'email' as const,
      title: '备用邮箱',
      description: info?.email
        ? `已绑定：${maskEmail(info.email)}`
        : '未绑定，绑定后可接收安全通知',
      action: info?.email ? '更换' : '绑定',
      status: !!info?.email,
      color: '#722ed1',
    },
    {
      key: 'mfa' as const,
      title: 'MFA 认证',
      description: '开启后登录需要二次验证，更加安全',
      action: '设置',
      status: false,
      color: '#fa8c16',
    },
  ];
});

/** 弹窗标题 */
const modalTitle = computed(() => {
  const titles = {
    password: '修改密码',
    phone: userStore.userInfo?.mobile ? '更换手机号' : '绑定手机号',
    email: userStore.userInfo?.email ? '更换邮箱' : '绑定邮箱',
    mfa: 'MFA 设置',
  };
  return titles[modalType.value];
});

/** 打开弹窗 */
function openModal(type: 'email' | 'mfa' | 'password' | 'phone') {
  modalType.value = type;
  modalVisible.value = true;
  // 重置表单
  Object.assign(formState, {
    oldPassword: '',
    newPassword: '',
    confirmPassword: '',
    phone: '',
    phoneCode: '',
    email: '',
    emailCode: '',
  });
}

/** 发送验证码 */
async function sendCode(type: 'email' | 'phone') {
  // 先校验目标字段
  const field = type === 'phone' ? 'phone' : 'email';
  try {
    await formRef.value?.validateFields([field]);
  } catch {
    return;
  }

  const target = type === 'phone' ? formState.phone : formState.email;
  try {
    // 后端暂未提供验证码接口，保留交互占位
    message.info('验证码功能开发中，敬请期待');
    countdown.value = 60;
    timer = setInterval(() => {
      countdown.value--;
      if (countdown.value <= 0 && timer) {
        clearInterval(timer);
        timer = null;
      }
    }, 1000);
  } catch {
    message.error('发送失败');
  }
}

/** 提交修改 */
async function handleSubmit() {
  try {
    await formRef.value?.validateFields();
  } catch {
    return;
  }

  modalLoading.value = true;
  try {
    switch (modalType.value) {
      case 'email': {
        // 后端暂未提供绑定邮箱接口
        message.info('绑定邮箱功能开发中，敬请期待');
        break;
      }

      case 'mfa': {
        message.info('MFA 功能开发中');
        break;
      }

      case 'password': {
        // 修改本人密码：PUT /api/system/users/{user_id}/own-password（service_system）
        const userId = userStore.userInfo?.userId;
        if (!userId) {
          throw new Error('用户信息缺失');
        }
        await defHttp.put(`/api/system/users/${userId}/own-password`, {
          old_password: formState.oldPassword,
          new_password: formState.newPassword,
        });
        message.success('密码修改成功');
        break;
      }

      case 'phone': {
        // 后端暂未提供绑定手机接口
        message.info('绑定手机功能开发中，敬请期待');
        break;
      }
    }

    modalVisible.value = false;
    await authStore.fetchUserInfo();
  } catch {
    message.error('操作失败');
  } finally {
    modalLoading.value = false;
  }
}
</script>

<template>
  <div class="security-setting">
    <!-- 安全项卡片列表 -->
    <div class="security-grid">
      <div
        v-for="item in securityItems"
        :key="item.key"
        class="security-card"
        :class="{ 'is-bound': item.status }"
      >
        <!-- 图标区域 -->
        <div class="card-icon" :style="{ background: `${item.color}15` }">
          <component
            :is="iconMap[item.key]"
            :style="{ color: item.color, fontSize: '24px' }"
          />
        </div>

        <!-- 内容区域 -->
        <div class="card-content">
          <div class="card-header">
            <span class="card-title">{{ item.title }}</span>
            <span
              class="card-status"
              :class="item.status ? 'bound' : 'unbound'"
            >
              <CheckCircleFilled v-if="item.status" />
              <ExclamationCircleFilled v-else />
              {{ item.status ? '已设置' : '未设置' }}
            </span>
          </div>
          <p class="card-desc">{{ item.description }}</p>
        </div>

        <!-- 操作按钮 -->
        <div class="card-action">
          <Button
            :type="item.status ? 'default' : 'primary'"
            size="small"
            @click="openModal(item.key)"
          >
            {{ item.action }}
          </Button>
        </div>
      </div>
    </div>

    <!-- 修改弹窗 -->
    <Modal
      v-model:open="modalVisible"
      :title="modalTitle"
      :confirm-loading="modalLoading"
      :width="520"
      centered
      destroy-on-close
      class="security-modal"
      @ok="handleSubmit"
    >
      <!-- 修改密码 -->
      <Form
        v-if="modalType === 'password'"
        ref="formRef"
        :model="formState"
        :rules="passwordRules"
        layout="vertical"
        class="security-form"
      >
        <Form.Item label="当前密码" name="oldPassword">
          <Input.Password
            v-model:value="formState.oldPassword"
            placeholder="请输入当前密码"
            size="large"
          />
        </Form.Item>
        <Form.Item label="新密码" name="newPassword">
          <Input.Password
            v-model:value="formState.newPassword"
            placeholder="请输入新密码（至少6位）"
            size="large"
          />
        </Form.Item>
        <Form.Item label="确认密码" name="confirmPassword">
          <Input.Password
            v-model:value="formState.confirmPassword"
            placeholder="请再次输入新密码"
            size="large"
          />
        </Form.Item>
      </Form>

      <!-- 绑定手机 -->
      <Form
        v-else-if="modalType === 'phone'"
        ref="formRef"
        :model="formState"
        :rules="phoneRules"
        layout="vertical"
        class="security-form"
      >
        <Form.Item label="手机号" name="phone">
          <Input
            v-model:value="formState.phone"
            placeholder="请输入手机号"
            size="large"
            :maxlength="11"
          >
            <template #prefix>
              <MobileOutlined style="color: #bfbfbf" />
            </template>
          </Input>
        </Form.Item>
        <Form.Item label="验证码" name="phoneCode">
          <div class="code-input-group">
            <Input
              v-model:value="formState.phoneCode"
              placeholder="请输入验证码"
              size="large"
              :maxlength="6"
            >
              <template #prefix>
                <KeyOutlined style="color: #bfbfbf" />
              </template>
            </Input>
            <Button
              :type="countdown > 0 ? 'default' : 'primary'"
              size="large"
              :disabled="countdown > 0"
              @click="sendCode('phone')"
            >
              {{ countdown > 0 ? `${countdown}s 后重试` : '获取验证码' }}
            </Button>
          </div>
        </Form.Item>
      </Form>

      <!-- 绑定邮箱 -->
      <Form
        v-else-if="modalType === 'email'"
        ref="formRef"
        :model="formState"
        :rules="emailRules"
        layout="vertical"
        class="security-form"
      >
        <Form.Item label="邮箱地址" name="email">
          <Input
            v-model:value="formState.email"
            placeholder="请输入邮箱地址"
            size="large"
          >
            <template #prefix>
              <MailOutlined style="color: #bfbfbf" />
            </template>
          </Input>
        </Form.Item>
        <Form.Item label="验证码" name="emailCode">
          <div class="code-input-group">
            <Input
              v-model:value="formState.emailCode"
              placeholder="请输入验证码"
              size="large"
              :maxlength="6"
            >
              <template #prefix>
                <KeyOutlined style="color: #bfbfbf" />
              </template>
            </Input>
            <Button
              :type="countdown > 0 ? 'default' : 'primary'"
              size="large"
              :disabled="countdown > 0"
              @click="sendCode('email')"
            >
              {{ countdown > 0 ? `${countdown}s 后重试` : '获取验证码' }}
            </Button>
          </div>
        </Form.Item>
      </Form>

      <!-- MFA 设置 -->
      <div v-else-if="modalType === 'mfa'" class="mfa-placeholder">
        <SafetyCertificateOutlined class="mfa-icon" />
        <p class="mfa-text">MFA 多因素认证功能开发中</p>
        <p class="mfa-desc">开启后，登录时需要额外的身份验证步骤</p>
      </div>
    </Modal>
  </div>
</template>

<style scoped>
.security-setting {
  padding: 8px 0;
}

.security-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.security-card {
  display: flex;
  gap: 16px;
  align-items: center;
  padding: 20px;
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 12px;
  transition: all 0.3s ease;
}

.security-card:hover {
  border-color: #d9d9d9;
  box-shadow: 0 4px 12px rgb(0 0 0 / 8%);
  transform: translateY(-2px);
}

.security-card.is-bound {
  background: linear-gradient(135deg, #fff 0%, #f6ffed 100%);
  border-color: #f6ffed;
}

.card-icon {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 12px;
}

.card-content {
  flex: 1;
  min-width: 0;
}

.card-header {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
}

.card-status {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 10px;
}

.card-status.bound {
  color: #52c41a;
  background: #f6ffed;
}

.card-status.unbound {
  color: #faad14;
  background: #fffbe6;
}

.card-desc {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  color: #6b7280;
  white-space: nowrap;
}

.card-action {
  flex-shrink: 0;
}

.security-form {
  padding: 8px 0;
}

.code-input-group {
  display: flex;
  gap: 12px;
}

.code-input-group :deep(.ant-input-affix-wrapper) {
  flex: 1;
}

.code-input-group :deep(.ant-btn) {
  width: 130px;
}

.mfa-placeholder {
  padding: 40px 20px;
  text-align: center;
}

.mfa-icon {
  font-size: 48px;
  color: #fa8c16;
}

.mfa-text {
  margin: 16px 0 8px;
  font-size: 16px;
  font-weight: 500;
  color: #1f2937;
}

.mfa-desc {
  margin: 0;
  font-size: 13px;
  color: #9ca3af;
}
</style>
