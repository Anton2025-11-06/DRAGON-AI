-- =====================================================================================
-- 升级脚本（存量库执行）：知识评测「图谱增强深度/规模」快照列
-- 对应 v2_init.sql 里 tb_rag_eval_run 的 graph_depth / graph_scale 两列（新装库无需执行本脚本）。
--
-- 背景：评测发起时图谱增强那一路原先只有开关与反查开关，深度/规模沿用后端默认
-- （深度 2 / 每路 10 条，取值口径见 common_constants/rag_constant.py 的
--  KG_RETRIEVE_DEFAULT_DEPTH / KG_RETRIEVE_DEFAULT_SCALE）。现在评测页可显式配置并落快照，
--  与 top_k / score_threshold 同一口径：落库后库配置变更不影响历史回看。
--
-- 执行方式（MySQL 客户端连上目标库后执行本文件）：
--   ALTER 幂等性说明：MySQL 5.7+ 不支持 IF NOT EXISTS 的 ADD COLUMN，
--   重复执行会报 Duplicate column（列已存在），忽略即可。
-- =====================================================================================

ALTER TABLE `tb_rag_eval_run`
    ADD COLUMN `graph_depth` INT NOT NULL DEFAULT 2
        COMMENT '图谱增强邻域深度（快照，1-3；仅 with_graph 开启时生效）'
        AFTER `with_graph`,
    ADD COLUMN `graph_scale` INT NOT NULL DEFAULT 10
        COMMENT '图谱增强每一路召回规模（快照，1-50）'
        AFTER `graph_depth`;
