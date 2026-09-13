# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import service.service_file.routers.file_router as m

paths = [r.path for r in m.router.routes]
print("OK routes:", paths)

import service.service_file as sf
print("app title:", sf.app.title)
