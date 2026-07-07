from fastmcp import FastMCP
import os
import libsql
from datetime import datetime, timedelta
from difflib import get_close_matches
from dotenv import load_dotenv
from langchain_nvidia_ai_endpoints import ChatNVIDIA

load_dotenv()  # loads .env file automatically (works locally and on Horizon)

mcp = FastMCP("Expense Tracker")

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
# Set these environment variables in your Horizon deployment dashboard:
#   TURSO_DATABASE_URL  ->  libsql://<your-db-name>.turso.io
#   TURSO_AUTH_TOKEN    ->  your Turso auth token
#   NVIDIA_API_KEY      ->  your NVIDIA NIM API key (for AI categorization)
# ---------------------------------------------------------------------------

_db = None


def get_db():
    """
    Return a cached libsql connection to the Turso cloud database.
    Includes a health check — if the connection is stale (e.g. after
    a Horizon container restart or Turso timeout), it reconnects automatically.
    """
    global _db
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url:
        raise RuntimeError(
            "TURSO_DATABASE_URL environment variable is not set. "
            "Add it in your Horizon deployment settings."
        )
    if _db is not None:
        # Health check: run a lightweight ping query
        # If it fails, the connection is dead — reconnect
        try:
            _db.execute("SELECT 1")
        except Exception:
            _db = None  # discard stale connection
    if _db is None:
        _db = libsql.connect(url, auth_token=token)
    return _db


def init_db():
    """
    Create all tables and indexes if they do not already exist.
    """
    db = get_db()

    # ── users ─────────────────────────────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            user_token TEXT    NOT NULL UNIQUE,
            created_at TEXT    NOT NULL
        )
    """)

    # ── expenses ──────────────────────────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL DEFAULT 1,
            amount       REAL    NOT NULL,
            description  TEXT    NOT NULL,
            category     TEXT    NOT NULL,
            expense_date TEXT    NOT NULL,
            created_at   TEXT    NOT NULL,
            is_deleted   INTEGER DEFAULT 0,
            deleted_at   TEXT
        )
    """)
    
    # Safely add user_id to expenses if it doesn't exist (migration)
    try:
        db.execute("ALTER TABLE expenses ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass # Column likely already exists

    try:
        db.execute("ALTER TABLE expenses ADD COLUMN is_deleted INTEGER DEFAULT 0")
        db.execute("ALTER TABLE expenses ADD COLUMN deleted_at TEXT")
    except Exception:
        pass

    db.execute("CREATE INDEX IF NOT EXISTS idx_expense_date ON expenses (expense_date)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_category ON expenses (category)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_description ON expenses (description)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_expense_user ON expenses (user_id)")

    # ── budgets ───────────────────────────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL DEFAULT 1,
            category   TEXT    NOT NULL,
            amount     REAL    NOT NULL,
            created_at TEXT    NOT NULL,
            updated_at TEXT    NOT NULL,
            UNIQUE(user_id, category)
        )
    """)
    
    # Safely handle budgets migration
    try:
        db.execute("ALTER TABLE budgets ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
        
    # Recreate budgets table to enforce the new UNIQUE(user_id, category) constraint properly
    db.execute("""
        CREATE TABLE IF NOT EXISTS budgets_new (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL DEFAULT 1,
            category   TEXT    NOT NULL,
            amount     REAL    NOT NULL,
            created_at TEXT    NOT NULL,
            updated_at TEXT    NOT NULL,
            UNIQUE(user_id, category)
        )
    """)
    try:
        # Move old data
        db.execute("INSERT OR IGNORE INTO budgets_new (id, user_id, category, amount, created_at, updated_at) SELECT id, 1, category, amount, created_at, updated_at FROM budgets")
        db.execute("DROP TABLE budgets")
        db.execute("ALTER TABLE budgets_new RENAME TO budgets")
    except Exception:
        pass

    db.execute("CREATE INDEX IF NOT EXISTS idx_budget_user ON budgets (user_id)")

    # ── reminders ─────────────────────────────────────────────────────────────
    db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL DEFAULT 1,
            description   TEXT    NOT NULL,
            amount        REAL,
            due_date      TEXT,
            is_recurring  INTEGER DEFAULT 0,
            recurring_day INTEGER,
            is_done       INTEGER DEFAULT 0,
            created_at    TEXT    NOT NULL
        )
    """)
    
    try:
        db.execute("ALTER TABLE reminders ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
        
    db.execute("CREATE INDEX IF NOT EXISTS idx_reminder_user ON reminders (user_id)")

    # Create the default user if it doesn't exist so existing data doesn't orphan
    try:
        db.execute("INSERT OR IGNORE INTO users (id, name, user_token, created_at) VALUES (1, 'Default Owner', 'ut_default_owner', datetime('now'))")
    except Exception:
        pass

    db.commit()



init_db()

import uuid

def validate_token(user_token: str) -> int:
    """Validates the user token and returns the user_id. Raises ValueError if invalid."""
    db = get_db()
    cur = db.execute("SELECT id FROM users WHERE user_token = ?", (user_token,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"Invalid user token: {user_token}. Please register first using register_user().")
    return row[0]

@mcp.tool()
def register_user(name: str):
    """
    Register a new user to the Expense Tracker and get a unique secure token.
    Save this token and use it for all future operations.
    """
    token = f"ut_{uuid.uuid4().hex[:8]}"
    db = get_db()
    db.execute(
        "INSERT INTO users (name, user_token, created_at) VALUES (?, ?, datetime('now'))",
        (name, token)
    )
    db.commit()
    return {
        "message": "Registration successful! Important: save this token to your Custom Instructions or Memory.",
        "name": name,
        "user_token": token
    }

# ---------------------------------------------------------------------------
# Category detection — keyword-based (fallback when NVIDIA key not set)
# ---------------------------------------------------------------------------

CATEGORIES = {
    "food": [
        "food", "dinner", "lunch", "breakfast",
        "restaurant", "hotel", "tea", "coffee", "snacks",
        "swiggy", "zomato", "biryani", "pizza", "burger",
        "shawarma", "juice", "ice cream", "cake", "chai"
    ],
    "transport": [
        "cab", "uber", "rapido", "auto", "bus",
        "train", "metro", "petrol", "diesel", "ola",
        "flight", "ticket", "fare", "toll", "parking"
    ],
    "groceries": [
        "grocery", "groceries", "vegetables", "rice",
        "milk", "fruits", "dal", "oil", "bigbasket",
        "blinkit", "zepto", "dmart", "supermarket"
    ],
    "entertainment": [
        "movie", "netflix", "prime", "game", "spotify",
        "hotstar", "youtube", "concert", "event", "show"
    ],
    "healthcare": [
        "medicine", "doctor", "hospital", "pharmacy",
        "clinic", "lab", "test", "health", "dental"
    ],
    "shopping": [
        "shopping", "clothes", "shirt", "shoes", "amazon",
        "flipkart", "myntra", "meesho", "jacket", "dress"
    ],
    "utilities": [
        "electricity", "water", "gas", "internet", "wifi",
        "mobile", "recharge", "jio", "airtel", "bsnl", "bill"
    ],
    "education": [
        "course", "books", "college", "fees", "tuition",
        "udemy", "coursera", "stationery", "notebook", "pen"
    ],
    "other": []
}

VALID_CATEGORIES = list(CATEGORIES.keys())


def detect_category_keyword(description: str) -> str:
    """Keyword-based category detection (fast, no API needed)."""
    desc = description.lower()
    all_words = []

    for category, keywords in CATEGORIES.items():
        for keyword in keywords:
            if keyword in desc:
                return category
            all_words.append(keyword)

    for word in desc.split():
        match = get_close_matches(word, all_words, n=1, cutoff=0.7)
        if match:
            matched_word = match[0]
            for category, keywords in CATEGORIES.items():
                if matched_word in keywords:
                    return category

    return "other"


def detect_category_ai(description: str) -> str:
    """AI-powered category detection using NVIDIA NIM via LangChain (single expense)."""
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        return detect_category_keyword(description)

    try:
        client = ChatNVIDIA(
            model="meta/llama-3.1-8b-instruct",
            api_key=api_key,
            temperature=0.2,
            top_p=0.7,
            max_completion_tokens=50,  # single category name — 50 is more than enough
        )
        category_list = ", ".join(VALID_CATEGORIES)
        prompt = (
            f"Categorize this expense into exactly one of these categories: "
            f"{category_list}.\n"
            f"Expense: '{description}'\n"
            f"Reply with ONLY the category name, nothing else."
        )
        response = client.invoke([{"role": "user", "content": prompt}])
        result = response.content.strip().lower()

        # Validate the AI response is a known category
        if result in VALID_CATEGORIES:
            return result
        # Try partial match in case model adds extra words
        for cat in VALID_CATEGORIES:
            if cat in result:
                return cat
        # Fallback if response is unrecognised
        return detect_category_keyword(description)
    except Exception:
        # Fallback to keyword matching if AI call fails
        return detect_category_keyword(description)


def detect_categories_batch(descriptions: list) -> list:
    """
    Categorize ALL descriptions in ONE single API call.
    Returns a list of categories in the same order as descriptions.
    This is far more efficient than calling the AI once per expense.
    One call handles 5 expenses or 100 expenses — no rate limit concern.
    """
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        # No API key — keyword match all
        return [detect_category_keyword(d) for d in descriptions]

    try:
        client = ChatNVIDIA(
            model="meta/llama-3.1-8b-instruct",
            api_key=api_key,
            temperature=0.2,
            top_p=0.7,
            # Each category is ~2-3 tokens; 100 categories = ~300 tokens max
            max_completion_tokens=1024,
        )
        category_list = ", ".join(VALID_CATEGORIES)
        numbered = "\n".join(f"{i+1}. {d}" for i, d in enumerate(descriptions))
        prompt = (
            f"Categorize each expense below into exactly one of: {category_list}.\n"
            f"Return ONLY a comma-separated list of categories in the SAME ORDER.\n"
            f"No numbers, no explanations — just category names separated by commas.\n\n"
            f"Expenses:\n{numbered}"
        )
        response = client.invoke([{"role": "user", "content": prompt}])
        raw = response.content.strip().lower()

        # Parse comma-separated response
        parts = [p.strip() for p in raw.split(",")]

        # Validate and map each part to a known category
        categories = []
        for i, part in enumerate(parts):
            if part in VALID_CATEGORIES:
                categories.append(part)
            else:
                # Try partial match
                matched = next((cat for cat in VALID_CATEGORIES if cat in part), None)
                if matched:
                    categories.append(matched)
                else:
                    # Fallback to keyword for this one item
                    categories.append(detect_category_keyword(descriptions[i]) if i < len(descriptions) else "other")

        # If AI returned wrong number of categories, fill remainder with keyword matching
        while len(categories) < len(descriptions):
            categories.append(detect_category_keyword(descriptions[len(categories)]))

        return categories[:len(descriptions)]

    except Exception:
        # Fallback: keyword match all if batch call fails
        return [detect_category_keyword(d) for d in descriptions]


def detect_category(description: str, use_ai: bool = True) -> str:
    """
    Detect category for a single expense.
    - use_ai=True (default): uses NVIDIA NIM if key is set
    - use_ai=False: always uses keyword matching
    """
    if use_ai and os.environ.get("NVIDIA_API_KEY"):
        return detect_category_ai(description)
    return detect_category_keyword(description)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_date(date_string: str) -> bool:
    try:
        datetime.strptime(date_string, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validate_amount(amount: float) -> tuple[bool, str]:
    if amount <= 0:
        return False, "Amount must be greater than 0"
    if amount > 10_000_000:
        return False, "Amount seems too large (max 1 crore per expense)"
    return True, ""


def validate_description(description: str) -> tuple[bool, str]:
    desc = description.strip()
    if not desc:
        return False, "Description cannot be empty"
    if len(desc) < 2:
        return False, "Description is too short (minimum 2 characters)"
    if len(desc) > 500:
        return False, "Description is too long (maximum 500 characters)"
    return True, ""


def row_to_dict(row) -> dict:
    return {
        "id":           row[0],
        "amount":       row[1],
        "description":  row[2],
        "category":     row[3],
        "expense_date": row[4],
        "created_at":   row[5],
    }


# ---------------------------------------------------------------------------
# MCP Tools — ADD
# ---------------------------------------------------------------------------


@mcp.tool()
def add_expense(
    user_token: str,
    amount: float,
    description: str,
    expense_date: str = None,
):
    """
    Add a single new expense.
    Category is automatically detected from the description (AI-powered if NVIDIA key is set).
    expense_date must be in YYYY-MM-DD format (defaults to today).
    Amount must be positive.
    """
    # Validate amount
    ok, err = validate_amount(amount)
    if not ok:
        return {"success": False, "error": err}

    # Validate description
    ok, err = validate_description(description)
    if not ok:
        return {"success": False, "error": err}

    if expense_date is None:
        expense_date = datetime.now().strftime("%Y-%m-%d")

    if not validate_date(expense_date):
        return {"success": False, "error": "Date must be in YYYY-MM-DD format"}

    category = detect_category(description.strip())
    db = get_db()
    user_id = validate_token(user_token)

    cur = db.execute(
        """
        INSERT INTO expenses (user_id, amount, description, category, expense_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, amount, description.strip(), category, expense_date, datetime.now().isoformat()),
    )
    db.commit()

    return {
        "success": True,
        "expense": {
            "id":           cur.lastrowid,
            "amount":       amount,
            "description":  description.strip(),
            "category":     category,
            "expense_date": expense_date,
        },
    }


@mcp.tool()
def add_bulk_expenses(user_token: str, expenses: list):
    """
    Add multiple expenses at once (maximum 100 at a time).
    Each expense must be a dict with: amount, description, and optionally expense_date.
    Example: [{"amount": 100, "description": "lunch"}, {"amount": 50, "description": "tea"}]
    AI categorization is used for batches of 40 or fewer (safe within 40 RPM limit).
    Larger batches (41-100) use fast keyword matching instead.
    """
    if not expenses:
        return {"success": False, "error": "Expenses list cannot be empty"}

    if len(expenses) > 100:
        return {"success": False, "error": f"Maximum 100 expenses allowed at once. You provided {len(expenses)}."}

    db = get_db()
    user_id = validate_token(user_token)
    results = []
    errors = []
    now = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")

    # -----------------------------------------------------------------------
    # Batch AI categorization — ONE API call for ALL descriptions at once
    # Much more efficient than calling AI per expense. No rate limit concern.
    # -----------------------------------------------------------------------
    valid_descriptions = []  # only descriptions that passed validation
    valid_indices = []       # their positions in the results list (to be filled)

    for i, exp in enumerate(expenses):
        index = i + 1

        # Validate required fields
        if "amount" not in exp or "description" not in exp:
            errors.append({"index": index, "error": "Each expense needs 'amount' and 'description'"})
            continue

        amount = exp["amount"]
        description = str(exp.get("description", "")).strip()
        expense_date = exp.get("expense_date", today)

        ok, err = validate_amount(amount)
        if not ok:
            errors.append({"index": index, "description": description, "error": err})
            continue

        ok, err = validate_description(description)
        if not ok:
            errors.append({"index": index, "error": err})
            continue

        if not validate_date(expense_date):
            errors.append({"index": index, "description": description, "error": "Invalid date format, use YYYY-MM-DD"})
            continue

        # This expense is valid — stage it for batch processing
        valid_descriptions.append(description)
        valid_indices.append({"amount": amount, "description": description, "expense_date": expense_date})

    # ONE API call to categorize ALL valid descriptions at once
    categories = detect_categories_batch([e["description"] for e in valid_indices])

    # Build all rows to insert at once
    rows_to_insert = [
        (user_id, exp["amount"], exp["description"], cat, exp["expense_date"], now)
        for exp, cat in zip(valid_indices, categories)
    ]

    # ONE executemany() call — inserts ALL expenses in a single DB operation
    db.executemany(
        "INSERT INTO expenses (user_id, amount, description, category, expense_date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        rows_to_insert,
    )
    db.commit()  # ONE commit for everything

    # Build results — fetch the inserted IDs
    cur = db.execute(
        f"SELECT id, amount, description, category, expense_date FROM expenses ORDER BY id DESC LIMIT {len(rows_to_insert)}"
    )
    inserted_rows = list(reversed(cur.fetchall()))
    results = [
        {"id": r[0], "amount": r[1], "description": r[2], "category": r[3], "expense_date": r[4]}
        for r in inserted_rows
    ]

    return {
        "success":  True,
        "inserted": len(results),
        "failed":   len(errors),
        "expenses": results,
        "errors":   errors if errors else None,
    }



# ---------------------------------------------------------------------------
# MCP Tools — GET / SEARCH
# ---------------------------------------------------------------------------


@mcp.tool()
def get_all_expenses(user_token: str, limit: int = 200):
    """
    Get expenses sorted by latest date.
    limit: max number of rows to return (default 200, max 1000).
    Use a smaller limit for faster responses, larger for full exports.
    Without a limit, large datasets would overflow ChatGPT's context window.
    """
    limit = max(1, min(limit, 1000))  # clamp between 1 and 1000
    db = get_db()
    cur = db.execute(
        "SELECT * FROM expenses ORDER BY expense_date DESC, id DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    return {
        "count": len(rows),
        "limit": limit,
        "expenses": [row_to_dict(row) for row in rows],
    }


@mcp.tool()
def get_expenses_by_date(user_token: str, date: str):
    """
    Get expenses for a specific date.
    Format: YYYY-MM-DD
    """
    if not validate_date(date):
        return {"success": False, "error": "Date must be in YYYY-MM-DD format"}

    db = get_db()
    cur = db.execute(
        "SELECT * FROM expenses WHERE expense_date = ? ORDER BY id DESC",
        (date,),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


@mcp.tool()
def search_expenses(user_token: str, keyword: str):
    """Search expenses by description or category."""
    db = get_db()
    cur = db.execute(
        """
        SELECT * FROM expenses
        WHERE description LIKE ?
           OR category    LIKE ?
        ORDER BY expense_date DESC
        """,
        (f"%{keyword}%", f"%{keyword}%"),
    )
    return [row_to_dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# MCP Tools — SUMMARIES & ANALYTICS
# ---------------------------------------------------------------------------


@mcp.tool()
def monthly_summary(user_token: str, month: str = None):
    """
    Get expense summary grouped by category for a given month.
    month format: YYYY-MM (defaults to current month).
    Example: monthly_summary("2026-06")
    """
    if month is None:
        month = datetime.now().strftime("%Y-%m")

    # Use date range instead of LIKE so the idx_expense_date index is used
    year_str, month_str = month.split("-")
    import calendar
    last_day = calendar.monthrange(int(year_str), int(month_str))[1]
    start_date = f"{month}-01"
    end_date   = f"{month}-{last_day:02d}"

    db = get_db()
    cur = db.execute(
        """
        SELECT category, SUM(amount), COUNT(*)
        FROM expenses
        WHERE expense_date >= ? AND expense_date <= ?
        GROUP BY category
        ORDER BY SUM(amount) DESC
        """,
        (start_date, end_date),
    )
    rows = cur.fetchall()

    categories = {}
    total = 0.0
    for row in rows:
        amt = float(row[1])
        categories[row[0]] = {"amount": amt, "count": row[2]}
        total += amt

    return {
        "month":         month,
        "start_date":    start_date,
        "end_date":      end_date,
        "total_expense": round(total, 2),
        "categories":    categories,
    }


@mcp.tool()
def weekly_summary(user_token: str):
    """
    Get expense summary for the current week (Monday to today).
    Shows total and category breakdown.
    """
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    db = get_db()

    cur = db.execute(
        """
        SELECT category, SUM(amount), COUNT(*)
        FROM expenses
        WHERE expense_date >= ? AND expense_date <= ?
        GROUP BY category
        ORDER BY SUM(amount) DESC
        """,
        (str(monday), str(today)),
    )
    rows = cur.fetchall()

    categories = {}
    total = 0.0
    for row in rows:
        amt = float(row[1])
        categories[row[0]] = {"amount": amt, "count": row[2]}
        total += amt

    return {
        "week_start":    str(monday),
        "week_end":      str(today),
        "total_expense": round(total, 2),
        "categories":    categories,
    }


@mcp.tool()
def yearly_summary(user_token: str, year: str = None):
    """
    Get expense summary broken down month by month for a given year.
    year format: YYYY (defaults to current year).
    Example: yearly_summary("2026")
    """
    if year is None:
        year = str(datetime.now().year)

    # Use date range instead of LIKE so the idx_expense_date index is used
    start_date = f"{year}-01-01"
    end_date   = f"{year}-12-31"

    db = get_db()
    cur = db.execute(
        """
        SELECT strftime('%Y-%m', expense_date) AS month,
               SUM(amount),
               COUNT(*)
        FROM expenses
        WHERE expense_date >= ? AND expense_date <= ?
        GROUP BY month
        ORDER BY month
        """,
        (start_date, end_date),
    )
    rows = cur.fetchall()

    months = {}
    grand_total = 0.0
    for row in rows:
        amt = float(row[1])
        months[row[0]] = {"amount": amt, "count": row[2]}
        grand_total += amt

    return {
        "year":        year,
        "grand_total": round(grand_total, 2),
        "months":      months,
    }


@mcp.tool()
def spending_analytics(user_token: str):
    """
    Get smart spending insights:
    - Total Income vs Total Expenses -> Net Savings & Savings Rate
    - Daily average this month
    - Top spending category
    - Biggest single expense ever
    - Total number of expenses
    - Spending this week vs last week
    """
    db = get_db()
    user_id = validate_token(user_token)
    today = datetime.now().date()
    month = datetime.now().strftime("%Y-%m")

    # Total Income
    cur = db.execute("SELECT SUM(amount) FROM income WHERE user_id = ? AND is_deleted = 0", (user_id,))
    total_income = cur.fetchone()[0] or 0.0

    # Total expenses count and sum
    cur = db.execute("SELECT COUNT(*), SUM(amount) FROM expenses WHERE user_id = ? AND is_deleted = 0", (user_id,))
    r = cur.fetchone()
    total_count = int(r[0] or 0)
    total_expenses = float(r[1] or 0.0)

    # Net Savings & Savings Rate
    net_savings = total_income - total_expenses
    savings_rate = round((net_savings / total_income * 100), 2) if total_income > 0 else 0.0

    # Daily average this month
    cur = db.execute(
        "SELECT SUM(amount), COUNT(DISTINCT expense_date) FROM expenses WHERE user_id = ? AND is_deleted = 0 AND expense_date LIKE ?",
        (user_id, month + "%")
    )
    r = cur.fetchone()
    monthly_total = float(r[0] or 0)
    active_days = int(r[1] or 1)
    daily_avg = round(monthly_total / active_days, 2)

    # Top spending category this month
    cur = db.execute(
        "SELECT category, SUM(amount) as total FROM expenses WHERE user_id = ? AND is_deleted = 0 AND expense_date LIKE ? GROUP BY category ORDER BY total DESC LIMIT 1",
        (user_id, month + "%")
    )
    top = cur.fetchone()
    top_category = {"category": top[0], "amount": float(top[1])} if top else None

    # Biggest single expense ever
    cur = db.execute(
        "SELECT id, amount, description, expense_date FROM expenses WHERE user_id = ? AND is_deleted = 0 ORDER BY amount DESC LIMIT 1",
        (user_id,)
    )
    big = cur.fetchone()
    biggest = {"id": big[0], "amount": float(big[1]), "description": big[2], "date": big[3]} if big else None

    # This week vs last week
    monday_this = today - timedelta(days=today.weekday())
    monday_last = monday_this - timedelta(days=7)
    sunday_last = monday_this - timedelta(days=1)

    cur = db.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND is_deleted = 0 AND expense_date >= ? AND expense_date <= ?",
        (user_id, str(monday_this), str(today))
    )
    this_week = round(float(cur.fetchone()[0] or 0), 2)

    cur = db.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id = ? AND is_deleted = 0 AND expense_date >= ? AND expense_date <= ?",
        (user_id, str(monday_last), str(sunday_last))
    )
    last_week = round(float(cur.fetchone()[0] or 0), 2)

    week_change = round(this_week - last_week, 2)
    week_change_pct = round((week_change / last_week * 100), 1) if last_week > 0 else None

    return {
        "wealth_summary": {
            "total_income": round(total_income, 2),
            "total_expenses": round(total_expenses, 2),
            "net_savings": round(net_savings, 2),
            "savings_rate_percent": savings_rate
        },
        "this_month":        month,
        "monthly_total":     round(monthly_total, 2),
        "daily_average":     daily_avg,
        "top_category":      top_category,
        "biggest_expense":   biggest,
        "total_expenses":    total_count,
        "total_spent_ever":  total_ever,
        "this_week":         this_week,
        "last_week":         last_week,
        "week_change":       week_change,
        "week_change_pct":   f"{'+' if week_change_pct and week_change_pct > 0 else ''}{week_change_pct}%" if week_change_pct is not None else "N/A",
    }


# ---------------------------------------------------------------------------
# MCP Tools — UPDATE
# ---------------------------------------------------------------------------


@mcp.tool()
def update_expense(
    expense_id: int,
    amount: float = None,
    description: str = None,
    expense_date: str = None,
    category: str = None,
):
    """
    Update one or more fields of an existing expense by its ID.
    Only pass the fields you want to change — the rest stay unchanged.
    category must be one of: food, transport, groceries, entertainment,
    healthcare, shopping, utilities, education, other.
    """
    if all(v is None for v in [amount, description, expense_date, category]):
        return {"success": False, "error": "Provide at least one field to update"}

    fields = []
    values = []

    if amount is not None:
        ok, err = validate_amount(amount)
        if not ok:
            return {"success": False, "error": err}
        fields.append("amount = ?")
        values.append(amount)

    if description is not None:
        ok, err = validate_description(description)
        if not ok:
            return {"success": False, "error": err}
        fields.append("description = ?")
        values.append(description.strip())
        # Auto-update category if description changes and category not explicitly set
        if category is None:
            new_cat = detect_category(description.strip())
            fields.append("category = ?")
            values.append(new_cat)

    if expense_date is not None:
        if not validate_date(expense_date):
            return {"success": False, "error": "Date must be in YYYY-MM-DD format"}
        fields.append("expense_date = ?")
        values.append(expense_date)

    if category is not None:
        if category not in VALID_CATEGORIES:
            return {"success": False, "error": f"Invalid category. Choose from: {', '.join(VALID_CATEGORIES)}"}
        # Override the auto-category from description if user explicitly sets one
        if "category = ?" in fields:
            idx = fields.index("category = ?")
            values[idx] = category
        else:
            fields.append("category = ?")
            values.append(category)

    values.append(expense_id)
    db = get_db()
    cur = db.execute(
        f"UPDATE expenses SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    db.commit()

    if cur.rowcount == 0:
        return {"success": False, "error": f"Expense ID {expense_id} not found"}

    # Return updated record
    cur2 = db.execute("SELECT * FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id))
    updated = cur2.fetchone()
    return {"success": True, "message": "Expense updated", "expense": row_to_dict(updated)}


# ---------------------------------------------------------------------------
# MCP Tools — DELETE
# ---------------------------------------------------------------------------


@mcp.tool()
def delete_expense(user_token: str, expense_id: int):
    """Delete a single expense by its ID."""
    db = get_db()
    user_id = validate_token(user_token)
    cur = db.execute("UPDATE expenses SET is_deleted = 1, deleted_at = datetime('now') WHERE id = ? AND user_id = ?", (expense_id, user_id))
    db.commit()

    if cur.rowcount == 0:
        return {"success": False, "error": f"Expense ID {expense_id} not found"}

    return {"success": True, "message": f"Expense ID {expense_id} deleted"}


@mcp.tool()
def bulk_delete_expenses(user_token: str, ids: list):
    """
    Delete multiple expenses by a list of IDs at once.
    Example: bulk_delete_expenses([10, 11, 12, 13])
    Maximum 100 IDs at a time.
    """
    if not ids:
        return {"success": False, "error": "IDs list cannot be empty"}

    if len(ids) > 100:
        return {"success": False, "error": f"Maximum 100 IDs allowed. You provided {len(ids)}."}

    placeholders = ",".join("?" * len(ids))
    db = get_db()
    cur = db.execute(f"DELETE FROM expenses WHERE id IN ({placeholders})", ids)
    db.commit()

    return {
        "success":  True,
        "deleted":  cur.rowcount,
        "message":  f"{cur.rowcount} of {len(ids)} expenses deleted",
    }


@mcp.tool()
def delete_expenses_by_category(user_token: str, category: str):
    """
    Delete all expenses in a specific category.
    category must be one of: food, transport, groceries, entertainment,
    healthcare, shopping, utilities, education, other.
    """
    if category not in VALID_CATEGORIES:
        return {"success": False, "error": f"Invalid category. Choose from: {', '.join(VALID_CATEGORIES)}"}

    db = get_db()
    # First count how many will be deleted
    cur = db.execute("SELECT COUNT(*) FROM expenses WHERE category = ?", (category,))
    count = cur.fetchone()[0]

    if count == 0:
        return {"success": False, "error": f"No expenses found in category '{category}'"}

    db.execute("DELETE FROM expenses WHERE category = ?", (category,))
    db.commit()

    return {
        "success": True,
        "deleted": count,
        "message": f"{count} '{category}' expenses deleted",
    }


@mcp.tool()
def delete_expenses_by_description(user_token: str, keyword: str):
    """
    Delete all expenses whose description contains the given keyword.
    Example: delete_expenses_by_description("netflix") deletes all Netflix entries.
    """
    if not keyword or len(keyword.strip()) < 2:
        return {"success": False, "error": "Keyword must be at least 2 characters"}

    db = get_db()
    # First show what will be deleted
    cur = db.execute(
        "SELECT COUNT(*) FROM expenses WHERE description LIKE ?",
        (f"%{keyword}%",)
    )
    count = cur.fetchone()[0]

    if count == 0:
        return {"success": False, "error": f"No expenses found matching '{keyword}'"}

    db.execute("DELETE FROM expenses WHERE description LIKE ?", (f"%{keyword}%",))
    db.commit()

    return {
        "success": True,
        "deleted": count,
        "message": f"{count} expenses matching '{keyword}' deleted",
    }


@mcp.tool()
def delete_all_expenses(user_token: str, confirm: bool = False):
    """
    Delete ALL expenses permanently.
    You MUST pass confirm=True to execute this. This cannot be undone.
    """
    if not confirm:
        return {
            "success": False,
            "error":   "Safety check failed. Pass confirm=True to delete ALL expenses. THIS CANNOT BE UNDONE.",
        }

    db = get_db()
    cur = db.execute("SELECT COUNT(*) FROM expenses")
    total = cur.fetchone()[0]

    db.execute("DELETE FROM expenses")
    db.commit()

    return {
        "success": True,
        "deleted": total,
        "message": f"All {total} expenses have been permanently deleted.",
    }


# ---------------------------------------------------------------------------
# MCP Tools — BUDGET SYSTEM
# ---------------------------------------------------------------------------


@mcp.tool()
def set_budget(user_token: str, category: str, amount: float):
    """
    Set or update a monthly budget for a category.
    The budget is stored PERMANENTLY — it applies to every future month
    automatically without needing to be reset each month.
    To change it, call set_budget again with the new amount.
    Categories: food, transport, groceries, entertainment, healthcare,
                shopping, utilities, education, other
    Example: set_budget('food', 3000) — limits food spend to Rs 3000/month
    """
    category = category.strip().lower()
    if category not in VALID_CATEGORIES:
        return {
            "success": False,
            "error":   f"Invalid category '{category}'. Valid: {', '.join(VALID_CATEGORIES)}",
        }

    ok, err = validate_amount(amount)
    if not ok:
        return {"success": False, "error": err}

    now = datetime.now().isoformat()
    db  = get_db()

    # UPSERT — insert new or update existing budget for this category
    db.execute(
        """
        INSERT INTO budgets (category, amount, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category) DO UPDATE SET
            amount     = excluded.amount,
            updated_at = excluded.updated_at
        """,
        (category, amount, now, now),
    )
    db.commit()

    return {
        "success":  True,
        "message":  f"Budget set: Rs {amount} per month for '{category}'. Applies every month automatically.",
        "category": category,
        "amount":   amount,
    }


@mcp.tool()
def get_budget_status(user_token: str, month: str = None):
    """
    Show spending vs budget for all categories for a given month.
    Highlights which categories are over budget, near limit, or safe.
    month format: YYYY-MM (defaults to current month).
    """
    if month is None:
        month = datetime.now().strftime("%Y-%m")

    import calendar
    year_str, month_str = month.split("-")
    last_day   = calendar.monthrange(int(year_str), int(month_str))[1]
    start_date = f"{month}-01"
    end_date   = f"{month}-{last_day:02d}"

    db = get_db()

    # Fetch all set budgets
    cur = db.execute("SELECT category, amount FROM budgets ORDER BY category")
    budgets = {row[0]: row[1] for row in cur.fetchall()}

    if not budgets:
        return {
            "success": False,
            "error":   "No budgets set yet. Use set_budget(category, amount) to set one.",
        }

    # Fetch this month's spending per category
    cur = db.execute(
        """
        SELECT category, SUM(amount)
        FROM expenses
        WHERE expense_date >= ? AND expense_date <= ?
        GROUP BY category
        """,
        (start_date, end_date),
    )
    spent_map = {row[0]: float(row[1]) for row in cur.fetchall()}

    result    = {}
    total_budget = 0.0
    total_spent  = 0.0

    for cat, budget_amt in budgets.items():
        spent   = spent_map.get(cat, 0.0)
        pct     = round((spent / budget_amt) * 100, 1) if budget_amt > 0 else 0
        remaining = round(budget_amt - spent, 2)

        if pct >= 100:
            status = "OVER BUDGET"
        elif pct >= 80:
            status = "WARNING — near limit"
        else:
            status = "OK"

        result[cat] = {
            "budget":    budget_amt,
            "spent":     round(spent, 2),
            "remaining": remaining,
            "percent":   pct,
            "status":    status,
        }
        total_budget += budget_amt
        total_spent  += spent

    return {
        "month":        month,
        "total_budget": round(total_budget, 2),
        "total_spent":  round(total_spent, 2),
        "remaining":    round(total_budget - total_spent, 2),
        "categories":   result,
    }


@mcp.tool()
def delete_budget(user_token: str, category: str):
    """
    Remove the monthly budget for a category.
    After deletion, no budget check will be applied for that category.
    """
    category = category.strip().lower()
    db = get_db()
    cur = db.execute("SELECT id FROM budgets WHERE category = ?", (category,))
    if not cur.fetchone():
        return {"success": False, "error": f"No budget found for category '{category}'."}

    db.execute("DELETE FROM budgets WHERE category = ?", (category,))
    db.commit()
    return {
        "success": True,
        "message": f"Budget for '{category}' removed. No limit will be applied going forward.",
    }


# ---------------------------------------------------------------------------
# MCP Tools — REMINDERS
# ---------------------------------------------------------------------------


@mcp.tool()
def set_reminder(
    description:   str,
    due_date:      str   = None,
    amount:        float = None,
    is_recurring:  bool  = False,
    recurring_day: int   = None,
):
    """
    Set a payment reminder.
    Two types:
      1. One-time:  due_date="YYYY-MM-DD"  (e.g. "pay electricity bill on Aug 5")
      2. Recurring: is_recurring=True, recurring_day=<day>  (e.g. "pay rent every 1st")
    amount is optional — use it when you know the fixed amount (e.g. rent Rs 8000).
    Examples:
      set_reminder('Pay electricity', due_date='2026-08-05', amount=600)
      set_reminder('Pay rent', amount=8000, is_recurring=True, recurring_day=1)
    """
    description = description.strip()
    if len(description) < 2:
        return {"success": False, "error": "Description must be at least 2 characters."}

    # Validate for one-time reminder
    if not is_recurring:
        if not due_date:
            return {"success": False, "error": "due_date is required for one-time reminders (format: YYYY-MM-DD)."}
        if not validate_date(due_date):
            return {"success": False, "error": "due_date must be in YYYY-MM-DD format."}

    # Validate for recurring reminder
    if is_recurring:
        if recurring_day is None:
            return {"success": False, "error": "recurring_day (1-31) is required for recurring reminders."}
        if not (1 <= recurring_day <= 31):
            return {"success": False, "error": "recurring_day must be between 1 and 31."}

    # Validate optional amount
    if amount is not None:
        ok, err = validate_amount(amount)
        if not ok:
            return {"success": False, "error": err}

    now = datetime.now().isoformat()
    db  = get_db()
    cur = db.execute(
        """
        INSERT INTO reminders
            (description, amount, due_date, is_recurring, recurring_day, is_done, created_at)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (description, amount, due_date, int(is_recurring), recurring_day, now),
    )
    db.commit()

    msg = (
        f"Recurring reminder set: '{description}' every {recurring_day} of the month."
        if is_recurring
        else f"Reminder set: '{description}' due on {due_date}."
    )
    return {
        "success":      True,
        "id":           cur.lastrowid,
        "message":      msg,
        "description":  description,
        "amount":       amount,
        "due_date":     due_date,
        "is_recurring": is_recurring,
        "recurring_day": recurring_day,
    }


@mcp.tool()
def get_upcoming_reminders(user_token: str, days: int = 7):
    """
    Show all reminders due within the next N days (default: 7).
    Includes:
      - One-time reminders with a due_date in the window
      - Recurring reminders whose next trigger falls within the window
    Only shows reminders not yet marked as done.
    """
    days  = max(1, min(days, 90))   # clamp 1–90 days
    today = datetime.now().date()
    until = today + timedelta(days=days)
    db    = get_db()

    cur = db.execute(
        "SELECT id, description, amount, due_date, is_recurring, recurring_day FROM reminders WHERE is_done = 0"
    )
    all_reminders = cur.fetchall()

    upcoming = []
    for row in all_reminders:
        rid, desc, amt, due_date_str, is_rec, rec_day = row

        if is_rec:
            # Calculate next occurrence of recurring_day in current or next month
            # Try this month first, then next month
            for delta_months in [0, 1]:
                try:
                    import calendar
                    check_year  = today.year  + (1 if today.month + delta_months > 12 else 0)
                    check_month = (today.month + delta_months - 1) % 12 + 1
                    max_day = calendar.monthrange(check_year, check_month)[1]
                    actual_day  = min(rec_day, max_day)
                    next_due = datetime(check_year, check_month, actual_day).date()
                    if today <= next_due <= until:
                        days_left = (next_due - today).days
                        upcoming.append({
                            "id":          rid,
                            "description": desc,
                            "amount":      amt,
                            "due_date":    str(next_due),
                            "days_until":  days_left,
                            "type":        "recurring (monthly)",
                            "urgency":     "TODAY" if days_left == 0 else ("TOMORROW" if days_left == 1 else f"in {days_left} days"),
                        })
                        break
                except ValueError:
                    continue
        else:
            if due_date_str:
                try:
                    due = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                    if today <= due <= until:
                        days_left = (due - today).days
                        upcoming.append({
                            "id":          rid,
                            "description": desc,
                            "amount":      amt,
                            "due_date":    due_date_str,
                            "days_until":  days_left,
                            "type":        "one-time",
                            "urgency":     "TODAY" if days_left == 0 else ("TOMORROW" if days_left == 1 else f"in {days_left} days"),
                        })
                except ValueError:
                    continue

    # Sort by days_until ascending (most urgent first)
    upcoming.sort(key=lambda x: x["days_until"])

    return {
        "window_days": days,
        "count":       len(upcoming),
        "reminders":   upcoming,
        "message":     f"{len(upcoming)} reminder(s) due in the next {days} days." if upcoming else f"No reminders due in the next {days} days.",
    }


@mcp.tool()
def delete_reminder(user_token: str, reminder_id: int):
    """
    Permanently delete a reminder by its ID.
    Use get_upcoming_reminders() first to see IDs.
    """
    db  = get_db()
    cur = db.execute("SELECT description FROM reminders WHERE id = ?", (reminder_id,))
    row = cur.fetchone()
    if not row:
        return {"success": False, "error": f"No reminder found with ID {reminder_id}."}

    db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    db.commit()
    return {
        "success": True,
        "message": f"Reminder '{row[0]}' (ID {reminder_id}) deleted.",
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)

@mcp.tool()
def get_trash(user_token: str):
    """View all expenses that have been soft-deleted (moved to trash)."""
    db = get_db()
    user_id = validate_token(user_token)
    cur = db.execute(
        "SELECT id, amount, description, category, expense_date, deleted_at FROM expenses WHERE user_id = ? AND is_deleted = 1 ORDER BY deleted_at DESC",
        (user_id,)
    )
    rows = cur.fetchall()
    return {
        "count": len(rows),
        "trash": [
            {"id": r[0], "amount": r[1], "description": r[2], "category": r[3], "expense_date": r[4], "deleted_at": r[5]}
            for r in rows
        ]
    }

@mcp.tool()
def restore_expense(user_token: str, expense_id: int):
    """Restore an expense from the trash back to active expenses."""
    db = get_db()
    user_id = validate_token(user_token)
    cur = db.execute(
        "UPDATE expenses SET is_deleted = 0, deleted_at = NULL WHERE id = ? AND user_id = ? AND is_deleted = 1",
        (expense_id, user_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return {"success": False, "error": f"Expense ID {expense_id} not found in trash."}
    return {"success": True, "message": f"Expense {expense_id} successfully restored."}

@mcp.tool()
def empty_trash(user_token: str, confirm: bool = False):
    """Permanently delete all expenses currently in the trash. Requires confirm=True."""
    if not confirm:
        return {"error": "You must set confirm=True to permanently empty the trash."}
    db = get_db()
    user_id = validate_token(user_token)
    cur = db.execute("DELETE FROM expenses WHERE user_id = ? AND is_deleted = 1", (user_id,))
    db.commit()
    return {"success": True, "message": f"Permanently deleted {cur.rowcount} items from trash."}

@mcp.tool()
def permanent_delete_expense(user_token: str, expense_id: int):
    """Permanently delete a specific expense by ID, bypassing the trash can."""
    db = get_db()
    user_id = validate_token(user_token)
    cur = db.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id))
    db.commit()
    if cur.rowcount == 0:
        return {"success": False, "error": f"Expense ID {expense_id} not found."}
    return {"success": True, "message": f"Expense {expense_id} permanently deleted."}


# ---------------------------------------------------------------------------
# MCP Tools - INCOME
# ---------------------------------------------------------------------------

@mcp.tool()
def add_income(
    user_token: str,
    amount: float,
    source: str,
    income_date: str = None,
):
    """
    Add a new income source (e.g. Salary, Freelance, Gift).
    income_date must be in YYYY-MM-DD format (defaults to today).
    Amount must be positive.
    """
    ok, err = validate_amount(amount)
    if not ok: return {"success": False, "error": err}
    if not source.strip(): return {"success": False, "error": "Source cannot be empty."}
    
    if income_date is None:
        income_date = datetime.now().strftime("%Y-%m-%d")
    if not validate_date(income_date):
        return {"success": False, "error": "Date must be in YYYY-MM-DD format"}

    db = get_db()
    user_id = validate_token(user_token)

    cur = db.execute(
        """
        INSERT INTO income (user_id, amount, source, income_date, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, amount, source.strip(), income_date, datetime.now().isoformat()),
    )
    db.commit()
    return {
        "success": True,
        "income": {
            "id":          cur.lastrowid,
            "amount":      amount,
            "source":      source.strip(),
            "income_date": income_date,
        },
    }

@mcp.tool()
def get_income(user_token: str, limit: int = 50):
    """Get recent income entries sorted by date."""
    db = get_db()
    user_id = validate_token(user_token)
    cur = db.execute(
        f"SELECT id, amount, source, income_date FROM income WHERE user_id = ? AND is_deleted = 0 ORDER BY income_date DESC LIMIT {limit}",
        (user_id,)
    )
    rows = cur.fetchall()
    return {
        "count": len(rows),
        "income": [{"id": r[0], "amount": r[1], "source": r[2], "income_date": r[3]} for r in rows]
    }

@mcp.tool()
def delete_income(user_token: str, income_id: int):
    """Soft delete an income entry by ID."""
    db = get_db()
    user_id = validate_token(user_token)
    cur = db.execute(
        "UPDATE income SET is_deleted = 1, deleted_at = datetime('now') WHERE id = ? AND user_id = ?", 
        (income_id, user_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return {"success": False, "error": f"Income ID {income_id} not found."}
    return {"success": True, "message": f"Income {income_id} moved to trash."}


# ---------------------------------------------------------------------------
# MCP Tools - EXPORT
# ---------------------------------------------------------------------------
@mcp.tool()
def export_to_csv(user_token: str, year: str = None, month: str = None):
    """
    Export expenses to CSV format. 
    Can filter by year (e.g. '2026') and/or month (e.g. '07').
    Returns the raw CSV string.
    """
    import csv
    import io
    db = get_db()
    user_id = validate_token(user_token)
    
    query = "SELECT id, amount, description, category, expense_date FROM expenses WHERE user_id = ? AND is_deleted = 0"
    params = [user_id]
    
    if year:
        query += " AND substr(expense_date, 1, 4) = ?"
        params.append(year)
    if month:
        # Ensures month is 2 digits
        month = str(month).zfill(2)
        query += " AND substr(expense_date, 6, 2) = ?"
        params.append(month)
        
    query += " ORDER BY expense_date DESC"
    
    cur = db.execute(query, params)
    rows = cur.fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["ID", "Date", "Category", "Amount", "Description"])
    
    # Rows
    for r in rows:
        writer.writerow([r[0], r[4], r[3], r[1], r[2]])
        
    return {
        "count": len(rows),
        "csv_content": output.getvalue()
    }
