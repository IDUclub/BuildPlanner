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
| `GET` | `/buildplanner/health`, `/buildplanner/logs/log_file` | служебные |

Все прогоны требуют `Authorization: Bearer <keycloak_token>` — токен прокидывается
в Urban API, GenPlanner и GenBuilder как есть; сам сервис его не валидирует.

## События SSE

Словарь совпадает с GenPlanner и GenBuilder, плюс три своих — `indicators`,
`territory_indicators` и `profile_selected`:

`chat_created` · `token` · `progress` · **`indicators`** · **`territory_indicators`** ·
**`profile_selected`** · `zones` · `roads` · `result` · `file` · `warning` · `error` · `done`

Стадии `progress`: `fetch_indicators` → `select_profile` → `genplanner` → `map_zones` → `genbuilder` → `assemble`.

HTTP-статус потока всегда `200`: фатальная ошибка приходит событием `error` внутри потока.
Если GenBuilder упал, зоны всё равно уже отданы — это полезный частичный результат, а не провал.

## Раскладка

```
app/
  clients/            urban_api · genplanner · genbuilder
  common/             auth · chat_storage · llm · api_handlers · constants · exceptions · logging
  pipeline/           profile_selector · zone_mapper · targets_policy · pipeline_service · events
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
