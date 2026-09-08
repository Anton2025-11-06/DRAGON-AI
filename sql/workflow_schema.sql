-- ============================================================
-- DRAGON-AI 工作流编排模块建表脚本
-- 模块: service_workflow / workflow_engine
-- 说明: 6 张业务表 + 1 张文件表。引擎运行时只读快照，
--       编辑态写 tb_workflow.graph，发布时快照进 tb_workflow_version。
-- 依赖: 主库（与 tb_model 同库）；幂等执行（IF NOT EXISTS）
-- ============================================================

-- 1. 工作流主表（草稿区）
CREATE TABLE IF NOT EXISTS `tb_workflow` (
  `id`              INT          NOT NULL AUTO_INCREMENT COMMENT '工作流ID',
  `name`            VARCHAR(128) NOT NULL COMMENT '工作流名称',
  `description`     VARCHAR(500)          DEFAULT NULL COMMENT '描述',
  -- DRAFT 草稿 / PUBLISHED 已发布 / ARCHIVED 已归档
  `status`          VARCHAR(16)  NOT NULL DEFAULT 'DRAFT' COMMENT '状态',
  -- VueFlow 画布数据 {nodes:[{id,type,label,position,data}],edges:[{id,source,sourceHandle,target,targetHandle}]}
  `graph`           JSON                  DEFAULT NULL COMMENT '图定义（草稿）',
  -- 工作流级输入变量定义（表单渲染用，与 START 节点 fields 双写保持一致）
  `input_variables` JSON                  DEFAULT NULL COMMENT '输入变量定义',
  `output_variables` JSON                 DEFAULT NULL COMMENT '输出变量定义',
  `current_version` INT          NOT NULL DEFAULT 0 COMMENT '当前已发布版本号',
  `created_by`      INT          NOT NULL DEFAULT 0 COMMENT '创建人 user_id',
  `create_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_wf_status` (`status`),
  KEY `idx_wf_created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流主表';

-- 2. 工作流版本快照表（发布时快照，运行时只读）
CREATE TABLE IF NOT EXISTS `tb_workflow_version` (
  `id`              INT          NOT NULL AUTO_INCREMENT COMMENT '版本ID',
  `workflow_id`     INT          NOT NULL COMMENT '工作流ID',
  `version`         INT          NOT NULL COMMENT '版本号（单调递增）',
  -- 发布时刻的 graph + input/output variables 完整快照
  `graph_snapshot`  JSON         NOT NULL COMMENT '图快照',
  `input_variables`  JSON                 DEFAULT NULL COMMENT '输入变量定义快照',
  `output_variables` JSON                 DEFAULT NULL COMMENT '输出变量定义快照',
  `change_log`      VARCHAR(500)          DEFAULT NULL COMMENT '变更说明',
  `published`       TINYINT      NOT NULL DEFAULT 1 COMMENT '是否已发布 1是 0是历史草稿版本',
  `created_by`      INT          NOT NULL DEFAULT 0 COMMENT '发布人',
  `create_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发布时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_wf_ver` (`workflow_id`, `version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流版本快照表';

-- 3. 执行实例表（一次工作流运行 = 一条记录）
CREATE TABLE IF NOT EXISTS `tb_workflow_execution` (
  `id`               VARCHAR(36)  NOT NULL COMMENT '执行ID（UUID，对外即 executionId）',
  `workflow_id`      INT          NOT NULL COMMENT '工作流ID',
  `workflow_version` INT          NOT NULL DEFAULT 0 COMMENT '执行时的工作流版本（0=草稿调试）',
  -- PENDING/RUNNING/COMPLETED/FAILED/PAUSED/CANCELLED
  `status`           VARCHAR(16)  NOT NULL DEFAULT 'PENDING' COMMENT '执行状态',
  -- DEBUG 画布调试 / API 外部调用 / AGENT 智能体触发
  `trigger_type`     VARCHAR(16)  NOT NULL DEFAULT 'DEBUG' COMMENT '触发来源',
  `inputs`           JSON                  DEFAULT NULL COMMENT '输入参数',
  `outputs`          JSON                  DEFAULT NULL COMMENT '输出结果',
  `variables`        JSON                  DEFAULT NULL COMMENT '全局变量快照（暂停/快照恢复用）',
  -- 节点执行状态聚合 {nodeId: {order,status,input,output,error,duration}}
  `node_states`      JSON                  DEFAULT NULL COMMENT '节点执行状态聚合',
  `error_message`    TEXT                  DEFAULT NULL COMMENT '错误信息',
  `input_tokens`     INT          NOT NULL DEFAULT 0 COMMENT '输入Token数',
  `output_tokens`    INT          NOT NULL DEFAULT 0 COMMENT '输出Token数',
  `llm_call_count`   INT          NOT NULL DEFAULT 0 COMMENT 'LLM调用次数',
  `duration_ms`      INT          NOT NULL DEFAULT 0 COMMENT '总耗时(毫秒)',
  `started_at`       DATETIME              DEFAULT NULL COMMENT '开始时间',
  `completed_at`     DATETIME              DEFAULT NULL COMMENT '结束时间',
  `current_node_id`  VARCHAR(64)           DEFAULT NULL COMMENT '当前节点（暂停时）',
  `breakpoints`      JSON                  DEFAULT NULL COMMENT '断点节点ID列表',
  `user_id`          INT          NOT NULL DEFAULT 0 COMMENT '执行人',
  `create_time`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_exec_wf` (`workflow_id`, `create_time`),
  KEY `idx_exec_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流执行实例表';

-- 4. 节点执行明细表（调试面板 NodeTracePanel 数据源）
CREATE TABLE IF NOT EXISTS `tb_workflow_node_execution` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '明细ID',
  `execution_id`  VARCHAR(36)  NOT NULL COMMENT '执行ID',
  `node_id`       VARCHAR(64)  NOT NULL COMMENT '节点ID',
  `node_type`     VARCHAR(32)  NOT NULL COMMENT '节点类型',
  -- RUNNING/COMPLETED/FAILED/SKIPPED
  `status`        VARCHAR(16)  NOT NULL DEFAULT 'RUNNING' COMMENT '节点状态',
  `node_order`    INT          NOT NULL DEFAULT 0 COMMENT '执行顺序',
  `input`         JSON                  DEFAULT NULL COMMENT '输入数据',
  `output`        JSON                  DEFAULT NULL COMMENT '输出数据',
  `error`         TEXT                  DEFAULT NULL COMMENT '错误信息',
  `duration_ms`   INT          NOT NULL DEFAULT 0 COMMENT '耗时(毫秒)',
  `started_at`    DATETIME              DEFAULT NULL COMMENT '开始时间',
  `completed_at`  DATETIME              DEFAULT NULL COMMENT '结束时间',
  PRIMARY KEY (`id`),
  KEY `idx_nexec_exec` (`execution_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流节点执行明细表';

-- 5. 工作流模板表（内置模板用种子数据）
CREATE TABLE IF NOT EXISTS `tb_workflow_template` (
  `id`          INT          NOT NULL AUTO_INCREMENT COMMENT '模板ID',
  `name`        VARCHAR(128) NOT NULL COMMENT '模板名称',
  `description` VARCHAR(500)          DEFAULT NULL COMMENT '模板描述',
  -- CONVERSATION/GENERATION/RAG/EXTRACTION/SUMMARY/CUSTOM
  `category`    VARCHAR(32)  NOT NULL DEFAULT 'CUSTOM' COMMENT '模板分类',
  `icon`        VARCHAR(64)           DEFAULT NULL COMMENT '模板图标',
  `graph`       JSON         NOT NULL COMMENT '图定义',
  `input_variables`  JSON             DEFAULT NULL COMMENT '输入变量定义',
  `output_variables` JSON             DEFAULT NULL COMMENT '输出变量定义',
  `is_built_in` TINYINT      NOT NULL DEFAULT 0 COMMENT '是否内置模板',
  `created_by`  INT          NOT NULL DEFAULT 0 COMMENT '创建人',
  `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_tpl_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流模板表';

-- 6. 工作流 API Key 表（对外暴露工作流）
CREATE TABLE IF NOT EXISTS `tb_workflow_api_key` (
  `id`             INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
  `workflow_id`    INT          NOT NULL COMMENT '工作流ID',
  `name`           VARCHAR(128) NOT NULL COMMENT 'Key 名称',
  -- wf_ 前缀 + 随机串，仿 tb_model_apply 的 mk_ 机制
  `api_key`        VARCHAR(64)  NOT NULL COMMENT 'API Key',
  `rate_limit`     INT          NOT NULL DEFAULT 0 COMMENT '每分钟调用上限 0=不限',
  -- ACTIVE/REVOKED/EXPIRED
  `status`         VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE' COMMENT '状态',
  `expire_time`    DATETIME              DEFAULT NULL COMMENT '过期时间',
  `last_used_time` DATETIME              DEFAULT NULL COMMENT '最近使用时间',
  `total_calls`    INT          NOT NULL DEFAULT 0 COMMENT '累计调用次数',
  `create_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_wf_api_key` (`api_key`),
  KEY `idx_wfak_wf` (`workflow_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流API Key表';

-- 7. 工作流临时文件表（DOC_EXTRACTOR / Vision 输入用）
CREATE TABLE IF NOT EXISTS `tb_workflow_file` (
  `file_id`      VARCHAR(64)  NOT NULL COMMENT '文件ID',
  `name`         VARCHAR(256) NOT NULL COMMENT '原始文件名',
  `size`         BIGINT       NOT NULL DEFAULT 0 COMMENT '文件大小(字节)',
  `content_type` VARCHAR(128)          DEFAULT NULL COMMENT 'MIME 类型',
  -- 本地存储路径 / 对象存储 key
  `storage_path` VARCHAR(500) NOT NULL COMMENT '存储路径',
  `user_id`      INT          NOT NULL DEFAULT 0 COMMENT '上传人',
  `create_time`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
  PRIMARY KEY (`file_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流临时文件表';
