# -*- coding: utf-8 -*-
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.common_constants.model_registry import MODEL_REGISTRY, registry_models

print(json.dumps(MODEL_REGISTRY, ensure_ascii=False, indent=1))
print("dashscope rerank:", registry_models("dashscope", "text_rerank"))
print("dashscope t2i:", registry_models("dashscope", "text_to_image"))