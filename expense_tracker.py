from fastmcp import FastMCP
import os
import sqlite3
import tempfile
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path
from shutil import copy2

mcp = FastMCP("Expense Tracker")

DEFAULT_DB = Path(
    __file__
).resolve().parent / "expenses.db"


def _path_is_writable(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        test_file = path.parent / f".write_test_{os.getpid()}"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return True
    except OSError:
        return False


def _candidate_db_paths() -> list[Path]:
    candidates = []

    env_path = os.environ.get("EXPENSES_DB_PATH")
    if env_path:
        candidates.append(Path(env_path))

    candidates.append(DEFAULT_DB)
    candidates.append(Path.home() / ".expense_tracker" / "expenses.db")
    candidates.append(Path(tempfile.gettempdir()) / "expenses.db")

    unique_candidates = []
    seen = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve(strict=False)
        if resolved not in seen:
            unique_candidates.append(resolved)
            seen.add(resolved)

    return unique_candidates


def resolve_db_path() -> Path:
    candidates = _candidate_db_paths()

    for candidate in candidates:
        if _path_is_writable(candidate):
            target = candidate
            break
    else:
        target = candidates[-1]

    if not target.exists():
        for source in candidates:
            if source != target and source.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    copy2(source, target)
                    break
                except OSError:
                    continue

    return target


DB = resolve_db_path()

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


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB))

    conn.execute("""
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount REAL NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        expense_date TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


init_db()


def detect_category(description: str):

    desc = description.lower()

    all_words = []

    for category, keywords in CATEGORIES.items():

        for keyword in keywords:

            if keyword in desc:
                return category

            all_words.append(keyword)

    words = desc.split()

    for word in words:

        match = get_close_matches(
            word,
            all_words,
            n=1,
            cutoff=0.7
        )

        if match:

            matched_word = match[0]

            for category, keywords in CATEGORIES.items():

                if matched_word in keywords:
                    return category

    return "other"


def validate_date(date_string: str):

    try:
        datetime.strptime(date_string, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def row_to_dict(row):

    return {
        "id": row[0],
        "amount": row[1],
        "description": row[2],
        "category": row[3],
        "expense_date": row[4],
        "created_at": row[5]
    }


@mcp.tool()
def add_expense(
    amount: float,
    description: str,
    expense_date: str = None
):
    """
    Add a new expense.
    Category is automatically detected from description.
    """

    if expense_date is None:
        expense_date = datetime.now().strftime("%Y-%m-%d")

    if not validate_date(expense_date):
        return {
            "success": False,
            "error": "Date must be in YYYY-MM-DD format"
        }

    category = detect_category(description)

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO expenses(
            amount,
            description,
            category,
            expense_date,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            amount,
            description,
            category,
            expense_date,
            datetime.now().isoformat()
        )
    )

    conn.commit()

    expense_id = cur.lastrowid

    conn.close()

    return {
        "success": True,
        "expense": {
            "id": expense_id,
            "amount": amount,
            "description": description,
            "category": category,
            "expense_date": expense_date
        }
    }


@mcp.tool()
def get_all_expenses():
    """
    Get all expenses sorted by latest date.
    """

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM expenses
        ORDER BY expense_date DESC, id DESC
    """)

    rows = cur.fetchall()

    conn.close()

    return [row_to_dict(row) for row in rows]


@mcp.tool()
def get_expenses_by_date(date: str):
    """
    Get expenses for a specific date.
    Format: YYYY-MM-DD
    """

    if not validate_date(date):
        return {
            "success": False,
            "error": "Date must be in YYYY-MM-DD format"
        }

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM expenses
        WHERE expense_date = ?
        ORDER BY id DESC
        """,
        (date,)
    )

    rows = cur.fetchall()

    conn.close()

    return [row_to_dict(row) for row in rows]


@mcp.tool()
def search_expenses(keyword: str):
    """
    Search expenses by description or category.
    """

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM expenses
        WHERE description LIKE ?
           OR category LIKE ?
        ORDER BY expense_date DESC
        """,
        (
            f"%{keyword}%",
            f"%{keyword}%"
        )
    )

    rows = cur.fetchall()

    conn.close()

    return [row_to_dict(row) for row in rows]


@mcp.tool()
def monthly_summary():
    """
    Get current month's expense summary.
    """

    month = datetime.now().strftime("%Y-%m")

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute(
        """
        SELECT category,
               SUM(amount)
        FROM expenses
        WHERE expense_date LIKE ?
        GROUP BY category
        """,
        (month + "%",)
    )

    rows = cur.fetchall()

    conn.close()

    categories = {}
    total = 0

    for category, amount in rows:
        amount = float(amount)
        categories[category] = amount
        total += amount

    return {
        "month": month,
        "total_expense": total,
        "categories": categories
    }


@mcp.tool()
def update_expense(
    expense_id: int,
    new_amount: float
):
    """
    Update expense amount by ID.
    """

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE expenses
        SET amount = ?
        WHERE id = ?
        """,
        (new_amount, expense_id)
    )

    conn.commit()

    updated = cur.rowcount

    conn.close()

    if updated == 0:
        return {
            "success": False,
            "error": "Expense not found"
        }

    return {
        "success": True,
        "message": "Expense updated"
    }


@mcp.tool()
def delete_expense(
    expense_id: int
):
    """
    Delete expense by ID.
    """

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM expenses
        WHERE id = ?
        """,
        (expense_id,)
    )

    conn.commit()

    deleted = cur.rowcount

    conn.close()

    if deleted == 0:
        return {
            "success": False,
            "error": "Expense not found"
        }

    return {
        "success": True,
        "message": "Expense deleted"
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)