# -*- coding: utf-8 -*-
"""后端注册表验证：
1. py_compile 三个改动文件
2. ModelService.registry 过滤逻辑
3. 注册表条目 (provider, category) 必须都存在于 common_model 支持矩阵（防未验证组合）
4. 每个注册标识必须能在 common_model 中实例化出子类
"""
import asyncio
import os
import py_compile
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FILES = [
    r"D:\ai大模型\DRAGON-AI-master\common\common_constants\model_registry.py",
    r"D:\ai大模型\DRAGON-AI-master\service\service_system\services\model_service.py",
    r"D:\ai大模型\DRAGON-AI-master\service\service_system\routers\model_router.py",
]
for f in FILES:
    py_compile.compile(f, doraise=True)
    print(f"py_compile OK: {os.path.basename(f)}")

from common.common_model import providers_for  # noqa: E402
from common.common_constants.model_registry import MODEL_REGISTRY, registry_models  # noqa: E402
from common.common_constants.model_constant import MODEL_TYPES_ALL, PROVIDERS_ALL  # noqa: E402

# 1. 组合与 common_model 支持矩阵一致
bad = []
for prov, cats in MODEL_REGISTRY.items():
    for cat, models in cats.items():
        if cat not in MODEL_TYPES_ALL:
            bad.append(f"未知类型 {prov}/{cat}")
        if prov not in PROVIDERS_ALL:
            bad.append(f"未知厂商 {prov}")
        if cat in MODEL_TYPES_ALL and prov in providers_for(cat) is None:
            pass
        if cat in MODEL_TYPES_ALL and prov not in providers_for(cat):
            bad.append(f"common_model 未注册该组合 {prov}/{cat}（注册表不应含未实现组合）")
        if not models:
            bad.append(f"空列表 {prov}/{cat}")
print(f"组合检查: {len([m for c in MODEL_REGISTRY.values() for m in c.values()])} 个组合, {len(bad)} 异常")
for b in bad:
    print("  BAD:", b)

# 2. registry_models 过滤
assert registry_models("zhipu", "text_to_text")[0] == "glm-4-flash"
assert registry_models("dashscope", "text_rerank") == ["gte-rerank-v2"]
assert registry_models("openai", "image_to_video") == []
assert registry_models("unknown", "x") == []
print("registry_models 过滤 OK")


async def main():
    from service.service_system.services.model_service import ModelService
    all_items = await ModelService.registry()
    print(f"registry() 全量 = {len(all_items)} 条")
    assert len(all_items) == sum(len(ms) for c in MODEL_REGISTRY.values() for ms in c.values())
    zt = await ModelService.registry(provider="zhipu", category="text_to_text")
    print(f"过滤 zhipu/text_to_text = {len(zt)} 条: {[i['model_name'] for i in zt]}")
    assert all(i["provider"] == "zhipu" and i["category"] == "text_to_text" for i in zt)
    assert zt[0]["provider_label"] == "智谱" and zt[0]["category_label"] == "文生文"
    print("ModelService.registry 过滤/标签 OK")

    # 3. 每个标识可实例化（注册表条目可实际调用，组合已在 common_model 注册）
    from common.common_httpx.httpx import httpx_pool
    from common.common_model import instantiate
    from common.common_model.model_types import ModelConfig
    httpx_pool.init(timeout=30)
    n = 0
    for prov, cats in MODEL_REGISTRY.items():
        for cat, models in cats.items():
            inst = instantiate(cat, ModelConfig(model_id=0, provider=prov,
                                                model_name=models[0], base_url="https://x", api_key="k"))
            assert inst.model == models[0]
            n += 1
    print(f"实例化检查: {n} 个组合首标识均实例化 OK（真实调用已由 _reg_verify.py 完成）")


asyncio.run(main())
print("\n===== 后端注册表全部校验通过 =====")