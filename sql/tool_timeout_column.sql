-- =====================================================================================
-- B 批（工具能力改造）表结构迁移：tb_tool
--   1. 新增 timeout 列：工具调用超时（毫秒），工作流「工具」节点未显式配置时的默认值
--   2. parameters_schema 由 VARCHAR(2000) 扩为 TEXT：参数定义改成与工作流代码节点同构的
--      inputs 数组后，条目多、带中文说明，2000 字符容易截断
-- 全部语句幂等（先查 INFORMATION_SCHEMA 再 PREPARE 执行），可重复执行。
-- =====================================================================================

-- 1. timeout 列
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_tool' AND COLUMN_NAME = 'timeout'),
    'SELECT ''tb_tool.timeout 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_tool` ADD COLUMN `timeout` INT NOT NULL DEFAULT 10000 COMMENT ''执行超时（毫秒），工具节点未配置时取此值'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. parameters_schema 扩为 TEXT（仅当前不是 text 时才改，避免重复重建表）
SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_tool'
             AND COLUMN_NAME = 'parameters_schema' AND DATA_TYPE = 'text'),
    'SELECT ''tb_tool.parameters_schema 已是 TEXT，跳过'' AS msg',
    'ALTER TABLE `tb_tool` MODIFY COLUMN `parameters_schema` TEXT DEFAULT NULL COMMENT ''参数定义 JSON（{parameters:[{name,type,required,description,default}]}）'''));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
