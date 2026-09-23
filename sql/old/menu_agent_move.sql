-- =====================================================================================
-- 智能体菜单调整（可重复执行）
--   1. 「技能管理 / MCP连接 / 工具管理」提升为「智能体」一级菜单，与「工作流编排」同级
--   2. 删除已废弃的「工具目录」目录节点
--   3. 「MCP连接管理」更名为「MCP连接」
--
-- 覆盖三种现状：① 仍挂在 智能体/工具目录 下；② 已经挂在 智能体 下；③ 菜单缺失需补种。
-- 按钮权限（menu_type=3）的 parent_id 指向菜单行本身，改名与移动都不影响，无需迁移。
--
-- 说明：MySQL 不允许 UPDATE/DELETE 的目标表同时出现在子查询里，故所有子查询都套一层
--       派生表 (SELECT ... FROM tb_menu) t 让其物化，这与 new_init_.sql 的写法一致。
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- 1. 把「工具目录」下的全部子菜单整体提升到「智能体」一级
--    （不只点名的三个：目录要删除，残留子菜单也必须有一级位置，否则会变成孤儿菜单）
-- -------------------------------------------------------------------------------------
UPDATE `tb_menu` SET
    `parent_id` = (SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) t),
    `sort` = CASE `menu_name`
                 WHEN '工作流编排'  THEN 1
                 WHEN '技能管理'    THEN 2
                 WHEN 'MCP连接管理' THEN 3
                 WHEN 'MCP连接'     THEN 3
                 WHEN '工具管理'    THEN 4
                 ELSE `sort`
             END
WHERE `parent_id` = (SELECT t.menu_id FROM (
    SELECT menu_id FROM tb_menu
    WHERE menu_name='工具目录' AND menu_type=1
      AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)
) t);

-- -------------------------------------------------------------------------------------
-- 2. 「MCP连接管理」→「MCP连接」（顺带把路径/组件/图标钉成终态）
--    若此句报 1062 重复键，说明同一父级下已存在另一个「MCP连接」行，
--    先用下面的排查语句确认后删掉多余行再重跑：
--      SELECT menu_id, parent_id, menu_name, component FROM tb_menu
--      WHERE menu_name IN ('MCP连接','MCP连接管理');
-- -------------------------------------------------------------------------------------
UPDATE `tb_menu` SET
    `menu_name` = 'MCP连接',
    `path`      = 'mcp',
    `component` = 'views/wemirr/ai/agent/mcp/index.vue',
    `icon`      = 'lucide:wrench',
    `sort`      = 3
WHERE `menu_name` = 'MCP连接管理';

-- -------------------------------------------------------------------------------------
-- 3. 删除已空的「工具目录」目录节点（只按 menu_type=1 删目录，不会碰到页面菜单）
-- -------------------------------------------------------------------------------------
DELETE FROM `tb_menu` WHERE `menu_name` = '工具目录' AND `menu_type` = 1;

-- -------------------------------------------------------------------------------------
-- 4. 补种四个一级菜单（缺失时补齐，已存在时由 uk_parent_name 幂等跳过）
-- -------------------------------------------------------------------------------------
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '工作流编排', 2, 'workflow', 'views/wemirr/ai/workflow/list/index.vue', 'lucide:workflow', 1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '技能管理',   2, 'skill',    'views/wemirr/ai/agent/skill/index.vue', 'lucide:sparkles', 2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), 'MCP连接',    2, 'mcp',      'views/wemirr/ai/agent/mcp/index.vue',     'lucide:wrench',    3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '工具管理',   2, 'tool',     'views/wemirr/ai/agent/tool/index.vue',    'lucide:code',      4);

-- -------------------------------------------------------------------------------------
-- 5. 补种「MCP连接」按钮权限（同名已存在则跳过；权限标识不变，角色授权无需重做）
-- -------------------------------------------------------------------------------------
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP列表',     3, 'workflow:mcp:list',   1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP新增',     3, 'workflow:mcp:add',    2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP修改',     3, 'workflow:mcp:edit',   3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP删除',     3, 'workflow:mcp:delete', 4),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP测试连接', 3, 'workflow:mcp:test',   5);

-- -------------------------------------------------------------------------------------
-- 6. 补种「工具管理」按钮权限（同上，仅在缺失时插入）
-- -------------------------------------------------------------------------------------
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具列表',   3, 'workflow:tool:list',   1),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具新增',   3, 'workflow:tool:add',    2),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具修改',   3, 'workflow:tool:edit',   3),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具删除',   3, 'workflow:tool:delete', 4),
((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具测试运行', 3, 'workflow:tool:test',   5);

-- -------------------------------------------------------------------------------------
-- 执行后自检：期望看到 4 行一级菜单（parent_id=智能体），且没有「工具目录」残留
--   SELECT menu_id, parent_id, menu_name, path, component, sort FROM tb_menu
--   WHERE menu_name IN ('工作流编排','技能管理','MCP连接','工具管理','工具目录')
--   ORDER BY parent_id, sort;
-- -------------------------------------------------------------------------------------
