-- =====================================================================================
-- 升级脚本（存量库执行）：深度探索消息表「本轮最终输出物引用」列
-- 对应 v2_init.sql 里 tb_harness_message 的 artifacts 列（新装库无需执行本脚本）。
--
-- 背景：深度探索新增「最终输出物文件单 + 自动预览」能力——智能体在收尾时调用
--   present_output 工具显式登记本轮交付给用户的文件（只填最终产物，不填中间/临时
--   文件），arq worker 在事件循环里识别这次调用、发 turn.artifacts SSE 帧并把清单
--   随 ASSISTANT 行落库；前端据此在对话里渲染可点击文件卡片并自动在右侧工作区预览。
--   跨进程交接（worker → API 回放）只有 MySQL 这两行，故输出物引用必须落库。
--
-- 执行方式（MySQL 客户端连上目标库后执行本文件）：
--   ALTER 幂等性说明：MySQL 5.7+ 不支持 IF NOT EXISTS 的 ADD COLUMN，
--   重复执行会报 Duplicate column（列已存在），忽略即可。
-- =====================================================================================

ALTER TABLE `tb_harness_message`
    ADD COLUMN `artifacts` JSON NULL
        COMMENT '本轮最终输出物引用 [{name,path,description}]（落 ASSISTANT 行；present_output 工具登记、worker 捕获，前端据此渲染文件卡片并自动预览）'
        AFTER `attachments`;
