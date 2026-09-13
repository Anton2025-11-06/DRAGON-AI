# -*- coding: utf-8 -*-
"""排查 role 菜单配置：菜单表 + 前端视图文件比对"""
import pymysql

conn = pymysql.connect(
    host='121.43.156.100', port=3306, user='admin',
    password='Jzh@616294', database='ai2', charset='utf8mb4',
)
cur = conn.cursor(pymysql.cursors.DictCursor)

# 1. 系统管理下所有菜单
cur.execute("""SELECT menu_id, parent_id, menu_name, path, component, sort, visible, perm
               FROM tb_menu WHERE parent_id = 7 OR menu_name LIKE '%%角色%%' ORDER BY sort""")
rows = cur.fetchall()

out = []
out.append('=== 菜单表中 role 相关及系统管理(7)下菜单 ===')
for r in rows:
    out.append(f"id={r['menu_id']} pid={r['parent_id']} name={r['menu_name']} path={r['path']} comp={r['component']} sort={r['sort']} visible={r['visible']}")

# 2. 权限点（存在菜单表 perm 字段）
cur.execute("""SELECT perm FROM tb_menu WHERE perm LIKE 'system:role%%' OR perm LIKE '%%role%%'""")
perms = [r['perm'] for r in cur.fetchall()]
out.append('\n=== role 权限点 ===')
out.append(', '.join(perms))

conn.close()

with open('D:/ai大模型/DRAGON-AI-master/site/_shots/role_menu.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')