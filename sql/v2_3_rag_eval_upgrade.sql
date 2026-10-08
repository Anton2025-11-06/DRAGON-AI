-- =====================================================================================
-- v2.3 知识评测（RAGAS）升级脚本（存量库专用；全新环境直接跑 sql/v2_init.sql 即可，不必跑本文件）
-- =====================================================================================
-- 对应这一轮的产品改动：
--   1) 新增知识评测模块 → 建两张表 tb_rag_eval_run（评测运行：配置快照+状态+平均耗时）、
--      tb_rag_eval_item（逐问答对：召回/生成/五项得分与三段耗时）。
--   2) 菜单：知识库下新增页面「知识评测」（component=views/wemirr/ai/rag/eval/index.vue）
--      + 功能权限点 ai:kb:eval（页面准入 + router 上 @has_permission）。
--   3) ACL 资源动作新增 eval（在代码 resource_guard.RESOURCE_SPECS 里，不动表结构）：
--      授权了某库的 eval 动作，才能在评测页看到这个非本人库并对它跑评测。
--
-- 怎么执行（同一个连接跑完，PART 2 要用 @会话变量记 menu_id，换连接会丢）：
--   mysql -u<user> -p <db> < sql/v2_3_rag_eval_upgrade.sql
-- 重复执行安全：建表用 CREATE TABLE IF NOT EXISTS；菜单/权限点种子一律 INSERT IGNORE
--   （父级 + 名字撞 uk_parent_name 自然跳过）；ADMIN 补授权靠 uk_role_menu 幂等。
-- =====================================================================================

-- =====================================================================================
-- PART 1  建两张评测表（与 sql/v2_init.sql §2.4/§2.5 完全一致，IF NOT EXISTS 可重复跑）
-- =====================================================================================
CREATE TABLE IF NOT EXISTS `tb_rag_eval_run` (
    `run_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `kb_id` BIGINT UNSIGNED NOT NULL COMMENT '被评测的知识库',
    `kb_name` VARCHAR(128) DEFAULT NULL COMMENT '知识库名称冗余（列表展示不回连）',
    `name` VARCHAR(255) DEFAULT NULL COMMENT '本次运行名称',
    `generation_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '生成答案的对话模型 id（text_to_text）',
    `judge_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'RAGAS 裁判模型 id（text_to_text）',
    `embed_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'answer_relevancy 相似度向量模型 id',
    `top_k` INT NOT NULL DEFAULT 5 COMMENT '检索参数快照 top_k',
    `score_threshold` FLOAT NOT NULL DEFAULT 0.2 COMMENT '检索参数快照 得分阈值',
    `retrieval_mode` VARCHAR(16) DEFAULT NULL COMMENT 'VECTOR/KEYWORD/HYBRID，NULL=库默认',
    `with_graph` TINYINT NOT NULL DEFAULT 0 COMMENT '是否开启图谱增强召回',
    `graph_source_chunks` INT NOT NULL DEFAULT 0 COMMENT '图谱一路回捞的原文规模（0=默认）',
    `total_pairs` INT NOT NULL DEFAULT 0 COMMENT '问答对总数',
    `done_pairs` INT NOT NULL DEFAULT 0 COMMENT '已完成条项数（逐对回填递增）',
    `status` VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/RUNNING/DONE/FAILED',
    `avg_latency_ms` INT NOT NULL DEFAULT 0 COMMENT '平均逐对延迟（召回+生成，不含打分）',
    `error` VARCHAR(1000) DEFAULT NULL COMMENT '整体失败原因',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID（运行历史归属判定列）',
    `owner_dept_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '归属部门',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`run_id`),
    KEY `idx_kb` (`kb_id`),
    KEY `idx_owner_status` (`created_by`, `status`),
    KEY `idx_create_time` (`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识评测运行表（RAGAS）';

CREATE TABLE IF NOT EXISTS `tb_rag_eval_item` (
    `item_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `run_id` BIGINT UNSIGNED NOT NULL COMMENT '所属运行（tb_rag_eval_run.run_id）',
    `question` MEDIUMTEXT NOT NULL COMMENT '用户问题',
    `reference` MEDIUMTEXT COMMENT '参考答案（标准答案）',
    `generated_answer` MEDIUMTEXT COMMENT '本次召回后生成的答案',
    `contexts` JSON COMMENT '召回片段 [{content,score,recall,...}]（送 ragas 打分的资料）',
    `faithfulness` FLOAT DEFAULT NULL COMMENT '忠实度',
    `answer_relevancy` FLOAT DEFAULT NULL COMMENT '答案相关性',
    `context_precision` FLOAT DEFAULT NULL COMMENT '上下文精确率',
    `context_recall` FLOAT DEFAULT NULL COMMENT '上下文召回率',
    `answer_correctness` FLOAT DEFAULT NULL COMMENT '答案正确性',
    `took_recall_ms` INT NOT NULL DEFAULT 0 COMMENT '召回耗时',
    `took_generate_ms` INT NOT NULL DEFAULT 0 COMMENT '生成耗时',
    `took_score_ms` INT NOT NULL DEFAULT 0 COMMENT '打分耗时',
    `status` VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/DONE/FAILED',
    `error` VARCHAR(1000) DEFAULT NULL COMMENT '本项失败原因',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`item_id`),
    KEY `idx_run` (`run_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='逐问答对评测结果表（RAGAS）';

-- =====================================================================================
-- PART 2  菜单改造：知识库下新增「知识评测」页 + ai:kb:eval 功能权限点
-- =====================================================================================
SET @d_kb = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '知识库' LIMIT 1);

SELECT IF(@d_kb IS NULL,
          '没有「知识库」一级菜单，先跑 sql/v2_init.sql 的 PART 8.1 再回来',
          '找到「知识库」一级菜单，继续') AS `前置检查`;

-- 2.1 页面菜单（sort=4，排在知识维护/知识检索/图谱检索之后；撞 uk_parent_name 自然 IGNORE）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_kb, '知识评测', 2, 'eval', 'views/wemirr/ai/rag/eval/index.vue', 'lucide:clipboard-check', 4);

SET @m_kb_eval = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '知识评测' LIMIT 1);

-- 2.2 功能权限点：ai:kb:eval（评测页准入 + 后端 eval_router 的 @has_permission）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_kb_eval, '知识评测', 3, 'ai:kb:eval', 1);

-- =====================================================================================
-- PART 3  ADMIN 补授权（与 v2_init.sql PART 9 同一句，uk_role_menu 保证幂等）
-- =====================================================================================
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m WHERE r.`role_code` = 'ADMIN';

-- =====================================================================================
-- PART 4  执行后自查（期望值就写在列名里，不符就是中途有语句被客户端吞了）
-- =====================================================================================
SELECT '评测两张表（应为 2）' AS `自查项`, COUNT(*) AS `实际`
FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('tb_rag_eval_run', 'tb_rag_eval_item');

SELECT '知识库下页面菜单（应含 知识评测）' AS `自查项`,
       GROUP_CONCAT(`menu_name` ORDER BY `sort`) AS `实际`
FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_type` = 2 AND `is_deleted` = 0;

SELECT '评测权限点（应为 1：ai:kb:eval）' AS `自查项`, COUNT(*) AS `实际`
FROM `tb_menu` WHERE `menu_type` = 3 AND `perm` = 'ai:kb:eval';

SELECT 'ADMIN 已授评测菜单（应 >= 2：页面 + 权限点）' AS `自查项`, COUNT(*) AS `实际`
FROM `tb_role_menu` rm
JOIN `tb_role` r ON r.`role_id` = rm.`role_id`
JOIN `tb_menu` m ON m.`menu_id` = rm.`menu_id`
WHERE r.`role_code` = 'ADMIN'
  AND (m.`menu_id` = @m_kb_eval OR m.`perm` = 'ai:kb:eval');
