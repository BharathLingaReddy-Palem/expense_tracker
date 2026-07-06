# 💸 Expense Tracker MCP Server

An AI-powered expense tracking assistant built with **FastMCP**, deployed on **Horizon**, backed by **Turso** cloud database, and integrated with **ChatGPT / Claude** via MCP connector.

---

## 🏗️ Architecture

```
You (ChatGPT / Claude)
        ↓
Horizon MCP Server  ←── expense_tracker.py (FastMCP)
        ↓
NVIDIA NIM API      ←── Llama 3.1 for smart categorization (ONE batch call)
        ↓
Turso Cloud DB      ←── Persistent libsql (executemany batch insert)
```

---

## ✨ Features

- ✅ **15 MCP tools** — add, read, update, delete, analytics
- ✅ **AI-powered batch categorization** — ONE NVIDIA NIM call for all bulk expenses
- ✅ **Persistent cloud database** — Turso (libsql) survives server restarts
- ✅ **3 DB indexes** — 10–100x faster queries on date, category, description
- ✅ **Connection health check** — auto-reconnects if Turso connection drops
- ✅ **Smart analytics** — weekly, monthly, yearly summaries + spending insights
- ✅ **Bulk operations** — add/delete up to 100 expenses, all inserted at once
- ✅ **Input validation** — rejects negative amounts, empty descriptions
- ✅ **Safe delete** — `delete_all_expenses` requires `confirm=True`
- ✅ **Auto category detection** — falls back to keyword matching if AI key not set

---

## 🔄 How It Works — Bulk Add Flow

```
You: "Add chai 30, uber 150, shawarma 200, jio bill 299, gym 800"
                    ↓
ChatGPT calls: add_bulk_expenses([5 items])
                    ↓
Step 1 — Validate all 5 expenses  (no DB, no AI)
                    ↓
Step 2 — ONE API call to NVIDIA NIM (Llama 3.1)
         prompt: "categorize: chai, uber, shawarma, jio bill, gym"
         response: "food, transport, food, utilities, healthcare"
                    ↓
Step 3 — ONE executemany() inserts all 5 into Turso at once
                    ↓
Step 4 — ONE db.commit()
Done!
```

| Operation | Count |
|---|---|
| NVIDIA API calls | **1** (regardless of batch size) |
| DB INSERT operations | **1** (executemany) |
| db.commit() | **1** |

---

## 🛠️ All 15 MCP Tools

### ➕ Add Tools

#### 1. `add_expense(amount, description, expense_date?)`
Add a single expense. Category is auto-detected by AI (Llama 3.1).
```
Example: "Add Rs 250 for lunch today"
→ add_expense(amount=250, description="lunch")
→ {id: 55, amount: 250, category: "food", date: "2026-07-06"}
```

#### 2. `add_bulk_expenses(expenses)`
Add up to 100 expenses in ONE call. Uses ONE AI API call for all categorizations, then ONE DB batch insert.
```
Example: "Add chai 30, uber 150, dinner 300"
→ add_bulk_expenses([
    {"amount": 30,  "description": "chai"},
    {"amount": 150, "description": "uber"},
    {"amount": 300, "description": "dinner"}
  ])
→ {inserted: 3, failed: 0, expenses: [...]}
```

---

### 📖 Read / Search Tools

#### 3. `get_all_expenses(limit?)`
Get expenses sorted by latest date. Default limit 200, max 1000.
```
Example: "Show me my last 50 expenses"
→ get_all_expenses(limit=50)
→ {count: 50, limit: 50, expenses: [...]}
```

#### 4. `get_expenses_by_date(date)`
Get expenses for a specific date (format: YYYY-MM-DD). Uses `idx_expense_date` index.
```
Example: "What did I spend on July 5th?"
→ get_expenses_by_date(date="2026-07-05")
→ [expenses from July 5 only]
```

#### 5. `search_expenses(keyword)`
Search expenses by keyword in description or category. Uses `idx_description` index.
```
Example: "Find all uber expenses"
→ search_expenses(keyword="uber")
→ [all expenses with 'uber' in description or category]
```

---

### 📊 Analytics Tools

#### 6. `monthly_summary(month?)`
Category-wise total for any month (defaults to current month). Uses date range query with `idx_expense_date`.
```
Example: "How much did I spend in June?"
→ monthly_summary(month="2026-06")
→ {total: 8500, categories: {food: 2100, transport: 900, ...}}
```

#### 7. `weekly_summary()`
This week's spending from Monday to today.
```
Example: "How much have I spent this week?"
→ weekly_summary()
→ {week_start: "2026-07-06", total: 1200, categories: {...}}
```

#### 8. `yearly_summary(year?)`
Month-by-month breakdown for any year (defaults to current year). Uses date range with index.
```
Example: "Show my spending for 2026"
→ yearly_summary(year="2026")
→ {"2026-01": {amount: 5200}, "2026-02": {amount: 4800}, ...}
```

#### 9. `spending_analytics()`
Smart insights — daily average, top category, biggest expense, week-over-week trend.
```
Example: "Give me a full analysis of my spending"
→ spending_analytics()
→ {
    daily_average: 320,
    top_category: {category: "food", amount: 2100},
    biggest_expense: {description: "Books", amount: 900},
    this_week: 1200,
    last_week: 980,
    week_change_pct: "+22.4%"
  }
```

---

### ✏️ Update Tool

#### 10. `update_expense(expense_id, amount?, description?, expense_date?, category?)`
Update any field — pass only what you want to change. Updating description auto-recategorizes via AI.
```
Example: "Fix the amount of expense 44 to 500"
→ update_expense(expense_id=44, amount=500)

Example: "Change description of expense 44 to chai and biscuits"
→ update_expense(expense_id=44, description="chai and biscuits")
   → category auto-updates to "food" too!

Example: "Update expense 44 — Rs 600, office lunch, July 4"
→ update_expense(expense_id=44, amount=600, description="office lunch", expense_date="2026-07-04")
```

---

### 🗑️ Delete Tools

#### 11. `delete_expense(expense_id)`
Delete one expense by ID.
```
Example: "Delete expense 44"
→ delete_expense(expense_id=44)
→ {success: true, message: "Expense ID 44 deleted"}
```

#### 12. `bulk_delete_expenses(ids)`
Delete multiple expenses at once by a list of IDs (max 100).
```
Example: "Delete expenses 45, 46, 47 and 48"
→ bulk_delete_expenses(ids=[45, 46, 47, 48])
→ {deleted: 4, message: "4 of 4 expenses deleted"}
```

#### 13. `delete_expenses_by_category(category)`
Delete all expenses in a specific category. Uses `idx_category` index.
```
Example: "Delete all my entertainment expenses"
→ delete_expenses_by_category(category="entertainment")
→ {deleted: 7, message: "7 'entertainment' expenses deleted"}
```

#### 14. `delete_expenses_by_description(keyword)`
Delete all expenses whose description contains a keyword.
```
Example: "Delete all Netflix entries"
→ delete_expenses_by_description(keyword="netflix")
→ {deleted: 3, message: "3 expenses matching 'netflix' deleted"}
```

#### 15. `delete_all_expenses(confirm)`
Permanently delete ALL expenses. Requires `confirm=True` as a safety check.
```
Example: "Delete all my expenses, start fresh"
→ delete_all_expenses(confirm=True)
→ {deleted: 53, message: "All 53 expenses permanently deleted"}

Without confirm=True → returns error, nothing is deleted ✅
```

---

## 🤖 AI Categorization

When `NVIDIA_API_KEY` is set, the AI categorizes expenses using **Llama 3.1 8B Instruct**:

### Single expense — 1 API call
```
add_expense(250, "grabbed shawarma") → AI → "food"
```

### Bulk expenses — still just 1 API call
```
add_bulk_expenses([5 items])
 → ONE prompt: "categorize: chai, uber, shawarma, jio bill, gym"
 ← ONE response: "food, transport, food, utilities, healthcare"
 → saves all 5 with correct categories in ONE DB operation
```

### Before vs After AI

| Description | Without AI | With AI |
|---|---|---|
| "grabbed shawarma" | `other` ❌ | `food` ✅ |
| "paid jio recharge" | `other` ❌ | `utilities` ✅ |
| "EMI for bike" | `other` ❌ | `transport` ✅ |
| "went to gym today" | `other` ❌ | `healthcare` ✅ |
| "college fees paid" | `other` ❌ | `education` ✅ |
| "paid electricity bill" | `other` ❌ | `utilities` ✅ |

> If `NVIDIA_API_KEY` is not set, keyword-based matching is used as fallback.

---

## 🗄️ Database Design

### Schema

```sql
CREATE TABLE expenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    amount       REAL    NOT NULL,
    description  TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    expense_date TEXT    NOT NULL,   -- format: YYYY-MM-DD
    created_at   TEXT    NOT NULL    -- ISO 8601 timestamp
);
```

### Indexes (Production-Grade)

```sql
-- Speeds up: get_by_date, monthly_summary, weekly_summary, yearly_summary
CREATE INDEX idx_expense_date ON expenses (expense_date);

-- Speeds up: monthly_summary, analytics, delete_by_category
CREATE INDEX idx_category ON expenses (category);

-- Speeds up: search_expenses, delete_by_description
CREATE INDEX idx_description ON expenses (description);
```

> Without indexes: O(n) full table scan on every query.
> With indexes: O(log n) B-tree lookup — **10–100x faster** as data grows.

### Connection Health Check

The DB connection is tested with `SELECT 1` before every request. If Turso drops the connection (e.g. container restart, timeout), it automatically reconnects — no crashes, no stale connections.

---

## 📂 Categories Supported

`food` · `transport` · `groceries` · `entertainment` · `healthcare` · `shopping` · `utilities` · `education` · `other`

---

## ⚙️ Environment Variables

Set these in your **Horizon deployment dashboard** (or `.env` file for local):

| Variable | Required | Description |
|---|---|---|
| `TURSO_DATABASE_URL` | ✅ Yes | Your Turso DB URL — `libsql://<name>.turso.io` |
| `TURSO_AUTH_TOKEN` | ✅ Yes | Your Turso auth token (JWT) |
| `NVIDIA_API_KEY` | ⚡ Optional | NVIDIA NIM API key — enables AI categorization |

> Without `NVIDIA_API_KEY`, the app works with keyword-based categorization.

---

## 🚀 Run Locally

```bash
# Install dependencies
uv sync

# Create .env file
echo "TURSO_DATABASE_URL=libsql://your-db.turso.io" >> .env
echo "TURSO_AUTH_TOKEN=your-token" >> .env
echo "NVIDIA_API_KEY=nvapi-..." >> .env   # optional

# Run the server
uv run python expense_tracker.py
```

Server runs on `http://0.0.0.0:8000`

---

## ☁️ Deploy on Horizon

1. Push this repo to GitHub
2. Go to [horizon.mcpcloud.io](https://horizon.mcpcloud.io)
3. Connect your GitHub repo → select `expense_tracker.py` as entry point
4. Add the 3 environment variables in Horizon dashboard settings
5. Copy the generated MCP URL
6. Paste into ChatGPT / Claude MCP connector

---

## 📦 Tech Stack

| Component | Technology |
|---|---|
| MCP Framework | [FastMCP](https://gofastmcp.com) |
| Cloud Database | [Turso](https://turso.tech) (libsql) |
| MCP Hosting | [Horizon](https://horizon.mcpcloud.io) |
| AI Categorization | [NVIDIA NIM](https://build.nvidia.com) — Llama 3.1 8B Instruct |
| LLM Client | [LangChain NVIDIA AI Endpoints](https://python.langchain.com/docs/integrations/chat/nvidia_ai_endpoints) |
| Runtime | Python 3.13+ with [uv](https://docs.astral.sh/uv/) |

---

## 📄 Files

| File | Purpose |
|---|---|
| `expense_tracker.py` | Main MCP server — all 15 tools |
| `pyproject.toml` | Project config and dependencies |
| `uv.lock` | Locked dependency versions |
| `.env` | Local secrets (not committed to git) |
| `.gitignore` | Excludes `.env`, `*.db`, `.venv` |
| `PRODUCTION_READY_ROADMAP.md` | Future improvement ideas |

---

## 🔮 Roadmap

- [ ] `get_expenses_by_range(start, end)` — query by date range
- [ ] `export_expenses(month, format)` — export to CSV
- [ ] Budget system — set limits per category, get alerts
- [ ] Recurring expense tracker — EMIs, subscriptions, bills
- [ ] Multi-user support — isolated data per user with API key auth
- [ ] Tags and notes on expenses
- [ ] Spending reminders and alerts
