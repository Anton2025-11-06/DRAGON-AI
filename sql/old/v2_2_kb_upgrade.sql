-- =====================================================================================
-- v2.2 知识库模块改造升级脚本（存量库专用；全新环境直接跑 sql/v2_init.sql 即可，不必跑本文件）
-- =====================================================================================
-- 对应这一轮的产品改动：
--   1) 图谱抽取模型与图片理解模型从 chat_model_id 里拆出来 → tb_knowledge_base 补两列
--      extract_model_id / image_model_id（旧库两列为 0，代码按 chat_model_id 回退，不影响存量）。
--   2) 菜单重排：「知识库文档」改名「知识维护」；列表行内的检索/图谱按钮提升为独立菜单
--      「知识检索」「图谱检索」；「知识库对话」页取消（连它的按钮权限点与角色授权一起删）。
--   3) 知识库启用/停用功能取消：status 列保留（不删数据），代码不再读写，本脚本不动它。
--
-- 怎么执行（同一个连接跑完，PART 2 要用 @会话变量记 menu_id，换连接会丢）：
--   mysql -u<user> -p <db> < sql/v2_2_kb_upgrade.sql
-- 重复执行安全：ALTER 前先查 information_schema；菜单改名带条件（已经是新名就不再动）；
--   种子一律 INSERT IGNORE；ADMIN 补授权靠 uk_role_menu 幂等。
-- =====================================================================================

-- =====================================================================================
-- PART 1  tb_knowledge_base 补两个模型列（逐列预判后再 ALTER，重复跑不报错）
-- =====================================================================================
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'tb_knowledge_base' AND COLUMN_NAME = 'extract_model_id') = 0,
              'ALTER TABLE `tb_knowledge_base` ADD COLUMN `extract_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''知识图谱实体抽取模型 id（仅 doc 型且 graph_enabled=1 时必填），0=沿用 chat_model_id'' AFTER `chat_model_id`',
              'SELECT ''extract_model_id 已存在，跳过'' AS `提示`');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

SET @ddl = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'tb_knowledge_base' AND COLUMN_NAME = 'image_model_id') = 0,
              'ALTER TABLE `tb_knowledge_base` ADD COLUMN `image_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT ''图片理解模型 id（仅 doc 型且 parse_config.image_understand=true 时必填）'' AFTER `extract_model_id`',
              'SELECT ''image_model_id 已存在，跳过'' AS `提示`');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

-- chat_model_id 的语义已经收窄（只剩音视频型用），注释同步改掉，列本身不动
ALTER TABLE `tb_knowledge_base`
    MODIFY COLUMN `chat_model_id` BIGINT UNSIGNED NOT NULL DEFAULT 0
    COMMENT '媒体理解与摘要模型 id（audio_video 型必需）；doc 型已拆到 extract_model_id / image_model_id，本列仅作存量库回退';

-- status 列保留但功能已下线：注释改掉，避免后来的人以为还有启停这回事
ALTER TABLE `tb_knowledge_base`
    MODIFY COLUMN `status` TINYINT NOT NULL DEFAULT 1
    COMMENT '保留列（启用/停用功能已取消，代码不再读写，默认恒为 1）';

-- =====================================================================================
-- PART 2  菜单改造
-- =====================================================================================
SET @d_kb = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = 0 AND `menu_name` = '知识库' LIMIT 1);

SELECT IF(@d_kb IS NULL,
          '没有「知识库」一级菜单，先跑 sql/v2_init.sql 的 PART 8.1 再回来',
          '找到「知识库」一级菜单，继续') AS `前置检查`;

-- 2.1 「知识库文档」→「知识维护」（uk_parent_name 是 (parent_id, menu_name)，库里还没有新名才改）
UPDATE `tb_menu`
SET `menu_name` = '知识维护', `update_time` = NOW()
WHERE `parent_id` = @d_kb AND `menu_name` = '知识库文档'
  AND NOT EXISTS (SELECT 1 FROM (SELECT `menu_id` FROM `tb_menu`
                                 WHERE `parent_id` = @d_kb AND `menu_name` = '知识维护') t);

-- 2.2 取消「知识库对话」页：先删它的角色授权，再删菜单本身（它没有挂按钮权限点）
DELETE rm FROM `tb_role_menu` rm
JOIN `tb_menu` m ON m.`menu_id` = rm.`menu_id`
WHERE m.`parent_id` = @d_kb AND m.`menu_name` = '知识库对话';
DELETE FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '知识库对话';

-- 2.3 旧「知识库检索」页（弹窗形态、组件是个死链）改造成独立页「知识检索」。
--     先把它的 menu_id 存下来：它下面挂着一个同名的按钮权限点（ai:kb:search），
--     不改那个名字，2.5 的 INSERT IGNORE 会按 uk_parent_name 再插一条，perm 就重复了
SET @m_kb_search_old = (SELECT `menu_id` FROM `tb_menu`
                        WHERE `parent_id` = @d_kb AND `menu_name` = '知识库检索' LIMIT 1);
UPDATE `tb_menu`
SET `menu_name` = '知识检索', `path` = 'retrieve',
    `component` = 'views/wemirr/ai/rag/retrieve/index.vue',
    `icon` = 'lucide:scan-search', `sort` = 2, `update_time` = NOW()
WHERE `parent_id` = @d_kb AND `menu_name` = '知识库检索';
UPDATE `tb_menu` SET `menu_name` = '知识检索', `update_time` = NOW()
WHERE `parent_id` = @m_kb_search_old AND `menu_type` = 3 AND `perm` = 'ai:kb:search';

-- 2.4 补两个页面菜单（知识维护改名后 sort 仍是 1；知识检索若在上面没匹配到也在这里补齐）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `path`, `component`, `icon`, `sort`) VALUES
(@d_kb, '知识维护', 2, 'doc',      'views/wemirr/ai/rag/doc/index.vue',      'lucide:folder-open', 1),
(@d_kb, '知识检索', 2, 'retrieve', 'views/wemirr/ai/rag/retrieve/index.vue', 'lucide:scan-search', 2),
(@d_kb, '图谱检索', 2, 'graph',    'views/wemirr/ai/rag/graph/index.vue',    'lucide:network',     3);

SET @m_kb_doc = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '知识维护' LIMIT 1);
SET @m_kb_retrieve = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '知识检索' LIMIT 1);
SET @m_kb_graph = (SELECT `menu_id` FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` = '图谱检索' LIMIT 1);

-- 2.5 按钮权限点：ai:kb:search 已随上面的改名跟到新菜单下；
--     这里只补「图谱检索」页的准入位，并把知识维护页的两个按钮位补齐
--     （存量库里可能已有，父级 + 名字撞 uk_parent_name 自然被 IGNORE 跳过）
INSERT IGNORE INTO `tb_menu` (`parent_id`, `menu_name`, `menu_type`, `perm`, `sort`) VALUES
(@m_kb_doc, '知识库查询', 3, 'ai:kb:list',   3),
(@m_kb_doc, '新建知识库', 3, 'ai:kb:add',    4),
(@m_kb_retrieve, '知识检索', 3, 'ai:kb:search', 1),
(@m_kb_graph, '图谱检索', 3, 'ai:kb:graph',     1);

-- =====================================================================================
-- PART 3  ADMIN 补授权（与 v2_init.sql PART 9 同一句，uk_role_menu 保证幂等）
-- =====================================================================================
INSERT IGNORE INTO `tb_role_menu` (`role_id`, `menu_id`)
SELECT r.`role_id`, m.`menu_id` FROM `tb_role` r, `tb_menu` m WHERE r.`role_code` = 'ADMIN';

-- =====================================================================================
-- PART 4  执行后自查（期望值就写在列名里，不符就是中途有语句被客户端吞了）
-- =====================================================================================
SELECT '知识库表列数（应为 27）' AS `自查项`, COUNT(*) AS `实际`
FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'tb_knowledge_base';

SELECT '两个新模型列（应为 2）' AS `自查项`, COUNT(*) AS `实际`
FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'tb_knowledge_base'
  AND COLUMN_NAME IN ('extract_model_id', 'image_model_id');

SELECT '知识库下菜单（应为 知识维护/知识检索/图谱检索 三条）' AS `自查项`,
       GROUP_CONCAT(`menu_name` ORDER BY `sort`) AS `实际`
FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_type` = 2 AND `is_deleted` = 0;

SELECT '知识库按钮权限点（应为 4：list/add/search/graph）' AS `自查项`,
       GROUP_CONCAT(`perm` ORDER BY `perm`) AS `实际`
FROM `tb_menu` WHERE `menu_type` = 3 AND `perm` LIKE 'ai:%';

SELECT '残留的对话菜单（应为 0）' AS `自查项`, COUNT(*) AS `实际`
FROM `tb_menu` WHERE `parent_id` = @d_kb AND `menu_name` IN ('知识库对话', '知识库文档', '知识库检索');
