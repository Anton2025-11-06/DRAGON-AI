# -*- coding: utf-8 -*-
"""查看用户菜单树中 role 的完整路径与前端路由拼接关系"""
import json

import httpx

BASE = 'http://127.0.0.1:18000'

# 登录
with httpx.Client(timeout=10) as c:
    r = c.post(f'{BASE}/api/login/login', json={'username': 'admin', 'password': 'Admin@123'})
    token = r.json().get('data', {}).get('token')

    headers = {'Authorization': f'Bearer {token}'}
    r2 = c.get(f'{BASE}/api/system/users/me/menus', headers=headers)
    data = r2.json()

# 递归找 role 与 system 顶级
def walk(nodes, parents, out):
    for n in nodes:
        p = parents + [n.get('menu_name', n.get('name', '?'))]
        path = n.get('path')
        comp = n.get('component')
        if 'role' in str(path).lower() or '角色' in str(p[-1]):
            out.append((p, path, comp))
        children = n.get('children') or []
        if children:
            walk(children, p, out)

out_rows = []
if isinstance(data, dict) and 'data' in data:
    tree = data['data']
    walk(tree, [], out_rows)
    out_rows.append(('---TOP---', None, None))
    for n in tree:
        out_rows.append(([n.get('menu_name')], n.get('path'), n.get('component')))

with open('D:/ai大模型/DRAGON-AI-master/site/_shots/menu_tree_role.txt', 'w', encoding='utf-8') as f:
    f.write(json.dumps(data, ensure_ascii=False, indent=1)[:6000])
    f.write('\n\n===== role 定位 =====\n')
    for p, path, comp in out_rows:
        f.write(f"{' > '.join(p)} | path={path} | comp={comp}\n")
print('DONE')