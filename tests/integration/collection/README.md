# Проверки A3

Нужны установленный backend dev-extra, Python 3.12, PostgreSQL binaries и
свободные четыре loopback-порта стенда из deploy/syncthing/bench.py. Выполнять
из корня checkout. Сначала прочитайте [план коллектора](../../../docs/design/a/collection.md).

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
python -m pytest -c backend/pyproject.toml tests/unit tests/contract tests/integration/auth tests/integration/registry tests/integration/collection -m "not postgres" -q -W error
python tests/integration/collection/check_postgres.py --bin-dir "C:\Program Files\PostgreSQL\18\bin" --junitxml "$env:TEMP\a3-postgres.xml"
python tests/integration/collection/check_postgres.py --bin-dir "C:\Program Files\PostgreSQL\18\bin" --live-report "$env:TEMP\a3-native.json"
```

Первые два запуска: 427 тестов без PG и 69 SQL-сценариев; источник в SQL-сценариях
подставной. Третий запуск — настоящий Uvicorn, login с scrypt, private PostgreSQL,
два нативных Syncthing v2.1.7 и свежий процесс collection pass для каждого цикла.
HTTP-клиент переиспользуется из проверки A2, зависимости продукта не подменяются.

Runner создаёт отдельный SCRAM-кластер scc-a32-pg-* на случайном loopback-порту,
мигрирует и проверяет отсутствие drift. Рабочие URL не используются. Для запуска
PostgreSQL может требоваться обычная пользовательская среда вне песочницы агента.
В live-режиме XML не создаётся: результат записывается в --live-report с фактическими
временами/счётчиками и очисткой. Есть ограничение времени 600 секунд. Остановленный
кластер удаляется только после проверки собственного каталога; при неудачной
остановке он сохраняется с явным сообщением.

Native runner отказывается трогать запущенный стенд/занятые порты; использует
отдельный ignored runtime текущего checkout. Публичный pinned архив скачивается
и проверяется по SHA256 существующим prepare.py. Ключи/дома других checkout
не копируются. Рабочие узлы и пользовательские папки не используются.

Реальные сценарии:

1. Передача 600 публичных маленьких файлов в новой уникальной подпапке своего стенда,
   переполнение нативного буфера событий.
2. T-07: повтор реального пакета; T-08: bounded HistoryGap внутри доказанной эпохи.
3. Защищённая история, настоящая авторизация, config/runtime paused отдельно,
   одна node-проба за цикл независимо от двух папок.
4. Новый процесс коллектора восстанавливает DB proof; перезапуск API сохраняет
   сессию/историю; недолжный по интервалу проход не добавляет измерений.
5. T-05: outage одного узла, второй доступен; кеш/proof сохранены, история читается.
6. T-08: настоящий рестарт обоих источников, SourceRestarted и новая эпоха;
   T-05: актуальные наблюдения после возврата, прежние UUID папок.
7. T-08: намеренно установленный legacy anchor=NULL при неизменном источнике
   создаёт SourceContinuityLost. Это изменение только собственного тестового DB.
8. Реальные HTTP-пагинация/фильтры, payload allowlist и SQL unique source positions.
9. Полное восстановление исходного config папок; в finally остановка своих
   процессов, удаление только созданной fixture-подпапки и проверка утечек API/log.

Проверка меняет paused только своей демонстрационной папки через production
адаптер. Ручная проверка человеком такого изменения нужна до merge согласно
AGENTS.md; автоматический отчёт её не заменяет.

[Результат успешного запуска 2026-10-26](evidence/a35-native-2026-10-26.json)
не гарантирует те же счётчики следующему запуску: реальный источник асинхронен.
API в live_app.py и live_worker.py — только проверочная сборка. Постоянный
router/планировщик/единственный worker подключаются в A4.
