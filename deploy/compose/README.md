# Ранний core-стенд A4.2

**2026-10-26: A4.2 реализован и проверен на Linux Docker Engine в Windows Desktop
с WSL 2. Пройдены 9 контейнерных проверок; весь A4 ещё не завершён.**
См. [журнал](../../docs/agent-log/a/A4.md) и [план интеграции](../../docs/design/a/deployment.md).

Нужны Python 3.12 для помощника и Linux Docker Engine с Compose/Buildx.
На Windows помощник находит Docker Desktop в стандартных местах и использует
контекст desktop-linux без изменения глобального Docker context. При другой
установке передайте --docker и при необходимости --context. Образы закреплены
по digest, платформа раннего стенда — linux/amd64: [pins](images.json).
Tag/digest проверены в registry; эти образы использованы в успешном core-check.

Выполнять из корня checkout:

```powershell
python scripts/dev_stack.py prepare
python scripts/dev_stack.py check
python scripts/dev_stack.py up
python scripts/dev_stack.py admin-create --login admin
python scripts/dev_stack.py status
```

Prepare создаёт отдельный ignored runtime/scc-a4-dev с owner marker и случайными
паролями/ключами. Повтор сохраняет их; другая папка/порт/owner не принимается.
Все секреты остаются в private/; .env.example содержит только справочные публичные
параметры. Фактические project/port передаются флагами --project/--port, не через
неявное чтение произвольного .env. Compose получает явный defaults.env и окружение
помощника; секретные значения в compose config не подставляются. Private файлы
записываются с LF и на Windows: CRLF добавляет лишний символ к паролю PostgreSQL.

Up собирает image из backend-контекста и отдельного deploy/compose/image-контекста;
private runtime не передаётся сборке. Init-demo использует только новые собственные
named volumes, генерирует cert/Device ID и private REST key, создаёт две папки,
отключает внешнее обнаружение/relay/NAT/автообновления. При повторе существующие
идентичности и config сохраняются. Непомеченный непустой том не перезаписывается.

PostgreSQL 18 использует volume на /var/lib/postgresql. Migrate ждёт health БД,
явно применяет Alembic head и проверяет drift; API зависит от успешной миграции
и двух healthy Syncthing. Автоматического создания схемы или администратора в
API startup нет. Admin-create запускает существующий CLI с getpass в терминале;
пароль не принимается аргументом и не добавляется в журнал помощника.

После успешного старта API ожидается на http://127.0.0.1:8000. Host-порты PG и
Syncthing не публикуются, их сеть private internal. Только API дополнительно
подключён к api_access для loopback-публикации; отдельного сервиса proxy нет.
Помощник проверяет готовность также с host через опубликованный порт.
API image работает как UID 10001,
Syncthing — UID 1000; init-demo отдельно работает root для подготовки новых томов.
БД получает только password file; backend-операции читают server settings из
смонтированных файлов. Приложение не хранит содержимое синхронизируемых файлов.

В текущей core-сборке entrypoint явно добавляет защищённый router A3 через
create_app(..., routers=(events.router,)). Main/config/DTO/модели не менялись.
Расписаний B1, исполнителя B2, UI и автоматического scheduler здесь нет.
Для ограниченной явной проверки A3 после регистрации demo-a/demo-b через API:

```powershell
python scripts/dev_stack.py collect-once
```

Это один настоящий проход, а не постоянный worker. Общий цикл с B2 реализуется
в A4.3. Readiness подтверждает БД/ревизию, не готовность полного MVP.

Обычная остановка сохраняет volumes и private settings:

```powershell
python scripts/dev_stack.py down
```

Сброс только данных своего проекта требует отдельного точного подтверждения:

```powershell
python scripts/dev_stack.py down --delete-data --confirm-project scc-a4-dev
```

Перед up/down проверяются owner/project/stage labels всех контейнеров, volumes
и networks с именем проекта. Совпадение имени при чужом owner останавливает команду;
общий docker prune не используется. Сброс volumes не удаляет private settings,
повторный up создаст новые demo identity и пустую БД. Удаление private settings
не является способом переиспользовать тома старого owner.

## Проверки

```powershell
python -m pytest -c backend/pyproject.toml tests/integration/system/test_core_tools.py -q -W error
python tests/integration/system/check_core.py --report "$env:TEMP\a4-core.json"
```

Offline: 16 проверок повторной подготовки, формата LF, чужих путей/owner/resources
и размеров secret files. Они не запускают Docker. На снимке A4.2 набор без PG —
443 passed / 190 deselected; 427 прежних и 16 новых.
Актуальный общий запуск с проверками CI — в [инструкции A4.4a](../ci/README.md).

Check-core подготовлен для нового уникального scc-a4-test-* проекта: настоящие
build/migrate, CLI/scrypt/login/CSRF, два узла/четыре mapping, A3 вручную/история,
restart API и down/up без потери данных, повтор bootstrap и секреты/порты/logs.
В тестовом CLI пароль поступает через stdin без TTY; production bootstrap остаётся
интерактивным. В finally удаляются только owner-проверенные ресурсы тестового
проекта и его private runtime, build cache/image сохраняются. 2026-10-26 сценарий
прошёл полностью: 9 проверок, 50 начальных событий, 2 node probes и 8 folder
snapshots; после down/up — 4 node probes. До bootstrap защищённые API возвращают
503 auth_not_configured, после него без сессии — 401. Проверены реальный login,
CSRF, restart API, down/up с сохранением UUID/сессии/истории и запрет повторного
bootstrap. Итоговая inventory: 0 контейнеров/томов/сетей, runtime теста удалён.
При сбое check-core сохраняет рядом с JSON-отчётом журнал с заменой известных
private values, затем очищает свои ресурсы. CLI-диагностика остаётся в ignored runtime.

### Расширение A4.2b: реальные T-02–T-04

Тот же check-core теперь включает 12 проверок. После restart/down/up он использует
зарегистрированные UUID и настоящий A1 adapter через NodeResolver внутри API-контейнера:

- T-02: новый файл demo-b → demo-a, SHA256 отправителя и получателя совпадают.
- T-03: отдельный adapter PATCH, затем наблюдение paused config; новый файл общей
  папки отсутствует на A не менее 5 секунд и 3 наблюдений. В это же время файл
  соседней контрольной папки обязан передаться с правильным SHA256.
- T-04: adapter resume, известный idle и доставка отложенного файла по SHA256.
  В finally восстанавливается исходная unpaused config; полностью сравниваются
  конфигурации обеих папок на обоих узлах, изменения остальных полей запрещены.

При паузе Syncthing может возвращать пустое runtime state; это остаётся unknown,
не заменяется выдуманным состоянием paused. Успех PATCH отдельно не доказывает
выполнение. Во время управления также проверяются Device ID/start time и отсутствие
требования restart. Тест не перезапускает узлы для применения paused.

Файлы создаются только в собственных demo volumes под UID 1000, с новым случайным
именем и запретом перезаписи существующего файла. Fixture-only raw GET нужны для
полного сравнения конфигурации, POST scan — для индексации созданного файла:
watcher на стенде выключен, у CLI Syncthing 2.1.7 команды scan нет. Ключи читает
только процесс в API-контейнере из server settings; в аргументы/отчёт они не попадают.
В продуктовые API, адаптер, DTO и B-модули новые операции не добавлены.

27.10.2026 локально: 469 offline passed, 190 PostgreSQL deselected;
12 real core passed, пауза 9,5 s / 3 наблюдения, файлы по 81920 bytes.
Все 4 конфигурации восстановлены, собственных контейнеров/томов/сетей 0,
private runtime удалён. [Постоянный отчёт](evidence/transfer-2026-10-27.json)
сохраняет хеши проверенных исходников и первый failed-прогон с успешным cleanup.

Это прямые команды адаптера. Постоянный общий worker, правила B1/B2, системная
часть T-11, UI и VPS остаются следующими подэтапами; полный A4 остаётся draft.
