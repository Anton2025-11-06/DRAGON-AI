-- =====================================================================================
-- orch_upgrade.sql  智能体编排（单/团队/目标/SOP）老库升级脚本
--
-- 用途：已跑过旧版 v2_init.sql、库里已有基础表与菜单，只想补「智能体编排」这一增量时用。
--       全新初始化直接跑 v2_init.sql 即可，不必跑本文件。
--
-- 幂等：可反复执行。列存在与否用 information_schema 兜（MySQL 不支持 ADD COLUMN IF NOT EXISTS），
--       建表用 CREATE TABLE IF NOT EXISTS，菜单/授权用 INSERT IGNORE（靠各自 uk 去重）。
-- 必须整份在同一连接里执行（用 @会话变量记父级 menu_id）。
--
-- 与 v2_init.sql 对齐：PART 7.10 四张表 + tb_harness_message 两列 + PART 9.10 菜单 + PART 10 ADMIN 授权。
--
-- 本文件即「本次提交要做全部 SQL」的单一入口，共五段：
--   ① tb_harness_message 加 orch_mode / orch_config_id 两列（改表）
--   ② 新建 tb_orch_agent / tb_orch_team / tb_orch_goal / tb_orch_sop 四张表（建表）
--   ②b tb_orch_goal 坏结构自愈（早期版本把 updated_by 写成了 DATETIME 默认值且漏 update_time）
--   ③ tb_menu 菜单与权限点 17 行（目录 1 + 页面 4 + 按钮 12）
--   ④ tb_role_menu 把全部菜单重授 ADMIN
--   ⑤ 执行后自查
-- tb_acl 不需要动结构：resource_code 是 VARCHAR(32)，orch_agent/orch_team/orch_goal/orch_sop
-- 四个新编码直接写入即可（授权行由页面「授权」操作产生，不落种子）。
-- =====================================================================================

-- ---------------------------------------------------------------------
-- ① tb_harness_message 增加 orch_mode / orch_config_id 两列（已存在则跳过）
--    存量深度探索轮次取缺省 single/0，行为不变；worker 按两列分派流水线 + 派生 Redis AgentState 键。
--    列位 AFTER `artifacts` 只在 artifacts 列存在时才拼上：更老的库可能还没跑过
--    sql/old/upgrade_harness_message_artifacts_column.sql，硬写 AFTER 会报 1054。
--    表本身不存在（空库）则只打印提示、不执行 ALTER，避免 1146 中断整份脚本。
-- ---------------------------------------------------------------------
SET @t_hm = (SELECT COUNT(*) FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_harness_message');
SET @c_art = (SELECT COUNT(*) FROM information_schema.COLUMNS
              WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_harness_message' AND COLUMN_NAME = 'artifacts');

SET @c_exist = (SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_harness_message' AND COLUMN_NAME = 'orch_mode');
SET @sql = IF(@t_hm = 0,
    "SELECT '本库没有 tb_harness_message：空库请改跑 sql/v2_init.sql 全量初始化' AS msg",
    IF(@c_exist = 0,
       CONCAT("ALTER TABLE `tb_harness_message` ADD COLUMN `orch_mode` VARCHAR(16) NOT NULL DEFAULT 'single' COMMENT '编排模式 single/team/goal/sop（深度探索缺省 single，worker 按它分派流水线）'",
              IF(@c_art = 1, " AFTER `artifacts`", "")),
       "SELECT 'orch_mode 已存在，跳过' AS msg"));
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- orch_mode 在上面已保证存在（或整条跳过），故 AFTER `orch_mode` 安全
SET @c_exist = (SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_harness_message' AND COLUMN_NAME = 'orch_config_id');
SET @sql = IF(@t_hm = 0,
    "SELECT '本库没有 tb_harness_message：空库请改跑 sql/v2_init.sql 全量初始化' AS msg",
    IF(@c_exist = 0,
       "ALTER TABLE `tb_harness_message` ADD COLUMN `orch_config_id` BIGINT NOT NULL DEFAULT 0 COMMENT '编排配置 id（tb_orch_* 主键；深度探索为 0，与 orch_mode 一起派生 Redis AgentState 键）' AFTER `orch_mode`",
       "SELECT 'orch_config_id 已存在，跳过' AS msg"));
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------------
-- ② 四张编排配置表（对齐 service_harness/models/orch_agent.py）
--    统一审计四列：created_by / create_time / updated_by / update_time；is_deleted 软删。
--    行级可见/按钮由 ACL（resource_code=orch_agent/orch_team/orch_goal/orch_sop）驱动，
--    owner_col=created_by、id_col=id（见 resource_guard.RESOURCE_SPECS）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `tb_orch_agent` (
    `id`            BIGINT       NOT NULL AUTO_INCREMENT,
    `name`          VARCHAR(128) NOT NULL COMMENT '智能体名称',
    `purpose`       VARCHAR(500) COMMENT '用途',
    `system_prompt` MEDIUMTEXT   COMMENT '系统提示词（运行时经参数化 build_agent 覆盖默认段）',
    `mounts`        JSON         COMMENT '挂载快照 {skillIds:[],toolIds:[],mcpIds:[],kbIds:[],modelId:int}',
    `status`        INT          NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `created_by`    BIGINT       NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_by`    BIGINT       COMMENT '修改人用户ID',
    `update_time`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `is_deleted`    INT          NOT NULL DEFAULT 0 COMMENT '0-正常 1-已删除',
    PRIMARY KEY (`id`),
    KEY `idx_oa_owner` (`created_by`, `is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体编排-单智能体';

CREATE TABLE IF NOT EXISTS `tb_orch_team` (
    `id`              BIGINT       NOT NULL AUTO_INCREMENT,
    `name`            VARCHAR(128) NOT NULL COMMENT '团队名称',
    `purpose`         VARCHAR(500) COMMENT '用途',
    `leader_agent_id` BIGINT       COMMENT '主智能体（队长）指向 tb_orch_agent 克隆体 id',
    `members`         JSON         COMMENT '成员列表 [{agent_id,index,description}]（index 用于派生子 state 会话键）',
    `config_json`     JSON         COMMENT '团队完整配置快照（成员克隆体配置 + 主标识），不回写单智能体表',
    `status`          INT          NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `created_by`      BIGINT       NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_by`      BIGINT       COMMENT '修改人用户ID',
    `update_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `is_deleted`      INT          NOT NULL DEFAULT 0 COMMENT '0-正常 1-已删除',
    PRIMARY KEY (`id`),
    KEY `idx_ot_owner` (`created_by`, `is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体编排-团队模式';

CREATE TABLE IF NOT EXISTS `tb_orch_goal` (
    `id`                BIGINT       NOT NULL AUTO_INCREMENT,
    `name`              VARCHAR(128) NOT NULL COMMENT '目标名称',
    `purpose`           VARCHAR(500) COMMENT '用途',
    `executor_agent_id` BIGINT       COMMENT '执行者单智能体 id',
    `verifier_agent_id` BIGINT       COMMENT '验证者单智能体 id（最多两个智能体）',
    `config_json`       JSON         COMMENT '目标完整配置快照（执行者/验证者配置 + 标识），不回写单智能体表',
    `status`            INT          NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `created_by`        BIGINT       NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_by`        BIGINT       COMMENT '修改人用户ID',
    `update_time`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `is_deleted`        INT          NOT NULL DEFAULT 0 COMMENT '0-正常 1-已删除',
    PRIMARY KEY (`id`),
    KEY `idx_og_owner` (`created_by`, `is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体编排-目标模式';

CREATE TABLE IF NOT EXISTS `tb_orch_sop` (
    `id`          BIGINT       NOT NULL AUTO_INCREMENT,
    `name`        VARCHAR(128) NOT NULL COMMENT 'SOP 名称',
    `purpose`     VARCHAR(500) COMMENT '用途描述',
    `steps`       JSON         COMMENT '流程节点数组 [{name,description,executor_agent_id,verifier_agent_id,max_attempts,executor_config,verifier_config}]',
    `flow_order`  JSON         COMMENT '流程顺序（与 steps 数组序一致的显式快照）',
    `config_json` JSON         COMMENT 'SOP 完整配置快照（各节点执行者/检查者配置 + 标识），不回写单智能体表',
    `status`      INT          NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `created_by`  BIGINT       NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
    `create_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_by`  BIGINT       COMMENT '修改人用户ID',
    `update_time` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `is_deleted`    INT          NOT NULL DEFAULT 0 COMMENT '0-正常 1-已删除',
    PRIMARY KEY (`id`),
    KEY `idx_os_owner` (`created_by`, `is_deleted`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体编排-SOP模式';

-- ---------------------------------------------------------------------
-- ②b tb_orch_goal 坏结构自愈（只对已经建过表的老库生效，新库跑到这里全是跳过）
--    早期版本的本文件把 goal 的 updated_by 误写成
--      `updated_by` BIGINT NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
--    并且整张表漏了 update_time：严格模式（默认）下 CREATE 会直接 1067 中止、
--    表根本没建成；非严格模式可能已建成坏结构，而 CREATE TABLE IF NOT EXISTS
--    不会改动已存在的表，所以这里判定后补齐，使四张表审计列与 v2_init.sql 一致。
-- ---------------------------------------------------------------------
SET @t_goal = (SELECT COUNT(*) FROM information_schema.TABLES
               WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_orch_goal');

SET @g_ut = (SELECT COUNT(*) FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_orch_goal' AND COLUMN_NAME = 'update_time');
SET @sql = IF(@t_goal = 0,
    "SELECT 'tb_orch_goal 未建成，跳过自愈（上面的 CREATE 会按正确结构建）' AS msg",
    IF(@g_ut = 0,
       "ALTER TABLE `tb_orch_goal` ADD COLUMN `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER `updated_by`",
       "SELECT 'tb_orch_goal.update_time 已存在，跳过' AS msg"));
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- updated_by 被建成非 BIGINT 或带默认值（即当年那行坏定义）时改回可空无默认；
-- DATETIME→BIGINT 的转换值是 20261120 这类数字，纯审计列、无业务含义，不做数据回填。
SET @g_bad = (SELECT COUNT(*) FROM information_schema.COLUMNS
              WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_orch_goal' AND COLUMN_NAME = 'updated_by'
                AND (DATA_TYPE <> 'bigint' OR COLUMN_DEFAULT IS NOT NULL));
SET @sql = IF(@t_goal = 1 AND @g_bad = 1,
    "ALTER TABLE `tb_orch_goal` MODIFY COLUMN `updated_by` BIGINT NULL COMMENT '修改人用户ID'",
    "SELECT 'tb_orch_goal.updated_by 结构正常，跳过' AS msg");
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------------
-- ③ 菜单：智能体编排目录 + 四模式页面 + 每页按钮权限点（RBAC 只管 list/add/grant 三类全局按钮，
--    行级查看/使用/编辑/删除/验证按钮全由 ACL 行内 actions 驱动，不落权限点）。
--    依赖既有「智能体」一级目录（@d_agent）。若下面查不到该目录，@d_agent 会是 NULL，
--    目录行的 parent_id 非空约束会直接报错拦住——说明这是尚未跑过基础菜单初始化的空库，
--    应改跑全新 v2_init.sql，而不是本升级脚本。INSERT IGNORE 靠 tb_menu 唯一键幂等。
-- ---------------------------------------------------------------------
SET @d_agent = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '智能体');

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_agent, '智能体编排', 1, 'orch', 'BasicLayout', 'lucide:network', 5);
SET @d_orch = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_agent AND `menu_name` = '智能体编排');

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_orch, '单智能体', 2, 'single', 'views/wemirr/ai/agent/orch/single/index.vue', 'lucide:user-round', 1);
SET @m_orch_single = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_orch AND `menu_name` = '单智能体');

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_orch_single, '单智能体列表', 3, 'agent:orch:single:list', 1),
(@m_orch_single, '单智能体新增', 3, 'agent:orch:single:add',  2),
(@m_orch_single, '单智能体授权', 3, 'agent:orch:single:grant', 3);

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_orch, '团队模式', 2, 'team', 'views/wemirr/ai/agent/orch/team/index.vue', 'lucide:users', 2);
SET @m_orch_team = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_orch AND `menu_name` = '团队模式');

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_orch_team, '团队模式列表', 3, 'agent:orch:team:list', 1),
(@m_orch_team, '团队模式新增', 3, 'agent:orch:team:add',  2),
(@m_orch_team, '团队模式授权', 3, 'agent:orch:team:grant', 3);

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_orch, '目标模式', 2, 'goal', 'views/wemirr/ai/agent/orch/goal/index.vue', 'lucide:target', 3);
SET @m_orch_goal = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_orch AND `menu_name` = '目标模式');

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_orch_goal, '目标模式列表', 3, 'agent:orch:goal:list', 1),
(@m_orch_goal, '目标模式新增', 3, 'agent:orch:goal:add',  2),
(@m_orch_goal, '目标模式授权', 3, 'agent:orch:goal:grant', 3);

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_orch, 'SOP模式', 2, 'sop', 'views/wemirr/ai/agent/orch/sop/index.vue', 'lucide:list-checks', 4);
SET @m_orch_sop = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_orch AND `menu_name` = 'SOP模式');

INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_orch_sop, 'SOP模式列表', 3, 'agent:orch:sop:list', 1),
(@m_orch_sop, 'SOP模式新增', 3, 'agent:orch:sop:add',  2),
(@m_orch_sop, 'SOP模式授权', 3, 'agent:orch:sop:grant', 3);

-- ---------------------------------------------------------------------
-- ④ 角色授权：ADMIN 授予全部菜单与权限点（含新增编排菜单）。uk_role_menu 保证幂等，
--    重跑只会补齐当前所有菜单，不会重复；USER 角色不在升级脚本里放大权限（按需到角色管理勾）。
-- ---------------------------------------------------------------------
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m WHERE r.`role_code` = 'ADMIN';

-- =====================================================================================
-- 执行后自查
-- =====================================================================================
SELECT 'orch_mode 列（应为 1）' AS `自查项`, COUNT(*) AS `实际` FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_harness_message' AND COLUMN_NAME = 'orch_mode';
SELECT 'orch_config_id 列（应为 1）' AS `自查项`, COUNT(*) AS `实际` FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_harness_message' AND COLUMN_NAME = 'orch_config_id';
SELECT '编排表数量（应为 4）' AS `自查项`, COUNT(*) AS `实际` FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('tb_orch_agent','tb_orch_team','tb_orch_goal','tb_orch_sop');
-- 四张表审计列必须齐 4 列（created_by/create_time/updated_by/update_time），合计应为 16；
-- 少于 16 就是某张表少了审计列（ORM 写入会直接 Unknown column）。
SELECT '编排表审计列齐备（应为 16）' AS `自查项`, COUNT(*) AS `实际` FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME IN ('tb_orch_agent','tb_orch_team','tb_orch_goal','tb_orch_sop')
    AND COLUMN_NAME IN ('created_by','create_time','updated_by','update_time');
SELECT 'updated_by 类型应为 bigint 且无默认值（应为 4）' AS `自查项`, COUNT(*) AS `实际` FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME IN ('tb_orch_agent','tb_orch_team','tb_orch_goal','tb_orch_sop')
    AND COLUMN_NAME = 'updated_by' AND DATA_TYPE = 'bigint' AND COLUMN_DEFAULT IS NULL;
SELECT '编排菜单行数（应为 17：目录1+页面4+按钮12）' AS `自查项`, COUNT(*) AS `实际` FROM `tb_menu`
  WHERE `menu_name` IN ('智能体编排','单智能体','团队模式','目标模式','SOP模式')
     OR `perm` LIKE 'agent:orch:%';
SELECT '编排菜单已授 ADMIN（应为 17）' AS `自查项`, COUNT(*) AS `实际`
  FROM `tb_role_menu` rm JOIN `tb_role` r ON r.`role_id` = rm.`role_id`
  WHERE r.`role_code` = 'ADMIN'
    AND (rm.`menu_id` IN (SELECT `menu_id` FROM `tb_menu` WHERE `menu_name` IN ('智能体编排','单智能体','团队模式','目标模式','SOP模式'))
         OR rm.`menu_id` IN (SELECT `menu_id` FROM `tb_menu` WHERE `perm` LIKE 'agent:orch:%'));
