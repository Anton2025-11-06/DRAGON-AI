# -*- coding: utf-8 -*-
"""查看 tb_menu 表结构 + 全部菜单（带直接父菜单名），定位菜单层级与页面字段"""
import pymysql

conn = pymysql.connect(host="121.43.156.100", port=3306, user="admin",
                       password="Jzh@616294", database="ai2", charset="utf8mb4",
                       connect_timeout=8)
try:
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("DESCRIBE tb_menu")
        print("== tb_menu 字段 ==")
        for r in cur.fetchall():
            print(f"  {r['Field']:24s} {r['Type']}")
        print()
        cur.execute("""SELECT m.menu_id, m.parent_id, m.menu_name, m.menu_type,
                              m.path, m.component, m.perm, m.visible, m.sort,
                              p.menu_name AS parent_name
                       FROM tb_menu m LEFT JOIN tb_menu p ON p.menu_id = m.parent_id
                       ORDER BY m.menu_id""")
        print("== 全部菜单（含父名，按 id）==")
        for r in cur.fetchall():
            print(f"  id={r['menu_id']:4d} pid={r['parent_id']:4d} type={r['menu_type']} "
                  f"name={r['menu_name']} path={r['path']} comp={(r['component'] or '')[:55]} "
                  f"perm={r['perm'] or ''} visible={r['visible']} sort={r['sort']} | {r['parent_name']}")
finally:
    conn.close()