# Проверки приложения A4.4a

[application.yml](../../.github/workflows/application.yml) проверяет готовое ядро
A0–A3/A4.2 на GitHub-hosted ubuntu-24.04 с Python 3.12.14. События — pull_request,
push в main и workflow_dispatch. Никаких действующих repository secrets не нужно.
Это отдельная проверка приложения, она не заменяет H0 validate и не меняет защиту main.

Один вход [scripts/check_application.py](../../scripts/check_application.py)
выполняет последовательно:

1. Ruff check и format --check для backend/app, tests, обоих помощников A4 и image entrypoint.
2. Проверку OpenAPI/DTO/документированного контракта.
3. Unit/contract и интеграционные doubles с -m "not postgres", -W error.
4. Все SQL-тесты shared/auth/registry/collection на новом PostgreSQL 18.6 из
   закреплённого [образа](../compose/images.json). Четыре отдельные pytest-процесса
   исключают конфликт одноимённых фикстур A2/A3; ни один набор не исключается.
5. Настоящий [core-check](../../tests/integration/system/check_core.py): build,
   новая PG, миграции, API, два Syncthing, bootstrap/login/CSRF, история и restart/down/up.

PostgreSQL для SQL-тестов получает случайный пароль через file mount, отдельные
три БД, tmpfs для данных и случайный host-порт только на 127.0.0.1. Рабочие SCC/PG/
Compose-настройки удаляются из дочернего окружения. Его контейнер помечен UUID
владельца; уничтожение проверяет labels. Core использует собственный уникальный
Compose project, три named volumes и проверки владельца из A4.2. Общего prune нет.

Публичный JUnit обязательно имеет тесты и не имеет ошибок, failures или skipped;
отклонённые по marker тесты учитываются отдельным следующим набором. Реальный DB
пароль удаляется из публикуемого SQL XML до проверки результата. Ненулевой exit
code, отсутствие отчёта, пропуски и неуспешная очистка не дают зелёный результат.

## Локальный запуск

Нужны Python 3.12, зависимости backend/requirements-dev.txt с установленным
backend dev-extra, Git и работающий Linux Docker Engine с Compose/Buildx.
Команды выполняются из корня checkout. Приватная папка и папка отчётов должны быть
новыми, отдельными и расположенными вне дерева исходников; прежние не перезаписываются.

PowerShell, после установки зависимостей по корневому README:

```powershell
$ciRoot = Join-Path $env:TEMP ("scc-application-" + [guid]::NewGuid().ToString("N"))
python scripts/check_application.py run --work-dir "$ciRoot\private" --report-dir "$ciRoot\reports"
```

На Windows Docker Desktop находится автоматически, контекст по умолчанию desktop-linux.
Для другой установки можно передать --docker и --context. На Linux используется
текущий Docker context, глобальные настройки не изменяются:

```bash
ci_root="$(mktemp -d -t scc-application-XXXXXX)"
python scripts/check_application.py run --work-dir "$ci_root/private" --report-dir "$ci_root/reports"
```

После успеха собственные контейнеры, тома, сети и приватная папка удаляются;
образы/build cache и публичные отчёты сохраняются. После ошибки cleanup ресурсов
выполняется в finally, локальная приватная папка остаётся для диагностики. Удалить
её и повторить проверку очистки можно командой с теми же путями:

```powershell
python scripts/check_application.py cleanup --work-dir "$ciRoot\private" --report-dir "$ciRoot\reports"
```

Cleanup можно повторять; при отсутствии своей папки он ничего не удаляет.
В GitHub отдельный always-step повторяет cleanup также после ошибки/отмены.
При принудительном завершении job по timeout выполнение finally/always не
гарантировано; GitHub-hosted runner временный. Собственные ресурсы не удаляются
по одному лишь совпадению имени, при чужом owner операция завершается ошибкой.

## Отчёты и воспроизводимость

В artifacts по отдельному allowlist загружаются только summary.json, cleanup.json,
offline.xml, postgres.xml и core.json; срок хранения 7 дней. Private папки, Docker
inspect, stdout/stderr дочерних команд, credentials, конфигурации узлов и логи
не включаются в upload. Логи доступны только локально в private/logs при ошибке.
Core diagnostics.log не включён в artifact allowlist.

Summary содержит SHA checkout, хеши фактических Git-visible исходников, Python,
отдельные результаты SQL-групп/версии PG, ссылку на backend image ID и cleanup.
Незакоммиченная локальная проверка отличается от проверки точного SHA в GitHub;
сравнивать нужно хеши исходников и состояние рабочей папки. CI на GitHub считается
пройденным только после фактического workflow run, а не по наличию YAML.

Actions закреплены полными SHA официальных релизов checkout 7.0.1, setup-python
7.0.0, upload-artifact 7.0.2. persist-credentials выключен, permissions — contents:
read, pull_request_target не используется. Actionlint 1.7.12 закреплён версией
и SHA256 архива; необязательные shellcheck/pyflakes явно отключены.

Проверены официальные источники 2026-10-26:

- [События workflow](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
- [Фиксация Actions полным SHA](https://docs.github.com/en/actions/reference/security/secure-use).
- [actions/checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python).
- [upload-artifact и retention](https://github.com/actions/upload-artifact).
- [Actionlint 1.7.12 и контрольные суммы](https://github.com/rhysd/actionlint/releases/tag/v1.7.12).

Результаты текущего этапа — в [журнале A4](../../docs/agent-log/a/A4.md).
Постоянный worker, B1/B2/B3, UI, передача файлов и pause/resume по правилам,
полный системный T-11 и VPS остаются за пределами A4.4a.
