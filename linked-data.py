import sqlite3

class Node:
    def __init__(self, data):
        self.data = data
        self.next = None

class LinkedList:
    def __init__(self):
        self.head = None

    def append(self, new_data):
        new_node = Node(new_data)
        if not self.head:
            self.head = new_node
            return
        last = self.head
        while (last.next):
            last = last.next
        last.next = new_node

    def bubble_sort(self):
        swapped = True
        while swapped:
            swapped = False
            current = self.head
            while current and current.next:
                if 'username' in current.data.keys() and 'username' in current.next.data.keys():
                    if (current.data['username'] > current.next.data['username']):
                        temp = current.data.copy()
                        current.data = current.next.data
                        current.next.data = temp
                        swapped = True
                elif ('address' in current.data.keys() and 'address' in current.next.data.keys()) or \
                     ('street' in current.data.keys() and 'street' in current.next.data.keys()) or \
                     ('city' in current.data.keys() and 'city' in current.next.data.keys()) or \
                     ('province' in current.data.keys() and 'province' in current.next.data.keys()) or \
                     ('country' in current.data.keys() and 'country' in current.next.data.keys()) or \
                     ('zip' in current.data.keys() and 'zip' in current.next.data.keys()):
                    if self.compare(current.data['address'], current.next.data['address']):
                        temp = current.data.copy()
                        current.data = current.next.data
                        current.next.data = temp
                        swapped = True
                elif ('phone' in current.data.keys() and 'phone' in current.next.data.keys()) or \
                     ('linkedin' in current.data.keys() and 'linkedin' in current.next.data.keys()) or \
                     ('facebook' in current.data.keys() and 'facebook' in current.next.data.keys()) or \
                     ('instagram' in current.data.keys() and 'instagram' in current.next.data.keys()) or \
                     ('blog' in current.data.keys() and 'blog' in current.next.data.keys()):
                    if self.compare(current.data['phone'], current.next.data['phone']):
                        temp = current.data.copy()
                        current.data = current.next.data
                        current.next.data = temp
                        swapped = True
                current = current.next

    def compare(self, str1, str2):
        return str1.casefold() > str2.casefold()

    def print_list(self):
        current = self.head
        while (current):
            print(current.data)
            current = current.next


def linked_list_bubble_sort(user_data):
    linked_list = LinkedList()
    for user in user_data:
        linked_list.append(user)
    linked_list.bubble_sort()
    return linked_list.print_list()


# Sample usage
user_data = [
    {"username": "John", "address": {"street": "123 Main St", "city": "New York", "province": "NY", "country": "USA", "zip": "10001"}, 
     "phone": "+1 212 555 1234", 
     "social_accounts": {"linkedin": "johnny-linkedin", "facebook": "johnny-facebook"}},
    {"username": "Jane", "address": {"street": "456 Elm St", "city": "Los Angeles", "province": "CA", "country": "USA", "zip": "90001"}, 
     "phone": "+1 323 123 5678", 
     "social_accounts": {"linkedin": "janey-linkedin", "facebook": "janey-facebook"}},
    # Add more users here...
]

linked_list_bubble_sort(user_data)


# Sample usage with file-based SQL database
import sqlite3

# Create a new SQLite database or connect to an existing one
conn = sqlite3.connect('user_database.db')
cursor = conn.cursor()

# Create table for user data
cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    address TEXT,
                    phone TEXT,
                    social_accounts TEXT)''')

# Insert sample user data into the table
for i, user in enumerate(user_data):
    cursor.execute("INSERT INTO users (username, address, phone, social_accounts) VALUES (?, ?, ?, ?)",
                   (user['username'], str(user['address']), user['phone'], str(user['social_accounts'])))

# Commit changes and close connection
conn.commit()
conn.close()

# Read data from the table
cursor.execute("SELECT * FROM users")
rows = cursor.fetchall()

for row in rows:
    print(row)


def linked_list_bubble_sort_sql():
    # Create a new SQLite database or connect to an existing one
    conn = sqlite3.connect('user_database.db')
    cursor = conn.cursor()

    # Execute SQL query to retrieve data from the table
    cursor.execute("SELECT * FROM users")

    # Fetch all rows from the last executed statement
    rows = cursor.fetchall()

    linked_list = LinkedList()
    for row in rows:
        user_data = {
            'username': row[1],
            'address': {'street': '', 'city': '', 'province': '', 'country': '', 'zip': ''},
            'phone': '',
            'social_accounts': {}
        }
        
        # Assuming the table structure is as follows:
        # id INTEGER PRIMARY KEY AUTOINCREMENT,
        # username TEXT,
        # address TEXT,  (store address data separately)
        # phone TEXT,
        # social_accounts TEXT
        user_data['address']['street'] = ''
        user_data['phone'] = row[3]
        
        # Assuming the table stores social accounts as a comma-separated string
        social_accounts = str(row[4]).split(',')
        for account in social_accounts:
            account_name, account_url = account.split(':')
            user_data['social_accounts'][account_name] = account_url
        
        linked_list.append(user_data)

    linked_list.bubble_sort()

    # Return the sorted list of user data
    return linked_list.print_list()


# Call the function to perform bubble sort on linked list
linked_list_bubble_sort_sql()
