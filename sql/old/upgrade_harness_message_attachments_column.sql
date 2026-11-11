-- =====================================================================================
-- 升级脚本（存量库执行）：深度探索消息表「本轮附件引用」列
-- 对应 v2_init.sql 里 tb_harness_message 的 attachments 列（新装库无需执行本脚本）。
--
-- 背景：深度探索新增文件上传能力——前端把文件上传进沙箱工作区后，发消息时把
--   「文件名 + 工作区绝对路径」作为附件引用随 /chat 带上，落 USER 行；arq worker
--   经 turn_snapshot 取到后，把这些路径拼进给模型的正文提示，让模型用自带的
--   Read/Glob/Grep/Bash 工具自行读取解析（后端不做二进制预解析）。
--   跨进程交接（API → worker）只有 MySQL 这两行，故附件引用必须落库、不能只走请求参数。
--
-- 执行方式（MySQL 客户端连上目标库后执行本文件）：
--   ALTER 幂等性说明：MySQL 5.7+ 不支持 IF NOT EXISTS 的 ADD COLUMN，
--   重复执行会报 Duplicate column（列已存在），忽略即可。
-- =====================================================================================

ALTER TABLE `tb_harness_message`
    ADD COLUMN `attachments` JSON NULL
        COMMENT '本轮附件引用 [{name,path}]（落 USER 行；文件已存沙箱工作区，worker 注入路径提示让模型自行读取解析）'
        AFTER `mounts`;
