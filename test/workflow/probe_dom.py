# -*- coding: utf-8 -*-
"""CDP DOM 探查：列表页与编辑器关键元素。"""
import sys
import time

sys.path.insert(0, ".")
from cdp_driver import CDP


def nav_wait(c, url, wait=3.0):
    c.eval(f"location.href = '{url}'")
    time.sleep(wait)


c = CDP()
# 1. 列表页
nav_wait(c, "http://localhost:5666/agent/workflow", 4)
print("URL:", c.eval("location.href"))
print("标题:", c.eval("document.title"))
print("按钮:", c.eval("[...document.querySelectorAll('button')].map(b=>b.textContent.trim()).filter(t=>t).slice(0,20)"))
print("表格行数:", c.eval("document.querySelectorAll('.ant-table-tbody tr').length"))
print("工作流名:", c.eval("[...document.querySelectorAll('.ant-table-tbody tr td')].slice(0,15).map(td=>td.textContent.trim())"))
# 2. 菜单是否含工作流编排
print("侧边菜单:", c.eval("[...document.querySelectorAll('.ant-menu li, aside li')].map(li=>li.textContent.trim()).filter(t=>t.length<15).slice(0,25)"))
c.close()
