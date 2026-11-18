# A10: нативный стенд на macOS 26.1 Apple Silicon

Это инструкция реализации и независимой приёмки A10. Поддержка готовится для
Python **3.12 ARM64**, Syncthing **2.1.7** и двух собственных тестовых узлов.
Результаты на Windows, doubles Darwin API и проверенный Mac ZIP не заменяют
выполнение этих команд на Mac. До такого прогона совместимость не принята.

## Что изменено

- В `deploy/syncthing/release.json` раздельно закреплены платформа, формат архива,
  имя файла и SHA-256. Mac ARM64 использует официальный ZIP с `syncthing`;
  Windows — ZIP с `syncthing.exe`, Linux amd64 — tar.gz с `syncthing`.
- SHA-256 проверяется до извлечения и при каждом использовании кеша. Выбирается
  только бинарник в корне release; одноимённые service/firewall-файлы из `etc`
  не извлекаются. Каталог архива целиком не распаковывается. Проверяются пределы
  скачивания/размера, дубликаты и тип записи; ссылки не становятся бинарником.
- `platform_name()` отказывает Intel/Rosetta и неподдержанным платформам.
  Нативность проверяется через `sysctl.proc_translated`, ошибки наблюдения не
  выдаются за ARM64. Mac amd64 и Linux arm64 этой карточкой не добавлены.
- Darwin использует `libproc`: `proc_pidinfo(PROC_PIDTBSDINFO)` и `proc_pidpath`.
  Время создания с микросекундами читается до и после пути; PID, начало жизни
  процесса и исполняемый файл сравниваются с сохранённой идентичностью.
  Устаревший PID не разрешает shutdown, ошибки чтения не считаются остановкой.
  Только `ESRCH` означает исчезнувший PID; `ENOENT` пути, отказ доступа и
  неполный ответ остаются ошибкой наблюдения с сохранением runtime.
  Если идентичность не удалось получить сразу после spawn, сохраняется
  `pending_pid`: start/status/stop возвращают ошибку до ручной диагностики,
  а не объявляют процесс остановленным и не реконфигурируют его home.
- После проверки принадлежности используется аутентифицированный HTTP shutdown.
  Mac не использует `/proc`, `pidfd` или сигнал по PID. Если процесс продолжает
  работать, возвращается ограниченная ошибка и runtime сохраняется. HTTP 200
  сам по себе не считается завершением. Windows handles и Linux pidfd сохранены.
- Backend API, зависимости, правила, worker, Compose и CI здесь не меняются.
  Общая сборка — A4, постоянная платформенная матрица — B6.

## Отдельная рабочая папка и окружение

Выполняет danilsdk в своей копии репозитория. Существующие рабочие папки не
переключать. Сверить полный SHA PR перед запуском и записать его в результат:

```sh
git fetch origin main codex/a10-macos-compatibility
git worktree add --detach ../scc-a10-review origin/codex/a10-macos-compatibility
cd ../scc-a10-review
git rev-parse HEAD origin/main
sw_vers
uname -m
```

Нужны нативные Python 3.12, PostgreSQL и Node, удовлетворяющий `frontend/package.json`.
Если используется ARM64 Homebrew, он обычно находится в `/opt/homebrew`:

```sh
/opt/homebrew/bin/brew install python@3.12 postgresql@17 node@22
export PATH="$(/opt/homebrew/bin/brew --prefix node@22)/bin:$PATH"
/opt/homebrew/bin/python3.12 -c 'import platform, sys; print(sys.version); print(platform.machine())'
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python scripts/harness.py setup
```

Ожидается `arm64`; интерпретатор из Intel Homebrew или запуск Terminal под
Rosetta не подходит. Для PostgreSQL используется `brew --prefix postgresql@17`,
а не путь пользователя. `brew services start` не нужен: проверки сами создают
частные кластеры на случайных loopback-портах. Рабочий PostgreSQL не используется.
Не отключать проверку TLS при ошибке загрузки релиза; исправить доверенные CA
окружения и повторить загрузку с неизменной проверкой SHA-256.

## Проверки по порядку

Все native-проверки запускать последовательно: они используют одни порты
18384/28384 и 22101/22102. Если порт занят, стенд отказывается его забирать.
Нельзя останавливать чужие процессы ради освобождения порта.

```sh
mkdir -p deploy/syncthing/runtime/a10-reports deploy/syncthing/runtime/a10-temp
export TMPDIR="$PWD/deploy/syncthing/runtime/a10-temp"
export PYTHONIOENCODING=utf-8
PG_BIN="$(/opt/homebrew/bin/brew --prefix postgresql@17)/bin"

.venv/bin/python scripts/harness.py review-plan --branch codex/a10-macos-compatibility --base origin/main --head HEAD --author playerboom --reviewer danilsdk
.venv/bin/python scripts/harness.py validate --task A10 --branch codex/a10-macos-compatibility --base origin/main --policy-ref origin/main

.venv/bin/python deploy/syncthing/bench.py verify
.venv/bin/python tests/integration/syncthing_adapter/check_d01.py
.venv/bin/python docs/design/a/check_postgres.py --bin-dir "$PG_BIN"
.venv/bin/python tests/integration/auth/check_postgres.py --bin-dir "$PG_BIN" --suite all
.venv/bin/python tests/integration/auth/check_postgres.py --bin-dir "$PG_BIN" --suite registry-live --live-report deploy/syncthing/runtime/a10-reports/registry-macos.json
.venv/bin/python tests/integration/collection/check_postgres.py --bin-dir "$PG_BIN"
.venv/bin/python tests/integration/collection/check_postgres.py --bin-dir "$PG_BIN" --live-report deploy/syncthing/runtime/a10-reports/collection-macos.json

.venv/bin/python deploy/syncthing/bench.py status
git status --short
```

Не продолжать после неуспешной команды: сохранить её код завершения и приватный
лог, проверить `bench.py status` и сообщения частного PG runner. Отчёты с
незавершённым cleanup не являются успешной приёмкой. Здесь нет автоматической
очистки произвольных каталогов или завершения процесса по одному PID.

`verify` подтверждает начальную изоляцию и передачу файла по содержимому.
`check_d01.py` включает реальную проверку адаптера: pause/resume, отсутствие
передачи в паузе, доставку после resume, неизменность контрольных папок,
восстановление конфигурации и restart. Затем проверяются checkpoint после
перезапуска backend и потеря непрерывности при управляемой коллизии status.
Нативный рестарт Syncthing в одну секунду этим сценарием не заявляется.

SQL-прогоны проверяют схему/locks, авторизацию и реестр, события/курсоры/history.
`registry-live` проверяет настоящий HTTP API A2 и два источника, а
`collection --live-report` — принятый A3, сохранение событий/наблюдений,
API истории и восстановление после перезапуска. В этих сценариях PostgreSQL,
HTTP API и Syncthing настоящие; doubles системных вызовов используются только
в contract-тестах `test_platforms.py`.

`check_d01.py` пишет только игнорируемый `runtime/d01-verification.json`.
Обновление исторического tracked evidence требует явного `--export-evidence`.
Остальные команды выше также не экспортируют fixtures. XML, API-ключи,
сертификаты, архивы, PID, локальные пути и сырые логи остаются вне Git.

## Результат и ручная приёмка

В review PR записать: точные head/base, macOS version/build, архитектуру Python,
версии Python/PostgreSQL/Node/Syncthing, коды завершения и число тестов каждого
прогона. Приложить только проверенную обезличенную сводку. Логи не копировать
целиком: сначала проверить отсутствие API-ключей, паролей, session/CSRF-токенов
и личных путей. Архивный digest уже закреплён в Git; новые секреты не нужны.

Человек проверяет собственную demo-конфигурацию до merge: loopback endpoints,
отключённые discovery/relay/NAT, два собственных home, исходный paused=false,
неизменность соседних папок и восстановление всех четырёх конфигураций после
испытаний. Ручное подтверждение привязывается к проверенному SHA, а не к старому PR.

После успешного завершения нет работающих API/worker/PG/Syncthing этого прогона;
`bench.py status` показывает пустой список `running`. Каталог runtime с бинарником,
принадлежащими стенду home и приватными отчётами может остаться. Если Mac shutdown
не сработал, сохранять runtime и диагностировать его собственный процесс; не
удалять файлы работающего узла и не заменять проверку принадлежности командой kill.

До реального Mac-прогона и подтверждения конфигурации PR не считается принятым.
После merge A10 B начинает B6. Контейнерный прогон Docker Desktop ARM64 в A4
и полный UI-сценарий B4 — отдельные результаты.

## Источники

- [Официальный release 2.1.7](https://github.com/syncthing/syncthing/releases/tag/v2.1.7)
  и [checksum manifest](https://github.com/syncthing/syncthing/releases/download/v2.1.7/sha256sum.txt.asc).
- [Apple: обнаружение Rosetta](https://developer.apple.com/documentation/apple-silicon/about-the-rosetta-translation-environment).
- [Apple XNU: proc_bsdinfo и константы](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h)
  и [libproc wrappers](https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c),
  [ошибки vnode lookup в proc_pidpath](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/proc_info.c).
- [Homebrew Python 3.12](https://formulae.brew.sh/formula/python@3.12)
  и [PostgreSQL 17](https://formulae.brew.sh/formula/postgresql@17),
  [Node 22](https://formulae.brew.sh/formula/node@22).
