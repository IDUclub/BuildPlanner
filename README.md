# BuildPlanner API

Оркестратор пайплайна **GenPlanner → GenBuilder**. По `scenario_id` выбирает градостроительный
профиль из показателей Urban API, генерирует территориальные зоны и застраивает их —
синхронным ответом или SSE-потоком в чате.


## Пайплайн

| Шаг | Что происходит | Где |
|---|---|---|
| 1 | `GET /api/v1/scenarios/{id}/indicators_values` | Urban API |
| 2 | Наибольший из 10 индикаторов семейства 269 → `profile_id` | здесь |
| 3 | `GET /default/func_ratio` → `POST /run_func_generation` → зоны и дороги | GenPlanner |
| 4 | Зоны → блоки таксономии GenBuilder + `targets_by_zone` | здесь |
| 5 | `POST /generate/by_territory` → застройка | GenBuilder |
| 6 | Сценарий под сервисной учёткой + сообщение в брокер → расчёт оценок | Urban API |
| 7 | `GET /optimize/scheduler` → очередь строительства, `GET /optimize/teps` → обеспеченность | SIRTEP |

Индикаторы семейства 284 (286 бизнес-кластер, 287 промзона, 288 логистика) в выборе **не участвуют** —
они измеряются в баллах и несравнимы с безразмерными 271–280.

## Запуск

```bash
cp .env.example .env.development
poetry install --with dev
poetry run uvicorn app.main:app --reload --port 8080
```

Документация — на `/api/docs`. Сервис поднимается и без vLLM и без ChatStorage:
чат тогда работает без разбора реплики и без сохранения истории (в логах будет предупреждение).

```bash
make format   # isort + black
make lint     # pylint
make test     # pytest
```

## Ручки

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/buildplanner/scenarios/{id}/run` | синхронный прогон, один JSON |
| `POST` | `/buildplanner/scenarios/{id}/run/stream` | тот же прогон потоком, без чата |
| `POST` | `/buildplanner/scenarios/{id}/chat/stream` | диалоговый прогон (SSE) |
| `GET` | `/buildplanner/scenarios/{id}/indicators` | показатели проекта таблицей, без генерации |
| `GET` | `/buildplanner/reference/indicators` | какие индикаторы участвуют в выборе |
| `GET` | `/buildplanner/reference/profiles` | профили и их застройка в GenBuilder |
| `GET` | `/buildplanner/files/{slot}/{result_id}` | слой прогона из хранилища — по ссылке из события `file` |
| `GET` | `/buildplanner/health`, `/buildplanner/logs/log_file` | служебные |

Все прогоны требуют `Authorization: Bearer <keycloak_token>` — токен прокидывается
в Urban API, GenPlanner и GenBuilder как есть; сам сервис его не валидирует.

## События SSE

Словарь совпадает с GenPlanner и GenBuilder, плюс четыре своих — `indicators`,
`territory_indicators`, `profile_selected` и `scenario_published`:

`chat_created` · `token` · `progress` · **`indicators`** · **`territory_indicators`** ·
**`profile_selected`** · `zones` · `roads` · `result` · **`scenario_published`** ·
`file` · `warning` · `error` · `done`

HTTP-статус потока всегда `200`: фатальная ошибка приходит событием `error` внутри потока.
Если GenBuilder упал, зоны всё равно уже отданы — это полезный частичный результат, а не провал.

**Кадр.** Ключ `type` становится именем события, остальное — JSON в `data`, кириллица
не экранируется. Между событиями идут ping-комментарии keep-alive — их пропускают.

```
event: progress
data: {"stage": "genplanner", "content": "Генерирую территориальные зоны"}
```

**Порядок в `chat/stream`.** В квадратных скобках — события, которые приходят не всегда.

```
[warning load_history]                        история чата недоступна
[chat_created]                                только для нового чата
token × N                                     текст ответа модели кусками
                                              — модель не запускает пайплайн: сразу done
progress fetch_indicators
indicators
territory_indicators | warning fetch_indicators
progress select_profile
profile_selected  [warning non_buildable_profile]
progress genplanner
zones → file(zones)
[roads → file(roads)]                         если GenPlanner вернул дороги
progress map_zones  [warning zones_skipped]
[warning no_volume_target] [warning services_region_unknown]
progress genbuilder
progress assemble
result → file(buildings) → [file × N от GenBuilder]
[progress publish_scenario → scenario_published  [warning …]]   если публикация включена
done
```

`run/stream` отдаёт то же самое без `load_history`, `chat_created` и `token`; его `done` — `{}`.

**Что в событиях.**

| Событие | Поля |
|---|---|
| `chat_created` | `chat_id`, `title` |
| `token` | `content` — кусок текста, склеивать подряд |
| `progress` | `stage`, `content` — готовая подпись стадии; у `select_profile` — причина выбора профиля |
| `indicators` | `values` — десять индикаторов выбора: `indicator_id`, `name`, `profile_id`, `raw`, `normalized` |
| `territory_indicators` | `total`, `highlights`, `sections` — см. «Витрина показателей» |
| `profile_selected` | `profile_id`, `profile_name`, `indicator_id`, `reason`, `buildable`, `missing_indicator_ids`, `scoreboard` |
| `zones` | `source: "genplanner"`, `content` — FeatureCollection с русскими атрибутами |
| `roads` | `content` — FeatureCollection с русскими атрибутами |
| `result` | `content` — FeatureCollection зданий с атрибутами GenBuilder, `summary` |
| `file` | описание сохранённого слоя — см. «Слои на карте» |
| `scenario_published` | `project_id`, `scenario_id`, `zones_written`, `buildings_written`, `buildings_failed`, `buildings_total`, `services_written`, `services_failed`, `unknown_zone_names`, `unknown_service_names`, `notified`, `notified_events`, `failed_stage`, `error` |
| `sirtep_schedule` | `content` — ответ SIRTEP как есть (`provision.house_construction_period` и `service_construction_period` — период каждого объекта, по ним красится карта), `summary` — сводка |
| `sirtep_provision` | `content` — ТЭПы как есть, `summary` — `final_by_service`, `unbuilt_services` |
| `master_plan_summary` | итог прогона: `buildings`, `published`, `schedule`, `provision`, `warnings`; приходит последним перед `done` |
| `warning` | `stage`, `detail` — машинный код или текст причины, `message` — текст для пользователя |
| `error` | `stage`, `detail` — **строка** с JSON исходной ошибки, её надо распарсить |
| `done` | `chat_id`, `assistant_message_id`; без запуска пайплайна `assistant_message_id` — `null` |

Стадии `progress` и их подписи по умолчанию:

| `stage` | `content` |
|---|---|
| `fetch_indicators` | Читаю показатели сценария |
| `select_profile` | Выбираю профиль застройки |
| `genplanner` | Генерирую территориальные зоны |
| `map_zones` | Готовлю блоки для застройки |
| `genbuilder` | Расставляю застройку |
| `assemble` | Собираю результат |
| `publish_scenario` | Сохраняю сценарий для расчёта оценок |
| `sirtep` | Считаю очерёдность строительства |

`result.summary` — сводка по зданиям плюс то, что прислал в своём `summary` GenBuilder:

```json
{
  "buildings": 312,
  "buildings_by_zone": {"residential": 280, "business": 32},
  "residents": 9400,
  "living_area_m2": 282000,
  "building_area_m2": 61000,
  "services": [{"name": "school", "count": 2, "capacity": 1100}],
  "profile": "жилая многоэтажная",
  "blocks": {"...": "mapping_summary"}
}
```

**Когда части событий не будет.** `warning` не останавливает прогон, `error` — останавливает,
но `done` приходит всегда.

| Ситуация | Что меняется |
|---|---|
| `skip_generation: true` | после зон — `progress assemble` «Застройка пропущена по запросу»; нет `result` и `file(buildings)` |
| застраивать нечего (рекреация, сельхоз) | `warning genbuilder nothing_to_build`; нет `result` |
| GenBuilder упал | `warning genbuilder`; нет `result` |
| слой не сохранился | один `warning store_layer`, дальше ни одного `file`; сами `zones`/`roads`/`result` приходят |
| публикация | идёт и в трёх первых случаях, если `publish` не выключен и есть зоны |
| нет жилых домов или сервисов в записанном сценарии | `warning sirtep sirtep_skipped` с причиной; нет `sirtep_schedule` |
| `sirtep_wait_provision: false` | есть `sirtep_schedule`, нет `sirtep_provision` |
| ТЭПы не досчитались за таймаут | `warning sirtep`; очередь остаётся, `master_plan_summary` приходит |
| фатальная ошибка | `error`, затем `done` |

После перезагрузки событий уже нет: карта перерисовывается по частям `kind: "file"` из истории
чата (см. «Слои на карте»), а текст ответа — из самого сообщения: реплика модели, таблица
«Показатели территории», причина выбора профиля, строка о сохранённом сценарии и тексты предупреждений.

## Слои на карте: контракт для фронтенда

Генерация показывается на карте дважды: сразу — из самого потока, и после перезагрузки
чата — по ссылкам из истории. Поток сам по себе не переживает перезагрузку, поэтому каждый
слой ещё и кладётся в хранилище, а в поток и в сообщение ассистента уходит ссылка на него.

**Живой поток.** Слой приходит целиком, а сразу за ним — `file` с ссылкой на его копию:

```
zones  → file(name=zones)  → roads → file(name=roads) → … → result → file(name=buildings) → scenario_published
```

| Слой | Живое событие | Где GeoJSON | `file.name` |
|---|---|---|---|
| Территориальные зоны | `zones` | `content` — FeatureCollection зон GenPlanner | `zones` |
| Дороги | `roads` | `content` — FeatureCollection дорог | `roads` |
| Застройка | `result` | `content` — FeatureCollection зданий, рядом `summary` | `buildings` |

`roads` приходит, только если GenPlanner их вернул. `result` и `file(buildings)` не придут,
если застройка пропущена (`skip_generation`, нечего строить, GenBuilder упал) — тогда
на карте остаются зоны и дороги, а причина приходит `warning`.

**Событие `file`** — описание слоя, байтов в нём нет:

```json
{
  "type": "file",
  "name": "zones",
  "title": "Территориальные зоны",
  "role": "result",
  "url": "https://<PUBLIC_BASE_URL>/buildplanner/files/zones/3f2c…e1",
  "download_url": null,
  "filename": "zones.geojson",
  "mime_type": "application/geo+json",
  "source_service": "buildplanner"
}
```

Файлы, которые отдал сам GenBuilder, приходят тем же событием с `source_service: "genbuilder"`.

**Атрибуты объектов.** Зоны и дороги приходят с русскими ключами — панель атрибутов
показывает их как есть. Правила те же, что в чате GenPlanner; сохранённые файлы совпадают
с потоком. Синхронный `POST …/run`, GenBuilder и запись в Urban API работают с исходными
машинными свойствами GenPlanner.

| Слой | Свойство | Значение |
|---|---|---|
| Зоны | `Территориальная зона` | жилая, рекреационная, промышленная, общественно-деловая, транспортная, сельскохозяйственная, специального назначения; «не определена», если вид неизвестен |
| Зоны | `Сгенерирована` | `Да` / `Нет`; нет ключа — GenPlanner не сообщил |
| Зоны | `Идентификатор исходной зоны` | только у зон, взятых из существующего зонирования |
| Дороги | `Название`, `Адрес` | только у существующих дорог |
| Дороги | `Ширина, м` | число |
| Дороги | `physical_object_type_id` | как в Urban API, для стиля |
| Дороги | `road_lvl` | без перевода: `regulated highway`, `local road, level N`, `user_roads` |
| Дороги | `road_class` | для легенды: `highway`, `street`, `existing`; нет ключа — уровень незнакомый |

Застройка (`result`, `file(buildings)`) не переводится: ключи остаются машинными, а подписи
и справочники значений фронт берёт у GenBuilder — `GET /generate/properties_schema`.
Своя копия подписей здесь разошлась бы с ней при первом изменении в GenBuilder.

**История чата.** Сообщение ассистента в ChatStorage — текст первой частью, затем по части
`{"kind": "file", "payload": {...}}` на каждый слой; в `payload` те же поля, что в событии
`file`, кроме пустых. Чтобы перерисовать карту, фронт берёт части `kind == "file"`,
раскладывает их по `name` и запрашивает `url` с тем же `Authorization: Bearer <token>`.
Ответ — `application/geo+json`, тот же FeatureCollection, что был в потоке. Неизвестный слой
или прогон — `404`, недоступное хранилище — `502`.

**Если слой не сохранился**, в потоке будет один `warning` со `stage: "store_layer"`, а
событий `file` до конца прогона больше не будет. Это не ошибка: карта из потока уже
нарисована, пропадёт только перерисовка после перезагрузки.

**Хранилище.** MinIO, если заданы все четыре `MINIO_*` (частичный набор — ошибка
конфигурации, сервис запустится без хранилища и напишет это в лог); если не задан ни один —
каталог `OUTPUTS_DIR` на диске, что годится только для локальной разработки. MinIO живёт
в закрытой сети, поэтому файлы отдаются через сам сервис, а не прямыми ссылками в бакет.
`MINIO_ADDRESS` — адрес S3 API вида `http://host:9000` (не веб-консоли); схема `https://` включает TLS.
Ссылки строятся от `PUBLIC_BASE_URL` — адреса сервиса, как его видит браузер; без него
берётся адрес входящего запроса, который за прокси бывает внутренним.

## Раскладка

```
app/
  clients/            urban_api · urban_scenario_writer · genplanner · genbuilder
  common/             auth · chat_storage · object_storage · llm · api_handlers · constants ·
                      exceptions · logging
  pipeline/           profile_selector · zone_mapper · targets_policy · pipeline_service ·
                      indicators_view · scenario_publisher · geo_layers · events
  chat/               chat_service · agent/prompts
  system/             health · logs
```

Три таблицы перевода между чужими системами идентификаторов собраны в одном месте —
[`app/common/constants/pipeline_constants.py`](app/common/constants/pipeline_constants.py).
Большинство ошибок пайплайна сводится к рассинхрону одной из них, поэтому они покрыты
тестом на полноту (`tests/test_constants.py`).

## Показатели сценария

Схема `indicators_values` сверена с OpenAPI стенда (`ScenarioIndicatorValue`):

- фильтр по индикаторам делает сам Urban API — query-параметр `indicator_ids`, id через запятую;
- `indicator_id` лежит внутри вложенного объекта `indicator`, а не в корне строки;
- полей `date_value`/`value_type` нет — свежесть определяют `updated_at`/`created_at`;
- значение может быть привязано к гексагону (`hexagon_id`), и таких строк в ответе большинство.

Отсюда правило выбора в `UrbanApiClient.latest_values_by_indicator`: сначала территориальное
значение (`hexagon_id` пуст), среди равных — самое свежее. Иначе можно сравнить агрегат по
территории у одного индикатора с одной ячейкой у другого и выбрать не тот профиль.

## Витрина показателей

Кроме десяти индикаторов выбора сервис показывает и остальные показатели проекта —
событием `territory_indicators` и ручкой `GET /scenarios/{id}/indicators`. Два уровня
в одном ответе, чтобы фронтенд сам решал, сколько показать:

- `highlights` — «паспорт территории», 8 показателей в фиксированном порядке
  (численность, плотность, площадь, урбанизация, % земель НП, средний возраст,
  плотность УДС, ИКГС). Именно они уходят Markdown-таблицей в текст ответа чата;
- `sections` — всё остальное, разложенное по разделам справочника
  `GET /api/v1/indicators_groups` (Демография, Транспорт, Экономика…), строки внутри
  раздела отсортированы по `list_label` естественным порядком (`1.2.10` после `1.2.9`).

Названия, единицы и нумерацию отдаёт сам Urban API (`indicator.name_full`,
`measurement_unit`, `list_label`) — своего справочника показателей у сервиса нет,
он бы неизбежно разошёлся со стендом. Наше только два списка в
[`pipeline_constants.py`](app/common/constants/pipeline_constants.py):
порядок «паспорта» и русские заголовки разделов.

Показатель состоит сразу в нескольких группах (`Численность населения` — и в `regional`,
и в `demogrphy`), поэтому раздел выбирается по `INDICATOR_GROUP_ORDER`: тематические
группы вперёд, сборные (`regional`, `common`) в конец. Показатели вне групп — включая
семейство 269 — попадают в «Прочие показатели».

Запрос отдельный от выбора профиля и **нефатальный**: выбор тянет ровно десять
индикаторов и обязан быть предсказуемым, витрина забирает у сценария всё. Если она
не собралась, прогон продолжается, а наверх уходит `warning`. Справочник групп
кэшируется в памяти на час.

## Запрос в GenPlanner

`POST /genplanner/run_func_generation` объявлен как `Annotated[GenPlannerFuncZonesDTO, Depends(...)]`,
а не как тело запроса, поэтому FastAPI раскладывает поля DTO по двум местам:

| Где | Поля |
|---|---|
| query | `project_id`, `scenario_id`, `roads_extend_distance`, `elevation_angle`, `ignore_default_relations`, `test` |
| body | `territory_balance` (обязательно), `fix_zones`, `min_block_area`, `functional_zones`, `neighbour_pairs`, `forbidden_pairs` |

`project_id` обязателен, поэтому сервис сначала достаёт его из `GET /api/v1/scenarios/{id}`
(`scenario.project.project_id`); переопределяется полем `project_id` в опциях прогона.

## Запрос в GenBuilder

`POST /generate/by_territory` принимает `TerritoryRequest`. Два места, где легко ошибиться:

**`targets_by_zone` — параметр снаружи, зона внутри.** GenBuilder читает
`targets_by_zone["floors_avg"]["residential"]`, а не `["residential"]["floors_avg"]`.
Обе формы — `dict[str, dict]`, поэтому перевёрнутую он принимает молча и игнорирует,
откатываясь на свои дефолты. Внутри сервиса таргеты живут по зонам (так их удобно
переопределять через `targets_overrides`), а `to_genbuilder_targets` разворачивает их
перед отправкой. Допустимые параметры: `residents`, `coverage_area`, `floors_avg`,
`density_scenario` (только `min`/`mean`/`max`), `default_floor_group`.

**Без цели объёма зона не застраивается.** Генерация разбита на три ветки, у каждой
свой порог: жильё требует `residents > 0`, промзона/транспорт/спецназначение —
`coverage_area > 0`, деловая и «базовая» — любое из двух. Зоны без цели попадают
в событие `warning` с кодом `no_volume_target`.

Обе цели абсолютные — на всю территорию, поэтому политика задаёт их удельно
(`residents_per_ha` и `coverage_ratio`), а абсолютные значения считаются от суммарной
площади блоков зоны. Площадь берётся из самой геометрии по сферической формуле
([`geo_area.py`](app/pipeline/geo_area.py)) — без geopandas, чтобы не тянуть стек GDAL
ради одной формулы. Площади по зонам едут в `mapping_summary.area_ha`.

Удельные величины ограничены сверху: плотность — потолком своей группы этажности,
застроенность — `COVERAGE_RATIO_CAP`. Абсолютные `residents`/`coverage_area`
в `targets_overrides` побеждают расчёт по площади (см. «Число жителей»).

Блоки: у каждой feature обязателен непустой `properties.zone` и геометрия
Polygon/MultiPolygon. `properties.floors_group` на блоке перекрывает `default_floor_group`.

## Число жителей

Не спрашивается — ни у фронта, ни в чате. Пайплайн выводит его сам:
площадь блоков зоны × нормативная плотность профиля, ограниченная потолком
своей группы этажности. Это и есть политика D4 «максимум в нормативных рамках»:
задавать число снаружи означало бы либо недобрать ёмкость, либо выйти за норматив.

Перебить расчёт всё ещё можно точечно — абсолютным `targets_overrides.residential.residents`;
это ручной аварийный выход, а не обычный путь. Итоговые опции прогона сохраняются
в `metadata` ответного сообщения чата, чтобы переопределения пережили перезагрузку.

## Публикация сценария и запуск оценок

Оценки считают сторонние сервисы, подписанные на Kafka. Своего продюсера сервис
не держит: у Urban API есть HTTP-фасад над брокером (`/api/broker/...`) — обычный
POST, которым мы и пользуемся.

Порядок в `ScenarioPublisher` обязателен и не переставляется:

| | Действие | Ручка |
|---|---|---|
| 1 | проект-контейнер сервисной учётки (один на исходный проект) | `POST /api/v1/projects?user_id=…` |
| 2 | копия исходного сценария в этот проект | `POST /api/v1/scenarios/{id}` |
| 3 | зоны GenPlanner — одним запросом, ручка принимает массив | `POST /scenarios/{id}/functional_zones` |
| 4 | здания GenBuilder — по два запроса на здание | `POST /scenarios/{id}/physical_objects` → `/buildings` |
| 5 | объявление в брокер | `POST /api/broker/scenario_events/…` |

Сообщения уходят **последними**: сервисы оценок по ним идут читать сценарий, и опередить
запись данных нельзя — они посчитают пустоту. Пустой сценарий не анонсируется вовсе.

**Почему сервисная учётка.** Владельца можно задать ровно в одном месте API —
`POST /api/v1/projects?user_id=...`; у `Copy Scenario` его нет, сценарий наследует проект,
а проект — своего хозяина. Поэтому генерация складывается в проект сервисного аккаунта
и в проекте пользователя не появляется. Сам `user_id` не настраивается: он берётся из
claim `sub` того же токена, которым мы пишем, — иначе учётка и её секрет могли бы разъехаться.

Исходный проект читается токеном пользователя (свой проект видит только он), пишется —
сервисным. Это единственное место, где в одном действии участвуют оба токена.

Публикация не фатальна: если Urban API недоступен или прав не хватило, прогон отдаёт
`warning` со стадией `publish_scenario`, а зоны и застройка остаются у пользователя.


## Очерёдность строительства (SIRTEP)

Стадия идёт **после** публикации и по опубликованному сценарию: SIRTEP читает его из
Urban API сам, поэтому без `PUBLISH_TO_URBAN` включать её бессмысленно: стадия включается,
только когда заданы и `SIRTEP_API`, и публикация. Токен нужен **сервисный**: сценарий лежит в проекте сервисной
учётки, пользовательский его не увидит, а SIRTEP токен не проверяет, а передаёт дальше. Сам заголовок при этом обязателен: без
`Authorization` ручки отвечают 401, не дойдя до Urban API.

| | Действие | Ручка |
|---|---|---|
| 1 | очередь строительства, синхронно | `GET /optimize/scheduler` |
| 2 | обеспеченность по периодам, опрос до готовности | `GET /optimize/teps` |

`profile_id` всегда `1`: в provision-ветке профиль выбирает только саму ветку, а `/optimize/teps`
версии 0.3.1 принимает лишь 1, 2 и 8. Профиль прогона на это не влияет.

**Когда не зовём.** SIRTEP отвечает 400 без жилых домов и 500 без сервисов, и ни то, ни другое
пользователю ничего не объясняет. Поэтому условие проверяется по записанным данным заранее:
сценарий записан целиком (сбой на шаге `broker` не помеха — данные на месте), есть хотя бы один
дом типа «Жилой дом» и хотя бы один сервис. Иначе — `warning` с причиной.

Проверяется именно состав записанного, а не профиль прогона: дома жилыми делает тип физобъекта,
и нежилой профиль может дать жилые кварталы в балансе зон, и наоборот.

**Почему `/teps` опрашивается.** ТЭПы считаются фоном после очереди. Версия 0.3.1 на незавершённом
расчёте отвечает 500 «кэш повреждён» вместо «в процессе» — задача кладётся в кэш с датой в
идентификаторе, а ищется без неё. Поэтому любой 5xx до истечения `SIRTEP_PROVISION_TIMEOUT_SECONDS`
считается «ещё не готово», а 4xx поднимается сразу.

Синхронный `POST /run` ждёт ТЭПы наравне с потоком: иначе обеспеченность недостижима — своего
токена к служебному проекту у вызывающего нет. Отказ от ожидания — опцией `sirtep_wait_provision`,
она работает одинаково в обоих путях.

Стадия не фатальна: очередь не посчиталась — зоны, застройка и сценарий остаются у пользователя.
Глобально запись **выключена по умолчанию** и включается только явно — `PUBLISH_TO_URBAN=true`:
сервис деплоится в dev-кластер автоматически, и запись в общий стенд не должна включаться сама.
На отдельный прогон выключается `publish: false` в опциях.

**Новой таблицы перевода зон не понадобилось.** `functional_zone_type_id` Urban API —
то же пространство идентификаторов, что и профили GenPlanner: 1 residential, 2 recreation,
7 business, 13 residential_multistorey. Все 12 наших профилей есть в справочнике стенда
(`tests/test_constants.py` это стережёт), поэтому `properties.territory_zone` пишется как есть,
а справочник нужен только чтобы убедиться, что стенд такой id знает. Имя — запасной путь:
в справочнике имена английские (`residential_multistorey`), русское лежит в `zone_nickname`,
а GenPlanner отдаёт своё («жилая многоэтажная») — его переводит `ZONE_NAME_TO_PROFILE`.
Зона, которую не удалось опознать, пропускается и попадает в `unknown_zone_names`.

Здания раскладываются по типам физобъектов: `residential` → «Жилой дом», остальные →
«Нежилое здание». В константах имена, а не id: id у каждого стенда свои.

## Чат: ChatStorage и vLLM

Форма запросов сверена с клиентами GenPlanner и GenBuilder — оба сервиса ходят
в те же общие сервисы.

**ChatStorage.** Аутентификация — сервисный токен Keycloak (`client_credentials`),
пользователь передаётся заголовком `X-User-Id`; без него запрос отклоняется.
В теле `create_chat` `scenario_id` и `project_id` лежат **на верхнем уровне**,
а не в `metadata`, иначе чат не привяжется к сценарию. `metadata` необнуляемая —
всегда шлём хотя бы `{}`. Сообщения многочастные: `parts: [{kind, payload}]`.

**vLLM.** OpenAI-совместимый `/v1/chat/completions`; базовый URL нормализуется,
чтобы `/v1` не удвоился. В `json_schema` уходят только `name` и `schema` —
`strict` соседние сервисы не шлют. Об ошибке vLLM сообщает полем `error` в теле,
в том числе со статусом `200` и внутри потока, поэтому тело проверяется отдельно.
Битый SSE-фрейм пропускается с предупреждением, а не рвёт начатый ответ;
структурный ответ разбирается и из обёртки ```` ```json ````.
