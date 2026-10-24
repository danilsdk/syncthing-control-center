# A0.2 — запуск системного backend-каркаса

Статус: реализован системный каркас; модели и миграция добавлены в
[A0.3](a03-runbook.md). Предметные API и администраторские сессии ещё не готовы.
PR A0 остаётся Draft до завершения A0.4 и проверок.

## Воспроизводимая установка

Требуется Python **3.12**. Все команды выполняются из корня репозитория.
Прямые зависимости закреплены в backend/pyproject.toml; полные наборы —
backend/requirements.txt и backend/requirements-dev.txt. Setuptools для сборки
также закреплён. Версии обновляются вместе с проверкой обоих наборов.

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt -e "./backend[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Если Python не находится в PATH, вместо `py -3.12` указывается полный путь
к интерпретатору 3.12. Активация окружения не требуется.

Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt -e './backend[dev]'
.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Для установки без инструментов разработки используется
`python -m pip install -r backend/requirements.txt -e ./backend`.
Editable-установка предполагает сохранённый checkout; упаковка миграций для
другого способа поставки рассматривается вместе с A0.3/A4.

## Что доступно

| Запрос | Сейчас | Значение |
|---|---|---|
| GET `/health/live` | 200, `{"status":"ok"}` | Процесс отвечает; доступ к БД не нужен |
| GET `/health/ready` | 503 без БД/миграции | Работоспособность зависимостей не подтверждена |
| GET `/docs` | 200 | Автоматическое описание только реализованных маршрутов |
| GET `/openapi.json` | 200 | Runtime-спецификация каркаса |
| Не реализованный предметный маршрут | 404, единый Error | Предметная реализация ещё отсутствует |

Все ответы имеют новый серверный UUID `X-Request-ID`. Ошибки соответствуют
формату Error из общего контракта; исключения/введённые значения не отражаются
в их сообщениях. Ошибки валидации и фреймворка также имеют этот формат.

Readiness выполняет read-only `SELECT 1` и сверку текущей Alembic-ревизии
с единственной ожидаемой head в backend/migrations. Ревизия добавлена в A0.3.
Без её применения **даже доступная PostgreSQL не даёт ложный 200**.
Проверка инициализации не создаёт таблицы, не запускает миграцию и не отправляет
команды Syncthing. Engine создаётся без открытия подключения и закрывается при остановке.

## Настройки

Читаются только переменные с префиксом `SCC_`. `.env` автоматически не читается.
Реальные значения задаются локально/средой запуска и не добавляются в Git.

| Переменная | По умолчанию / назначение |
|---|---|
| SCC_ENVIRONMENT | development; также test/production |
| SCC_DATABASE_URL | Не задана. Только postgresql+psycopg с именем БД |
| SCC_DB_CONNECT_TIMEOUT_SECONDS | 3, диапазон 1–30 |
| SCC_DB_STATEMENT_TIMEOUT_MS | 3000, диапазон 100–30000; также lock_timeout |
| SCC_DB_POOL_SIZE | 5, максимум 10; max_overflow=0 |
| SCC_DB_POOL_TIMEOUT_SECONDS | 3, диапазон 1–30 |
| SCC_ALLOWED_ORIGINS | JSON-массив; по умолчанию http://localhost:8000, production требует HTTPS |
| SCC_CONNECTIONS | JSON-объект серверных псевдонимов; по умолчанию пуст |
| SCC_SESSION_SECRET | Не задан; при наличии минимум 32 символа, сам по себе не включает авторизацию |
| SCC_RECONCILIATION_INTERVAL_SECONDS | 15, пока только конфигурация будущего B2 |
| SCC_OBSERVATION_INTERVAL_SECONDS | 30, пока только конфигурация будущего A3 |
| SCC_STALE_AFTER_SECONDS | 60, не короче интервала наблюдения |

Ниже только демонстрационные значения, не работающие ключи:

```powershell
$env:SCC_DATABASE_URL = 'postgresql+psycopg://demo:replace-me@127.0.0.1:5432/scc_demo'
$env:SCC_ALLOWED_ORIGINS = '["http://localhost:8000"]'
$env:SCC_CONNECTIONS = '{"node-a":{"display_name":"Demo A","base_url":"http://syncthing-a:8384","api_key":"replace-in-local-environment"}}'
```

Добавление connection_ref в конфигурацию не вызывает сетевые обращения и не
регистрирует узел автоматически. Поля base_url, api_key, database_url и session_secret
исключены из repr/обычной сериализации настроек. Ошибки настройки скрывают исходный ввод.

## Подключение следующих модулей

`create_app()` принимает Settings, Database и последовательность APIRouter.
Переданные предметные роутеры включаются под `/api` с общей `require_admin`.
Сейчас она всегда возвращает 503 auth_not_configured; cookie, заголовок и наличие
session_secret не обходят запрет. В A2 её заменит настоящая проверка сессии.
Заглушки входа, успешного CRUD и сетевых команд не добавлялись.

`get_session` предоставляет синхронную SQLAlchemy Session; commit выполняет
вызывающий сервис, а не зависимость. Незавершённая транзакция откатывается при
закрытии сессии. Блокировки и durable sent_at появятся в A0.4/B2.
В lifespan нет планировщика или фонового worker.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ruff check --config backend/pyproject.toml backend tests/unit/bootstrap
.\.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml tests/unit tests/contract
.\.venv\Scripts\python.exe docs/design/a/check_contract.py
.\.venv\Scripts\python.exe docs/design/a/smoke_bootstrap.py
```

Unit-тесты не используют сеть, настоящую БД, личные папки или действующие ключи.
Успех readiness с ревизией проверяется на test doubles и не считается испытанием PostgreSQL.
Smoke запускает отдельный Uvicorn на временном loopback-порту, проверяет настоящий
HTTP и останавливает только свой процесс. SCC-настройки пользователя этому процессу
не передаются; Syncthing и PostgreSQL не затрагиваются.

backend.json подготовлен для setup/product-checks H0. Полный harness до принятия
H0 в рабочую базу не заявляется проверенным. Отдельная проверка реальной PostgreSQL
и миграции описана в [A0.3](a03-runbook.md).
