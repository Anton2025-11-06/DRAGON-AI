-- 模型工具调用能力位：tb_model 新增列（非幂等，重复执行会因列已存在报错）
-- supports_function_call：该模型是否真支持 OpenAI tools / tool_calls 协议。
--   工作流侧双闸门的其中一道：文生文 LLM 节点只有在模型管理登记了本列 = 1 时
--   才允许插入 MCP / 工具 / 工作流（引擎运行时同样按它忽略节点上的 tools）。
ALTER TABLE tb_model ADD COLUMN supports_function_call TINYINT NOT NULL DEFAULT 0 COMMENT '是否支持工具调用 1支持 0不支持';
