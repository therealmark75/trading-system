from database.db import get_connection, get_user_by_id
from config.settings import DATABASE_PATH

user = get_user_by_id(DATABASE_PATH, 2)
print('User:', dict(user) if user else None)
