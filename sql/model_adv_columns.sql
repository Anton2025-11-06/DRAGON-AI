-- 模型高级设置改造：tb_model 新增列（幂等，重复执行会因列已存在报错，请用 site/_migrate_model_adv.py）
-- supports_stream / supports_thinking：能力布尔标记（测试 UI 是否显示开关 + 广场展示）
-- stream_param / thinking_param：声明"开启流式/思考"的参数键名（广场展示 + 调用注入）
-- common_params：常用参数富列表 [{name,default,desc,type}]，展示与编辑用
-- model_params（已存在）：由 common_params 派生的 {name: 转型后default}，common_model 调用时合并注入
ALTER TABLE tb_model ADD COLUMN supports_stream TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持流消息 1支持 0不支持';
ALTER TABLE tb_model ADD COLUMN supports_thinking TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持思考模式 1支持 0不支持';
ALTER TABLE tb_model ADD COLUMN stream_param VARCHAR(64) NULL COMMENT '开启流式的参数键名(默认 stream)';
ALTER TABLE tb_model ADD COLUMN thinking_param VARCHAR(64) NULL COMMENT '开启思考的参数键名';
ALTER TABLE tb_model ADD COLUMN common_params JSON NULL COMMENT '常用参数列表 [{name,default,desc,type}]';
