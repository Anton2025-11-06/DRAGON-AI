-- =====================================================================================
-- 「审批节点 + 指定 executionId 再提交 + 大模型记忆」表结构迁移
-- 设计冻结：docs/workflow-execution-contract.md（表结构与 variables 两段形状见 §3）
--          审批与记忆的业务口径见 docs/workflow-approval-memory.md
--
-- tb_workflow_execution 新增 3 列：
--   1. submit_mode       本轮（最近一次）提交模式：RETRY 全部重跑 / CONTINUE 审批后恢复
--                        （空 = 首次执行，这行还没被 submit 跑过）
--   2. awaiting_node_id  停在审批等待时的那个节点 id（多份待办时记第一个）
--                        列表页据此展示，欠谁审批以 variables 的 pauseState 为准
--   3. graph_hash        图拓扑指纹（节点 id 集合 + 边集合的 sha1 前 16 位）
--                        再提交时与当前图不一致即拒绝，防止按旧状态续跑改过的画布
--
-- 旧列处置：
--   breakpoints —— 断点功能整体废弃（暂停改由审批节点唯一驱动），列已下线（第 6 段 DROP）。
--
-- 记忆不新增列：按需求决策放进 tb_workflow_execution.node_states 里对应节点的
-- JSON 字段（llmMessages），随节点状态一起落库/恢复。
--
-- 全部语句幂等（先查 INFORMATION_SCHEMA 再 PREPARE 执行），可重复执行。
-- =====================================================================================

-- 1. submit_mode
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'submit_mode'),
    'SELECT ''tb_workflow_execution.submit_mode 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` ADD COLUMN `submit_mode` VARCHAR(16) DEFAULT NULL COMMENT ''本轮提交模式 RETRY/CONTINUE，空=首次执行'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. awaiting_node_id
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'awaiting_node_id'),
    'SELECT ''tb_workflow_execution.awaiting_node_id 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` ADD COLUMN `awaiting_node_id` VARCHAR(64) DEFAULT NULL COMMENT ''等待人工审批的节点ID（PAUSED 时非空）'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 3. graph_hash
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'graph_hash'),
    'SELECT ''tb_workflow_execution.graph_hash 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` ADD COLUMN `graph_hash` VARCHAR(16) DEFAULT NULL COMMENT ''图拓扑指纹（节点id+边集合），再提交前漂移校验'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 4. node_states 列注释升级（它是跨轮恢复的权威源，注释要和 variables 区分开）
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'node_states'
             AND COLUMN_COMMENT LIKE '%跨轮恢复的权威源%'),
    'SELECT ''tb_workflow_execution.node_states 注释已是新口径，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` MODIFY COLUMN `node_states` JSON DEFAULT NULL COMMENT ''节点状态聚合（跨轮恢复权威源：input/output/branch/llmMessages/review*）'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 5. variables 列注释改口（它不再是排障快照，而是两段式跨轮状态的唯一载体）
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'variables'
             AND COLUMN_COMMENT LIKE '%两段式跨轮状态%'),
    'SELECT ''tb_workflow_execution.variables 注释已是新口径，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` MODIFY COLUMN `variables` JSON DEFAULT NULL COMMENT ''两段式跨轮状态：roundRequest + pauseState'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 6. breakpoints 列下线（断点功能整体废弃，ORM 已不再映射该列，存量库一并 DROP）
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'breakpoints'),
    'ALTER TABLE `tb_workflow_execution` DROP COLUMN `breakpoints`',
    'SELECT ''tb_workflow_execution.breakpoints 不存在，跳过'' AS msg'));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
