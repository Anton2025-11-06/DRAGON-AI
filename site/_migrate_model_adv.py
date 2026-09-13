# -*- coding: utf-8 -*-
"""tb_model 高级设置列幂等迁移。

新增列：supports_stream / supports_thinking / stream_param / thinking_param / common_params。
model_params 列已存在（复用为 common_params 派生的调用注入字典），此处不新建。
幂等：先查 information_schema 已有列，缺失才 ALTER。
"""
import pymysql

DDL = [
    ("supports_stream", "ALTER TABLE tb_model ADD COLUMN supports_stream TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持流消息 1支持 0不支持'"),
    ("supports_thinking", "ALTER TABLE tb_model ADD COLUMN supports_thinking TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持思考模式 1支持 0不支持'"),
    ("stream_param", "ALTER TABLE tb_model ADD COLUMN stream_param VARCHAR(64) NULL COMMENT '开启流式的参数键名(默认 stream)'"),
    ("thinking_param", "ALTER TABLE tb_model ADD COLUMN thinking_param VARCHAR(64) NULL COMMENT '开启思考的参数键名'"),
    ("common_params", "ALTER TABLE tb_model ADD COLUMN common_params JSON NULL COMMENT '常用参数列表 [{name,default,desc,type}]'"),
]

conn = pymysql.connect(
    host='121.43.156.100', port=3306, user='admin',
    password='Jzh@616294', database='ai2', charset='utf8mb4',
    autocommit=True,
)
cur = conn.cursor()

out = []
cur.execute("""SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA='ai2' AND TABLE_NAME='tb_model'""")
existing = {r[0] for r in cur.fetchall()}
out.append(f"现有列: {sorted(existing)}")

for col, sql in DDL:
    if col in existing:
        out.append(f"[skip] {col} 已存在")
        continue
    cur.execute(sql)
    out.append(f"[ok] 新增列 {col}")

# 复核最终列
cur.execute("""SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA='ai2' AND TABLE_NAME='tb_model'""")
final = {r[0] for r in cur.fetchall()}
out.append(f"迁移后列: {sorted(final)}")
need = {c for c, _ in DDL}
out.append(f"缺失检查: {'全部到位' if need.issubset(final) else (need - final)}")

conn.close()

with open('D:/ai大模型/DRAGON-AI-master/site/_shots/migrate_model_adv.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('DONE')
