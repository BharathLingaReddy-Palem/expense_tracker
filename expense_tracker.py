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
    """Return a cached libsql connection to the Turso cloud database."""
    global _db
    if _db is None:
        url = os.environ.get("TURSO_DATABASE_URL")
        token = os.environ.get("TURSO_AUTH_TOKEN", "")
        if not url:
            raise RuntimeError(
                "TURSO_DATABASE_URL environment variable is not set. "
                "Add it in your Horizon deployment settings."
            )
        _db = libsql.connect(url, auth_token=token)
    return _db


def init_db():
    """Create the expenses table if it does not already exist."""
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            amount       REAL    NOT NULL,
            description  TEXT    NOT NULL,
            category     TEXT    NOT NULL,
            expense_date TEXT    NOT NULL,
            created_at   TEXT    NOT NULL
        )
    """)
    db.commit()


init_db()

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
    """AI-powered category detection using NVIDIA NIM via LangChain."""
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        return detect_category_keyword(description)

    try:
        client = ChatNVIDIA(
            model="meta/llama-3.1-8b-instruct",
            api_key=api_key,
            temperature=0.2,
            top_p=0.7,
            max_completion_tokens=50,  # category name is 1-3 tokens; 50 gives safe headroom
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


def detect_category(description: str, use_ai: bool = True) -> str:
    """
    Detect category.
    - use_ai=True (default): uses NVIDIA NIM if key is set (for single expense adds)
    - use_ai=False: always uses keyword matching (for bulk adds to avoid 40 RPM limit)
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

    cur = db.execute(
        """
        INSERT INTO expenses (amount, description, category, expense_date, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (amount, description.strip(), category, expense_date, datetime.now().isoformat()),
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
def add_bulk_expenses(expenses: list):
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
    results = []
    errors = []
    now = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")

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

        # Use AI for small batches (≤40) — safe within 40 RPM limit
        # Use keyword matching for large batches (>40) — avoids rate limit
        use_ai_for_this_batch = len(expenses) <= 40
        category = detect_category(description, use_ai=use_ai_for_this_batch)
        cur = db.execute(
            "INSERT INTO expenses (amount, description, category, expense_date, created_at) VALUES (?, ?, ?, ?, ?)",
            (amount, description, category, expense_date, now),
        )
        results.append({
            "id":          cur.lastrowid,
            "amount":      amount,
            "description": description,
            "category":    category,
            "expense_date": expense_date,
        })

    db.commit()

    return {
        "success": True,
        "inserted": len(results),
        "failed": len(errors),
        "expenses": results,
        "errors": errors if errors else None,
    }


# ---------------------------------------------------------------------------
# MCP Tools — GET / SEARCH
# ---------------------------------------------------------------------------


@mcp.tool()
def get_all_expenses():
    """Get all expenses sorted by latest date."""
    db = get_db()
    cur = db.execute(
        "SELECT * FROM expenses ORDER BY expense_date DESC, id DESC"
    )
    return [row_to_dict(row) for row in cur.fetchall()]


@mcp.tool()
def get_expenses_by_date(date: str):
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
def search_expenses(keyword: str):
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
def monthly_summary(month: str = None):
    """
    Get expense summary grouped by category for a given month.
    month format: YYYY-MM (defaults to current month).
    Example: monthly_summary("2026-06")
    """
    if month is None:
        month = datetime.now().strftime("%Y-%m")

    db = get_db()
    cur = db.execute(
        """
        SELECT category, SUM(amount), COUNT(*)
        FROM expenses
        WHERE expense_date LIKE ?
        GROUP BY category
        ORDER BY SUM(amount) DESC
        """,
        (month + "%",),
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
        "total_expense": round(total, 2),
        "categories":    categories,
    }


@mcp.tool()
def weekly_summary():
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
def yearly_summary(year: str = None):
    """
    Get expense summary broken down month by month for a given year.
    year format: YYYY (defaults to current year).
    Example: yearly_summary("2026")
    """
    if year is None:
        year = str(datetime.now().year)

    db = get_db()
    cur = db.execute(
        """
        SELECT strftime('%Y-%m', expense_date) as month,
               SUM(amount),
               COUNT(*)
        FROM expenses
        WHERE expense_date LIKE ?
        GROUP BY month
        ORDER BY month
        """,
        (year + "%",),
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
def spending_analytics():
    """
    Get smart spending insights:
    - Daily average this month
    - Top spending category
    - Biggest single expense ever
    - Total number of expenses
    - Spending this week vs last week
    """
    db = get_db()
    today = datetime.now().date()
    month = datetime.now().strftime("%Y-%m")

    # Daily average this month
    cur = db.execute(
        "SELECT SUM(amount), COUNT(DISTINCT expense_date) FROM expenses WHERE expense_date LIKE ?",
        (month + "%",)
    )
    r = cur.fetchone()
    monthly_total = float(r[0] or 0)
    active_days = int(r[1] or 1)
    daily_avg = round(monthly_total / active_days, 2)

    # Top spending category this month
    cur = db.execute(
        "SELECT category, SUM(amount) as total FROM expenses WHERE expense_date LIKE ? GROUP BY category ORDER BY total DESC LIMIT 1",
        (month + "%",)
    )
    top = cur.fetchone()
    top_category = {"category": top[0], "amount": float(top[1])} if top else None

    # Biggest single expense ever
    cur = db.execute(
        "SELECT id, amount, description, expense_date FROM expenses ORDER BY amount DESC LIMIT 1"
    )
    big = cur.fetchone()
    biggest = {"id": big[0], "amount": float(big[1]), "description": big[2], "date": big[3]} if big else None

    # Total expenses count and sum
    cur = db.execute("SELECT COUNT(*), SUM(amount) FROM expenses")
    r = cur.fetchone()
    total_count = int(r[0] or 0)
    total_ever = round(float(r[1] or 0), 2)

    # This week vs last week
    monday_this = today - timedelta(days=today.weekday())
    monday_last = monday_this - timedelta(days=7)
    sunday_last = monday_this - timedelta(days=1)

    cur = db.execute(
        "SELECT SUM(amount) FROM expenses WHERE expense_date >= ? AND expense_date <= ?",
        (str(monday_this), str(today))
    )
    this_week = round(float(cur.fetchone()[0] or 0), 2)

    cur = db.execute(
        "SELECT SUM(amount) FROM expenses WHERE expense_date >= ? AND expense_date <= ?",
        (str(monday_last), str(sunday_last))
    )
    last_week = round(float(cur.fetchone()[0] or 0), 2)

    week_change = round(this_week - last_week, 2)
    week_change_pct = round((week_change / last_week * 100), 1) if last_week > 0 else None

    return {
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
    cur2 = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
    updated = cur2.fetchone()
    return {"success": True, "message": "Expense updated", "expense": row_to_dict(updated)}


# ---------------------------------------------------------------------------
# MCP Tools — DELETE
# ---------------------------------------------------------------------------


@mcp.tool()
def delete_expense(expense_id: int):
    """Delete a single expense by its ID."""
    db = get_db()
    cur = db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    db.commit()

    if cur.rowcount == 0:
        return {"success": False, "error": f"Expense ID {expense_id} not found"}

    return {"success": True, "message": f"Expense ID {expense_id} deleted"}


@mcp.tool()
def bulk_delete_expenses(ids: list):
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
def delete_expenses_by_category(category: str):
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
def delete_expenses_by_description(keyword: str):
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
def delete_all_expenses(confirm: bool = False):
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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
