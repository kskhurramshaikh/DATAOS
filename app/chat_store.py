from app.db import get_conn


def create_conversation(user_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return cur.lastrowid


def get_conversation_owner(conversation_id: int) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return row["user_id"] if row else None


def add_message(conversation_id: int, role: str, content: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )
        conn.commit()


def get_history(conversation_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def list_conversations(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at FROM conversations WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [{"id": r["id"], "created_at": r["created_at"]} for r in rows]
