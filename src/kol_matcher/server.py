from fastmcp import FastMCP

mcp = FastMCP("TikTok KOL Matcher MCP Server")

@mcp.tool
def ping() -> str:
    return "Why are you ping me????"

def main():
    mcp.run()

if __name__ == "__main__":
    main()