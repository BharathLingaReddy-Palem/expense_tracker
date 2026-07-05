from fastmcp import FastMCP
import os
import libsql
from datetime import datetime
from difflib import get_close_matches
from dotenv import load_dotenv

load_dotenv()  # loads .env file automatically (works locally and on Horizon)

mcp = FastMCP("Expense Tracker")

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------
# Set these environment variables in your Horizon deployment dashboard:
#   TURSO_DATABASE_URL  ->  libsql://<your-db-name>.turso.io
#   TURSO_AUTH_TOKEN    ->  your Turso auth token
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
# Category detection
# ---------------------------------------------------------------------------

CATEGORIES = {
    "food": [
        "food", "dinner", "lunch", "breakfast",
        "restaurant", "hotel", "tea", "coffee", "snacks"
    ],
    "transport": [
        "cab", "uber", "rapido", "auto",
        "bus", "train", "metro"
    ],
    "groceries": [
        "grocery", "groceries", "vegetables",
        "rice", "milk", "fruits"
    ],
    "entertainment": [
        "movie", "netflix", "prime", "game"
    ],
    "healthcare": [
        "medicine", "doctor", "hospital"
    ],
    "shopping": [
        "shopping", "clothes", "shirt", "shoes"
    ],
    "other": []
}


def detect_category(description: str) -> str:
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


def validate_date(date_string: str) -> bool:
    try:
        datetime.strptime(date_string, "%Y-%m-%d")
        return True
    except ValueError:
        return False


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
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def add_expense(
    amount: float,
    description: str,
    expense_date: str = None,
):
    """
    Add a new expense.
    Category is automatically detected from the description.
    expense_date must be in YYYY-MM-DD format (defaults to today).
    """
    if expense_date is None:
        expense_date = datetime.now().strftime("%Y-%m-%d")

    if not validate_date(expense_date):
        return {"success": False, "error": "Date must be in YYYY-MM-DD format"}

    category = detect_category(description)
    db = get_db()

    cur = db.execute(
        """
        INSERT INTO expenses (amount, description, category, expense_date, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (amount, description, category, expense_date, datetime.now().isoformat()),
    )
    db.commit()

    return {
        "success": True,
        "expense": {
            "id":           cur.lastrowid,
            "amount":       amount,
            "description":  description,
            "category":     category,
            "expense_date": expense_date,
        },
    }


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


@mcp.tool()
def monthly_summary():
    """Get the current month expense summary grouped by category."""
    month = datetime.now().strftime("%Y-%m")
    db = get_db()
    cur = db.execute(
        """
        SELECT category, SUM(amount)
        FROM expenses
        WHERE expense_date LIKE ?
        GROUP BY category
        """,
        (month + "%",),
    )
    rows = cur.fetchall()

    categories = {}
    total = 0.0
    for row in rows:
        amt = float(row[1])
        categories[row[0]] = amt
        total += amt

    return {
        "month":         month,
        "total_expense": total,
        "categories":    categories,
    }


@mcp.tool()
def update_expense(expense_id: int, new_amount: float):
    """Update the amount of an existing expense by its ID."""
    db = get_db()
    cur = db.execute(
        "UPDATE expenses SET amount = ? WHERE id = ?",
        (new_amount, expense_id),
    )
    db.commit()

    if cur.rowcount == 0:
        return {"success": False, "error": "Expense not found"}

    return {"success": True, "message": "Expense updated"}


@mcp.tool()
def delete_expense(expense_id: int):
    """Delete an expense by its ID."""
    db = get_db()
    cur = db.execute(
        "DELETE FROM expenses WHERE id = ?",
        (expense_id,),
    )
    db.commit()

    if cur.rowcount == 0:
        return {"success": False, "error": "Expense not found"}

    return {"success": True, "message": "Expense deleted"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
