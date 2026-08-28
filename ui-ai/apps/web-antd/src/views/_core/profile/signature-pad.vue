<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';

import {
  ClearOutlined,
  EditOutlined,
  SaveOutlined,
} from '@ant-design/icons-vue';
import { Button, message } from 'ant-design-vue';

const props = defineProps<{
  /** 当前签名图片URL */
  value?: string;
}>();

const emit = defineEmits<{
  /** 签名保存成功后触发 */
  change: [url: string];
}>();

const canvasRef = ref<HTMLCanvasElement>();
const isDrawing = ref(false);
const hasSignature = ref(false);
const saving = ref(false);

let ctx: CanvasRenderingContext2D | null = null;

/** 初始化画布 */
onMounted(() => {
  const canvas = canvasRef.value;
  if (!canvas) return;

  ctx = canvas.getContext('2d');
  if (!ctx) return;

  // 设置画布大小
  canvas.width = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;

  // 设置画笔样式
  ctx.strokeStyle = '#000';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  // 填充白色背景
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
});

onUnmounted(() => {
  ctx = null;
});

/** 获取坐标 */
function getPosition(e: MouseEvent | TouchEvent) {
  const canvas = canvasRef.value;
  if (!canvas) return { x: 0, y: 0 };

  const rect = canvas.getBoundingClientRect();
  if ('touches' in e) {
    return {
      x: e.touches[0]!.clientX - rect.left,
      y: e.touches[0]!.clientY - rect.top,
    };
  }
  return {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top,
  };
}

/** 开始绘制 */
function startDrawing(e: MouseEvent | TouchEvent) {
  if (!ctx) return;
  isDrawing.value = true;
  hasSignature.value = true;
  const { x, y } = getPosition(e);
  ctx.beginPath();
  ctx.moveTo(x, y);
}

/** 绘制中 */
function draw(e: MouseEvent | TouchEvent) {
  if (!isDrawing.value || !ctx) return;
  e.preventDefault();
  const { x, y } = getPosition(e);
  ctx.lineTo(x, y);
  ctx.stroke();
}

/** 结束绘制 */
function stopDrawing() {
  isDrawing.value = false;
}

/** 清空画布 */
function clearCanvas() {
  const canvas = canvasRef.value;
  if (!canvas || !ctx) return;
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  hasSignature.value = false;
}

/** 保存签名 */
async function saveSignature() {
  const canvas = canvasRef.value;
  if (!canvas || !hasSignature.value) {
    message.warning('请先签名');
    return;
  }

  saving.value = true;
  try {
    // 后端暂未提供签名存储接口，本地转 dataURL 保存并回显，保留完整手写签名交互
    const dataUrl = canvas.toDataURL('image/png');
    emit('change', dataUrl);
    message.success('签名已保存（后端存储开发中，暂存本地）');
  } catch {
    message.error('签名保存失败');
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="signature-pad-wrapper">
    <!-- 当前签名预览 -->
    <div v-if="props.value" class="current-signature">
      <div class="section-label">
        <EditOutlined />
        <span>当前签名</span>
      </div>
      <div class="signature-preview">
        <img :src="props.value" alt="签名" />
      </div>
    </div>

    <!-- 签名画布区域 -->
    <div class="canvas-section">
      <div class="section-label">
        <EditOutlined />
        <span>{{ props.value ? '重新签名' : '手写签名' }}</span>
      </div>
      <div class="signature-container" :class="{ drawing: isDrawing }">
        <canvas
          ref="canvasRef"
          class="signature-canvas"
          @mousedown="startDrawing"
          @mousemove="draw"
          @mouseup="stopDrawing"
          @mouseleave="stopDrawing"
          @touchstart="startDrawing"
          @touchmove="draw"
          @touchend="stopDrawing"
        ></canvas>
        <div v-if="!hasSignature" class="signature-placeholder">
          <EditOutlined class="placeholder-icon" />
          <span>请在此区域内签名</span>
        </div>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="action-bar">
      <Button size="large" @click="clearCanvas">
        <template #icon><ClearOutlined /></template>
        清空画布
      </Button>
      <Button
        type="primary"
        size="large"
        :loading="saving"
        @click="saveSignature"
      >
        <template #icon><SaveOutlined /></template>
        保存签名
      </Button>
    </div>
  </div>
</template>

<style scoped>
.signature-pad-wrapper {
  max-width: 820px;
}

.current-signature {
  margin-bottom: 24px;
}

.section-label {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
  font-size: 15px;
  font-weight: 500;
  color: #374151;
}

.signature-preview {
  display: inline-block;
  padding: 16px 24px;
  background: linear-gradient(135deg, #fafafa 0%, #f5f5f5 100%);
  border: 1px solid #e5e7eb;
  border-radius: 12px;
}

.signature-preview img {
  height: 60px;
  object-fit: contain;
}

.canvas-section {
  margin-bottom: 20px;
}

.signature-container {
  position: relative;
  width: 100%;
  height: 300px;
  overflow: hidden;
  background: linear-gradient(135deg, #fff 0%, #fafafa 100%);
  border: 2px dashed #d1d5db;
  border-radius: 16px;
  transition: all 0.3s ease;
}

.signature-container:hover {
  border-color: #9ca3af;
}

.signature-container.drawing {
  border-color: #1890ff;
  border-style: solid;
  box-shadow: 0 0 0 3px rgb(24 144 255 / 10%);
}

.signature-canvas {
  width: 100%;
  height: 100%;
  touch-action: none;
  cursor: crosshair;
}

.signature-placeholder {
  position: absolute;
  top: 50%;
  left: 50%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  color: #9ca3af;
  pointer-events: none;
  transform: translate(-50%, -50%);
}

.placeholder-icon {
  font-size: 32px;
  opacity: 0.5;
}

.action-bar {
  display: flex;
  gap: 12px;
}

.action-bar :deep(.ant-btn) {
  min-width: 120px;
  border-radius: 8px;
}
</style>
