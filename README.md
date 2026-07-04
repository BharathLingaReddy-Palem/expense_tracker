## Expense Tracker MCP Server

This repository contains a FastMCP expense tracker server that is ready to run over HTTP for MCP Cloud / Claude connector use.

### Files to keep in GitHub

- `expense_tracker.py`
- `pyproject.toml`
- `uv.lock`
- `README.md`
- `.gitignore`
- `.python-version`

### Local-only file

- `expenses.db` is created automatically by the app and should not be committed.

### Run locally

```bash
uv run python expense_tracker.py
```

The server runs with HTTP transport on port 8000.

### For MCP Cloud

Use the GitHub repo as the source, then point the cloud deployment to `expense_tracker.py`.
The app starts with:

```python
mcp.run(transport="http", host="0.0.0.0", port=8000)
```

If your cloud host mounts the source tree as read-only, set a writable database path:

```bash
EXPENSES_DB_PATH=/tmp/expenses.db
```
