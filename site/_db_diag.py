# -*- coding: utf-8 -*-
"""远程 ai2 库现状排查：tb_skill 表 / tb_menu 菜单 / 权限点 / 模型体验菜单"""
import pymysql

conn = pymysql.connect(host="121.43.156.100", port=3306, user="admin",
                       password="Jzh@616294", database="ai2", charset="utf8mb4",
                       connect_timeout=8)
try:
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        # 1. tb_skill 是否存在
        cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='ai2' AND TABLE_NAME='tb_skill'")
        print("tb_skill exists:", bool(cur.fetchall()))

        # 2. 菜单表结构 + 相关菜单
        cur.execute("""SELECT menu_id, parent_id, menu_name, menu_type, route_path, component, visible, order_num
                       FROM tb_menu
                       WHERE menu_name IN ('工作流编排','技能管理','MCP连接','工具管理','模型对话','模型体验','工具')
                          OR route_path LIKE '%skill%' OR route_path LIKE '%mcp%' OR route_path LIKE '%tool%'
                       ORDER BY parent_id, order_num""")
        rows = cur.fetchall()
        print(f"\n相关菜单 {len(rows)} 条:")
        for r in rows:
            print(f"  id={r['menu_id']} pid={r['parent_id']} name={r['menu_name']} type={r['menu_type']} "
                  f"route={r['route_path']} comp={r['component']} visible={r['visible']} order={r['order_num']}")

        # 3. 权限点
        cur.execute("SELECT menu_id, parent_id, menu_name, perms FROM tb_menu WHERE perms LIKE 'workflow:skill%' OR perms LIKE 'workflow:mcp%' OR perms LIKE 'workflow:tool%'")
        perms = cur.fetchall()
        print(f"\n权限点 {len(perms)} 条:")
        for r in perms:
            print(f"  id={r['menu_id']} pid={r['parent_id']} name={r['menu_name']} perms={r['perms']}")

        # 4. 顶级菜单（parent_id=0）
        cur.execute("SELECT menu_id, menu_name, route_path, order_num FROM tb_menu WHERE parent_id=0 ORDER BY order_num")
        print("\n顶级菜单:")
        for r in cur.fetchall():
            print(f"  id={r['menu_id']} name={r['menu_name']} route={r['route_path']} order={r['order_num']}")
finally:
    conn.close()