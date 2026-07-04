from fastmcp import FastMCP
import random

mcp = FastMCP("Demo Server")

@mcp.tool()
def roll_dice(num_dice: int):
    return [random.randint(1, 6) for _ in range(num_dice)]

@mcp.tool()
def add_numbers(a: int, b: int):
    return a + b

if __name__ == "__main__":
    mcp.run()