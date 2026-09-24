-- =====================================================================================
-- DRAGON-AI 数据库初始化（全新部署的唯一入口）
-- 版本：v2 ｜ 覆盖 24 张业务表 + 角色/管理员/部门/菜单/按钮权限种子
--
-- 怎么执行：
--   mysql -h <host> -P 3306 -u <user> -p --default-character-set=utf8mb4 < sql/v2_init.sql
--   1) 整个文件必须用同一个连接跑完：PART 8 用 @会话变量 记菜单 id，换连接会丢。
--   2) 换库名只改 PART 0 的两处（建库 + USE）。
--   3) login / gateway / system / rag / workflow 五个服务共用这一个库，一份文件建全。
--   4) 重复执行安全：建表 IF NOT EXISTS，种子全部 INSERT IGNORE（行已存在就不动，
--      不会把现场改过的口令、角色名、菜单排序冲回去）。
--   5) MySQL 5.7+ / 8.x。JSON 与 TEXT 列一律不写 DEFAULT NULL（5.7 会报 1101，
--      而可空本身就是它们的默认值）。
--
-- sql/ 下其它文件与本文件的关系（内容已全部并入本文件，它们只为存量库升级而保留）：
--   new_init_.sql                          用户/RBAC/审计/RAG/模型/模型对话/智能体资源 18 张表 + 菜单种子
--   workflow_schema.sql                    工作流 6 张表
--   approval_memory_columns.sql            执行表 submit_mode / awaiting_node_id / graph_hash，下线 breakpoints
--   execution_pause_generation_column.sql  执行表 pause_generation
--   workflow_nested_execution_columns.sql  执行表 parent_exec_id / parent_node_id 及其索引
--   model_adv_columns.sql                  模型表能力位与常用参数（supports_stream 等 5 列）
--   model_function_call_column.sql         模型表 supports_function_call
--   tool_timeout_column.sql                工具表 timeout，parameters_schema 扩为 TEXT
--   mcp_description.sql                    MCP 表 description
--   skill_zip_storage.sql                  技能表 zip_file_name / zip_storage_name
--   menu_agent_move.sql                    智能体菜单提升与改名（本文件按最终形态直接种）
--   init_nacos.yaml                        不是 SQL：Nacos 配置中心的初始化内容
--   → 已经跑过老版本的存量库别指望本文件补列：CREATE TABLE IF NOT EXISTS 遇到老表会整条跳过，
--     缺的列不会补上，请按上表逐个执行对应的增量脚本。全新环境只用本文件。
--
-- 本文件不写的数据（各有归属，不是表结构）：
--   tb_model               模型登记带着各厂商 api_key，属环境密钥，部署后在「模型广场-模型列表」录入
--   tb_workflow_template   5 个内置模板由 workflow 服务首次请求时自动写入
--                          （WorkflowService.seed_builtin_templates），这里不重复种
--   会话 / 在线用户 / 限流 / 队列 / 向量：分别落在 Redis、Nacos、Milvus，MySQL 里没有对应表
--   按钮权限点按后端 @has_permission 实际校验的清单生成；代码已不校验的历史权限点不再种
-- =====================================================================================

-- =====================================================================================
-- PART 0  建库（库名只在这两行出现）
-- =====================================================================================
CREATE DATABASE IF NOT EXISTS `ai3` DEFAULT CHARACTER SET utf8mb4;
USE `ai3`;

-- =====================================================================================
-- PART 1  用户 / RBAC / 审计（login、gateway、system 共用）
-- =====================================================================================

-- 1.1 用户表
CREATE TABLE IF NOT EXISTS `tb_user` (
    `user_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `username` VARCHAR(128) NOT NULL COMMENT '用户名',
    `password` VARCHAR(1000) NOT NULL COMMENT '密码（b32hex 编码存储）',
    `email` VARCHAR(128) DEFAULT NULL COMMENT '邮箱',
    `phone` VARCHAR(32) DEFAULT NULL COMMENT '手机号',
    `real_name` VARCHAR(64) DEFAULT NULL COMMENT '真实姓名',
    `dept_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '所属部门ID（数据权限组织单元，0表示未分配）',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态：0-禁用，1-启用',
    `is_deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '是否删除：0-未删除，1-已删除',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`user_id`),
    UNIQUE KEY `uk_username` (`username`),
    KEY `idx_dept` (`dept_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- 1.2 角色表
CREATE TABLE IF NOT EXISTS `tb_role` (
    `role_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `role_code` VARCHAR(64) NOT NULL COMMENT '角色编码',
    `role_name` VARCHAR(64) NOT NULL COMMENT '角色名称',
    `description` VARCHAR(255) DEFAULT NULL COMMENT '描述',
    `data_scope` TINYINT NOT NULL DEFAULT 4 COMMENT '数据权限：1-全部 2-本部门及以下 3-本部门 4-仅本人 5-自定义部门',
    `dept_ids` VARCHAR(1024) DEFAULT NULL COMMENT '自定义数据权限部门ID（逗号分隔）',
    `is_builtin` TINYINT NOT NULL DEFAULT 0 COMMENT '内置角色保护：1-不可删除',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-禁用 1-启用',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`role_id`),
    UNIQUE KEY `uk_role_code` (`role_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色表';

-- 1.3 菜单/权限表（menu_type=3 且 perm 非空即按钮权限点）
CREATE TABLE IF NOT EXISTS `tb_menu` (
    `menu_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `parent_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '父级ID，0为根',
    `menu_name` VARCHAR(64) NOT NULL COMMENT '名称',
    `menu_type` TINYINT NOT NULL DEFAULT 2 COMMENT '1-目录 2-菜单 3-按钮权限',
    `path` VARCHAR(255) DEFAULT NULL COMMENT '前端路由',
    `component` VARCHAR(255) DEFAULT NULL COMMENT '前端组件',
    `perm` VARCHAR(128) DEFAULT NULL COMMENT '权限标识 system:user:add',
    `icon` VARCHAR(64) DEFAULT NULL,
    `sort` INT NOT NULL DEFAULT 0,
    `visible` TINYINT NOT NULL DEFAULT 1 COMMENT '是否显示：0-隐藏 1-显示',
    `status` TINYINT NOT NULL DEFAULT 1,
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`menu_id`),
    KEY `idx_parent` (`parent_id`),
    UNIQUE KEY `uk_parent_name` (`parent_id`, `menu_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='菜单/权限表';

-- 1.4 用户-角色关联表
CREATE TABLE IF NOT EXISTS `tb_user_role` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `role_id` BIGINT UNSIGNED NOT NULL,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_role` (`user_id`, `role_id`),
    KEY `idx_user` (`user_id`),
    KEY `idx_role` (`role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户角色关联表';

-- 1.5 角色-菜单关联表
CREATE TABLE IF NOT EXISTS `tb_role_menu` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `role_id` BIGINT UNSIGNED NOT NULL,
    `menu_id` BIGINT UNSIGNED NOT NULL,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_role_menu` (`role_id`, `menu_id`),
    KEY `idx_role` (`role_id`),
    KEY `idx_menu` (`menu_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色菜单关联表';

-- 1.6 部门表（数据权限的基础组织单元）
CREATE TABLE IF NOT EXISTS `tb_dept` (
    `dept_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `parent_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '父部门ID，0为根',
    `dept_name` VARCHAR(64) NOT NULL COMMENT '部门名称',
    `sort` INT NOT NULL DEFAULT 0,
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-禁用 1-启用',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`dept_id`),
    KEY `idx_parent` (`parent_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='部门表';

-- 1.7 系统操作日志表（审计，trace_id 串起一次全链路请求）
CREATE TABLE IF NOT EXISTS `tb_operate_log` (
    `log_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `trace_id` VARCHAR(64) NOT NULL COMMENT '链路追踪ID（一次请求全链路唯一）',
    `user_id` BIGINT UNSIGNED DEFAULT NULL COMMENT '操作人用户ID，未登录为空',
    `username` VARCHAR(128) DEFAULT NULL COMMENT '操作人用户名',
    `module` VARCHAR(64) DEFAULT NULL COMMENT '业务模块',
    `operation` VARCHAR(128) DEFAULT NULL COMMENT '操作描述',
    `method` VARCHAR(16) NOT NULL COMMENT '请求方法',
    `path` VARCHAR(255) NOT NULL COMMENT '请求路径(含参数)',
    `params` TEXT COMMENT '请求参数 JSON（敏感字段已脱敏）',
    `ip` VARCHAR(64) DEFAULT NULL COMMENT '客户端IP',
    `user_agent` VARCHAR(255) DEFAULT NULL COMMENT '客户端UA',
    `status` INT NOT NULL DEFAULT 200 COMMENT '响应状态码',
    `cost_ms` INT NOT NULL DEFAULT 0 COMMENT '耗时(毫秒)',
    `error_msg` VARCHAR(1000) DEFAULT NULL COMMENT '错误信息',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`log_id`),
    KEY `idx_trace` (`trace_id`),
    KEY `idx_user` (`user_id`),
    KEY `idx_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统操作日志表';

-- =====================================================================================
-- PART 2  知识库 RAG（service_rag；正文与元数据在 MySQL，向量在 Milvus）
-- =====================================================================================

-- 2.1 知识库表
CREATE TABLE IF NOT EXISTS `tb_knowledge_base` (
    `kb_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `kb_name` VARCHAR(128) NOT NULL COMMENT '知识库名称',
    `description` VARCHAR(500) DEFAULT NULL,
    `owner_id` BIGINT UNSIGNED NOT NULL COMMENT '所有者用户ID',
    `is_public` TINYINT NOT NULL DEFAULT 0 COMMENT '0-私有 1-公开',
    `status` TINYINT NOT NULL DEFAULT 1,
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`kb_id`),
    KEY `idx_owner` (`owner_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库表';

-- 2.2 知识库文档表
CREATE TABLE IF NOT EXISTS `tb_document` (
    `doc_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `kb_id` BIGINT UNSIGNED NOT NULL,
    `doc_name` VARCHAR(255) NOT NULL,
    `file_path` VARCHAR(500) NOT NULL COMMENT '文件存储路径',
    `file_size` BIGINT NOT NULL DEFAULT 0,
    `file_type` VARCHAR(16) DEFAULT NULL,
    `chunk_count` INT NOT NULL DEFAULT 0 COMMENT '分块数',
    `status` TINYINT NOT NULL DEFAULT 0 COMMENT '0-待处理 1-解析中 2-向量化中 3-完成 -1-失败',
    `error_msg` VARCHAR(500) DEFAULT NULL,
    `uploader_id` BIGINT UNSIGNED NOT NULL,
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`doc_id`),
    KEY `idx_kb` (`kb_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库文档表';

-- 2.3 文档分块表（分块正文，向量在 Milvus 的 kb_{kb_id} collection）
CREATE TABLE IF NOT EXISTS `tb_document_chunk` (
    `chunk_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `doc_id` BIGINT UNSIGNED NOT NULL,
    `kb_id` BIGINT UNSIGNED NOT NULL,
    `chunk_index` INT NOT NULL DEFAULT 0,
    `content` TEXT NOT NULL COMMENT '分块正文（向量存Milvus）',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`chunk_id`),
    KEY `idx_doc` (`doc_id`),
    KEY `idx_kb` (`kb_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='文档分块表';

-- 2.4 知识库授权共享表
CREATE TABLE IF NOT EXISTS `tb_kb_share` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `kb_id` BIGINT UNSIGNED NOT NULL,
    `share_type` TINYINT NOT NULL DEFAULT 1 COMMENT '1-用户 2-角色',
    `target_id` BIGINT UNSIGNED NOT NULL COMMENT '目标用户ID或角色ID',
    `expire_time` DATETIME DEFAULT NULL COMMENT '过期时间，空为永久',
    `create_by` BIGINT UNSIGNED NOT NULL,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_kb` (`kb_id`),
    KEY `idx_target` (`target_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库授权共享表';

-- =====================================================================================
-- PART 3  模型广场（service_system）
-- =====================================================================================

-- 3.1 模型表（登记只需 base_url + provider + category，端点由 common_model 各类型子类自拼；
--     supports_* 是能力位，决定测试 UI 开关、广场展示以及工作流节点能不能给该模型挂工具）
CREATE TABLE IF NOT EXISTS `tb_model` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(128) NOT NULL COMMENT '模型名称',
    `category` VARCHAR(32) NOT NULL COMMENT '能力类型 code，取值见 common/common_constants/model_constant.py 的 MT_*（text_to_text/text_embedding/...）',
    `provider` VARCHAR(32) NOT NULL COMMENT '提供商: openai/dashscope/zhipu',
    `model_name` VARCHAR(128) NOT NULL COMMENT '模型标识(API 调用时使用)',
    `base_url` VARCHAR(500) DEFAULT NULL COMMENT '模型接口基础地址(各厂商 OpenAI 兼容/原生基础 URL)',
    `gateway_url` VARCHAR(500) DEFAULT NULL COMMENT '模型网关地址(展示给调用方)',
    `api_key` VARCHAR(500) DEFAULT NULL COMMENT '管理端密钥',
    `rate_limit_qps` INT NOT NULL DEFAULT 0 COMMENT '每秒并发限制 0=不限',
    `model_params` JSON COMMENT '模型调用参数(JSON 字典: temperature/top_k/extra_body 等，由 common_params 派生)',
    `supports_stream` TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持流消息 1支持 0不支持',
    `supports_thinking` TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持思考模式 1支持 0不支持',
    `supports_function_call` TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持工具调用 1支持 0不支持',
    `stream_param` VARCHAR(64) DEFAULT NULL COMMENT '开启流式的参数键名(默认 stream)',
    `thinking_param` VARCHAR(64) DEFAULT NULL COMMENT '开启思考的参数键名',
    `common_params` JSON COMMENT '常用参数列表 [{name,default,desc,type}]',
    `tutorial_md` MEDIUMTEXT COMMENT '使用教程 Markdown',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '启用状态 1启用 0停用',
    `created_by` INT NOT NULL DEFAULT 0 COMMENT '创建人 user_id',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category` (`category`),
    KEY `idx_provider` (`provider`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='模型广场-模型';

-- 3.2 模型申请审批表（通过后签发 mk_ 前缀 API Key，授权存 Redis 供网关鉴权）
CREATE TABLE IF NOT EXISTS `tb_model_apply` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `model_id` INT NOT NULL COMMENT '模型 id',
    `user_id` INT NOT NULL COMMENT '申请人 user_id',
    `username` VARCHAR(64) DEFAULT NULL COMMENT '申请人用户名',
    `dept_id` INT NOT NULL DEFAULT 0 COMMENT '申请人部门 id',
    `reason` VARCHAR(500) DEFAULT NULL COMMENT '申请理由',
    `status` TINYINT NOT NULL DEFAULT 0 COMMENT '0待审批 1通过 2拒绝',
    `api_key` VARCHAR(500) DEFAULT NULL COMMENT '审批通过后生成的 API Key',
    `reject_reason` VARCHAR(500) DEFAULT NULL COMMENT '拒绝原因',
    `audit_by` VARCHAR(64) DEFAULT NULL COMMENT '审批人',
    `audit_time` DATETIME DEFAULT NULL COMMENT '审批时间',
    `apply_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '申请时间',
    PRIMARY KEY (`id`),
    KEY `idx_model` (`model_id`),
    KEY `idx_user` (`user_id`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='模型广场-申请审批';

-- =====================================================================================
-- PART 4  模型体验（service_workflow；菜单叫「模型体验」，表名沿用 model_chat）
-- =====================================================================================

-- 4.1 会话表
CREATE TABLE IF NOT EXISTS `tb_model_chat_session` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `user_id` INT NOT NULL COMMENT '所属用户',
    `title` VARCHAR(128) NOT NULL DEFAULT '新会话' COMMENT '会话标题',
    `model_apply_id` INT NOT NULL DEFAULT 0 COMMENT '授权记录 apply_id',
    `model_name` VARCHAR(128) DEFAULT NULL COMMENT '模型标识',
    `reasoning` TINYINT NOT NULL DEFAULT 0 COMMENT '深度思考偏好 0关 1开',
    `stream` TINYINT NOT NULL DEFAULT 1 COMMENT '流式偏好 0关 1开',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user` (`user_id`, `update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='模型对话-会话';

-- 4.2 消息表
CREATE TABLE IF NOT EXISTS `tb_model_chat_message` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `session_id` INT NOT NULL COMMENT '会话 id',
    `role` VARCHAR(16) NOT NULL COMMENT '角色 USER/ASSISTANT',
    `content` MEDIUMTEXT COMMENT '消息内容',
    `reasoning_content` MEDIUMTEXT COMMENT '深度思考内容',
    `model_name` VARCHAR(128) DEFAULT NULL COMMENT '模型标识',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_session` (`session_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='模型对话-消息';

-- =====================================================================================
-- PART 5  智能体资源：MCP 连接 / 工具 / 技能（service_workflow）
-- =====================================================================================

-- 5.1 MCP 服务器连接配置表
CREATE TABLE IF NOT EXISTS `tb_mcp_server` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name` VARCHAR(128) NOT NULL COMMENT '服务名称',
    `type` VARCHAR(16) NOT NULL DEFAULT 'SSE' COMMENT '连接类型 SSE/STDIO',
    `url` VARCHAR(500) DEFAULT NULL COMMENT 'SSE 连接地址',
    `command` VARCHAR(255) DEFAULT NULL COMMENT 'STDIO 启动命令',
    `args` VARCHAR(1000) DEFAULT NULL COMMENT 'STDIO 启动参数 JSON 数组',
    `env` VARCHAR(1000) DEFAULT NULL COMMENT '环境变量 JSON 对象',
    `description` VARCHAR(500) DEFAULT NULL COMMENT 'MCP 描述（这个连接是做什么的）',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='MCP 服务器连接配置表';

-- 5.2 动态 Python 函数工具表（源码入库，执行走受限沙箱）
CREATE TABLE IF NOT EXISTS `tb_tool` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name` VARCHAR(128) NOT NULL COMMENT '工具名称',
    `description` VARCHAR(500) DEFAULT NULL COMMENT '工具描述',
    `function_code` TEXT NOT NULL COMMENT 'Python 函数源码（import + def，入口 main 优先）',
    `parameters_schema` TEXT COMMENT '参数定义 JSON（{parameters:[{name,type,required,description,default}]}）',
    `timeout` INT NOT NULL DEFAULT 10000 COMMENT '执行超时（毫秒），工具节点未配置时取此值',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='动态 Python 函数工具表';

-- 5.3 技能目录表（SKILL.zip 原件存入公共存储，code 唯一；预览/编辑按需从存储取回解压）
CREATE TABLE IF NOT EXISTS `tb_skill` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name` VARCHAR(128) NOT NULL COMMENT '技能名称',
    `code` VARCHAR(128) NOT NULL COMMENT '技能标识（唯一）',
    `description` VARCHAR(500) DEFAULT NULL COMMENT '技能描述',
    `category` VARCHAR(64) DEFAULT NULL COMMENT '分类',
    `icon` VARCHAR(128) DEFAULT NULL COMMENT '图标',
    `tags` VARCHAR(500) DEFAULT NULL COMMENT '标签 JSON 数组',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `skill_path` VARCHAR(500) DEFAULT NULL COMMENT '展示用逻辑目录路径（skills/{code}）',
    `resource_count` INT NOT NULL DEFAULT 0 COMMENT '资源文件数',
    `zip_file_name` VARCHAR(255) DEFAULT NULL COMMENT '上传的 zip 包原始文件名',
    `zip_storage_name` VARCHAR(255) DEFAULT NULL COMMENT 'zip 原件在公共存储中的文件名（下载/删除句柄）',
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_code` (`code`),
    KEY `idx_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='技能目录表（zip 原件存储）';

-- =====================================================================================
-- PART 6  工作流编排（service_workflow）
--   执行域对外只有一个 executionId 与一个 submit 动作，口径见
--   docs/workflow-execution-contract.md（表结构与 variables 两段形状见其 §3）
-- =====================================================================================

-- 6.1 工作流主表（草稿区：编辑态写 graph，发布时快照进 tb_workflow_version）
CREATE TABLE IF NOT EXISTS `tb_workflow` (
  `id`              INT          NOT NULL AUTO_INCREMENT COMMENT '工作流ID',
  `name`            VARCHAR(128) NOT NULL COMMENT '工作流名称',
  `description`     VARCHAR(500)          DEFAULT NULL COMMENT '描述',
  `status`          VARCHAR(16)  NOT NULL DEFAULT 'DRAFT' COMMENT '状态 DRAFT 草稿 / PUBLISHED 已发布 / ARCHIVED 已归档',
  `graph`           JSON                  COMMENT '图定义（草稿）{nodes:[{id,type,label,position,data}],edges:[{id,source,sourceHandle,target,targetHandle}]}',
  `input_variables` JSON                  COMMENT '工作流级输入变量定义（表单渲染用，与 START 节点 fields 双写保持一致）',
  `output_variables` JSON                 COMMENT '输出变量定义',
  `current_version` INT          NOT NULL DEFAULT 0 COMMENT '当前已发布版本号',
  `created_by`      INT          NOT NULL DEFAULT 0 COMMENT '创建人 user_id',
  `create_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_wf_status` (`status`),
  KEY `idx_wf_created_by` (`created_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流主表';

-- 6.2 工作流版本快照表（发布时快照，运行时只读它）
CREATE TABLE IF NOT EXISTS `tb_workflow_version` (
  `id`              INT          NOT NULL AUTO_INCREMENT COMMENT '版本ID',
  `workflow_id`     INT          NOT NULL COMMENT '工作流ID',
  `version`         INT          NOT NULL COMMENT '版本号（单调递增）',
  `graph_snapshot`  JSON         NOT NULL COMMENT '发布时刻的图快照',
  `input_variables`  JSON        COMMENT '输入变量定义快照',
  `output_variables` JSON        COMMENT '输出变量定义快照',
  `change_log`      VARCHAR(500)          DEFAULT NULL COMMENT '变更说明',
  `published`       TINYINT      NOT NULL DEFAULT 1 COMMENT '是否已发布 1是 0是历史草稿版本',
  `created_by`      INT          NOT NULL DEFAULT 0 COMMENT '发布人',
  `create_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发布时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_wf_ver` (`workflow_id`, `version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流版本快照表';

-- 6.3 执行实例表（一次工作流运行 = 一条记录；id 即对外唯一的 executionId）
CREATE TABLE IF NOT EXISTS `tb_workflow_execution` (
  `id`               VARCHAR(36)  NOT NULL COMMENT '执行ID（UUID，对外即 executionId）',
  `workflow_id`      INT          NOT NULL COMMENT '工作流ID',
  `workflow_version` INT          NOT NULL DEFAULT 0 COMMENT '执行时的工作流版本（0=草稿调试）',
  `status`           VARCHAR(16)  NOT NULL DEFAULT 'PENDING' COMMENT '执行状态 PENDING/RUNNING/COMPLETED/FAILED/PAUSED/CANCELLED',
  `trigger_type`     VARCHAR(16)  NOT NULL DEFAULT 'DEBUG' COMMENT '触发来源 DEBUG 画布调试 / API 外部调用 / AGENT 智能体触发',
  `inputs`           JSON                  COMMENT '输入参数',
  `outputs`          JSON                  COMMENT '输出结果',
  `variables`        JSON                  COMMENT '两段式跨轮状态：roundRequest + pauseState',
  `node_states`      JSON                  COMMENT '节点状态聚合（跨轮恢复权威源：input/output/branch/llmMessages/review*）',
  `error_message`    TEXT                  COMMENT '错误信息',
  `input_tokens`     INT          NOT NULL DEFAULT 0 COMMENT '输入Token数',
  `output_tokens`    INT          NOT NULL DEFAULT 0 COMMENT '输出Token数',
  `llm_call_count`   INT          NOT NULL DEFAULT 0 COMMENT 'LLM调用次数',
  `duration_ms`      INT          NOT NULL DEFAULT 0 COMMENT '总耗时(毫秒)',
  `started_at`       DATETIME              DEFAULT NULL COMMENT '开始时间',
  `completed_at`     DATETIME              DEFAULT NULL COMMENT '结束时间',
  `current_node_id`  VARCHAR(64)           DEFAULT NULL COMMENT '当前节点（暂停时）',
  `submit_mode`      VARCHAR(16)           DEFAULT NULL COMMENT '本轮提交模式 RETRY 全部重跑/CONTINUE 审批后恢复，空=首次执行',
  `awaiting_node_id` VARCHAR(64)           DEFAULT NULL COMMENT '停在审批等待的节点ID（多份待办时记第一个），欠谁审批以 variables 的 pauseState 为准',
  `pause_generation` INT          NOT NULL DEFAULT 1 COMMENT '挂起代次：第几次开跑（含唤醒），事件过期判定用',
  `graph_hash`       VARCHAR(16)           DEFAULT NULL COMMENT '图拓扑指纹（节点id+边集合），再提交前漂移校验',
  `parent_exec_id`   VARCHAR(36)           DEFAULT NULL COMMENT '父执行ID（【工作流】节点发起的子执行）',
  `parent_node_id`   VARCHAR(64)           DEFAULT NULL COMMENT '父执行中发起本子执行的节点ID',
  `user_id`          INT          NOT NULL DEFAULT 0 COMMENT '执行人',
  `ip`           VARCHAR(64)            COMMENT '客户端IP',
  `create_time`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_exec_wf` (`workflow_id`, `create_time`),
  KEY `idx_exec_status` (`status`),
  KEY `idx_wf_exec_parent` (`parent_exec_id`, `parent_node_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流执行实例表';

-- 6.4 节点执行明细表（调试面板逐节点回放的数据源；跨轮恢复读的是执行表的 node_states，
--     审批改写的值也落在 node_states，明细行保留该节点当轮跑出来的历史）
CREATE TABLE IF NOT EXISTS `tb_workflow_node_execution` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '明细ID',
  `execution_id`  VARCHAR(36)  NOT NULL COMMENT '执行ID',
  `node_id`       VARCHAR(64)  NOT NULL COMMENT '节点ID',
  `node_type`     VARCHAR(32)  NOT NULL COMMENT '节点类型',
  `status`        VARCHAR(16)  NOT NULL DEFAULT 'RUNNING' COMMENT '节点状态 RUNNING/COMPLETED/FAILED/CANCELLED/TIMEOUT/AWAITING（AWAITING=等待人工审批，非终态）',
  `node_order`    INT          NOT NULL DEFAULT 0 COMMENT '执行顺序',
  `input`         JSON                  COMMENT '输入数据',
  `output`        JSON                  COMMENT '输出数据',
  `error`         TEXT                  COMMENT '错误信息',
  `duration_ms`   INT          NOT NULL DEFAULT 0 COMMENT '耗时(毫秒)',
  `started_at`    DATETIME              DEFAULT NULL COMMENT '开始时间',
  `completed_at`  DATETIME              DEFAULT NULL COMMENT '结束时间',
  PRIMARY KEY (`id`),
  KEY `idx_nexec_exec` (`execution_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流节点执行明细表';

-- 6.5 工作流模板表（内置模板由服务首启写入，也承载用户自建/导入的模板）
CREATE TABLE IF NOT EXISTS `tb_workflow_template` (
  `id`          INT          NOT NULL AUTO_INCREMENT COMMENT '模板ID',
  `name`        VARCHAR(128) NOT NULL COMMENT '模板名称',
  `description` VARCHAR(500)          DEFAULT NULL COMMENT '模板描述',
  `category`    VARCHAR(32)  NOT NULL DEFAULT 'CUSTOM' COMMENT '模板分类 CONVERSATION/GENERATION/RAG/EXTRACTION/SUMMARY/CUSTOM',
  `icon`        VARCHAR(64)           DEFAULT NULL COMMENT '模板图标',
  `graph`       JSON         NOT NULL COMMENT '图定义',
  `input_variables`  JSON             COMMENT '输入变量定义',
  `output_variables` JSON             COMMENT '输出变量定义',
  `is_built_in` TINYINT      NOT NULL DEFAULT 0 COMMENT '是否内置模板（内置不可改删）',
  `created_by`  INT          NOT NULL DEFAULT 0 COMMENT '创建人',
  `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_tpl_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流模板表';

-- 6.6 工作流 API Key 表（对外暴露工作流，wf_ 前缀，仿 tb_model_apply 的 mk_ 机制）
CREATE TABLE IF NOT EXISTS `tb_workflow_api_key` (
  `id`             INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
  `workflow_id`    INT          NOT NULL COMMENT '工作流ID',
  `name`           VARCHAR(128) NOT NULL COMMENT 'Key 名称',
  `api_key`        VARCHAR(64)  NOT NULL COMMENT 'API Key',
  `rate_limit`     INT          NOT NULL DEFAULT 0 COMMENT '每分钟调用上限 0=不限',
  `status`         VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE' COMMENT '状态 ACTIVE/REVOKED/EXPIRED',
  `expire_time`    DATETIME              DEFAULT NULL COMMENT '过期时间',
  `last_used_time` DATETIME              DEFAULT NULL COMMENT '最近使用时间',
  `total_calls`    INT          NOT NULL DEFAULT 0 COMMENT '累计调用次数',
  `create_time`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_wf_api_key` (`api_key`),
  KEY `idx_wfak_wf` (`workflow_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='工作流API Key表';

-- =====================================================================================
-- PART 7  种子：角色 / 管理员 / 根部门
-- =====================================================================================

-- 内置角色：靠 uk_role_code 幂等。已存在就什么都不改，因为 role_name/data_scope
-- 都可能在「角色管理」里被调过，初始化文件不该每次把它们拨回默认值
-- （不用 ON DUPLICATE KEY UPDATE + VALUES()：那个写法在 MySQL 8 已弃用，会报 warning）
INSERT IGNORE INTO `tb_role` (`role_code`, `role_name`, `description`, `data_scope`, `is_builtin`) VALUES ('ADMIN', '超级管理员', '系统内置超管', 1, 1);
INSERT IGNORE INTO `tb_role` (`role_code`, `role_name`, `description`, `data_scope`, `is_builtin`) VALUES ('USER', '普通用户', '注册默认角色', 4, 1);

-- 管理员账号：admin / Admin@123（库里存 b32hexencode(明文)；登录后请改密）
-- 已存在时 INSERT IGNORE 直接跳过，改过的密码不会被重跑文件重置
INSERT IGNORE INTO `tb_user` (`username`, `password`, `email`, `real_name`, `dept_id`) VALUES ('admin', '85I6QQBE80OJ4CO=', 'admin@ai.local', '系统管理员', 1);
INSERT IGNORE INTO `tb_user_role` (`user_id`, `role_id`)
SELECT u.`user_id`, r.`role_id` FROM `tb_user` u, `tb_role` r
WHERE u.`username` = 'admin' AND r.`role_code` = 'ADMIN';

-- 根部门（数据权限的组织单元）：显式指定 dept_id=1，重复执行按主键跳过
INSERT IGNORE INTO `tb_dept` (`dept_id`, `parent_id`, `dept_name`, `sort`) VALUES (1, 0, '总公司', 1);

-- =====================================================================================
-- PART 8  种子：菜单与按钮权限（前端 vben 路由规范：目录 component='BasicLayout'，
--   菜单 component='views/wemirr/xx/index.vue'，按钮 menu_type=3 且 perm 非空）
--   父级 id 用 @会话变量记，所以本文件必须整份在同一个连接里执行。
-- =====================================================================================

-- 8.1 一级目录
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(0, '首页',       1, '/home',        'BasicLayout', 'lucide:home',      0),
(0, '知识库',     1, '/kb',          'BasicLayout', 'lucide:book-open', 2),
(0, '智能体',     1, '/agent',       'BasicLayout', 'lucide:bot',       3),
(0, '模型广场',   1, '/model-plaza', 'BasicLayout', 'lucide:boxes',     4),
(0, '模型工厂',   1, '/model',       'BasicLayout', 'lucide:factory',   5),
(0, '数据集工厂', 1, '/dataset',     'BasicLayout', 'lucide:database',  6),
(0, '系统管理',   1, '/system',      'BasicLayout', 'lucide:settings',  99);
SET @d_home = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '首页');
SET @d_kb = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '知识库');
SET @d_agent = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '智能体');
SET @d_plaza = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '模型广场');
SET @d_model_factory = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '模型工厂');
SET @d_dataset = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '数据集工厂');
SET @d_system = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '系统管理');

-- 8.2 系统管理下的页面菜单
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_system, '用户管理',   2, 'user',       'views/wemirr/system/user/index.vue',          'lucide:users',       1),
(@d_system, '角色管理',   2, 'role',       'views/wemirr/system/auth/role/index.vue',     'lucide:shield',      2),
(@d_system, '菜单管理',   2, 'menu',       'views/wemirr/system/auth/menu/index.vue',     'lucide:menu',        3),
(@d_system, '部门管理',   2, 'org',        'views/wemirr/system/org/index.vue',           'lucide:building-2',  4),
(@d_system, '在线用户',   2, 'online',     'views/wemirr/system/online/index.vue',        'lucide:radio',       5),
(@d_system, '限流配置',   2, 'rate-limit', 'views/wemirr/system/rate-limit/index.vue',    'lucide:gauge',       6),
(@d_system, '操作日志',   2, 'opt-log',    'views/wemirr/system/log/opt-log.vue',         'lucide:file-text',   7),
(@d_system, '队列监控',   2, 'arq-monitor', 'views/wemirr/system/arq-monitor/index.vue',  'lucide:server',      8);
SET @m_user = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '用户管理');
SET @m_role = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '角色管理');
SET @m_menu = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '菜单管理');
SET @m_dept = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '部门管理');
SET @m_online = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '在线用户');
SET @m_rate = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '限流配置');
SET @m_log = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '操作日志');
SET @m_arq = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_system AND `menu_name` = '队列监控');

-- 8.3 首页 / 知识库 / 模型工厂 / 数据集工厂 下的页面菜单
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_home, '深度探索', 2, 'explorer', 'views/wemirr/home/index.vue', NULL, 1),
(@d_kb, '知识库文档', 2, 'doc',  'views/wemirr/ai/rag/doc/index.vue',  'lucide:folder-open',    1),
(@d_kb, '知识库对话', 2, 'chat', 'views/wemirr/ai/chat/rag/index.vue', 'lucide:messages-square', 2),
(@d_model_factory, '模型训练',     2, 'train',    'views/wemirr/model/train/index.vue',    NULL, 1),
(@d_model_factory, '模型部署',     2, 'deploy',   'views/wemirr/model/deploy/index.vue',   NULL, 2),
(@d_model_factory, '模型评测',     2, 'eval',     'views/wemirr/model/eval/index.vue',     NULL, 3),
(@d_model_factory, 'NoteBook开发', 2, 'notebook', 'views/wemirr/model/notebook/index.vue', NULL, 4),
(@d_model_factory, '模型归档',     2, 'archive',  'views/wemirr/model/archive/index.vue',  NULL, 5),
(@d_dataset, '数据集管理', 2, 'list', 'views/wemirr/dataset/index.vue', NULL, 1);

-- 8.4 智能体下的四个页面菜单（技能/MCP/工具已与工作流编排同级，不再有「工具目录」中间层）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_agent, '工作流编排', 2, 'workflow', 'views/wemirr/ai/workflow/list/index.vue', 'lucide:workflow', 1),
(@d_agent, '技能管理',   2, 'skill',    'views/wemirr/ai/agent/skill/index.vue',   'lucide:sparkles', 2),
(@d_agent, 'MCP连接',    2, 'mcp',      'views/wemirr/ai/agent/mcp/index.vue',     'lucide:wrench',   3),
(@d_agent, '工具管理',   2, 'tool',     'views/wemirr/ai/agent/tool/index.vue',    'lucide:code',     4);
SET @m_workflow = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_agent AND `menu_name` = '工作流编排');
SET @m_skill = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_agent AND `menu_name` = '技能管理');
SET @m_mcp = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_agent AND `menu_name` = 'MCP连接');
SET @m_tool = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_agent AND `menu_name` = '工具管理');

-- 8.5 模型广场下的页面菜单
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_plaza, '模型列表', 2, 'model',      'views/wemirr/ai/model-plaza/index.vue',                'lucide:box',             1),
(@d_plaza, '申请审批', 2, 'apply',      'views/wemirr/ai/model-plaza/apply.vue',                'lucide:clipboard-check', 2),
(@d_plaza, '模型体验', 2, 'experience', 'views/wemirr/ai/model-plaza/experience/index.vue',      'lucide:flask-conical',   3);
SET @m_model = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_plaza AND `menu_name` = '模型列表');
SET @m_model_apply = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_plaza AND `menu_name` = '申请审批');

-- 8.6 按钮权限点：系统管理（与 service_system 各路由的 @has_permission 一一对应）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_user, '用户查询', 3, 'system:user:list',   1),
(@m_user, '用户新增', 3, 'system:user:add',    2),
(@m_user, '用户编辑', 3, 'system:user:edit',   3),
(@m_user, '用户删除', 3, 'system:user:delete', 4),
(@m_user, '分配角色', 3, 'system:user:assign', 5),
(@m_user, '重置密码', 3, 'system:user:reset',  6),
(@m_role, '角色查询', 3, 'system:role:list',   1),
(@m_role, '角色新增', 3, 'system:role:add',    2),
(@m_role, '角色编辑', 3, 'system:role:edit',   3),
(@m_role, '角色删除', 3, 'system:role:delete', 4),
(@m_role, '分配权限', 3, 'system:role:assign', 5),
(@m_menu, '菜单查询', 3, 'system:menu:list',   1),
(@m_menu, '菜单新增', 3, 'system:menu:add',    2),
(@m_menu, '菜单编辑', 3, 'system:menu:edit',   3),
(@m_menu, '菜单删除', 3, 'system:menu:delete', 4),
(@m_dept, '部门查询', 3, 'system:dept:list',   1),
(@m_dept, '部门新增', 3, 'system:dept:add',    2),
(@m_dept, '部门编辑', 3, 'system:dept:edit',   3),
(@m_dept, '部门删除', 3, 'system:dept:delete', 4),
(@m_online, '在线用户查询', 3, 'system:online:list', 1),
(@m_online, '在线用户踢出', 3, 'system:online:kick', 2),
(@m_rate, '限流配置查询', 3, 'system:rate-limit:list',    1),
(@m_rate, '限流策略配置', 3, 'system:rate-limit:edit',    2),
(@m_rate, '限流策略发布', 3, 'system:rate-limit:publish', 3),
(@m_log, '日志查询', 3, 'system:log:list', 1),
(@m_arq, '队列监控查询', 3, 'system:arq:list', 1);

-- 8.7 按钮权限点：模型广场与智能体（与 service_workflow 各路由的 @has_permission 一一对应；
--     模型列表查询 system:model:list 后端暂未挂装饰器，留作页面按钮显隐与后续启用）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_model, '模型列表查询', 3, 'system:model:list',   1),
(@m_model, '模型新增',     3, 'system:model:add',    2),
(@m_model, '模型修改',     3, 'system:model:edit',   3),
(@m_model, '模型删除',     3, 'system:model:delete', 4),
(@m_model_apply, '模型申请审批', 3, 'system:model:audit', 1),
(@m_workflow, '工作流列表', 3, 'workflow:workflow:list',    1),
(@m_workflow, '工作流新增', 3, 'workflow:workflow:add',     2),
(@m_workflow, '工作流编辑', 3, 'workflow:workflow:edit',    3),
(@m_workflow, '工作流删除', 3, 'workflow:workflow:delete',  4),
(@m_workflow, '工作流发布', 3, 'workflow:workflow:publish', 5),
(@m_workflow, '执行记录查询', 3, 'workflow:execution:list', 6),
(@m_workflow, '执行提交',     3, 'workflow:execution:run',  7),
(@m_workflow, '执行取消', 3, 'workflow:execution:cancel', 8),
(@m_workflow, 'API Key 查询', 3, 'workflow:apikey:list',    9),
(@m_workflow, 'API Key 新增', 3, 'workflow:apikey:add',     10),
(@m_workflow, 'API Key 编辑', 3, 'workflow:apikey:edit',    11),
(@m_workflow, 'API Key 删除', 3, 'workflow:apikey:delete',  12),
(@m_workflow, '模板新增', 3, 'workflow:template:add',    13),
(@m_workflow, '模板编辑', 3, 'workflow:template:edit',   14),
(@m_workflow, '模板删除', 3, 'workflow:template:delete', 15),
(@m_skill, '技能列表',     3, 'workflow:skill:list',      1),
(@m_skill, '技能预览',     3, 'workflow:skill:view',      2),
(@m_skill, '技能上传',     3, 'workflow:skill:add',       3),
(@m_skill, '技能包替换',   3, 'workflow:skill:replace',   4),
(@m_skill, '技能重命名',   3, 'workflow:skill:rename',    5),
(@m_skill, '技能启停',     3, 'workflow:skill:edit',      6),
(@m_skill, '技能删除',     3, 'workflow:skill:delete',    7),
(@m_skill, '技能包下载',   3, 'workflow:skill:download',  8),
(@m_skill, '技能文件编辑', 3, 'workflow:skill:editSkill', 9),
(@m_mcp, 'MCP列表',         3, 'workflow:mcp:list',          1),
(@m_mcp, 'MCP新增',         3, 'workflow:mcp:add',           2),
(@m_mcp, 'MCP修改',         3, 'workflow:mcp:edit',          3),
(@m_mcp, 'MCP删除',         3, 'workflow:mcp:delete',        4),
(@m_mcp, '列表页测试连接',  3, 'workflow:mcp:test-external', 5),
(@m_mcp, '编辑页测试连接',  3, 'workflow:mcp:test-internal', 6),
(@m_mcp, 'MCP工具列表',     3, 'workflow:mcp:toolList',      7),
(@m_mcp, 'MCP工具调用',     3, 'workflow:mcp:call',          8),
(@m_tool, '工具列表',    3, 'workflow:tool:list',   1),
(@m_tool, '工具新增',    3, 'workflow:tool:add',    2),
(@m_tool, '工具修改',    3, 'workflow:tool:edit',   3),
(@m_tool, '工具删除',    3, 'workflow:tool:delete', 4),
(@m_tool, '工具测试运行', 3, 'workflow:tool:test',  5);

-- =====================================================================================
-- PART 9  种子：角色授权
-- =====================================================================================

-- ADMIN 授予全部菜单与权限点（新增菜单后重跑本文件即可补授权，uk_role_menu 保证幂等）
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m WHERE r.`role_code` = 'ADMIN';

-- USER 默认只开放：首页（深度探索）+ 模型广场（模型列表/模型体验）。
-- 其余菜单按企业需要到「系统管理-角色管理」里勾，不在初始化里放大权限。
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m
WHERE r.`role_code` = 'USER'
  AND m.`menu_id` IN (@d_home, @d_plaza,
                      (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_home AND `menu_name` = '深度探索'),
                      @m_model,
                      (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_plaza AND `menu_name` = '模型体验'));

-- =====================================================================================
-- PART 10  执行后自查（结果不符合就是中途有语句没跑完，检查客户端有没有吞掉报错）
-- =====================================================================================
-- 表 24 张；菜单 99 行 = 目录 7 + 页面 24 + 按钮权限点 68；ADMIN 授权应覆盖这 99 行
SELECT '表数量（应为 24）' AS `自查项`, COUNT(*) AS `实际` FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE();
SELECT '菜单行数（应为 99）' AS `自查项`, COUNT(*) AS `实际` FROM `tb_menu`;
SELECT '菜单分层（应为 1:7 / 2:24 / 3:68）' AS `自查项`, `menu_type` AS `层级`, COUNT(*) AS `实际`
FROM `tb_menu` GROUP BY `menu_type` ORDER BY `menu_type`;
SELECT 'ADMIN 授权行数（应为 99）' AS `自查项`, COUNT(*) AS `实际`
FROM `tb_role_menu` rm JOIN `tb_role` r ON r.`role_id` = rm.`role_id` WHERE r.`role_code` = 'ADMIN';
