from sqlalchemy import or_, select

from common.common_log.log_init import log
from common.common_milvus.milvus import milvus_client
from common.common_mysql.mysql import mysql_client
from common.common_rag.embed_client import EmbeddingClient
from common.common_rag.rerank_client import RerankClient
from common.common_rag.rrf import reciprocal_rank_fusion
from common.common_rag.text_splitter import extract_keywords
from service.service_rag.models.kb_entity import DocumentChunk
from service.service_rag.schemas.kb_schema import SearchRequest
from service.service_rag.services.kb_service import KbService


class RetrievalService:
    """
    知识库检索流水线：
    关键词检索(MySQL+BM25) + 向量检索(Milvus) → RRF 融合 → Rerank API 重排
    """

    KEYWORD_CANDIDATE_LIMIT = 200   # 关键词路候选上限
    VECTOR_TOP_K = 50               # 向量路每库 TopK
    FUSION_TOP_N = 20               # RRF 融合后参与重排的条数
    RRF_K = 60                      # RRF 平滑系数

    @staticmethod
    async def search(request: SearchRequest, user_id: int):
        async with mysql_client.get_session() as session:
            visible = await KbService.visible_kb_ids(session, user_id)
            if request.kb_ids:
                kb_ids = [kb for kb in request.kb_ids if kb in visible]
            else:
                kb_ids = visible
            if not kb_ids:
                return []

            keywords = extract_keywords(request.query)
            kw_ranks = await RetrievalService._keyword_search(
                session, kb_ids, keywords, request.query)
            q_vec = await EmbeddingClient.embed_one(request.query)
            vec_ranks = []
            if q_vec:
                for kb_id in kb_ids:
                    try:
                        await milvus_client.ensure_collection(kb_id)
                        hits = await milvus_client.search(kb_id, q_vec, RetrievalService.VECTOR_TOP_K)
                        vec_ranks.extend(h["chunk_id"] for h in sorted(hits, key=lambda x: -x["score"]))
                    except Exception as e:
                        log.warning(f"Milvus search failed kb={kb_id}: {str(e)}")

            if not kw_ranks and not vec_ranks:
                return []

            fused = reciprocal_rank_fusion([kw_ranks, vec_ranks], k=RetrievalService.RRF_K)
            fused_top = [cid for cid, _ in fused[:RetrievalService.FUSION_TOP_N]]
            if not fused_top:
                return []

            rows = (await session.execute(
                select(DocumentChunk).where(
                    DocumentChunk.chunk_id.in_(fused_top),
                    DocumentChunk.is_deleted == 0))).scalars().all()
            chunk_map = {c.chunk_id: c for c in rows}
            ordered_contents = [chunk_map[cid].content for cid in fused_top if cid in chunk_map]

            indices = await RerankClient.rerank(request.query, ordered_contents)

            results = []
            fused_map = dict(fused)
            for rank, idx in enumerate(indices[:request.top_k]):
                chunk = chunk_map[fused_top[idx]]
                results.append({
                    "rank": rank + 1,
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "kb_id": chunk.kb_id,
                    "content": chunk.content,
                    "rrf_score": fused_map[chunk.chunk_id],
                })
            log.info(f"Retrieval done: query={request.query[:50]}, hits={len(results)}")
            return results

    @staticmethod
    async def _keyword_search(session, kb_ids: list, keywords: list, query: str) -> list:
        """MySQL LIKE 粗筛 + BM25 精排，返回按相关度降序的 chunk_id 列表"""
        if not keywords or not kb_ids:
            return []
        conds = [DocumentChunk.content.like(f"%{kw}%") for kw in keywords]
        rows = (await session.execute(
            select(DocumentChunk.chunk_id, DocumentChunk.content)
            .where(DocumentChunk.kb_id.in_(kb_ids), DocumentChunk.is_deleted == 0,
                   or_(*conds))
            .limit(RetrievalService.KEYWORD_CANDIDATE_LIMIT))).all()
        if not rows:
            return []
        ids = [r[0] for r in rows]
        try:
            from rank_bm25 import BM25Okapi
            corpus = [list(r[1].replace("\n", " ")) for r in rows]
            bm25 = BM25Okapi(corpus)
            scores = bm25.get_scores(list(query.replace("\n", " ")))
            ranked = sorted(zip(ids, scores), key=lambda x: x[1], reverse=True)
            return [cid for cid, s in ranked if s > 0] or ids
        except ImportError:
            log.warning("rank-bm25 not installed, keyword rank fallback to hit order")
            return ids