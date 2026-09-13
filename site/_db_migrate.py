# -*- coding: utf-8 -*-
"""远程 ai2 库增量迁移：
1) 建 tb_skill（5.3，与 new_init_.sql 一致）
2) 菜单提升（7.4）：技能管理/MCP连接管理/工具管理 → 智能体一级菜单，删「工具目录」
3) 模型对话 → 模型体验（7.8），指向 experience 页
4) skill 权限点（8.10）+ MCP/工具权限点补漏
5) ADMIN 全量授权 + USER 角色授权模型体验
"""
import pymysql

conn = pymysql.connect(host="121.43.156.100", port=3306, user="admin",
                       password="Jzh@616294", database="ai2", charset="utf8mb4",
                       connect_timeout=10, autocommit=False)
cur = conn.cursor()

SQL_BLOCKS = [
    # ---- 1. tb_skill 建表（与 new_init_.sql 5.3 一致）----
    """
    CREATE TABLE IF NOT EXISTS `tb_skill` (
        `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
        `name` VARCHAR(128) NOT NULL COMMENT '技能名称',
        `code` VARCHAR(128) NOT NULL COMMENT '技能标识（唯一，同时作为存储目录名）',
        `description` VARCHAR(500) DEFAULT NULL COMMENT '技能描述',
        `category` VARCHAR(64) DEFAULT NULL COMMENT '分类',
        `icon` VARCHAR(128) DEFAULT NULL COMMENT '图标',
        `tags` VARCHAR(500) DEFAULT NULL COMMENT '标签 JSON 数组',
        `status` TINYINT NOT NULL DEFAULT 1 COMMENT '0-停用 1-启用',
        `skill_path` VARCHAR(500) DEFAULT NULL COMMENT '展示用目录路径（skills/{code}）',
        `resource_count` INT NOT NULL DEFAULT 0 COMMENT '资源文件数',
        `created_by` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '创建人用户ID',
        `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uk_code` (`code`),
        KEY `idx_name` (`name`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='技能目录表'
    """,
    # ---- 2. 菜单提升（7.4）----
    """
    UPDATE `tb_menu` SET `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) t),
           `sort`=CASE `menu_name` WHEN '技能管理' THEN 2 WHEN 'MCP连接管理' THEN 3 WHEN '工具管理' THEN 4 ELSE `sort` END,
           `icon`=CASE `menu_name` WHEN '技能管理' THEN 'lucide:sparkles' WHEN 'MCP连接管理' THEN 'lucide:wrench' WHEN '工具管理' THEN 'lucide:code' ELSE `icon` END
    WHERE `menu_name` IN ('技能管理','MCP连接管理','工具管理')
      AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具目录' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) t)
    """,
    # 旧库直挂智能体下的兼容（工具管理曾直接挂智能体下）——本库无直挂副本，跳过以免与已提升菜单冲突
    "SELECT 1",
    """
    UPDATE `tb_menu` SET `path`='skill', `component`='views/wemirr/ai/agent/skill/index.vue', `icon`='lucide:sparkles', `sort`=2
    WHERE `menu_name`='技能管理' AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) t)
    """,
    # 删「工具目录」目录节点
    """
    DELETE FROM `tb_menu` WHERE `menu_name`='工具目录' AND `menu_type`=1
      AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) t)
    """,
    # 补齐缺失菜单（幂等：已存在的同名同父则跳过）
    """
    INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '工作流编排', 2, 'workflow', 'views/wemirr/ai/workflow/list/index.vue', 'lucide:workflow', 1),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '技能管理',   2, 'skill',    'views/wemirr/ai/agent/skill/index.vue',    'lucide:sparkles', 2),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), 'MCP连接管理', 2, 'mcp',     'views/wemirr/ai/agent/mcp/index.vue',      'lucide:wrench',    3),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0) m), '工具管理',   2, 'tool',    'views/wemirr/ai/agent/tool/index.vue',     'lucide:code',      4)
    """,
    # ---- 3. 模型对话 → 模型体验（7.8）----
    """
    UPDATE `tb_menu` SET `menu_name`='模型体验', `path`='experience', `component`='views/wemirr/ai/model-plaza/experience/index.vue',
           `icon`='lucide:flask-conical', `sort`=3
    WHERE `menu_name`='模型对话' AND `parent_id`=(SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) t)
    """,
    # 兜底：无「模型对话」时直接种「模型体验」（幂等）
    """
    INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) m), '模型体验', 2, 'experience', 'views/wemirr/ai/model-plaza/experience/index.vue', 'lucide:flask-conical', 3)
    """,
    # ---- 4. 权限点（8.9 / 8.10）----
    """
    INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP列表',     3, 'workflow:mcp:list',   1),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP新增',     3, 'workflow:mcp:add',    2),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP修改',     3, 'workflow:mcp:edit',   3),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP删除',     3, 'workflow:mcp:delete', 4),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='MCP连接管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), 'MCP测试连接', 3, 'workflow:mcp:test',   5),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具列表',   3, 'workflow:tool:list',   1),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具新增',   3, 'workflow:tool:add',    2),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具修改',   3, 'workflow:tool:edit',   3),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具删除',   3, 'workflow:tool:delete', 4),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='工具管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '工具测试运行', 3, 'workflow:tool:test',   5),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='技能管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '技能列表',   3, 'workflow:skill:list',   1),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='技能管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '技能新增',   3, 'workflow:skill:add',    2),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='技能管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '技能修改',   3, 'workflow:skill:edit',   3),
    ((SELECT m.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='技能管理' AND parent_id=(SELECT menu_id FROM tb_menu WHERE menu_name='智能体' AND parent_id=0)) m), '技能删除',   3, 'workflow:skill:delete', 4)
    """,
    # ---- 5. 角色授权 ----
    # ADMIN 全量
    """
    INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
    SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m WHERE r.`role_code`='ADMIN'
    """,
    # USER 角色：模型广场/模型列表/模型体验
    """
    INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
    SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m
    WHERE r.`role_code`='USER'
      AND m.`menu_name` IN ('首页','深度探索','模型广场','模型列表','模型体验')
      AND m.`parent_id` = CASE
            WHEN m.`menu_name` IN ('首页','模型广场') THEN 0
            WHEN m.`menu_name`='深度探索' THEN (SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='首页' AND parent_id=0) t)
            ELSE (SELECT t.menu_id FROM (SELECT menu_id FROM tb_menu WHERE menu_name='模型广场' AND parent_id=0) t)
          END
    """,
]

try:
    for i, sql in enumerate(SQL_BLOCKS):
        cur.execute(sql)
        conn.commit()
        print(f"block {i + 1} OK, affected={cur.rowcount}")
except Exception as e:
    conn.rollback()
    print(f"block {i + 1} FAILED: {e}")
    raise
finally:
    # ---- 验证 ----
    cur2 = conn.cursor()
    cur2.execute("""SELECT menu_id, parent_id, menu_name, menu_type, path, component, sort
                    FROM tb_menu WHERE menu_name IN ('工作流编排','技能管理','MCP连接管理','工具管理','模型体验','模型对话','工具目录')
                    ORDER BY parent_id, sort""")
    print("\n== 迁移后相关菜单 ==")
    for r in cur2.fetchall():
        print(f"  id={r[0]} pid={r[1]} name={r[2]} type={r[3]} path={r[4]} comp={(r[5] or '')[:48]} sort={r[6]}")
    cur2.execute("""SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='ai2' AND TABLE_NAME='tb_skill'""")
    print("tb_skill exists:", cur2.fetchone()[0] == 1)
    cur2.execute("""SELECT menu_name, perm FROM tb_menu WHERE perm LIKE 'workflow:skill:%' OR perm LIKE 'workflow:mcp:%' OR perm LIKE 'workflow:tool:%' ORDER BY perm""")
    print("\n== 权限点 ==")
    for r in cur2.fetchall():
        print(f"  {r[1]} ({r[0]})")
    cur2.execute("""SELECT COUNT(*) FROM tb_role_menu rm JOIN tb_menu m ON m.menu_id=rm.menu_id
                    JOIN tb_role r ON r.role_id=rm.role_id WHERE r.role_code='ADMIN'""")
    print("\nADMIN 授权菜单数:", cur2.fetchone()[0])
    cur2.execute("""SELECT COUNT(*) FROM tb_role_menu rm JOIN tb_menu m ON m.menu_id=rm.menu_id
                    JOIN tb_role r ON r.role_id=rm.role_id WHERE r.role_code='USER'""")
    print("USER 授权菜单数:", cur2.fetchone()[0])
    conn.close()