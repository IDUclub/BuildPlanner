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
| `GET` | `/buildplanner/reference/indicators` | какие индикаторы участвуют в выборе |
| `GET` | `/buildplanner/reference/profiles` | профили и их застройка в GenBuilder |
| `GET` | `/buildplanner/health`, `/buildplanner/logs/log_file` | служебные |

Все прогоны требуют `Authorization: Bearer <keycloak_token>` — токен прокидывается
в Urban API, GenPlanner и GenBuilder как есть; сам сервис его не валидирует.

## События SSE

Словарь совпадает с GenPlanner и GenBuilder, плюс два своих — `indicators` и `profile_selected`:

`chat_created` · `token` · `progress` · **`indicators`** · **`profile_selected`** · `zones` · `roads` ·
`result` · `file` · `warning` · `error` · `done`

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

## Запрос в GenPlanner

`POST /genplanner/run_func_generation` объявлен как `Annotated[GenPlannerFuncZonesDTO, Depends(...)]`,
а не как тело запроса, поэтому FastAPI раскладывает поля DTO по двум местам:

| Где | Поля |
|---|---|
| query | `project_id`, `scenario_id`, `roads_extend_distance`, `elevation_angle`, `ignore_default_relations`, `test` |
| body | `territory_balance` (обязательно), `fix_zones`, `min_block_area`, `functional_zones`, `neighbour_pairs`, `forbidden_pairs` |

`project_id` обязателен, поэтому сервис сначала достаёт его из `GET /api/v1/scenarios/{id}`
(`scenario.project.project_id`); переопределяется полем `project_id` в опциях прогона.
