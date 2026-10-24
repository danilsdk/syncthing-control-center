# Адаптер A1: передача A2 / A3 / B2

Состояние на 2026-10-25: A1.1–A1.5 подготовлены и проверены локально на Windows
amd64 / Syncthing **2.1.7** / Python **3.12.14**. Общий контракт остаётся
0.1.0-draft; [D-01 r2](../../../docs/design/a/d01-stream-continuity.md) требует
review B0. Локальная готовность не означает принятие A0/A1 или готовность к merge.
После аудита A0–A1 исправлены R1–R4; код и регрессии сохранены в 796f308.
Результаты и команды — в [журнале исправлений A6](../../../docs/agent-log/a/A6.md).

Весь доступ приложения к Syncthing проходит через этот пакет. Пакет не содержит
реестр, коллектор, scheduler или сохранение Execution. Проверки и границы A1 —
в [плане](../../../docs/design/a/syncthing-api.md), результаты —
в [журнале A1](../../../docs/agent-log/a/A1.md) и
[сводке приёмки](../../../tests/integration/syncthing_adapter/acceptance-a1.json).

## Подключение и владение

```python
from app.contracts.interfaces import NodeConnection, SourceCheckpoint
from app.syncthing_adapter import HttpAdapterFactory, ResolvedNode


def one_node_factory(
    connection: NodeConnection,
    expected_device_id: str | None,
    checkpoint: SourceCheckpoint | None,
) -> HttpAdapterFactory:
    targets = {
        connection.node_id: ResolvedNode(
            connection,
            expected_device_id=expected_device_id,
            checkpoint=checkpoint,
        )
    }
    return HttpAdapterFactory(targets.__getitem__)
```

Это пример построения фабрики одного узла из уже разрешённых серверных данных,
не готовый resolver A2. Для приложения resolver заново получает NodeConnection,
ожидаемый Device ID и checkpoint по **внутреннему UUID**. URL/API-ключ берутся
из серверной конфигурации через connection_ref; браузер их не передаёт.
ResolvedNode не следует сериализовать или журналировать как служебную запись.

`factory.for_node(node_id)` возвращает клиента; неизменный результат resolver
переиспользует клиента. Новый адрес, ключ, ожидаемый ID или checkpoint закрывают
старого клиента. Ошибка resolver запрещает использование кешированного клиента.
Закрытие во время connect запрещает последующее начало HTTP-отправки; начатый
запрос сохраняет консервативную классификацию исхода. На Windows закрытие
ожидающего чтения может завершиться по его socket deadline; мгновенная отмена
чтения не гарантируется. Ожидание ответа не удерживает блокировку отправки.
Владелец фабрики вызывает `close()` при завершении; при передаче в create_app
ресурсом владеет приложение. Не закрывать общего клиента после каждого запроса.

API синхронный. Из async-обработчика A2 его вызывают через ограниченный worker
thread/threadpool, не в event loop. Один клиент последовательно выполняет
операции под внутренней блокировкой, ожидание которой ограничено 5 секундами.
Длинный poll может задержать чтение/запись того же клиента; для отдельных
процессов collector и executor нужны собственные фабрики с одной серверной БД.
Фабрика не обеспечивает распределённую блокировку папки или singleton worker.

## A2: реестр и наблюдения

- Внутренние node_id/folder_id приложения отличаются от Device ID / Folder ID
  Syncthing. Методы папки принимают **внешний Folder ID** в контексте клиента узла.
- Общая зависимость get_adapter переводит not_found при разрешении узла в
  404/not_found; остальные AdapterError resolver — в 503/service_unavailable.
  Ошибки дальнейшего вызова методов обрабатывает владеющий сервис, включая
  write_outcome. Имя ConnectionSettings проверяется тем же DisplayName, что API.
- expected_device_id=None допустим только при первичном обнаружении. После
  регистрации A2 сохраняет Device ID и передаёт его при последующих обращениях.
  identity_mismatch не заменяет зарегистрированную идентичность новой.
- Checkpoint восстанавливается из managed_nodes: node_id, source_start_time_raw,
  source_session_id, source_stream_anchor. Для legacy anchor=null не является
  доказательством. Чужой UUID checkpoint отвергается до первого HTTP-запроса.
- read_system даёт источник/эпоху/ориентир; list_folders/read_folder — конфигурацию
  paused; read_runtime — отдельное runtime-наблюдение; restart_required — отдельный
  факт необходимости рестарта. Один из этих фактов не заменяет остальные.
- Неизвестный непустой runtime state сохраняется строкой. Пустой state у paused
  папки на 2.1.7 сейчас даёт invalid_response: состояние неизвестно, не idle.
  Отсутствующие счётчики остаются None. Конфигурацию paused по-прежнему можно читать.
- A2 не продвигает курсор/каноническую историю при обычном чтении статуса.
  Периодические наблюдения и история относятся к A3.

## A3: события и восстановление

`read_events(since, timeout_seconds=15)` использует фиксированный /rest/events
без смены фильтра. since — id этого потока, не globalID. Допустимы since от 0
до 2**63-1 и целый timeout 0–15. Успешный пустой пакет нормален; пустое окно
для установления доказательства даёт session_changed. SourceEvent.id также
ограничен 2**63-1; превышение в ответе даёт invalid_response до создания checkpoint.
Исходное время события не округляется; произвольные data, пути файлов и тексты
ошибок отбрасываются.

Checkpoint доказывает продолжение эпохи только при совпадении сохранённого
ориентира с событием текущего окна и прежнем raw startTime. После потери
доказательства старый since>0 отклоняется до успешного чтения с нуля. Нельзя
повторять старый курсор бесконечно или вычислять сессию только из startTime.

Порядок A3 под блокировкой строки узла:

1. Перечитать сохранённый checkpoint и курсор, получить клиент через resolver.
2. Прочитать пакет. При session_changed сверить источник и получить пакет
   с since=0. identity_mismatch требует исправления регистрации, не сброса ID.
3. При смене эпохи записать локальную границу: SourceRestarted для нового
   startTime, SourceContinuityLost для прежнего startTime без доказательства.
   Оба имеют kind=restart, source_event_id=null и previous_session_id.
4. Одной транзакцией записать границу, события, ограниченные gaps внутри эпохи,
   пакетный source_checkpoint и последний принятый source id. Пустой пакет
   не продвигает курсор. Сам ориентир может быть дальше последнего принятого id.
5. При ошибке транзакции не сохранять отдельно ни курсор, ни checkpoint.
   Уникальность source-событий — (node_id, source_session_id, source_event_id).

Если актуальный checkpoint уже изменился другим исполнением, старое наблюдение
не перезаписывает его: пакет перечитывается. Между недоказанными эпохами возможны
повторы; неизвестный диапазон не превращается в выдуманный HistoryGap.
Перезапуск backend с подтверждённым checkpoint сохраняет эпоху. Реализация
цикла, транзакций и локальных маркеров остаётся задачей A3.

## B2: запись и подтверждение

`set_paused(folder_id, bool)` отправляет один PATCH только с полем paused
и возвращает None. Он не выставляет applied, не перезапускает Syncthing и
не делает скрытые повторы. Контроль версии правила, общей блокировки папки,
sent_at и отдельной попытки — ответственность B2 по
[передаче A0.4](../../../docs/design/a/a04-handoff.md).

После отправки B2 отдельно наблюдает paused, restart_required и эпоху по D-04.
Изменившаяся эпоха или недостаток доказательства запрещают applied. Сетевая
ошибка не разрешает вслепую повторить старое намерение: сначала пересчитывается
актуальное состояние. Для read-ошибки write_outcome=None; для write-ошибки:

| Исход | Значение для B2 |
|---|---|
| not_applied | Доказанный отказ до отправки или 401/403/404; это не подтверждение применения |
| unknown | Таймаут, разрыв связи, 5xx, неверная ответная identity; команда могла быть принята |

## Ошибки и практические пределы

AdapterError содержит фиксированные code/retryable/write_outcome. Внешние тексты,
URL и API-ключи не выдаются. retryable=true для timeout/unreachable/session_changed
означает возможность повтора **после сверки**, не автоматический retry PATCH.
auth_error/invalid_response/identity_mismatch требуют разбирательства;
not_found может означать отсутствие узла в resolver или папки у источника;
контекст операции сохраняет вызывающий сервис.

Один обычный HTTP-запрос ограничен 5 секундами; poll — server timeout+5,
максимум 20 секунд. Проверка доказательства требует дополнительных запросов
status/events/status до/после poll: это не общий SLA в 5/20 секунд на метод.
Системный DNS resolver не отменяется отдельно; для проверенного стенда используются
IP loopback. До использования DNS-адресов в A4 нужно учесть этот предел.
Ответ ограничен 4 MiB. Redirects, proxy environment и невалидный JSON запрещены.
TLS проверяет сертификат и hostname, отключение проверки не поддерживается.
Реальный HTTPS с собственным CA и Linux-стенд отдельно не испытывались.

## Повторить проверки

Из корня репозитория, в backend-окружении Python 3.12:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
python -m pytest -c backend/pyproject.toml tests/unit tests/contract -q
python -m ruff check --config backend/pyproject.toml backend/app/syncthing_adapter deploy/syncthing tests/contract/syncthing_adapter tests/integration/syncthing_adapter
python tests/integration/syncthing_adapter/check_live.py --export-evidence
python tests/integration/syncthing_adapter/check_d01.py
```

Реальные команды последовательно владеют двумя частными демонстрационными
процессами и останавливают их в finally; заранее запущенный стенд нужно остановить.
Файлы/ключи находятся только в игнорируемом runtime. На Windows для native
процессов может потребоваться обычный пользовательский контекст вне sandbox.
Инструкция подготовки/портов — [стенд](../../../deploy/syncthing/README.md).
Реальную PostgreSQL-проверку запускают отдельно по
[D01](../../../docs/agent-log/a/D01.md); обычный pytest честно пропускает 85 тестов БД.

До merge человек просматривает changed_config_fields, сохранность трёх
нецелевых папок, requiresRestart, эпохи pause/resume и transfer_after_resume
в [реальном отчёте](../../../tests/integration/syncthing_adapter/evidence-adapter-v2.1.7.json).
Симуляция совпавших status-полей явно отделена от настоящего рестарта в
[отчёте D01](../../../tests/integration/syncthing_adapter/evidence-d01-v2.1.7.json).
Нативный рестарт в одну секунду и полная цепочка scheduler → БД → UI не заявляются.
