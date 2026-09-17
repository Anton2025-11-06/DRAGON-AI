-- =====================================================================================
-- MCP 连接补列迁移：tb_mcp_server
--   新增 description 列：描述这个 MCP 连接是做什么的（编辑/添加/查看页展示）
-- 语句幂等（先查 INFORMATION_SCHEMA 再 PREPARE 执行），可重复执行。
--   旧库执行本文件即可；全新库由 new_init_.sql 建表时直接带上该列。
-- =====================================================================================

SET @ddl = (SELECT IF(
    EXISTS(SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_mcp_server' AND COLUMN_NAME = 'description'),
    'SELECT ''tb_mcp_server.description 已存在，跳过'' AS msg',
    'ALTER TABLE `tb_mcp_server` ADD COLUMN `description` VARCHAR(500) DEFAULT NULL COMMENT ''MCP 描述（这个连接是做什么的）'' AFTER `env`'));
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
