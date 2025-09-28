import sqlite3
from werkzeug.security import generate_password_hash

def seed_test_users(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Test users data
    test_users = [
        {
            'email': 'admin@test.com',
            'password': 'admin123',
            'role': 'admin'
        },
        {
            'email': 'user@test.com',
            'password': 'user123',
            'role': 'user'
        },
        {
            'email': 'user2@test.com',
            'password': 'user123',
            'role': 'user'
        },
        {
            'email': 'user3@test.com',
            'password': 'user123',
            'role': 'user'
        },
        {
            'email': 'user4@test.com',
            'password': 'user123',
            'role': 'user'
        },
        {
            'email': 'user5@test.com',
            'password': 'user123',
            'role': 'user'
        },
        {
            'email': 'guest@guest.com',
            'password': 'guest',
            'role': 'guest'
        }
    ]

    print("🌱 Seeding test users...")

    for user_data in test_users:
        # Check if user already exists
        cursor.execute("SELECT id FROM users WHERE email = ?", (user_data['email'],))
        existing = cursor.fetchone()

        if existing:
            print(f"⚠️  User {user_data['email']} already exists, skipping...")
            continue

        # Hash password
        hashed_password = generate_password_hash(user_data['password'], method='pbkdf2:sha256', salt_length=16)

        # Insert user
        cursor.execute(
            "INSERT INTO users (email, password, role) VALUES (?, ?, ?)",
            (user_data['email'], hashed_password, user_data['role'])
        )

        print(f"✅ Created user: {user_data['email']} (role: {user_data['role']})")

    conn.commit()
    conn.close()
    print("✅ Test users seeded successfully!")

if __name__ == "__main__":
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(script_dir, "../instance/zpevnik.db")
    seed_test_users(db_path)
