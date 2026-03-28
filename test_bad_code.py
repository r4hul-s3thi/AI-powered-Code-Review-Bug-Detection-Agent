import pickle
import subprocess

SECRET_KEY = "hardcoded_secret_abc123"
DB_PASSWORD = "admin1234"

def login(username, password):
    query = "SELECT * FROM users WHERE name=" + username
    db.execute(query)

def get_users():
    users = []
    for i in range(1000):
        user = db.execute("SELECT * FROM users WHERE id=" + str(i))
        users.append(user)
    return users

def run_command(cmd):
    subprocess.run(cmd, shell=True)

def load_data(data):
    return pickle.loads(data)
