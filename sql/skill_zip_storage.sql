-- =====================================================================================
-- 技能管理：zip 原件存储改造（可重复执行）
--   需求：上传的 zip 统一存入公共存储（本地后端已改为永久保存 / OSS 对象），
--         数据表记录 zip 包文件名 + 存储句柄，不再把 zip 解压成常驻目录。
--   本脚本只补两列；服务层逻辑见 service/service_workflow/services/skill_service.py。
--
--   - zip_file_name     : 上传时的 zip 原始文件名（展示用）
--   - zip_storage_name  : zip 原件在 common_storage 中的文件名（下载/删除句柄）
--
-- 说明：MySQL 的 ADD COLUMN 不支持 IF NOT EXISTS，用信息模式预检查 + prepared 语句实现幂等。
-- =====================================================================================

-- 1. 补列 zip_file_name
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_skill' AND COLUMN_NAME = 'zip_file_name');
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE `tb_skill` ADD COLUMN `zip_file_name` VARCHAR(255) DEFAULT NULL COMMENT ''上传的 zip 包原始文件名'' AFTER `resource_count`',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. 补列 zip_storage_name
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_skill' AND COLUMN_NAME = 'zip_storage_name');
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE `tb_skill` ADD COLUMN `zip_storage_name` VARCHAR(255) DEFAULT NULL COMMENT ''zip 原件在公共存储中的文件名（下载/删除句柄）'' AFTER `zip_file_name`',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 3. 表注释更正（幂等）
ALTER TABLE `tb_skill` COMMENT = '技能目录表（zip 原件存储）';

-- 执行后自检：
--   SHOW COLUMNS FROM tb_skill LIKE 'zip_%';
-- =====================================================================================
