# -*- coding: utf-8 -*-
"""只读 dump 远程 ai2.tb_model 现有行，供设计 7→12 类型 + provider 迁移。"""
import pymysql

conn = pymysql.connect(host="121.43.156.100", port=3306, user="admin",
                       password="Jzh@616294", database="ai2", charset="utf8mb4")
try:
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute("SELECT id,name,category,provider,model_name,base_url,is_direct,status FROM tb_model ORDER BY id")
        rows = cur.fetchall()
        print(f"tb_model rows = {len(rows)}")
        for r in rows:
            print(r)
        # 统计分类/供应商分布
        cur.execute("SELECT category, COUNT(*) c FROM tb_model GROUP BY category")
        print("\n== category 分布 ==")
        for r in cur.fetchall():
            print(r)
        cur.execute("SELECT provider, COUNT(*) c FROM tb_model GROUP BY provider")
        print("\n== provider 分布 ==")
        for r in cur.fetchall():
            print(r)
finally:
    conn.close()
