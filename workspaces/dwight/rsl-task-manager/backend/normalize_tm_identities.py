from pathlib import Path
import sqlite3

CANONICAL_USERS = ["Dwight", "Jerry", "Resi", "Druck", "Aaron", "Taylor"]
LOGIN_USERS = ["Dwight", "Jerry", "Resi", "Druck", "Aaron", "Taylor"]
ALIASES = {
    "claw": "Jerry",
    "aaron": "Aaron",
    "taylor": "Taylor",
}
REMOVE_USERS = {"telegram", "aaron", "taylor", "Claw", "claw"}


def canonical_db_path():
    # backend/normalize_tm_identities.py -> backend -> rsl-task-manager -> dwight workspace root
    return Path(__file__).resolve().parents[2] / "taskmanager.db"


def canonicalize(value):
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    alias = ALIASES.get(normalized.lower())
    if alias:
        return alias
    for user in CANONICAL_USERS:
        if normalized.lower() == user.lower():
            return user
    return normalized


def main():
    db_path = canonical_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for table, column in [
        ('issues', 'created_by'),
        ('issues', 'assigned_to'),
        ('comments', 'username'),
        ('issue_images', 'uploaded_by'),
        ('issue_activity', 'actor'),
    ]:
        rows = cur.execute(f'SELECT rowid, {column} FROM {table}').fetchall()
        for rowid, value in rows:
            canonical = canonicalize(value)
            if canonical != value:
                cur.execute(f'UPDATE {table} SET {column} = ? WHERE rowid = ?', (canonical, rowid))

    rows = cur.execute('SELECT id, username FROM users').fetchall()
    seen = set()
    for user_id, username in rows:
        canonical = canonicalize(username)
        if canonical in CANONICAL_USERS:
            if canonical in seen:
                cur.execute('DELETE FROM users WHERE id = ?', (user_id,))
            else:
                if canonical != username:
                    cur.execute('UPDATE users SET username = ? WHERE id = ?', (canonical, user_id))
                seen.add(canonical)
        else:
            cur.execute('DELETE FROM users WHERE id = ?', (user_id,))

    existing = {row[0] for row in cur.execute('SELECT username FROM users')}
    for username in CANONICAL_USERS:
        if username not in existing:
            cur.execute('INSERT INTO users (username, created_at) VALUES (?, datetime("now"))', (username,))

    conn.commit()
    conn.close()


if __name__ == '__main__':
    main()
