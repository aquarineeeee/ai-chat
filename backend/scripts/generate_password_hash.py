from app.core.security import hash_password


if __name__ == "__main__":
    password = input("Password: ")
    print(hash_password(password))
