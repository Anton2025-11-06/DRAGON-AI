<script setup lang="ts">
/**
 * 文档提取器节点配置表单
 * 从文档中提取文本 (PDF、Word、Excel、PPT 等)
 */
import type {
  DocExtractorConfig,
  DocumentType,
  OcrConfig,
  OcrEngine,
  PaginationConfig,
} from '#/api/ai-workflow/types';

import { reactive, ref, watch } from 'vue';

import {
  FileExcelOutlined,
  FileMarkdownOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileWordOutlined,
  Html5Outlined,
} from '@ant-design/icons-vue';

import { VariableInput } from '../variable-selector';

// Props
interface Props {
  config: DocExtractorConfig;
  nodeId: string;
}

const props = defineProps<Props>();

// Emits
const emit = defineEmits<{
  (e: 'update:config', config: DocExtractorConfig): void;
}>();

// 文件大小（MB）
const maxFileSizeMB = ref<number | undefined>(10);

// 默认 OCR 配置
const defaultOcrConfig: OcrConfig = {
  enabled: false,
  language: 'chi_sim+eng',
  engine: 'PADDLE_OCR' as OcrEngine,
  preprocessImage: true,
  dpi: 300,
};

// 默认分页配置
const defaultPaginationConfig: PaginationConfig = {
  splitByPage: false,
  startPage: undefined,
  endPage: undefined,
  pageSeparator: '\n---\n',
};

// 内部表单类型，确保嵌套对象始终存在
interface FormData {
  fileVariable: string;
  supportedTypes: DocumentType[];
  outputVariable: string;
  extractMetadata: boolean;
  preserveFormatting: boolean;
  maxFileSize: number;
  ocrConfig: OcrConfig;
  paginationConfig: PaginationConfig;
}

// 表单数据
const formData = reactive<FormData>({
  fileVariable: '',
  supportedTypes: ['PDF', 'DOCX', 'XLSX', 'TXT'],
  outputVariable: 'extracted_text',
  extractMetadata: false,
  preserveFormatting: false,
  maxFileSize: 10 * 1024 * 1024, // 10MB
  ocrConfig: { ...defaultOcrConfig },
  paginationConfig: { ...defaultPaginationConfig },
});

// 监听配置变化
watch(
  () => props.config,
  (config) => {
    if (!config) return;
    formData.fileVariable = config.fileVariable || '';
    formData.supportedTypes = config.supportedTypes || [
      'PDF',
      'DOCX',
      'XLSX',
      'TXT',
    ];
    formData.outputVariable = config.outputVariable || 'extracted_text';
    formData.extractMetadata = config.extractMetadata ?? false;
    formData.preserveFormatting = config.preserveFormatting ?? false;
    formData.maxFileSize = config.maxFileSize || 10 * 1024 * 1024;
    formData.ocrConfig = { ...defaultOcrConfig, ...config.ocrConfig };
    formData.paginationConfig = {
      ...defaultPaginationConfig,
      ...config.paginationConfig,
    };
    // 转换为 MB
    maxFileSizeMB.value = Math.round(formData.maxFileSize / (1024 * 1024));
  },
  { immediate: true, deep: true },
);

// 处理文件大小变更
function handleFileSizeChange() {
  formData.maxFileSize = maxFileSizeMB.value
    ? maxFileSizeMB.value * 1024 * 1024
    : 10 * 1024 * 1024;
  handleChange();
}

// 处理配置变更
function handleChange() {
  emit('update:config', { ...formData });
}
</script>

<template>
  <a-form layout="vertical" :model="formData" class="node-form">
    <!-- 文件变量 -->
    <a-form-item label="文件变量" required>
      <VariableInput
        v-model="formData.fileVariable"
        :current-node-id="nodeId"
        placeholder="选择或输入文件变量"
        @change="handleChange"
      />
      <div class="form-hint">输入包含文件的变量引用</div>
    </a-form-item>

    <!-- 支持的文档类型 -->
    <a-form-item label="支持的文档类型">
      <a-checkbox-group
        v-model:value="formData.supportedTypes"
        @change="handleChange"
      >
        <div class="doc-type-grid">
          <a-checkbox value="PDF"> <FilePdfOutlined /> PDF </a-checkbox>
          <a-checkbox value="DOCX">
            <FileWordOutlined /> Word (.docx)
          </a-checkbox>
          <a-checkbox value="DOC">
            <FileWordOutlined /> Word (.doc)
          </a-checkbox>
          <a-checkbox value="XLSX">
            <FileExcelOutlined /> Excel (.xlsx)
          </a-checkbox>
          <a-checkbox value="XLS">
            <FileExcelOutlined /> Excel (.xls)
          </a-checkbox>
          <a-checkbox value="PPTX">
            <FilePptOutlined /> PPT (.pptx)
          </a-checkbox>
          <a-checkbox value="PPT"> <FilePptOutlined /> PPT (.ppt) </a-checkbox>
          <a-checkbox value="TXT">
            <FileTextOutlined /> 文本 (.txt)
          </a-checkbox>
          <a-checkbox value="CSV"> <FileTextOutlined /> CSV </a-checkbox>
          <a-checkbox value="MD">
            <FileMarkdownOutlined /> Markdown
          </a-checkbox>
          <a-checkbox value="HTML"> <Html5Outlined /> HTML </a-checkbox>
        </div>
      </a-checkbox-group>
      <div class="form-hint">选择允许处理的文档类型</div>
    </a-form-item>

    <!-- 输出变量名 -->
    <a-form-item label="输出变量名">
      <a-input
        v-model:value="formData.outputVariable"
        placeholder="extracted_text"
        @change="handleChange"
      />
    </a-form-item>

    <!-- 提取选项 -->
    <a-form-item label="提取选项">
      <div class="option-list">
        <a-checkbox
          v-model:checked="formData.extractMetadata"
          @change="handleChange"
        >
          提取元数据
        </a-checkbox>
        <div class="form-hint option-hint">
          提取文档标题、作者、创建时间等元数据
        </div>

        <a-checkbox
          v-model:checked="formData.preserveFormatting"
          @change="handleChange"
        >
          保留格式
        </a-checkbox>
        <div class="form-hint option-hint">尽可能保留原文档的格式结构</div>
      </div>
    </a-form-item>

    <!-- 最大文件大小 -->
    <a-form-item label="最大文件大小 (MB)">
      <a-input-number
        v-model:value="maxFileSizeMB"
        :min="1"
        :max="100"
        placeholder="10"
        style="width: 100%"
        @change="handleFileSizeChange"
      />
      <div class="form-hint">限制可处理的最大文件大小，默认 10MB</div>
    </a-form-item>

    <!-- OCR 配置 -->
    <a-collapse ghost>
      <a-collapse-panel key="ocr" header="OCR 配置（图片/扫描文档）">
        <a-form-item>
          <a-checkbox
            v-model:checked="formData.ocrConfig.enabled"
            @change="handleChange"
          >
            启用 OCR
          </a-checkbox>
          <div class="form-hint">对图片和扫描 PDF 进行文字识别</div>
        </a-form-item>

        <template v-if="formData.ocrConfig.enabled">
          <a-form-item label="OCR 引擎">
            <a-select
              v-model:value="formData.ocrConfig.engine"
              @change="handleChange"
            >
              <a-select-option value="TESSERACT">
                Tesseract (开源)
              </a-select-option>
              <a-select-option value="PADDLE_OCR">
                PaddleOCR (中文优化)
              </a-select-option>
              <a-select-option value="CLOUD_OCR">云端 OCR</a-select-option>
            </a-select>
          </a-form-item>

          <a-form-item label="识别语言">
            <a-select
              v-model:value="formData.ocrConfig.language"
              @change="handleChange"
            >
              <a-select-option value="chi_sim">简体中文</a-select-option>
              <a-select-option value="chi_tra">繁体中文</a-select-option>
              <a-select-option value="eng">英文</a-select-option>
              <a-select-option value="chi_sim+eng">中英混合</a-select-option>
              <a-select-option value="jpn">日文</a-select-option>
              <a-select-option value="kor">韩文</a-select-option>
            </a-select>
          </a-form-item>

          <a-form-item>
            <a-checkbox
              v-model:checked="formData.ocrConfig.preprocessImage"
              @change="handleChange"
            >
              图像预处理
            </a-checkbox>
            <div class="form-hint">
              对图像进行去噪、二值化等预处理以提高识别率
            </div>
          </a-form-item>

          <a-form-item label="DPI 设置">
            <a-input-number
              v-model:value="formData.ocrConfig.dpi"
              :min="72"
              :max="600"
              placeholder="300"
              style="width: 100%"
              @change="handleChange"
            />
            <div class="form-hint">
              PDF 转图像时的 DPI，越高识别越准但速度越慢
            </div>
          </a-form-item>
        </template>
      </a-collapse-panel>

      <a-collapse-panel key="pagination" header="分页配置">
        <a-form-item>
          <a-checkbox
            v-model:checked="formData.paginationConfig.splitByPage"
            @change="handleChange"
          >
            按页分割
          </a-checkbox>
          <div class="form-hint">将文档按页分割为数组输出</div>
        </a-form-item>

        <a-form-item label="页码范围">
          <a-space>
            <a-input-number
              v-model:value="formData.paginationConfig.startPage"
              :min="1"
              placeholder="起始页"
              style="width: 100px"
              @change="handleChange"
            />
            <span>至</span>
            <a-input-number
              v-model:value="formData.paginationConfig.endPage"
              :min="1"
              placeholder="结束页"
              style="width: 100px"
              @change="handleChange"
            />
          </a-space>
          <div class="form-hint">留空表示提取所有页面</div>
        </a-form-item>

        <a-form-item label="页面分隔符">
          <a-input
            v-model:value="formData.paginationConfig.pageSeparator"
            placeholder="\n---\n"
            @change="handleChange"
          />
          <div class="form-hint">不按页分割时，页面之间的分隔符</div>
        </a-form-item>
      </a-collapse-panel>
    </a-collapse>

    <!-- 支持的文档类型说明 -->
    <a-alert type="info" show-icon class="doc-type-info">
      <template #message>文档提取说明</template>
      <template #description>
        <ul class="info-list">
          <li>PDF: 支持文本 PDF 和扫描 PDF（需启用 OCR）</li>
          <li>Word: 支持 .doc 和 .docx 格式</li>
          <li>Excel: 提取所有工作表的内容</li>
          <li>PPT: 提取幻灯片中的文本内容</li>
          <li>文本文件: 自动检测编码</li>
        </ul>
      </template>
    </a-alert>
  </a-form>
</template>

<style scoped lang="less">
.node-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label) {
    padding-bottom: 4px;

    > label {
      font-size: 12px;
      color: #595959;
    }
  }

  .form-hint {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
  }

  .option-hint {
    margin-bottom: 8px;
    margin-left: 24px;
  }

  .doc-type-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;

    :deep(.ant-checkbox-wrapper) {
      display: flex;
      align-items: center;
      gap: 4px;
      margin-left: 0;
    }
  }

  .option-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  :deep(.ant-collapse-header) {
    padding: 8px 0 !important;
    font-size: 12px;
    color: #595959;
  }

  :deep(.ant-collapse-content-box) {
    padding: 0 !important;
  }

  .doc-type-info {
    margin-top: 16px;

    .info-list {
      padding-left: 16px;
      margin: 0;
      font-size: 12px;

      li {
        margin-bottom: 4px;

        &:last-child {
          margin-bottom: 0;
        }
      }
    }
  }
}
</style>
