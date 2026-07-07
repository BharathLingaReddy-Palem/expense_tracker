# 💸 Expense Tracker MCP Server

An AI-powered personal finance assistant built with **FastMCP**, deployed on **Horizon**, backed by **Turso** cloud database, and integrated with **ChatGPT / Claude** via MCP connector.

---

## 🏗️ Architecture

```
You (ChatGPT / Claude)
        ↓
Horizon MCP Server  ←── expense_tracker.py (FastMCP)
        ↓
NVIDIA NIM API      ←── Llama 3.1 for smart categorization (ONE batch call)
        ↓
Turso Cloud DB      ←── 3 persistent tables: expenses, budgets, reminders
```

---

## ✨ Features

- ✅ **Multi-User Architecture** — Single unified URL, multiple users, perfectly isolated data
- ✅ **22 MCP tools** — register, add, read, update, delete, analytics, budgets, reminders
- ✅ **AI-powered batch categorization** — ONE NVIDIA NIM call categorizes all bulk expenses
- ✅ **Persistent cloud database** — Turso (libsql) with 4 tables, survives restarts
- ✅ **4 DB indexes** — 10–100x faster queries on user_id, date, category, description
- ✅ **Connection health check** — auto-reconnects if Turso connection drops
- ✅ **Budget system** — set limits per category once, track all months automatically
- ✅ **Reminders** — one-time and monthly recurring payment reminders
- ✅ **Smart analytics** — weekly, monthly, yearly summaries + spending insights
- ✅ **Bulk operations** — add/delete up to 100 expenses, all inserted at once
- ✅ **Input validation** — rejects negative amounts, empty descriptions
- ✅ **Safe delete** — `delete_all_expenses` requires `confirm=True`

---

## 🔄 How It Works — Bulk Add Flow

```
You: "Add chai 30, uber 150, shawarma 200, jio bill 299, gym 800"
                    ↓
ChatGPT → add_bulk_expenses([5 items])
                    ↓
Step 1 — Validate all 5 expenses
Step 2 — ONE NVIDIA NIM API call: "categorize: chai, uber, shawarma, jio bill, gym"
          response: "food, transport, food, utilities, healthcare"
Step 3 — ONE executemany() inserts all 5 into Turso at once
Step 4 — ONE db.commit()
Done! 3 total operations regardless of batch size.
```

---

## 🛠️ All 22 MCP Tools

> **Note on Multi-User Auth:** Every tool requires a `user_token`. ChatGPT automatically handles passing this token for you once you register and tell it to memorize the token.

### 🔑 Authentication (1)

#### 1. `register_user(name)`
Registers you and provides a secure UUID token for the unified URL architecture.
```
"Register me as Ramesh"
→ {message: "Registration successful!", user_token: "ut_9f8b7c..."}
```

---

### ➕ Add Tools (2)

#### 2. `add_expense(user_token, amount, description, expense_date?)`
Add a single expense. Category auto-detected by NVIDIA Llama 3.1.
```
"Add Rs 250 for lunch today"
→ {id: 55, amount: 250, category: "food", date: "2026-07-06"}
```

#### 3. `add_bulk_expenses(user_token, expenses)`
Add up to 100 expenses — ONE AI call, ONE DB insert, ONE commit.
```
"Add chai 30, uber 150, dinner 300"
→ {inserted: 3, failed: 0, expenses: [...]}
```

---

### 📖 Read / Search Tools (3)

#### 3. `get_all_expenses(limit?)`
Get expenses sorted by latest date. Default limit 200, max 1000.
```
"Show me my last 50 expenses"
→ {count: 50, limit: 50, expenses: [...]}
```

#### 4. `get_expenses_by_date(date)`
Get expenses for a specific date (YYYY-MM-DD). Uses `idx_expense_date`.
```
"What did I spend on July 5th?"
→ get_expenses_by_date(date="2026-07-05")
```

#### 5. `search_expenses(keyword)`
Search by keyword in description or category. Uses `idx_description`.
```
"Find all uber expenses"
→ search_expenses(keyword="uber")
```

---

### 📊 Analytics Tools (4)

#### 6. `monthly_summary(month?)`
Category-wise totals for any month. Uses date range + index for speed.
```
"How much did I spend in June?"
→ monthly_summary(month="2026-06")
→ {total: 8500, categories: {food: 2100, transport: 900, ...}}
```

#### 7. `weekly_summary()`
This week's spending from Monday to today.
```
"How much have I spent this week?"
→ {week_start: "2026-07-06", total: 1200, categories: {...}}
```

#### 8. `yearly_summary(year?)`
Month-by-month breakdown. Uses date range + index.
```
"Show my spending for 2026"
→ {"2026-01": {amount: 5200}, "2026-02": {amount: 4800}, ...}
```

#### 9. `spending_analytics()`
Smart insights — daily average, top category, biggest expense, week-over-week trend.
```
"Give me a full analysis of my spending"
→ {
    daily_average: 320,
    top_category: {category: "food", amount: 2100},
    biggest_expense: {description: "Books", amount: 900},
    this_week: 1200, last_week: 980, week_change_pct: "+22.4%"
  }
```

---

### ✏️ Update Tool (1)

#### 10. `update_expense(expense_id, amount?, description?, expense_date?, category?)`
Update any field — pass only what you want to change.
```
"Fix expense 44 amount to 500"
→ update_expense(expense_id=44, amount=500)

"Change expense 44 description to chai and biscuits"
→ update_expense(expense_id=44, description="chai and biscuits")
  → category auto-updates to "food" via AI!
```

---

### 🗑️ Delete Tools (5)

#### 11. `delete_expense(expense_id)` — Delete one by ID
#### 12. `bulk_delete_expenses(ids)` — Delete multiple by list of IDs
#### 13. `delete_expenses_by_category(category)` — Wipe all of a category (uses index)
#### 14. `delete_expenses_by_description(keyword)` — Delete by keyword match
#### 15. `delete_all_expenses(confirm)` — Wipe everything (requires `confirm=True`)

---

### 💰 Budget System (3)

Budgets are set **once per category** and apply to **every future month automatically** — no monthly reset needed.

#### 16. `set_budget(category, amount)`
Create or update a budget. Works as an upsert — calling again updates the amount.
```
"Set my food budget to Rs 3000"
→ set_budget("food", 3000)
→ "Budget set: Rs 3000/month for 'food'. Applies every month automatically."

"Increase food budget to Rs 4000"
→ set_budget("food", 4000)   ← updates existing, no duplicate
```

#### 17. `get_budget_status(month?)`
Show current month's spending vs budget for all categories.
```
"How's my budget this month?"
→ get_budget_status()
→ {
    food:           {budget: 3000, spent: 1800, remaining: 1200, percent: 60, status: "OK"},
    transport:      {budget: 1500, spent: 1600, remaining: -100, percent: 107, status: "OVER BUDGET"},
    entertainment:  {budget: 500,  spent: 420,  remaining: 80,   percent: 84,  status: "WARNING — near limit"}
  }
```

Status flags:
- `OK` — under 80% of budget
- `WARNING — near limit` — 80–99% used
- `OVER BUDGET` — 100%+ exceeded

#### 18. `delete_budget(category)`
Remove a budget. No limit will be applied for that category after.
```
"Remove my entertainment budget"
→ delete_budget("entertainment")
```

---

### 📅 Reminders (3)

#### 19. `set_reminder(description, due_date?, amount?, is_recurring?, recurring_day?)`
Set a one-time or monthly recurring payment reminder.
```
One-time:
"Remind me to pay electricity Rs 600 on Aug 5"
→ set_reminder("Pay electricity", due_date="2026-08-05", amount=600)

Monthly recurring:
"Remind me to pay rent Rs 8000 every 1st of the month"
→ set_reminder("Pay rent", amount=8000, is_recurring=True, recurring_day=1)
  ← stored once, triggers every month forever
```

#### 20. `get_upcoming_reminders(days?)`
Show all reminders due within the next N days (default 7). Sorted by urgency.
```
"What payments are due this week?"
→ get_upcoming_reminders(days=7)
→ [
    {description: "Pay rent", amount: 8000, due_date: "2026-08-01", urgency: "in 3 days", type: "recurring"},
    {description: "Pay electricity", amount: 600, due_date: "2026-08-05", urgency: "in 7 days", type: "one-time"}
  ]
```

#### 21. `delete_reminder(reminder_id)`
Permanently delete a reminder by its ID.
```
"Delete the rent reminder"
→ first: get_upcoming_reminders() to find ID
→ then: delete_reminder(reminder_id=1)
```

---

## 🤖 AI Categorization

### Single expense — 1 API call
```
add_expense(250, "grabbed shawarma") → AI → "food" ✅
```

### Bulk expenses — still just 1 API call
```
add_bulk_expenses([5 items])
→ ONE prompt sent to NVIDIA NIM
← ONE response: "food, transport, food, utilities, healthcare"
→ executemany() inserts all 5 at once
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

> Falls back to keyword matching if `NVIDIA_API_KEY` not set.

---

## 🗄️ Database Design

### Tables

```sql
-- Core expense records
CREATE TABLE expenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    amount       REAL    NOT NULL,
    description  TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    expense_date TEXT    NOT NULL,   -- YYYY-MM-DD
    created_at   TEXT    NOT NULL
);

-- Per-category budgets (set once, applies every month)
CREATE TABLE budgets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    category   TEXT    NOT NULL UNIQUE,  -- one budget per category
    amount     REAL    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

-- One-time and recurring payment reminders
CREATE TABLE reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    description   TEXT    NOT NULL,
    amount        REAL,
    due_date      TEXT,               -- YYYY-MM-DD for one-time
    is_recurring  INTEGER DEFAULT 0,  -- 1 = monthly recurring
    recurring_day INTEGER,            -- day of month (1-31)
    is_done       INTEGER DEFAULT 0,
    created_at    TEXT    NOT NULL
);
```

### Indexes (Production-Grade)

```sql
CREATE INDEX idx_expense_date  ON expenses (expense_date);  -- O(log n) date queries
CREATE INDEX idx_category      ON expenses (category);       -- O(log n) category queries
CREATE INDEX idx_description   ON expenses (description);    -- O(log n) search
```

> Without indexes: O(n) full scan every query.
> With indexes: **10–100x faster** as data grows.

### Connection Health Check
Before every request: `SELECT 1` ping. If connection is stale (Turso timeout / container restart), it reconnects automatically — zero crashes.

---

## 📂 Categories

`food` · `transport` · `groceries` · `entertainment` · `healthcare` · `shopping` · `utilities` · `education` · `other`

---

## ⚙️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TURSO_DATABASE_URL` | ✅ Yes | `libsql://<name>.turso.io` |
| `TURSO_AUTH_TOKEN` | ✅ Yes | Turso JWT auth token |
| `NVIDIA_API_KEY` | ⚡ Optional | Enables AI categorization via Llama 3.1 |

---

## 🚀 Run Locally

```bash
uv sync

# Create .env
echo "TURSO_DATABASE_URL=libsql://your-db.turso.io" >> .env
echo "TURSO_AUTH_TOKEN=your-token" >> .env
echo "NVIDIA_API_KEY=nvapi-..." >> .env

uv run python expense_tracker.py
# Server: http://0.0.0.0:8000
```

---

## ☁️ Deploy on Horizon

1. Push to GitHub
2. [horizon.mcpcloud.io](https://horizon.mcpcloud.io) → connect repo → select `expense_tracker.py`
3. Add 3 env vars in Horizon dashboard
4. Copy MCP URL → paste into ChatGPT / Claude MCP connector

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
| `expense_tracker.py` | Main MCP server — all 21 tools |
| `pyproject.toml` | Project config and dependencies |
| `uv.lock` | Locked dependency versions |
| `.env` | Local secrets (not committed) |
| `.gitignore` | Excludes `.env`, `*.db`, `.venv` |
| `PRODUCTION_READY_ROADMAP.md` | Future improvement ideas |

---

## 🔮 Roadmap

- [ ] `get_expenses_by_range(start, end)` — query by custom date range
- [ ] `export_expenses(month, format)` — export to CSV
- [ ] Tags on expenses — label as "work", "reimbursable", etc.
- [ ] Multi-user support — isolated data per user with API key auth
