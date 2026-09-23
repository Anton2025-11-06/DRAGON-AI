-- =====================================================================================
-- 【工作流】节点（嵌套调用）表结构迁移
--
-- tb_workflow_execution 新增 2 列：
--   1. parent_exec_id   父执行 ID —— 本条执行是由另一条执行里的【工作流】节点发起的子执行
--   2. parent_node_id   父执行中发起本子执行的节点 ID
--
-- 为什么要落库而不是放进父节点的 node_states：子流程里有审批节点时子执行会落 PAUSED，
-- 父节点跟着挂起（AwaitingApproval + childExecutionId）；父恢复、以及「子执行终态回调
-- 找到等待中的父执行并驱动它续跑」都必须能从子行反查父行，跨进程（arq worker / API
-- 进程）也只有一条 DB 关系可依赖。
--
-- 全部语句幂等（先查 INFORMATION_SCHEMA 再 PREPARE 执行），可重复执行。
-- =====================================================================================

-- 1. parent_exec_id
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'parent_exec_id'),
    'SELECT ''tb_workflow_execution.parent_exec_id 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` ADD COLUMN `parent_exec_id` VARCHAR(36) DEFAULT NULL COMMENT ''父执行ID（工作流节点发起的子执行）'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. parent_node_id
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND COLUMN_NAME = 'parent_node_id'),
    'SELECT ''tb_workflow_execution.parent_node_id 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` ADD COLUMN `parent_node_id` VARCHAR(64) DEFAULT NULL COMMENT ''父执行中发起本子执行的节点ID'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 3. 父执行反查索引（子执行终态回调按 (parent_exec_id, parent_node_id) 定位等待中的父执行）
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_workflow_execution'
             AND INDEX_NAME = 'idx_wf_exec_parent'),
    'SELECT ''tb_workflow_execution.idx_wf_exec_parent 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_workflow_execution` ADD INDEX `idx_wf_exec_parent` (`parent_exec_id`, `parent_node_id`)'));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
