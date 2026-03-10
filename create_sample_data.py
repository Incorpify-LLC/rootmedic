import sqlite3
import random

# Create a new SQLite database or connect to an existing one
conn = sqlite3.connect('user_database.db')
cursor = conn.cursor()

# Insert some sample user data into the table
for i in range(10):
    username = f"user_{i}"
    address = {
        'street': f"123 Main St {random.randint(1, 1000)}",
        'city': "New York",
        'province': "NY",
        'country': "USA",
        'zip': str(random.randint(10000, 99999))
    }
    phone = f"+1 {random.randint(111, 999)}-{random.randint(111, 999)}-{random.randint(1111, 9999)}"
    social_accounts = {
        'linkedin': f"johnny-linkedin_{i}",
        'facebook': f"janey-facebook_{i}",
        'instagram': f"jane-johnson_{i}",
        'blog': f"https://example.com/blog_{i}"
    }
    
    # Execute SQL query to insert data into the table
    cursor.execute("INSERT INTO users (username, address, phone, social_accounts) VALUES (?, ?, ?, ?)",
                   (username, str(address), phone, str(social_accounts)))

# Commit changes and close connection
conn.commit()
conn.close()

print("Sample user data inserted successfully!")
