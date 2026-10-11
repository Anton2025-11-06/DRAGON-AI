# 深度探索四类平台能力 → agentscope Toolkit 的桥接层（只被 arq worker import）：
# - kb_search_tool.py    挂载知识库 → kb_search 工具（RagRetrievalService.search_rows）
# - mcp_tools.py         挂载 MCP 连接 → 每个远端工具一个动态 ToolBase（McpServerService）
# - platform_tools.py    挂载平台工具 → 受限沙箱执行（ToolService.call）
# - skill_seed.py        挂载技能 → zip 解压 → workspace skill_paths 种入沙箱
# - mount_loader.py      挂载校验（use 口径与 picker 同源）+ 三桥 + 技能种入的统一入口
