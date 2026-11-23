-- =====================================================================================
-- user_group_upgrade.sql  用户组（tb_user_group）本次提交的 SQL 增量
--
-- 用途：已经跑过 v2_init.sql（或 sql/old/ 里的历史升级脚本）、库里已有 tb_user_group 时用。
--       全新初始化直接跑 sql/v2_init.sql，不必跑本文件。
-- 幂等：可反复执行。索引与表存在与否用 information_schema 兜（MySQL 不支持改索引的 IF NOT EXISTS）。
-- 必须整份在同一连接里执行（用 @会话变量记判定结果）。
--
-- 本次改了两件结构性的事，代码侧已同步：
--   ① 组名从「全局唯一」改成「同一个创建人名下唯一」——不同人可以建同名组。
--      代码里的判重（usergroup_service._name_taken）原本只按名字比，等于拿全公司的
--      组名当占用，谁建组都要先猜一个没被人用过的名字；现在按 (created_by, group_name) 比。
--      键和代码必须一起改：只改代码会出现「判重说没占、INSERT 撞全局键」，报错还是那句
--      看不出谁占的「用户组名称已存在」。
--   ② 用户组删除改成物理删行（delete_group 直接 DELETE），is_deleted 不再写 1。
--      软删行会继续占着唯一键，所以顺手清掉历史遗留，名字才真释放。
--
-- 三段：① 清历史软删行 → ② 换唯一键 → ③ 执行后自查
-- =====================================================================================

-- ---------------------------------------------------------------------
-- 表存在性判定：空库（还没建 tb_user_group）不该跑本文件，下面的语句全部跳过并提示。
-- ---------------------------------------------------------------------
SET @t_ug = (SELECT COUNT(*) FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_user_group');

-- ---------------------------------------------------------------------
-- ① 清历史软删的用户组
--    先清成员行再清组本身：tb_user_group_member 没有软删列，逐组物理删。
--    这些行在界面上本来就读得到名字、却看不到本体（列表按 is_deleted=0 过滤），
--    属于纯粹的脏数据。指向被删组的授权行（tb_resource_acl）早已软删，无需再动。
-- ---------------------------------------------------------------------
SET @sql = IF(@t_ug = 0,
    "SELECT '本库没有 tb_user_group：空库请改跑 sql/v2_init.sql 全量初始化' AS msg",
    "DELETE m FROM `tb_user_group_member` m JOIN `tb_user_group` g ON g.`group_id` = m.`group_id` WHERE g.`is_deleted` = 1");
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql = IF(@t_ug = 0,
    "SELECT 'tb_user_group 不存在，跳过' AS msg",
    "DELETE FROM `tb_user_group` WHERE `is_deleted` = 1");
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------------
-- ② uk_group_name 从 (group_name) 换成 (created_by, group_name)
--    判定口径：索引第一列（SEQ_IN_INDEX=1）是 created_by 就是新形态，跳过。
--    存量数据一定满足新键——原来的全局唯一是新键的加强版，同一人名下不可能重名，
--    所以这里不需要冲突预处理，直接换。
-- ---------------------------------------------------------------------
SET @k_seq1 = (SELECT COLUMN_NAME FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_user_group'
                 AND INDEX_NAME = 'uk_group_name' AND SEQ_IN_INDEX = 1);

SET @sql = IF(@t_ug = 0,
    "SELECT 'tb_user_group 不存在，跳过换键' AS msg",
    IF(@k_seq1 IS NULL,
       "ALTER TABLE `tb_user_group` ADD UNIQUE KEY `uk_group_name` (`created_by`, `group_name`)",
       IF(@k_seq1 = 'created_by',
          "SELECT 'uk_group_name 已是 (created_by, group_name)，跳过' AS msg",
          "ALTER TABLE `tb_user_group` DROP INDEX `uk_group_name`, ADD UNIQUE KEY `uk_group_name` (`created_by`, `group_name`)")));
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------------
-- ③ 执行后自查：新键的第一列必须是 created_by，且不该再有待软删的行。
--    期望结果：uk_group_name_cols = created_by,group_name；legacy_soft_deleted_rows = 0
--    查不到行 = 索引不存在（说明这是空库，应改跑 v2_init.sql）。
-- ---------------------------------------------------------------------
SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS uk_group_name_cols,
       (SELECT COUNT(*) FROM `tb_user_group` WHERE `is_deleted` = 1) AS legacy_soft_deleted_rows
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_user_group' AND INDEX_NAME = 'uk_group_name'
GROUP BY INDEX_NAME;
