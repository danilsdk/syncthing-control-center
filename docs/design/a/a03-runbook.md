# A0.3 — модели, миграция и проверка PostgreSQL

Реализованы семь SQLAlchemy-моделей из data-model.md и начальная Alembic-ревизия
`a003_initial_schema`. Общие импорты доступны через `app.models`. Python — **3.12**,
как и у A/B/CI. Зависимости A0.2 не менялись.

## Применение к отдельной базе разработки

Сначала установите окружение по [A0.2](a02-runbook.md). Создайте пустую базу
`scc_demo` и отдельного пользователя с правами на её схему `public` средствами
PostgreSQL/pgAdmin. Для приложения не требуется роль суперпользователя.
Пароль задаётся локально и не попадает в Git. Ниже только пример подключения.

Из корня репозитория, Windows PowerShell:

```powershell
$env:SCC_DATABASE_URL = 'postgresql+psycopg://scc_demo:replace-me@127.0.0.1:5432/scc_demo'
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini current
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Для Linux используются `.venv/bin/python` и `export SCC_DATABASE_URL='...'`.
Настройки читаются из переменных окружения; автоматического чтения `.env` нет.
Если пароль содержит специальные символы URL, его нужно кодировать в URL.
Поставка на этом этапе — сохранённый checkout с editable-установкой;
миграции находятся в `backend/migrations` рядом с исходным приложением.

Без миграции `/health/ready` отвечает 503; после `upgrade head` — 200.
Это подтверждает доступ к схеме, но не готовность авторизации или Syncthing.
`alembic check` сравнивает модели со схемой без создания новой ревизии.
Старт FastAPI не применяет миграции и не создаёт администратора/worker.

`downgrade base` удаляет семь таблиц и их данные. В тесте ниже он выполняется
только на только что созданной временной БД; для рабочей базы разработки это
не обычная команда запуска.

## Воспроизводимая реальная проверка

Достаточно установленных PostgreSQL-бинарников; рабочая Windows-служба и пароль
её администратора не нужны. Проверка создаёт собственный кластер в новом
временном каталоге и базу `scc_a03_tests` на свободном loopback-порту.
Пароль генерируется для этого запуска, используется SCRAM, URL не печатается.

```powershell
.\.venv\Scripts\python.exe docs/design/a/check_postgres.py --bin-dir 'C:\Program Files\PostgreSQL\18\bin'
```

Linux с PostgreSQL в PATH (под обычным пользователем):

```bash
.venv/bin/python docs/design/a/check_postgres.py
```

Передача URL существующей базы этому скрипту не поддерживается. Он отфильтровывает
личные SCC/PG-настройки, запускает тесты с собственным подключением и останавливает
только экземпляр из созданного каталога. После остановки каталог удаляется.
При неудаче остановки данные сохраняются, путь выводится для восстановления.
В ограниченной песочнице Windows PostgreSQL может не создать restricted token;
тогда скрипт запускается в обычном пользовательском окружении.

Проверяются:

- upgrade → downgrade → upgrade и повторный upgrade; совпадение схемы с metadata;
- readiness 503 без ревизии и 200 после применения, включая запрос FastAPI с реальной БД;
- две конкурентные вставки активного правила и возможность хранить отключённые;
- уникальность события внутри узла/сессии, новые сессии и локальные маркеры;
- откат вставок событий и курсора одной неуспешной транзакции;
- сохранение исполнения и JSON snapshot при удалении правила;
- запрет ссылок истории на папку другого узла и удалений с потерей истории;
- поля/временные отметки команд, manual-ключи и одна pending-команда;
- разделение node/folder-наблюдений, сохранение неизвестных значений как NULL;
- пары полей наблюдений, неотрицательные счётчики и allowlist payload;
- UUID приложения, серверные defaults и обновление updated_at триггером для raw SQL.

UUID создаётся приложением при INSERT; даты/defaults и updated_at — PostgreSQL.
Сервисные ограничения остаются явно отдельными: IANA timezone, совпадение версии
и snapshot с правилом, допустимые переходы статусов, свежесть подтверждения,
advisory-блокировки и атомарное использование курсора коллектором.
Тест транзакции доказывает механизм БД, а не готовность будущего коллектора A3.

## Остальные проверки

```powershell
.\.venv\Scripts\python.exe -m ruff check --config backend/pyproject.toml backend tests/unit/bootstrap tests/contract/shared docs/design/a/check_postgres.py
.\.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml tests/unit tests/contract
.\.venv\Scripts\python.exe docs/design/a/check_contract.py
.\.venv\Scripts\python.exe docs/design/a/smoke_bootstrap.py
```

Обычный pytest выполняет unit/offline-проверки, а PostgreSQL-тесты явно пропускает
без приватного стенда. Такой пропуск не означает успешную проверку БД.
Для доказательства PostgreSQL обязателен отдельный `check_postgres.py` выше.
Фактическая платформа A0.3 — Windows / Python 3.12.14 / PostgreSQL 18.6;
Linux/другие версии PostgreSQL здесь не объявляются проверенными.

Общие типы, блокировки и точки подключения добавлены в [A0.4](a04-handoff.md).
Весь A0 подготовлен к review; контракт ещё не принят. Вход/CRUD/реальные вызовы
Syncthing относятся к следующим задачам A/B.
