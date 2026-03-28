import pickle
import subprocess

SECRET_KEY = "hardcoded_secret_123"
DB_PASSWORD = "admin1234"

def login(username, password):
    query = f"SELECT * FROM users WHERE name='{username}' AND pwd='{password}'"
    db.execute(query)

def get_users():
    users = []
    for id in range(1000):

        
        user = db.execute(f"SELECT * FROM users WHERE id={id}")
        users.append(user)
    return users

def run_command(cmd):
    subprocess.run(cmd, shell=True)

def load_data(data):
    return pickle.loads(data)
