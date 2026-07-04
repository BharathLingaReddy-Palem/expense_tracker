from fastmcp import FastMCP
from datetime import datetime
import platform
import random

mcp = FastMCP("Utility Server")


@mcp.tool()
def get_current_time():
    """Get current date and time"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool()
def multiply(a: int, b: int):
    """Multiply two numbers"""
    return a * b


@mcp.tool()
def divide(a: float, b: float):
    """Divide two numbers"""
    if b == 0:
        return "Cannot divide by zero"
    return a / b


@mcp.tool()
def generate_password(length: int):
    """Generate a random password"""
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%"
    return "".join(random.choice(chars) for _ in range(length))


@mcp.tool()
def system_info():
    """Get system information"""
    return {
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version()
    }


@mcp.tool()
def random_quote():
    quotes = [
        "Stay hungry, stay foolish.",
        "Practice makes perfect.",
        "Code, Learn, Repeat.",
        "Consistency beats intensity."
    ]
    return random.choice(quotes)


if __name__ == "__main__":
    mcp.run()
    