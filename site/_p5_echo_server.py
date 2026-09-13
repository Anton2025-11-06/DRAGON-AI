
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("echo")


@mcp.tool(description="Echo the input text back")
async def echo(text: str) -> str:
    return "echo: " + text


@mcp.tool(description="Add two integers")
async def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    mcp.run()
