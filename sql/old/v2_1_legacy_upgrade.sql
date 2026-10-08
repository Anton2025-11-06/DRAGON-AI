-- =====================================================================================
-- 存量库升级到 v2 的增量脚本：RAG 三表 + ACL 地基 + 部门组织树列 + 知识库权限点
--
-- 为什么会需要这个文件（云端 ai3 实测结论，全量对账见 test/_out_schema.txt）：
--   1) 缺 PART 1.7/1.8/1.9 三张表 tb_user_group / tb_user_group_member / tb_resource_acl
--      → 行级 ACL（知识库「授权」弹窗、可见范围判定）一查就 1146；
--   2) RAG 三表停在上一代列集合（9 / 13 / 7 列，v2 需要 25 / 26 / 20 列），而且
--      tb_document.status 从 tinyint 变成 VARCHAR(16) 状态机、tb_document_chunk 的
--      is_deleted 换成 available —— 列名与类型都要改，ADD COLUMN 补不出来，只能重建；
--   3) tb_dept 缺 node_type / org_root_id（v2 的组织树列）→ 建库取「创建人归属部门」时
--      org_id 算不出来，数据权限的部门口径整体失真；
--   4) tb_menu 里没有任何 ai:kb:* / ai:doc:* 权限点 → 非 ADMIN 调知识库接口一律 403。
--      ADMIN 走 is_admin 直接放行，所以用超管号自测是看不出来的，必须补种子；
--   5) tb_kb_share 是上一代的库共享表，v2 已改走 tb_resource_acl，代码里零引用
--      （处置语句放在 PART 6，注释状态，确认无历史数据后再手工放开）。
--
-- 怎么执行（同一个连接跑完，PART 4 要用 @会话变量记 menu_id，换连接会丢）：
--   mysql -h <host> -P 3306 -u <user> -p --default-character-set=utf8mb4 ai3 < sql/v2_1_legacy_upgrade.sql
--
-- 重复执行安全：建表 IF NOT EXISTS；ALTER 前先查 information_schema；种子一律 INSERT IGNORE。
-- ⚠️ PART 2 的重建带行数闸门：三张 RAG 表里有任意一行就**不会 DROP**，只在末尾自查里
--    如实报「列集合仍不一致」，此时请人工按 SPEC §10 导出原件后再迁，别让脚本替你删数据。
-- =====================================================================================

-- =====================================================================================
-- PART 1  ACL 地基（照抄 sql/v2_init.sql PART 1.7 / 1.8 / 1.9 的 DDL，一字不差）
-- =====================================================================================

CREATE TABLE IF NOT EXISTS `tb_user_group` (
    `group_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `group_name` VARCHAR(64) NOT NULL COMMENT '用户组名称',
    `description` VARCHAR(255) DEFAULT NULL COMMENT '描述',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`group_id`),
    UNIQUE KEY `uk_group_name` (`group_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户组表（ACL 授权主体）';

CREATE TABLE IF NOT EXISTS `tb_user_group_member` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `group_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_group_user` (`group_id`, `user_id`),
    KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户组成员表';

CREATE TABLE IF NOT EXISTS `tb_resource_acl` (
    `acl_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `resource_code` VARCHAR(32) NOT NULL COMMENT '资源类型编码 knowledge_base/document/workflow/workflow_template/tool/skill/mcp',
    `resource_id` BIGINT UNSIGNED NOT NULL COMMENT '资源实例主键',
    `grantee_type` TINYINT NOT NULL COMMENT '授权主体：1-用户 2-角色 3-部门 4-用户组 5-全员',
    `grantee_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '主体ID，全员时为0',
    `dept_include_sub` TINYINT NOT NULL DEFAULT 0 COMMENT '仅部门授权：0-仅本部门 1-含下级部门',
    `action` VARCHAR(16) NOT NULL COMMENT '动作码，合法取值以 common/common_permission/resource_guard.py 的 RESOURCE_SPECS 为准；长度不得超 16',
    `expire_time` DATETIME DEFAULT NULL COMMENT '过期时间，NULL=永久（过期不定时清理，判定即失效）',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '授权人（需持有该资源的 share）',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '更新人用户ID',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`acl_id`),
    UNIQUE KEY `uk_acl_row` (`resource_code`, `resource_id`, `grantee_type`, `grantee_id`, `action`, `dept_include_sub`),
    KEY `idx_acl_target` (`resource_code`, `resource_id`),
    KEY `idx_acl_grantee` (`grantee_type`, `grantee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='资源实例 ACL 表（一行=一个主体对一个实例的一个动作）';

-- =====================================================================================
-- PART 2  RAG 三表重建（行数闸门：只要有一行就不动，绝不替你删数据）
-- =====================================================================================

SET @rag_rows = (SELECT COUNT(*) FROM `tb_knowledge_base`)
              + (SELECT COUNT(*) FROM `tb_document`)
              + (SELECT COUNT(*) FROM `tb_document_chunk`);
-- 顺序：先删子（chunk）再删父（document），最后删库表；没有外键约束也按这个顺序，读起来不歧义
SET @ddl = IF(@rag_rows = 0,
              'DROP TABLE IF EXISTS `tb_document_chunk`, `tb_document`, `tb_knowledge_base`',
              'DO 0');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

-- 2.1 知识库表（三种类型共用一张表；kb_type 与向量模型一经创建永久锁定）
CREATE TABLE IF NOT EXISTS `tb_knowledge_base` (
    `kb_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `kb_name` VARCHAR(128) NOT NULL COMMENT '知识库名称',
    `description` VARCHAR(500) DEFAULT NULL COMMENT '描述',
    `kb_type` VARCHAR(16) NOT NULL DEFAULT 'doc' COMMENT '类型：doc-文档问答 image-图片搜索 audio_video-音视频搜索。创建后不可改——存储链路与向量空间按类型分叉',
    `org_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '组织隔离标识，随切片写入 ES、随实体写入 Neo4j。建库时取创建人的归属部门 id（无配置兜底：没有部门直接拒绝建库），创建后不可改',
    `embedding_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '向量模型 id（tb_model.id）。选定后永久锁定：换模型等于换向量空间，历史切片全部作废',
    `embedding_dim` INT NOT NULL DEFAULT 1024 COMMENT '向量维度，建库时按模型能力写入并锁定（三类知识库统一 1024、同一空间）',
    `rerank_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '重排序模型 id，0=不启用（建库页已不再提供该配置项，列保留兼容存量库）',
    `chat_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '媒体理解与摘要模型 id（audio_video 型必需）；doc 型已拆到 extract_model_id / image_model_id，本列仅作存量库回退',
    `extract_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '知识图谱实体抽取模型 id（仅 doc 型且 graph_enabled=1 时必填），0=沿用 chat_model_id',
    `image_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '图片理解模型 id（仅 doc 型且 parse_config.image_understand=true 时必填）',
    `parser_engine` VARCHAR(16) NOT NULL DEFAULT 'native' COMMENT '解析引擎：native/docling/mineru（仅 doc 型生效，image/audio_video 型不解析文档）',
    `parse_config` JSON COMMENT '解析与预处理配置 {remove_toc,remove_header_footer,image_understand} + 引擎专有参数，默认值见 rag_constant.PARSE_OPTION_DEFAULTS',
    `chunk_config` JSON COMMENT '分块配置 {strategy,chunk_size,chunk_overlap,delimiter,regex_pattern,max_chars,keep_table_header,context_augment,title_path}，默认值见 rag_constant.CHUNK_CONFIG_DEFAULTS',
    `retrieve_config` JSON COMMENT '检索配置 {top_k,score_threshold,vector_similarity_weight,rerank,keyword_boost}，默认值见 rag_constant.RETRIEVE_DEFAULTS',
    `graph_enabled` TINYINT NOT NULL DEFAULT 0 COMMENT '知识图谱库级开关：仅 doc 型可开；开了会在解析完成后自动投递图谱抽取任务，也可在文档列表勾选文档手动点「构建图谱」重跑',
    `doc_count` INT NOT NULL DEFAULT 0 COMMENT '文档数（列表页展示，增删后由 service 重算）',
    `chunk_count` INT NOT NULL DEFAULT 0 COMMENT '切片数',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '保留列（启用/停用功能已取消，代码不再读写，默认恒为 1）',
    `owner_dept_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '归属部门 id，仅展示与统计用；数据权限范围按 created_by 反查部门现算（resource_guard.build_visible_cond）',
    `version` INT NOT NULL DEFAULT 1 COMMENT '配置版本号，每次修改 +1。解析任务带着它跑，避免「改到一半的配置」被写进切片',
    `metadata` JSON COMMENT '扩展元数据（前端透传，不参与解析与检索逻辑）',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID（ACL 归属人判定列）',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '更新人用户ID',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`kb_id`),
    KEY `idx_type` (`kb_type`, `is_deleted`),
    KEY `idx_owner` (`created_by`),
    KEY `idx_dept` (`owner_dept_id`),
    KEY `idx_name` (`kb_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库表（doc/image/audio_video 三种类型共用）';

-- 2.2 知识库文档表（原件在公共存储，本表存元数据 + 状态机；进度在 Redis）
CREATE TABLE IF NOT EXISTS `tb_document` (
    `doc_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `kb_id` BIGINT UNSIGNED NOT NULL COMMENT '所属知识库（ES/Neo4j 的隔离维度，检索一律先过 kb_id 白名单）',
    `doc_name` VARCHAR(255) NOT NULL COMMENT '文档标题，列表页可改（接口 DTO 里叫 title）',
    `file_name` VARCHAR(255) DEFAULT NULL COMMENT '上传时的原始文件名',
    `file_path` VARCHAR(500) NOT NULL COMMENT '原件在公共存储中的 key（rag/raw/{kb_id}/{doc_id}.{ext}）',
    `file_ext` VARCHAR(16) DEFAULT NULL COMMENT '扩展名（小写、不含点），决定解析链路与分块默认策略',
    `file_size` BIGINT NOT NULL DEFAULT 0 COMMENT '字节数',
    `content_type` VARCHAR(128) DEFAULT NULL COMMENT 'MIME 类型（页面 contentType）',
    `media_type` VARCHAR(16) DEFAULT NULL COMMENT 'text/image/audio_video，与库 kb_type 同族，列表按类型决定预览形态',
    `parser_engine` VARCHAR(16) DEFAULT NULL COMMENT '本次实际使用的解析引擎（自动选择也回填结果，便于排查）',
    `parse_version` INT NOT NULL DEFAULT 0 COMMENT '产出这份解析结果时知识库的 version；与库当前 version 不一致即代表「配置已改，需要重解析」',
    `sidecar_path` VARCHAR(500) DEFAULT NULL COMMENT '解析产物 blocks.jsonl 的存储 key（重跑分块不必重新解析，也供全文预览与溯源）',
    `status` VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PARSING/ANALYZING/PROCESSING/PROCESSED/FAILED，合法流转见 rag_constant.DOC_STATUS_TRANSITIONS',
    `vectorized` TINYINT NOT NULL DEFAULT 0 COMMENT '向量已写入 ES：0-否 1-是。与 status 分开记，重切分或清向量可单独回退这一位',
    `graph_state` TINYINT NOT NULL DEFAULT 0 COMMENT '图谱构建态：0-未构建 1-构建中 2-已构建 3-失败（仅 doc 型且库开图谱；接口 DTO 的 graphed = 本列等于 2）',
    `chunk_count` INT NOT NULL DEFAULT 0 COMMENT '切片数',
    `page_count` INT NOT NULL DEFAULT 0 COMMENT '页数（PDF/PPT），非分页文档为 0',
    `media_duration` INT NOT NULL DEFAULT 0 COMMENT '音视频时长（秒），其他类型为 0',
    `media_summary` MEDIUMTEXT COMMENT '音视频理解摘要（audio_video 型：这段摘要就是被检索命中的正文，不做抽帧与 ASR）',
    `task_id` VARCHAR(64) DEFAULT NULL COMMENT '最近一次 ARQ 任务号（进度轮询与取消用）',
    `error_msg` VARCHAR(1000) DEFAULT NULL COMMENT '失败原因（status=FAILED 时给到页面）',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID（ACL 归属人判定列）',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '更新人用户ID',
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`doc_id`),
    KEY `idx_kb_status` (`kb_id`, `status`),
    KEY `idx_owner` (`created_by`),
    KEY `idx_task` (`task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库文档表（原件在公共存储，本表存元数据与状态机）';

-- 2.3 文档切片表（切片的完整正文只在这里；ES 里的 content 是为检索准备的副本）
--   本表是「可再生产物」：构建向量按 doc_id 整批物理替换，不做软删（v2_init.sql §2.3 同口径）
CREATE TABLE IF NOT EXISTS `tb_document_chunk` (
    `chunk_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `kb_id` BIGINT UNSIGNED NOT NULL COMMENT '冗余存储：按库统计/清理不必回连文档表',
    `doc_id` BIGINT UNSIGNED NOT NULL,
    `chunk_index` INT NOT NULL DEFAULT 0 COMMENT '切片序号。与 kb_id/doc_id 拼成 ES 文档 _id：{kb_id}_{doc_id}_{chunk_index}，重跑幂等覆盖',
    `chunk_type` VARCHAR(16) NOT NULL DEFAULT 'text' COMMENT 'text/image/audio_video（ES 侧同值，多模态检索按类型过滤）',
    `content` MEDIUMTEXT NOT NULL COMMENT '切片正文（表格已渲染成文本，图片块存大模型描述）',
    `embed_text` MEDIUMTEXT COMMENT '实际送向量模型的文本（面包屑 + 正文 + 前后文补齐）。与 content 分开存，页面预览看到的才是原文',
    `title_path` VARCHAR(500) DEFAULT NULL COMMENT '标题层级面包屑（strategy=title 时的归属路径）',
    `sheet_name` VARCHAR(128) DEFAULT NULL COMMENT 'Excel 工作表名',
    `page_num` INT NOT NULL DEFAULT 0 COMMENT '起始页码，0=非分页文档',
    `block_id` VARCHAR(64) DEFAULT NULL COMMENT '来源解析块 id，可对回 sidecar 里的版面信息',
    `media_url` VARCHAR(500) DEFAULT NULL COMMENT '图片/音视频在公共存储中的 key（ES 只存这个 URL，不存二进制）',
    `media_type` VARCHAR(32) DEFAULT NULL COMMENT '媒体 MIME 类型',
    `token_count` INT NOT NULL DEFAULT 0 COMMENT 'token 数（软预算与统计）',
    `available` TINYINT NOT NULL DEFAULT 1 COMMENT '0-人工停用（不删数据，检索直接排除）1-可用',
    `vectorized` TINYINT NOT NULL DEFAULT 0 COMMENT '本切片向量已写入 ES：0-否 1-是（批量重向量化的断点续跑依据）',
    `extra` JSON COMMENT '扩展（表头与行区间、图片位置框等，页面按需展开）',
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID（继承所属文档的归属人）',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`chunk_id`),
    UNIQUE KEY `uk_doc_chunk` (`doc_id`, `chunk_index`),
    KEY `idx_kb` (`kb_id`),
    KEY `idx_kb_type` (`kb_id`, `chunk_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='文档切片表（分块正文，ES 向量的 MySQL 权威副本）';

-- =====================================================================================
-- PART 3  部门组织树列（tb_dept 缺 node_type / org_root_id，逐列预判后再 ALTER）
-- =====================================================================================

SET @ddl = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'tb_dept' AND COLUMN_NAME = 'node_type') = 0,
              'ALTER TABLE `tb_dept` ADD COLUMN `node_type` TINYINT NOT NULL DEFAULT 1 COMMENT ''1-普通部门 2-法人组织（集团总部/子公司/板块）''',
              'DO 0');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

SET @ddl = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'tb_dept' AND COLUMN_NAME = 'org_root_id') = 0,
              'ALTER TABLE `tb_dept` ADD COLUMN `org_root_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''所属组织根dept_id（自己或最近的 node_type=2 祖先），建改部门时就地重算整棵子孙'' AFTER `node_type`, ADD KEY `idx_org_root` (`org_root_id`)',
              'DO 0');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

SET @ddl = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'tb_dept' AND COLUMN_NAME = 'created_by') = 0,
              'ALTER TABLE `tb_dept` ADD COLUMN `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''创建人用户ID''',
              'DO 0');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

SET @ddl = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'tb_dept' AND COLUMN_NAME = 'update_by') = 0,
              'ALTER TABLE `tb_dept` ADD COLUMN `update_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''更新人用户ID''',
              'DO 0');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

-- 回填：补列时 org_root_id 全是 0，而「根部门」本身就是组织根。
-- 把没有父部门（parent_id=0）的节点标成法人组织，并把它的 org_root_id 指到自己，
-- 子孙节点按最近的 node_type=2 祖先重算（层级很浅，两条 UPDATE 收敛，不在这里爬树）。
UPDATE `tb_dept` SET `node_type` = 2 WHERE `parent_id` = 0;
UPDATE `tb_dept` SET `org_root_id` = `dept_id` WHERE `parent_id` = 0;
UPDATE `tb_dept` c JOIN `tb_dept` p ON p.`dept_id` = c.`parent_id`
   SET c.`org_root_id` = IF(p.`node_type` = 2, p.`dept_id`, p.`org_root_id`)
 WHERE c.`parent_id` <> 0 AND c.`org_root_id` = 0;

-- =====================================================================================
-- PART 4  知识库权限点种子（v2_init.sql PART 8.3 / 8.8 / 8.9 的知识库子集）
--   只补「代码在校验而库里没有」的这些行；其余菜单一律不动（ALTER 过的排序不会被冲掉）
--   菜单名沿用本轮产品改动后的口径：知识维护 / 知识检索 / 图谱检索（旧名「知识库文档」等
--   的存量行由 sql/v2_2_kb_upgrade.sql 负责改名，本段只保证新环境一次种对）
-- =====================================================================================

SET @d_kb = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '知识库' LIMIT 1);

-- 4.1 缺知识库下三个页面菜单（知识检索/图谱检索两个独立页的接口挂各自的准入权限点）
SET @ddl = IF(@d_kb IS NULL,
              'SELECT ''没有「知识库」一级菜单，先跑 sql/v2_init.sql 的 PART 8.1 再回来'' AS `提示`',
              'DO 0');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_kb, '知识维护', 2, 'doc',      'views/wemirr/ai/rag/doc/index.vue',      'lucide:folder-open', 1),
(@d_kb, '知识检索', 2, 'retrieve', 'views/wemirr/ai/rag/retrieve/index.vue', 'lucide:scan-search', 2),
(@d_kb, '图谱检索', 2, 'graph',    'views/wemirr/ai/rag/graph/index.vue',    'lucide:network',     3);

SET @m_kb_doc = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '知识维护' LIMIT 1);
SET @m_kb_retrieve = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '知识检索' LIMIT 1);
SET @m_kb_graph = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '图谱检索' LIMIT 1);

-- 4.2 按钮/功能权限点：与 service_rag 各路由的 @has_permission 一一对应
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_kb_doc,   '知识库授权', 3, 'ai:kb:grant',  1),
(@m_kb_doc,   '文档授权',   3, 'ai:doc:grant', 2),
(@m_kb_doc,   '知识库查询', 3, 'ai:kb:list',   3),
(@m_kb_doc,   '新建知识库', 3, 'ai:kb:add',    4),
(@m_kb_retrieve, '知识检索', 3, 'ai:kb:search', 1),
(@m_kb_graph, '图谱检索', 3, 'ai:kb:graph',     1);

-- 4.3 ADMIN 补授权（与 v2_init.sql PART 9 同一句，uk_role_menu 保证幂等）
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m WHERE r.`role_code` = 'ADMIN';

-- =====================================================================================
-- PART 5  执行后自查（每段的期望值就写在列名里，不符就是中途有语句被客户端吞了）
-- =====================================================================================

SELECT 'ACL/用户组三表（应为 3）' AS `自查项`, COUNT(*) AS `实际`
FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('tb_user_group', 'tb_user_group_member', 'tb_resource_acl');

SELECT 'RAG 三表列数（应为 kb 27 / doc 26 / chunk 20）' AS `自查项`, TABLE_NAME AS `表`, COUNT(*) AS `列数`
FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('tb_knowledge_base', 'tb_document', 'tb_document_chunk')
GROUP BY TABLE_NAME ORDER BY TABLE_NAME;

SELECT 'org_root_id 已回填的部门（应等于部门总数）' AS `自查项`,
       (SELECT COUNT(*) FROM `tb_dept` WHERE `org_root_id` > 0) AS `实际`,
       (SELECT COUNT(*) FROM `tb_dept`) AS `总数`;

SELECT '知识库权限点（应为 6）' AS `自查项`, COUNT(*) AS `实际` FROM `tb_menu`
WHERE `perm` IN ('ai:kb:list', 'ai:kb:add', 'ai:kb:search', 'ai:kb:graph',
                 'ai:kb:grant', 'ai:doc:grant');

SELECT 'ADMIN 未授权菜单（应为 0）' AS `自查项`, COUNT(*) AS `实际` FROM `tb_menu` m
WHERE NOT EXISTS (SELECT 1 FROM `tb_role_menu` rm JOIN `tb_role` r ON r.`role_id` = rm.`role_id`
                  WHERE rm.`menu_id` = m.`menu_id` AND r.`role_code` = 'ADMIN');

-- =====================================================================================
-- PART 6  上一代残留表 tb_kb_share（v2 已改走 tb_resource_acl，代码零引用）
--   确认没有你要保的历史数据后，再手工放开这一句；放开的时机由你定，脚本不替你删。
-- =====================================================================================
-- DROP TABLE IF EXISTS `tb_kb_share`;
