-- =====================================================================================
-- 一个切片记多条媒体：tb_document_chunk.media_url 由 VARCHAR(500) 扩成 TEXT
-- （存量库专用；全新环境直接跑 sql/v2_init.sql，不必跑本文件）
-- =====================================================================================
-- 为什么要改列宽：
--   一页三张图、一节里插几张图，分块后是**同一条切片**覆盖多个媒体块。media_url 过去只
--   存第一条（分块层就只取第一个媒体引用），于是：
--     1) 其余媒体在字段层彻底消失——页面放不出、问答上下文里只剩正文那一个地址；
--     2) 清理链路按 media_url 反推要删的存储对象（parse_service._collect_objects），
--        没被记过的对象永久留在存储里。
--   现在全部句柄按换行拼成一个串存进本列（单片上限 20 条，见 rag_constant.MAX_MEDIA_REFS），
--   出口再拆。500 字符装不下多条地址（外链 URL 一条就能到两百字符），故改 TEXT。
--
-- 不需要动的东西：
--   ES 的 media_url 是 index=false 的 keyword，只是 _source 里的一个串，值变长不影响 mapping，
--   历史索引里是单条句柄也照样能被拆成一条列表；分块层的改动复用已有解析产物（sidecar），
--   存量文档点一次「构建向量」即生效，不必重新解析。
--
-- 怎么执行：
--   mysql -u<user> -p <db> < sql/v2_4_media_multi_refs.sql
--   VARCHAR→TEXT 是列类型变更，InnoDB 走 COPY 重建表：切片行数多的库请在业务低峰执行。
-- 重复执行安全：ALTER 前先查 information_schema，已经是 TEXT 就跳过。
-- =====================================================================================

SET @ddl = IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = 'tb_document_chunk' AND COLUMN_NAME = 'media_url'
               AND DATA_TYPE = 'text') = 0,
              'ALTER TABLE `tb_document_chunk` MODIFY COLUMN `media_url` TEXT COMMENT ''本切片覆盖的全部媒体在公共存储中的句柄（多个用换行分隔，单片上限 20 条；ES 只存这个串，不存二进制）''',
              'SELECT ''media_url 已是 TEXT，跳过'' AS `提示`');
PREPARE st FROM @ddl; EXECUTE st; DEALLOCATE PREPARE st;

-- =====================================================================================
-- 执行后自查（期望值就写在列名里，不符就是语句被客户端吞了）
-- =====================================================================================
SELECT 'media_url 列类型（应为 text）' AS `自查项`, DATA_TYPE AS `实际`
FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'tb_document_chunk' AND COLUMN_NAME = 'media_url';

-- 存量行不用迁数据：单条句柄本身就是「换行分隔」列表的长度为 1 的形态。
-- 这一句只是让你看一眼有多少行已经带上了多个句柄（重跑构建向量之后才会 > 0）。
SELECT '一条切片里记了多个媒体句柄的行数（重跑构建向量前应为 0）' AS `自查项`,
       COUNT(*) AS `实际`
FROM `tb_document_chunk` WHERE `media_url` LIKE '%\n%';
