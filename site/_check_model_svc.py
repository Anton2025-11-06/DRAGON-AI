# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import service.service_system.services.model_service as ms
import service.service_system.schemas.model_schema as sc
import service.service_system.models.model as mm
print("model_service OK")
print("CommonParam fields:", list(sc.CommonParam.model_fields.keys()))
print("SaveRequest has supports_stream:", "supports_stream" in sc.ModelSaveRequest.model_fields)
print("TestRequest has inputs:", "inputs" in sc.ModelTestRequest.model_fields)
print("Model ORM has common_params:", "common_params" in mm.Model.__table__.columns)
print("derive:", ms.ModelService._derive_model_params([
    {"name": "temperature", "default": "0.7", "type": "number"},
    {"name": "stream", "default": "true", "type": "boolean"},
]))
