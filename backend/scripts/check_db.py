import sqlite3

def check_database():
    conn = sqlite3.connect("backend/db/zpevnik.db")
    cursor = conn.cursor()

    print("=== USERS ===")
    cursor.execute("SELECT id, email, role FROM users")
    users = cursor.fetchall()
    for user in users:
        print(f"ID: {user[0]}, Email: {user[1]}, Role: {user[2]}")

    print("\n=== SONGBOOKS ===")
    cursor.execute("SELECT id, title, is_public, owner_id FROM songbooks WHERE id='00101'")
    songbooks = cursor.fetchall()
    for sb in songbooks:
        print(f"ID: {sb[0]}, Title: {sb[1]}, Is Public: {sb[2]}, Owner ID: {sb[3]}")

    print("\n=== USER SONGBOOK ACCESS ===")
    cursor.execute("SELECT user_id, songbook_id, permission FROM user_songbook_access")
    accesses = cursor.fetchall()
    for access in accesses:
        print(f"User ID: {access[0]}, Songbook ID: {access[1]}, Permission: {access[2]}")

    conn.close()

if __name__ == "__main__":
    check_database()
