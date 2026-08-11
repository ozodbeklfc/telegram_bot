import os
from dotenv import load_dotenv

# Локально переменные берутся из файла .env, на Railway — из вкладки Variables.
# Код при этом одинаковый: os.getenv() читает и то, и другое. На Railway файла
# .env нет (он в .gitignore и в контейнер не попадает), поэтому load_dotenv()
# просто ничего не найдёт и молча пройдёт дальше — это нормально.
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Postgres. На Railway эту переменную нужно добавить в Variables сервиса бота
# как ссылку на базу: New Variable -> Add Reference -> Postgres -> DATABASE_URL.
# Именно DATABASE_URL, а не DATABASE_PUBLIC_URL: внутренняя сеть Railway
# быстрее и не гоняет трафик через интернет.
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Локально — в файле .env, на Railway — во вкладке Variables."
    )
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не задан. На Railway: сервис бота -> Variables -> "
        "New Variable -> Add Reference -> Postgres -> DATABASE_URL."
    )
