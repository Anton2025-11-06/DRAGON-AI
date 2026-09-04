use ai;

CREATE TABLE IF NOT EXISTS `tb_user` (
    `user_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    `username` VARCHAR(128) NOT NULL COMMENT '用户名',
    `password` VARCHAR(1000) NOT NULL COMMENT '密码（加密存储）',
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

-- 兼容旧表：若 tb_user 已存在且缺 dept_id 列，则补充
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_user' AND COLUMN_NAME = 'dept_id');
SET @ddl = IF(@col_exists = 0, 'ALTER TABLE `tb_user` ADD COLUMN `dept_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''所属部门ID'' AFTER `real_name`, ADD KEY `idx_dept` (`dept_id`)', 'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;


-- ==================== RBAC ====================
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

-- 兼容旧表：补充 data_scope / is_builtin 列
SET @col2 = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_role' AND COLUMN_NAME = 'data_scope');
SET @ddl2 = IF(@col2 = 0, 'ALTER TABLE `tb_role` ADD COLUMN `data_scope` TINYINT NOT NULL DEFAULT 4 COMMENT ''数据权限范围'' , ADD COLUMN `dept_ids` VARCHAR(1024) DEFAULT NULL, ADD COLUMN `is_builtin` TINYINT NOT NULL DEFAULT 0', 'SELECT 1');
PREPARE stmt2 FROM @ddl2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;

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

-- 兼容旧表：补充 visible 列
SET @col3 = (SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_menu' AND COLUMN_NAME = 'visible');
SET @ddl3 = IF(@col3 = 0, 'ALTER TABLE `tb_menu` ADD COLUMN `visible` TINYINT NOT NULL DEFAULT 1 COMMENT ''是否显示'' AFTER `sort`', 'SELECT 1');
PREPARE stmt3 FROM @ddl3;
EXECUTE stmt3;
DEALLOCATE PREPARE stmt3;

-- 兼容旧表：补充父级+名称唯一索引（依赖 INSERT IGNORE 的幂等种子）
SET @idx_menu = (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_menu' AND INDEX_NAME = 'uk_parent_name');
SET @ddl_menu = IF(@idx_menu = 0, 'ALTER TABLE `tb_menu` ADD UNIQUE KEY `uk_parent_name` (`parent_id`, `menu_name`)', 'SELECT 1');
PREPARE s_menu FROM @ddl_menu;
EXECUTE s_menu;
DEALLOCATE PREPARE s_menu;

CREATE TABLE IF NOT EXISTS `tb_user_role` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `role_id` BIGINT UNSIGNED NOT NULL,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user` (`user_id`),
    KEY `idx_role` (`role_id`),
    UNIQUE KEY `uk_user_role` (`user_id`, `role_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户角色关联表';

-- 兼容旧表：补充用户-角色唯一索引（依赖 INSERT IGNORE 的幂等种子）
SET @idx_ur = (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_user_role' AND INDEX_NAME = 'uk_user_role');
SET @ddl_ur = IF(@idx_ur = 0, 'ALTER TABLE `tb_user_role` ADD UNIQUE KEY `uk_user_role` (`user_id`, `role_id`)', 'SELECT 1');
PREPARE s_ur FROM @ddl_ur;
EXECUTE s_ur;
DEALLOCATE PREPARE s_ur;

CREATE TABLE IF NOT EXISTS `tb_role_menu` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `role_id` BIGINT UNSIGNED NOT NULL,
    `menu_id` BIGINT UNSIGNED NOT NULL,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_role` (`role_id`),
    KEY `idx_menu` (`menu_id`),
    UNIQUE KEY `uk_role_menu` (`role_id`, `menu_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='角色菜单关联表';

-- 兼容旧表：补充角色-菜单唯一索引
SET @idx_rm = (SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tb_role_menu' AND INDEX_NAME = 'uk_role_menu');
SET @ddl_rm = IF(@idx_rm = 0, 'ALTER TABLE `tb_role_menu` ADD UNIQUE KEY `uk_role_menu` (`role_id`, `menu_id`)', 'SELECT 1');
PREPARE s_rm FROM @ddl_rm;
EXECUTE s_rm;
DEALLOCATE PREPARE s_rm;

-- ==================== 部门（数据权限组织单元） ====================
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

-- ==================== 系统操作日志（审计） ====================
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

-- ==================== 种子数据 ====================
-- 角色：ADMIN 超级管理员 / USER 注册默认角色
INSERT INTO `tb_role` (`role_code`, `role_name`, `description`, `data_scope`, `is_builtin`) VALUES ('ADMIN', '超级管理员', '系统内置超管', 1, 1)
ON DUPLICATE KEY UPDATE `role_name` = VALUES(`role_name`), `data_scope` = VALUES(`data_scope`), `is_builtin` = 1;
INSERT INTO `tb_role` (`role_code`, `role_name`, `description`, `data_scope`, `is_builtin`) VALUES ('USER', '普通用户', '注册默认角色', 4, 1)
ON DUPLICATE KEY UPDATE `role_name` = VALUES(`role_name`), `data_scope` = VALUES(`data_scope`), `is_builtin` = 1;

-- 管理员账号：admin / 初始密码 Admin@123 （b32hex 编码，登录后请修改）
INSERT INTO `tb_user` (`username`, `password`, `email`, `real_name`) VALUES ('admin', '85I6QQBE80OJ4CO=', 'admin@ai.local', '系统管理员')
ON DUPLICATE KEY UPDATE `username` = VALUES(`username`);
-- 幂等：仅当 admin 尚未绑定 ADMIN 角色时绑定（uk_user_role 兜底）
INSERT INTO `tb_user_role` (`user_id`, `role_id`)
SELECT u.`user_id`, r.`role_id` FROM `tb_user` u, `tb_role` r
WHERE u.`username` = 'admin' AND r.`role_code` = 'ADMIN'
  AND NOT EXISTS (SELECT 1 FROM `tb_user_role` ur WHERE ur.user_id = u.user_id AND ur.role_id = r.role_id);

-- ==================== 菜单种子（适配 ui-ai 前端 vben 路由规范） ====================
-- 目录 component='BasicLayout'，菜单 component='views/wemirr/xxx/index.vue'

-- 基础目录：幂等更新组件格式与图标（兼容旧种子）
UPDATE `tb_menu` SET `component`='BasicLayout', `icon`='lucide:settings', `path`='/system', `sort`=1 WHERE `menu_name`='系统管理' AND `parent_id`=0;
UPDATE `tb_menu` SET `component`='BasicLayout', `icon`='lucide:book-open', `path`='/kb', `sort`=2 WHERE `menu_name`='知识库' AND `parent_id`=0;
UPDATE `tb_menu` SET `component`='BasicLayout', `icon`='lucide:bot', `path`='/agent', `sort`=3 WHERE `menu_name`='智能体' AND `parent_id`=0;

-- 移除旧“技能中心”目录（技能管理已并入智能体菜单）
DELETE FROM `tb_menu` WHERE `menu_name`='技能中心' AND `parent_id`=0;

-- 新增目录：模型工厂 / 数据集工厂
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
(0, '模型工厂', 1, '/model', 'BasicLayout', NULL, 'lucide:factory', 4),
(0, '数据集工厂', 1, '/dataset', 'BasicLayout', NULL, 'lucide:database', 5);

-- 系统管理下的一级菜单（组件格式更新为 vben 页面路径）
UPDATE `tb_menu` SET `component`='views/wemirr/system/user/index.vue', `icon`='lucide:users' WHERE `menu_name`='用户管理' AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0) t);
UPDATE `tb_menu` SET `component`='views/wemirr/system/auth/role/index.vue', `icon`='lucide:shield' WHERE `menu_name`='角色管理' AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0) t);
UPDATE `tb_menu` SET `component`='views/wemirr/system/auth/menu/index.vue', `icon`='lucide:menu' WHERE `menu_name`='菜单管理' AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0) t);
UPDATE `tb_menu` SET `component`='views/wemirr/system/org/index.vue', `icon`='lucide:building-2' WHERE `menu_name`='部门管理' AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0) t);
UPDATE `tb_menu` SET `component`='views/wemirr/system/log/opt-log.vue', `icon`='lucide:file-text' WHERE `menu_name`='操作日志' AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0) t);

-- 智能体下的一级菜单（对应后端 service_workflow）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '智能体工作流', 2, 'workflow', 'views/wemirr/ai/workflow/list/index.vue', NULL, 'lucide:workflow', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '自主规划智能体', 2, 'agent', 'views/wemirr/ai/agent/config/index.vue', NULL, 'lucide:brain', 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '技能管理', 2, 'skill', 'views/wemirr/ai/agent/skill/index.vue', NULL, 'lucide:sparkles', 3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '工具管理', 2, 'tool', 'views/wemirr/ai/agent/mcp/index.vue', NULL, 'lucide:wrench', 4),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '模型管理', 2, 'model', 'views/wemirr/ai/model/config/index.vue', NULL, 'lucide:box', 5);

-- 知识库下的一级菜单（对应后端 service_rag）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='知识库' AND parent_id=0) m), '知识库文档', 2, 'doc', 'views/wemirr/ai/rag/doc/index.vue', NULL, 'lucide:folder-open', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='知识库' AND parent_id=0) m), '知识库对话', 2, 'chat', 'views/wemirr/ai/chat/rag/index.vue', NULL, 'lucide:messages-square', 2);

-- 模型工厂下的一级菜单（对应后端 service_train / service_inference / service_eval_model / service_notebook）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型工厂' AND parent_id=0) m), '模型训练', 2, 'train', 'views/wemirr/model/train/index.vue', NULL, 'lucide:activity', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型工厂' AND parent_id=0) m), '模型部署', 2, 'deploy', 'views/wemirr/model/deploy/index.vue', NULL, 'lucide:rocket', 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型工厂' AND parent_id=0) m), '模型评测', 2, 'eval', 'views/wemirr/model/eval/index.vue', NULL, 'lucide:gauge', 3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型工厂' AND parent_id=0) m), 'NoteBook开发', 2, 'notebook', 'views/wemirr/model/notebook/index.vue', NULL, 'lucide:terminal', 4),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型工厂' AND parent_id=0) m), '模型归档', 2, 'archive', 'views/wemirr/model/archive/index.vue', NULL, 'lucide:archive', 5);

-- 数据集工厂下的一级菜单（对应后端 service_datasets）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='数据集工厂' AND parent_id=0) m), '数据集管理', 2, 'list', 'views/wemirr/dataset/index.vue', NULL, 'lucide:database', 1);

-- 用户管理按钮权限点
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='用户管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '用户查询', 3, 'system:user:list', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='用户管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '用户新增', 3, 'system:user:add', 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='用户管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '用户编辑', 3, 'system:user:edit', 3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='用户管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '用户删除', 3, 'system:user:delete', 4),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='用户管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '分配角色', 3, 'system:user:assign', 5),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='用户管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '重置密码', 3, 'system:user:reset', 6);

-- 角色管理按钮权限点
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='角色管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '角色查询', 3, 'system:role:list', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='角色管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '角色新增', 3, 'system:role:add', 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='角色管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '角色编辑', 3, 'system:role:edit', 3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='角色管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '角色删除', 3, 'system:role:delete', 4),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='角色管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '分配权限', 3, 'system:role:assign', 5);

-- 菜单管理按钮权限点
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='菜单管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '菜单查询', 3, 'system:menu:list', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='菜单管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '菜单新增', 3, 'system:menu:add', 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='菜单管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '菜单编辑', 3, 'system:menu:edit', 3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='菜单管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '菜单删除', 3, 'system:menu:delete', 4);

-- 部门管理按钮权限点
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='部门管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '部门查询', 3, 'system:dept:list', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='部门管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '部门新增', 3, 'system:dept:add', 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='部门管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '部门编辑', 3, 'system:dept:edit', 3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='部门管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '部门删除', 3, 'system:dept:delete', 4);

-- 操作日志按钮权限点
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='操作日志' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='系统管理' AND parent_id=0)) m), '日志查询', 3, 'system:log:list', 1);

-- ADMIN 角色授予全部菜单权限（幂等，uk_role_menu 兜底）
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m WHERE r.`role_code` = 'ADMIN';

-- 根部门种子数据（数据权限组织单元）
INSERT INTO `tb_dept` (`parent_id`, `dept_name`, `sort`) VALUES (0, '总公司', 1);

-- ==================== 知识库 ====================
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

-- ==================== 技能中心 ====================
-- zip 包安全解压后存储于共享目录 SKILL_SHARE_DIR/{skill_id}_{skill_name}/，目录即加载
CREATE TABLE IF NOT EXISTS `tb_skill` (
    `skill_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `skill_name` VARCHAR(128) NOT NULL COMMENT '技能名称',
    `description` VARCHAR(500) DEFAULT NULL COMMENT '描述（SKILL.md 摘要）',
    `version` VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    `category` VARCHAR(64) DEFAULT NULL COMMENT '分类',
    `skill_path` VARCHAR(500) NOT NULL COMMENT '相对共享目录路径 {skill_id}_{skill_name}',
    `entry` VARCHAR(64) NOT NULL DEFAULT 'main.py' COMMENT '入口文件',
    `file_size` BIGINT NOT NULL DEFAULT 0 COMMENT 'zip 包字节数',
    `owner_id` BIGINT UNSIGNED NOT NULL COMMENT '创建者',
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`skill_id`),
    KEY `idx_owner` (`owner_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='技能表';

-- ==================== 智能体 ====================
CREATE TABLE IF NOT EXISTS `tb_agent` (
    `agent_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `agent_name` VARCHAR(128) NOT NULL COMMENT '智能体名称',
    `description` VARCHAR(500) DEFAULT NULL,
    `system_prompt` VARCHAR(2000) DEFAULT NULL COMMENT '系统提示词',
    `skill_dirs` VARCHAR(1000) DEFAULT NULL COMMENT '技能相对目录列表，逗号分隔（目录即加载）',
    `kb_ids` VARCHAR(1000) DEFAULT NULL COMMENT '关联知识库ID列表，逗号分隔',
    `llm_config` VARCHAR(2000) DEFAULT NULL COMMENT 'LLM 配置 JSON: base_url/api_key/model',
    `owner_id` BIGINT UNSIGNED NOT NULL,
    `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`agent_id`),
    KEY `idx_owner` (`owner_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体表';

CREATE TABLE IF NOT EXISTS `tb_conversation` (
    `conversation_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `agent_id` BIGINT UNSIGNED NOT NULL,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `title` VARCHAR(255) DEFAULT '新对话',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`conversation_id`),
    KEY `idx_agent_user` (`agent_id`, `user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体会话表';

CREATE TABLE IF NOT EXISTS `tb_agent_message` (
    `message_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `conversation_id` BIGINT UNSIGNED NOT NULL,
    `seq` INT NOT NULL DEFAULT 0 COMMENT '会话内序号',
    `role` VARCHAR(16) NOT NULL COMMENT 'user/assistant/tool',
    `content` TEXT NOT NULL,
    `meta_json` VARCHAR(2000) DEFAULT NULL COMMENT '规划过程日志 JSON',
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`message_id`),
    KEY `idx_conv` (`conversation_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体消息表';

CREATE TABLE IF NOT EXISTS `tb_agent_memory` (
    `memory_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id` BIGINT UNSIGNED NOT NULL,
    `agent_id` BIGINT UNSIGNED NOT NULL,
    `memory_type` VARCHAR(16) NOT NULL DEFAULT 'fact' COMMENT 'fact/summary/rule',
    `content` TEXT NOT NULL COMMENT '记忆内容（向量存 Milvus agent_memory 集合）',
    `is_deleted` TINYINT NOT NULL DEFAULT 0,
    `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`memory_id`),
    KEY `idx_user_agent` (`user_id`, `agent_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体跨会话记忆表';

-- ==================== 模型广场（service_system） ====================
CREATE TABLE IF NOT EXISTS `tb_model` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `name` VARCHAR(128) NOT NULL COMMENT '模型名称',
    `category` VARCHAR(32) NOT NULL COMMENT '分类: TEXT_GEN/EMBEDDING/RERANK/MULTIMODAL/IMAGE_GEN/AUDIO_GEN/VIDEO_GEN',
    `provider` VARCHAR(32) NOT NULL COMMENT '提供商: deepseek/qwen/doubao/hunyuan/kimi/openai',
    `model_name` VARCHAR(128) NOT NULL COMMENT '模型标识(API 调用时使用)',
    `base_url` VARCHAR(500) DEFAULT NULL COMMENT '接口基础地址',
    `api_key` VARCHAR(500) DEFAULT NULL COMMENT '管理端密钥',
    `rate_limit_qps` INT NOT NULL DEFAULT 0 COMMENT '每秒并发限制 0=不限',
    `model_params` JSON DEFAULT NULL COMMENT '模型调用参数(JSON 字典: temperature/top_k/extra_body 等)',
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

-- 模型广场顶级目录（幂等）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
(0, '模型广场', 1, '/model-plaza', 'BasicLayout', NULL, 'lucide:boxes', 6);

-- 模型列表菜单（原模型管理改造，幂等挂到模型广场下）
UPDATE `tb_menu` SET `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) t),
       `menu_name`='模型列表', `path`='model', `component`='views/wemirr/ai/model-plaza/index.vue', `icon`='lucide:box'
WHERE `menu_name` IN ('模型列表','模型管理') AND `parent_id` IN ((SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) t), (SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) t));
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) m), '模型列表', 2, 'model', 'views/wemirr/ai/model-plaza/index.vue', NULL, 'lucide:box', 1);

-- 申请审批菜单（仅后台管理可见）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `perm`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) m), '申请审批', 2, 'apply', 'views/wemirr/ai/model-plaza/apply.vue', NULL, 'lucide:clipboard-check', 2);

-- 模型广场按钮权限点（RBAC：system:model:*）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型列表' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0)) m), '模型列表查询', 3, 'system:model:list', NULL, 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型列表' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0)) m), '模型新增', 3, 'system:model:add', NULL, 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型列表' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0)) m), '模型修改', 3, 'system:model:edit', NULL, 3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型列表' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0)) m), '模型删除', 3, 'system:model:delete', NULL, 4),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='申请审批' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0)) m), '模型申请审批', 3, 'system:model:audit', NULL, 1);

-- 普通用户（USER 角色）可见：模型广场目录 + 模型列表（人人可看模型广场，申请审批仅管理员）
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m
WHERE r.`role_code` = 'USER'
  AND m.`menu_name` IN ('模型广场', '模型列表')
  AND m.`parent_id` = CASE WHEN m.`menu_name` = '模型广场' THEN 0
      ELSE (SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) t) END;


