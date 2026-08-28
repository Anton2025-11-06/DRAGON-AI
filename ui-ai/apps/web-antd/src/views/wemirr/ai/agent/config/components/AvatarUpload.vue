<script lang="ts" setup>
/**
 * 头像上传组件
 * 支持图片上传、预览、加载状态
 */
import { ref, watch } from 'vue';

import { CameraOutlined, LoadingOutlined } from '@ant-design/icons-vue';
import { message, Upload } from 'ant-design-vue';

import * as api from '../api';

interface Props {
  modelValue?: string;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
}>();

const uploading = ref(false);
const imageUrl = ref(props.modelValue);

// 监听外部值变化
watch(
  () => props.modelValue,
  (val) => {
    imageUrl.value = val;
  },
);

/** 上传前校验 */
const beforeUpload = (file: File) => {
  const isImage = file.type.startsWith('image/');
  if (!isImage) {
    message.error('请上传图片文件');
    return false;
  }

  const isLt2M = file.size / 1024 / 1024 < 2;
  if (!isLt2M) {
    message.error('图片大小不能超过 2MB');
    return false;
  }

  return true;
};

/** 自定义上传 */
const customRequest = async (options: any) => {
  const { file, onSuccess, onError } = options;

  uploading.value = true;

  try {
    const result = await api.uploadAvatar(file);
    imageUrl.value = result;
    emit('update:modelValue', result);
    onSuccess(result);
    message.success('上传成功');
  } catch (error) {
    onError(error);
    message.error('上传失败');
  } finally {
    uploading.value = false;
  }
};
</script>

<template>
  <div class="avatar-upload">
    <Upload
      :custom-request="customRequest"
      :before-upload="beforeUpload"
      :show-upload-list="false"
      accept="image/*"
    >
      <div class="upload-trigger" :class="{ 'has-image': imageUrl }">
        <!-- 已上传图片 -->
        <img v-if="imageUrl" :src="imageUrl" alt="头像" class="avatar-image" />

        <!-- 上传占位 -->
        <div v-else class="upload-placeholder">
          <CameraOutlined class="upload-icon" />
          <span class="upload-text">上传头像</span>
        </div>

        <!-- 悬浮遮罩 -->
        <div class="upload-overlay">
          <CameraOutlined />
          <span>{{ imageUrl ? '更换' : '上传' }}</span>
        </div>

        <!-- 加载状态 -->
        <div v-if="uploading" class="upload-loading">
          <LoadingOutlined spin />
        </div>
      </div>
    </Upload>
    <div class="upload-tip">支持 JPG、PNG，不超过 2MB</div>
  </div>
</template>

<style lang="less" scoped>
.avatar-upload {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}

.upload-trigger {
  position: relative;
  width: 96px;
  height: 96px;
  overflow: hidden;
  cursor: pointer;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4e8eb 100%);
  border: 2px dashed #d9d9d9;
  border-radius: 12px;
  transition: all 0.3s ease;

  &:hover {
    border-color: #722ed1;

    .upload-overlay {
      opacity: 1;
    }
  }

  &.has-image {
    border-color: transparent;
    border-style: solid;

    &:hover {
      border-color: #722ed1;
    }
  }
}

.avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.upload-placeholder {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;

  .upload-icon {
    font-size: 28px;
    color: #bbb;
  }

  .upload-text {
    font-size: 12px;
  }
}

.upload-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #fff;
  background: rgb(0 0 0 / 50%);
  opacity: 0;
  transition: opacity 0.3s ease;

  .anticon {
    font-size: 20px;
  }
}

.upload-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: #722ed1;
  background: rgb(255 255 255 / 85%);
}

.upload-tip {
  font-size: 12px;
  color: #999;
}
</style>
