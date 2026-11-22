**English** | [Русский](README.ru.md)

# BuildPlanner API

Orchestrator of the **GenPlanner → GenBuilder** pipeline. Given a `scenario_id`, it selects an urban
planning profile from the Urban API indicators, generates territorial zones and fills them with
buildings, returning either a synchronous response or an SSE stream in the chat.


## Pipeline

| Step | What happens | Where |
|---|---|---|
| 1 | `GET /api/v1/scenarios/{id}/indicators_values` | Urban API |
| 2 | The largest of the 10 indicators of family 269 → `profile_id` | here |
| 3 | `GET /default/func_ratio` → `POST /run_func_generation` → zones and roads | GenPlanner |
| 4 | Zones → GenBuilder taxonomy blocks + `targets_by_zone` | here |
| 5 | `POST /generate/by_territory` → buildings | GenBuilder |
| 6 | Scenario under the service account + message to the broker → computation of assessments | Urban API |
| 7 | `GET /optimize/scheduler` → construction schedule, `GET /optimize/teps` → service provision | SIRTEP |

Indicators of family 284 (286 business cluster, 287 industrial zone, 288 logistics) **do not take part**
in the selection: they are measured in points and are not comparable with the dimensionless 271–280.

## Running

```bash
cp .env.example .env.development
poetry install --with dev
poetry run uvicorn app.main:app --reload --port 8080
```

Documentation is at `/api/docs`. The service starts even without vLLM and without ChatStorage:
the chat then works without parsing the user's message and without saving history (a warning is logged).

```bash
make format   # isort + black
make lint     # pylint
make test     # pytest
```

## vLLM and Qwen

`VLLM_BASE_URL` sets the server address, `CHAT_MODEL` the exact model ID from
`GET /v1/models`, for example `qwen3.8-27b`. All requests disable thinking via
`chat_template_kwargs: {"enable_thinking": false}`. The history contains only
`user` and `assistant` messages; the system prompt is sent once at the beginning.
Qwen3.8-27B requires the matching `--reasoning-parser qwen3` on the server.

As in GenPlanner, a single structured call combines parameter parsing, the decision,
the answer and the chat title. It uses `priority=-100` (live / blocking),
configurable via `VLLM_CHAT_PRIORITY`. Streaming client calls use the same
priority. The value is a signed 64-bit integer; `0` disables prioritization.
Non-zero values require `--scheduling-policy priority` on vLLM.

If `content` is empty, the client accepts a complete JSON from `reasoning` or
`reasoning_content` only when `finish_reason=stop` and after schema validation.
Otherwise the empty response is retried once. HTTP errors are not retried, and
the reasoning text never ends up in the error message.

BuildPlanner composes the final text summary from the pipeline events;
there is no separate LLM call for it.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/buildplanner/scenarios/{id}/run` | synchronous run, a single JSON |
| `POST` | `/buildplanner/scenarios/{id}/run/stream` | the same run as a stream, without chat |
| `POST` | `/buildplanner/scenarios/{id}/chat/stream` | conversational run (SSE) |
| `GET` | `/buildplanner/scenarios/{id}/indicators` | project indicators as a table, without generation |
| `GET` | `/buildplanner/reference/indicators` | which indicators take part in the selection |
| `GET` | `/buildplanner/reference/profiles` | profiles and their development in GenBuilder |
| `GET` | `/buildplanner/files/{slot}/{result_id}` | a run layer from storage, via the link from the `file` event |
| `GET` | `/buildplanner/health`, `/buildplanner/logs/log_file` | service endpoints |

All runs require `Authorization: Bearer <keycloak_token>`; the token is passed as is
to Urban API, GenPlanner and GenBuilder; the service itself does not validate it.

## SSE events

The vocabulary matches GenPlanner and GenBuilder, plus four events of its own: `indicators`,
`territory_indicators`, `profile_selected` and `scenario_published`:

`chat_created` · `token` · `progress` · **`indicators`** · **`territory_indicators`** ·
**`profile_selected`** · `zones` · `roads` · `result` · **`scenario_published`** ·
`file` · `warning` · `error` · `done`

The HTTP status of the stream is always `200`: a fatal error arrives as an `error` event inside the stream.
If GenBuilder fails, the zones have already been delivered; that is a useful partial result, not a failure.

**Frame.** The `type` key becomes the event name, everything else is JSON in `data`; Cyrillic
is not escaped. Keep-alive ping comments are sent between events and should be skipped.

```
event: progress
data: {"stage": "genplanner", "content": "Генерирую территориальные зоны"}
```

**Order in `chat/stream`.** Events in square brackets do not always arrive.

```
[warning load_history]                        chat history unavailable
[chat_created]                                only for a new chat
token × N                                     model answer text in chunks
                                              — if the model does not start the pipeline: done right away
progress fetch_indicators
indicators
territory_indicators | warning fetch_indicators
progress select_profile
profile_selected  [warning non_buildable_profile]
progress genplanner
zones → file(zones)
[roads → file(roads)]                         if GenPlanner returned roads
progress map_zones  [warning zones_skipped]
[warning no_volume_target] [warning services_region_unknown]
progress genbuilder
progress assemble
result → file(buildings) → [file × N from GenBuilder]
[progress publish_scenario → scenario_published  [warning …]]   if publishing is enabled
done
```

`run/stream` emits the same, without `load_history`, `chat_created` and `token`; its `done` is `{}`.

**Geo layers.** The `file`, `zones`, `roads` and `buildings` events carry a compact descriptor
in `content`, not the full GeoJSON. The frontend loads `content.url`: it is a durable link
that, on MinIO, responds with `307` and a fresh temporary link; `content.download_url` can be
used for an immediate download. Only `url` is stored in the chat history.

**What the events contain.**

| Event | Fields |
|---|---|
| `chat_created` | `chat_id`, `title` |
| `token` | `content` — a chunk of text, concatenate in order |
| `progress` | `stage`, `content` — ready-made stage caption; for `select_profile`, the reason for the profile choice |
| `indicators` | `values` — the ten selection indicators: `indicator_id`, `name`, `profile_id`, `raw`, `normalized` |
| `territory_indicators` | `total`, `highlights`, `sections` — see [Indicator showcase](#indicator-showcase) |
| `profile_selected` | `profile_id`, `profile_name`, `indicator_id`, `reason`, `buildable`, `missing_indicator_ids`, `scoreboard` |
| `zones` | `source: "genplanner"`, `content` — FeatureCollection with Russian attributes |
| `roads` | `content` — FeatureCollection with Russian attributes |
| `result` | `content` — FeatureCollection of buildings with GenBuilder attributes, `summary` |
| `file` | description of a stored layer — see [Map layers](#map-layers-frontend-contract) |
| `scenario_published` | `project_id`, `scenario_id`, `zones_written`, `buildings_written`, `buildings_failed`, `buildings_total`, `services_written`, `services_failed`, `unknown_zone_names`, `unknown_service_names`, `notified`, `notified_events`, `failed_stage`, `error` |
| `sirtep_schedule` | `content` — SIRTEP response as is (`provision.house_construction_period` and `service_construction_period` — the period of each object, used to colour the map), `summary` — summary |
| `sirtep_provision` | `content` — TEP (planning metrics) as is, `summary` — `final_by_service`, `unbuilt_services` |
| `master_plan_summary` | run outcome: `buildings`, `published`, `schedule`, `provision`, `warnings`; arrives last, before `done` |
| `warning` | `stage`, `detail` — machine code or reason text, `message` — text for the user |
| `error` | `stage`, `detail` — a **string** containing the JSON of the original error; it must be parsed |
| `done` | `chat_id`, `assistant_message_id`; if the pipeline was not started, `assistant_message_id` is `null` |

`progress` stages and their default captions (the service emits them in Russian):

| `stage` | `content` | Meaning |
|---|---|---|
| `fetch_indicators` | Читаю показатели сценария | Reading scenario indicators |
| `select_profile` | Выбираю профиль застройки | Selecting the development profile |
| `genplanner` | Генерирую территориальные зоны | Generating territorial zones |
| `map_zones` | Готовлю блоки для застройки | Preparing blocks for development |
| `genbuilder` | Расставляю застройку | Placing buildings |
| `assemble` | Собираю результат | Assembling the result |
| `publish_scenario` | Сохраняю сценарий для расчёта оценок | Saving the scenario for assessment |
| `sirtep` | Считаю очерёдность строительства | Computing the construction sequence |

`result.summary` is a building summary plus whatever GenBuilder sent in its own `summary`:

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

(`"жилая многоэтажная"` = "multi-storey residential".)

**When some events will be missing.** A `warning` does not stop the run, an `error` does,
but `done` always arrives.

| Situation | What changes |
|---|---|
| `skip_generation: true` | after the zones — `progress assemble` "Застройка пропущена по запросу" (development skipped on request); no `result` and no `file(buildings)` |
| nothing to build (recreation, agriculture) | `warning genbuilder nothing_to_build`; no `result` |
| GenBuilder failed | `warning genbuilder`; no `result` |
| a layer was not stored | a single `warning store_layer`, then no `file` events at all; `zones`/`roads`/`result` themselves still arrive |
| publishing | also happens in the first three cases, if `publish` is not disabled and there are zones |
| no residential buildings or services in the written scenario | `warning sirtep sirtep_skipped` with the reason; no `sirtep_schedule` |
| `sirtep_wait_provision: false` | `sirtep_schedule` present, no `sirtep_provision` |
| TEP not computed within the timeout | `warning sirtep`; the schedule remains, `master_plan_summary` arrives |
| fatal error | `error`, then `done` |

After a reload the events are gone: the map is redrawn from the `kind: "file"` parts of the chat
history (see [Map layers](#map-layers-frontend-contract)), and the answer text comes from the message
itself: the model's reply, the "Territory indicators" table, the reason for the profile choice, the
line about the saved scenario and the warning texts.

## Map layers: frontend contract

Generation results are shown on the map twice: immediately from the stream itself, and after a chat
reload from the links in the history. The stream itself does not survive a reload, so every
layer is also put into storage, and a link to it is sent both in the stream and in the assistant message.

**Live stream.** A layer arrives in full, immediately followed by a `file` event with a link to its copy:

```
zones  → file(name=zones)  → roads → file(name=roads) → … → result → file(name=buildings) → scenario_published
```

| Layer | Live event | Where the GeoJSON is | `file.name` |
|---|---|---|---|
| Territorial zones | `zones` | `content` — FeatureCollection of GenPlanner zones | `zones` |
| Roads | `roads` | `content` — FeatureCollection of roads | `roads` |
| Buildings | `result` | `content` — FeatureCollection of buildings, with `summary` next to it | `buildings` |

`roads` arrives only if GenPlanner returned them. `result` and `file(buildings)` will not arrive
if development was skipped (`skip_generation`, nothing to build, GenBuilder failed); in that case
the zones and roads stay on the map, and the reason arrives as a `warning`.

**The `file` event** describes a layer and contains no bytes:

```json
{
  "type": "file",
  "name": "zones",
  "title": "Территориальные зоны",
  "role": "result",
  "url": "/buildplanner/files/zones/3f2c…e1",
  "download_url": null,
  "filename": "zones.geojson",
  "mime_type": "application/geo+json",
  "source_service": "buildplanner"
}
```

(`"Территориальные зоны"` = "Territorial zones".)

Files produced by GenBuilder itself arrive in the same event with `source_service: "genbuilder"`.

**Object attributes.** Zones and roads come with Russian keys, and the attribute panel
shows them as is. The rules are the same as in the GenPlanner chat; stored files match
the stream. The synchronous `POST …/run`, GenBuilder and writing to Urban API work with
GenPlanner's original machine properties.

| Layer | Property | Value |
|---|---|---|
| Zones | `Территориальная зона` (territorial zone) | жилая, рекреационная, промышленная, общественно-деловая, транспортная, сельскохозяйственная, специального назначения (residential, recreational, industrial, public and business, transport, agricultural, special purpose); «не определена» (undefined) if the type is unknown |
| Zones | `Сгенерирована` (generated) | `Да` / `Нет` (yes / no); key missing — GenPlanner did not report it |
| Zones | `Идентификатор исходной зоны` (source zone ID) | only for zones taken from existing zoning |
| Roads | `Название`, `Адрес` (name, address) | only for existing roads |
| Roads | `Ширина, м` (width, m) | number |
| Roads | `physical_object_type_id` | as in Urban API, for styling |
| Roads | `road_lvl` | not translated: `regulated highway`, `local road, level N`, `user_roads` |
| Roads | `road_class` | for the legend: `highway`, `street`, `existing`; key missing — unknown level |

Buildings (`result`, `file(buildings)`) are not translated: the keys stay machine-readable, and the
frontend takes labels and value dictionaries from GenBuilder via `GET /generate/properties_schema`.
A separate copy of the labels here would diverge from it at the first change in GenBuilder.

**Chat history.** The assistant message in ChatStorage has the text as its first part, followed by one
`{"kind": "file", "payload": {...}}` part per layer; `payload` has the same fields as the `file`
event, except empty ones. To redraw the map, the frontend takes the `kind == "file"` parts,
sorts them by `name` and requests `url` with the same `Authorization: Bearer <token>`.
The response is `application/geo+json`, the same FeatureCollection that was in the stream. An unknown
layer or run → `404`, unavailable storage → `502`.

**If a layer was not stored**, the stream contains a single `warning` with `stage: "store_layer"`, and
no further `file` events will arrive until the end of the run. This is not an error: the map from the
stream has already been drawn; only the redraw after a reload is lost.

**Storage.** MinIO, if all four `MINIO_*` variables are set (a partial set is a configuration
error: the service starts without storage and logs it); if none is set, the `OUTPUTS_DIR`
directory on disk, which is suitable only for local development. MinIO lives in a closed
network, so files are served through the service itself rather than via direct links to the bucket.
`MINIO_ADDRESS` is the S3 API address in the form `http://host:9000` (not the web console); the
`https://` scheme enables TLS. `url` is a relative path without the service address: the frontend
prepends its own base by `source_service` (for old records with an absolute link it strips the origin).
This way the internal pod address, which is not reachable from the external network, never ends up in
the history.

## Layout

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

The three translation tables between other systems' identifier spaces are collected in one place:
[`app/common/constants/pipeline_constants.py`](app/common/constants/pipeline_constants.py).
Most pipeline errors come down to one of them getting out of sync, so they are covered by a
completeness test (`tests/test_constants.py`).

## Scenario indicators

The `indicators_values` schema has been checked against the environment's OpenAPI (`ScenarioIndicatorValue`):

- filtering by indicator is done by Urban API itself: the `indicator_ids` query parameter, comma-separated IDs;
- `indicator_id` is inside the nested `indicator` object, not at the root of the row;
- there are no `date_value`/`value_type` fields; freshness is determined by `updated_at`/`created_at`;
- a value can be bound to a hexagon (`hexagon_id`), and most rows in the response are like that.

Hence the selection rule in `UrbanApiClient.latest_values_by_indicator`: territory-level values
first (`hexagon_id` empty), and among equals the most recent one. Otherwise one could compare the
territory aggregate of one indicator with a single cell of another and pick the wrong profile.

## Indicator showcase

Besides the ten selection indicators, the service also shows the project's other indicators,
via the `territory_indicators` event and the `GET /scenarios/{id}/indicators` endpoint. Two levels
in one response, so the frontend decides how much to show:

- `highlights` — the "territory passport", 8 indicators in a fixed order
  (population, density, area, urbanization, % of settlement land, average age,
  street and road network density, urban environment quality index). These are the ones sent
  as a Markdown table in the chat answer text;
- `sections` — everything else, grouped by the sections of the
  `GET /api/v1/indicators_groups` reference (Demography, Transport, Economy…); rows within a
  section are sorted by `list_label` in natural order (`1.2.10` after `1.2.9`).

Names, units and numbering are provided by Urban API itself (`indicator.name_full`,
`measurement_unit`, `list_label`); the service has no indicator reference of its own, since
it would inevitably diverge from the environment. Only two lists are ours, in
[`pipeline_constants.py`](app/common/constants/pipeline_constants.py):
the order of the "passport" and the Russian section headings.

An indicator belongs to several groups at once (`Численность населения` (population) is in both
`regional` and `demogrphy`), so the section is chosen by `INDICATOR_GROUP_ORDER`: thematic
groups first, catch-all groups (`regional`, `common`) last. Indicators outside any group,
including family 269, go to "Other indicators".

The request is separate from profile selection and is **non-fatal**: the selection fetches exactly ten
indicators and must be predictable, while the showcase pulls everything from the scenario. If it
cannot be assembled, the run continues and a `warning` is sent upstream. The groups reference
is cached in memory for an hour.

## Request to GenPlanner

`POST /genplanner/run_func_generation` is declared as `Annotated[GenPlannerFuncZonesDTO, Depends(...)]`,
not as a request body, so FastAPI splits the DTO fields between two places:

| Where | Fields |
|---|---|
| query | `project_id`, `scenario_id`, `roads_extend_distance`, `elevation_angle`, `ignore_default_relations`, `test` |
| body | `territory_balance` (required), `fix_zones`, `min_block_area`, `functional_zones`, `neighbour_pairs`, `forbidden_pairs` |

`project_id` is required, so the service first gets it from `GET /api/v1/scenarios/{id}`
(`scenario.project.project_id`); it can be overridden by the `project_id` field in the run options.

## Request to GenBuilder

`POST /generate/by_territory` accepts a `TerritoryRequest`. Two places where it is easy to make a mistake:

**`targets_by_zone`: parameter outside, zone inside.** GenBuilder reads
`targets_by_zone["floors_avg"]["residential"]`, not `["residential"]["floors_avg"]`.
Both forms are `dict[str, dict]`, so it silently accepts the inverted one and ignores it,
falling back to its defaults. Inside the service, targets are kept per zone (which makes them
easy to override via `targets_overrides`), and `to_genbuilder_targets` inverts them
before sending. Allowed parameters: `residents`, `coverage_area`, `floors_avg`,
`density_scenario` (only `min`/`mean`/`max`), `default_floor_group`.

**A zone without a volume target is not developed.** Generation is split into three branches, each with
its own threshold: housing requires `residents > 0`, industrial/transport/special-purpose zones
require `coverage_area > 0`, business and "basic" zones require either of the two. Zones without a
target are listed in a `warning` event with the code `no_volume_target`.

Both targets are absolute, for the whole territory, so the policy sets them as specific values
(`residents_per_ha` and `coverage_ratio`), and the absolute values are computed from the total
area of the zone's blocks. The area is computed from the geometry itself using a spherical formula
([`geo_area.py`](app/pipeline/geo_area.py)), without geopandas, so as not to pull in the GDAL stack
for a single formula. Per-zone areas go into `mapping_summary.area_ha`.

Specific values are capped: density by the ceiling of its floor-count group, coverage
by `COVERAGE_RATIO_CAP`. Absolute `residents`/`coverage_area` values in `targets_overrides`
take precedence over the area-based calculation (see [Number of residents](#number-of-residents)).

Blocks: every feature must have a non-empty `properties.zone` and a
Polygon/MultiPolygon geometry. `properties.floors_group` on a block overrides `default_floor_group`.

## Number of residents

It is not asked for, neither from the frontend nor in the chat. The pipeline derives it itself:
the area of the zone's blocks × the profile's standard density, capped by the ceiling of its
floor-count group. This is policy D4, "maximum within regulatory limits": setting the number
from outside would mean either under-using the capacity or exceeding the standard.

The calculation can still be overridden selectively with an absolute
`targets_overrides.residential.residents`; this is a manual emergency exit, not the normal path.
The final run options are saved in the `metadata` of the chat response message, so that overrides
survive a reload.

## Publishing the scenario and triggering assessments

Assessments are computed by third-party services subscribed to Kafka. The service does not run its own
producer: Urban API has an HTTP facade over the broker (`/api/broker/...`), a plain
POST, which is what we use.

The order in `ScenarioPublisher` is mandatory and cannot be rearranged:

| | Action | Endpoint |
|---|---|---|
| 1 | container project of the service account (one per source project) | `POST /api/v1/projects?user_id=…` |
| 2 | copy of the source scenario into this project | `POST /api/v1/scenarios/{id}` |
| 3 | GenPlanner zones, in one request (the endpoint accepts an array) | `POST /scenarios/{id}/functional_zones` |
| 4 | GenBuilder buildings, two requests per building | `POST /scenarios/{id}/physical_objects` → `/buildings` |
| 5 | announcement to the broker | `POST /api/broker/scenario_events/…` |

Messages are sent **last**: the assessment services go and read the scenario when they receive them,
and must not get ahead of the data being written, or they will compute an empty scenario. An empty
scenario is not announced at all.

**Why a service account.** The owner can be set in exactly one place in the API,
`POST /api/v1/projects?user_id=...`; `Copy Scenario` has no such option: a scenario inherits its project,
and a project inherits its owner. Therefore the generated result is placed in the service account's
project and does not appear in the user's project. The `user_id` itself is not configurable: it is taken
from the `sub` claim of the same token we write with; otherwise the account and its secret could drift apart.

The source project is read with the user's token (only the user can see their own project) and written
with the service token. This is the only place where both tokens take part in one action.

Publishing is non-fatal: if Urban API is unavailable or permissions are insufficient, the run emits a
`warning` with the `publish_scenario` stage, and the zones and buildings remain with the user.


## Construction sequence (SIRTEP)

This stage runs **after** publishing and works on the published scenario: SIRTEP reads it from
Urban API by itself, so enabling it without `PUBLISH_TO_URBAN` is pointless; the stage is enabled
only when both `SIRTEP_API` and publishing are configured. A **service** token is required: the scenario
lives in the service account's project, the user's token would not see it, and SIRTEP does not verify the
token but passes it on. The header itself is still mandatory: without `Authorization`, the endpoints
respond with 401 before reaching Urban API.

| | Action | Endpoint |
|---|---|---|
| 1 | construction schedule, synchronous | `GET /optimize/scheduler` |
| 2 | service provision by period, polled until ready | `GET /optimize/teps` |

The default horizon is **20 quarters of 100,000 m² each**, i.e. a ceiling of 2 million m² of residential
floor area of houses and service footprints. The library default of 40 quarters produced a sparse schedule
at our volumes: a measurement on the environment on 2026-09-16 showed 415,801 m², about four construction
periods stretched over thirty-five. If the volume does not fit into the horizon, SIRTEP does not fail but
returns the comment "Ни одно здание не будет построено" ("No building will be constructed"), which ends up
in the summary. The horizon can be changed per run with the `sirtep_periods` and
`sirtep_max_area_per_period` options.

`profile_id` is always `1`: in the provision branch the profile only selects the branch itself, and
`/optimize/teps` version 0.3.1 accepts only 1, 2 and 8. The run profile does not affect this.

**When we do not call it.** SIRTEP responds with 400 if there are no residential buildings and with 500 if
there are no services, and neither explains anything to the user. Therefore the condition is checked in
advance against the written data: the scenario has been written completely (a failure at the `broker` step
does not matter, the data is in place), there is at least one building of type "Жилой дом" (residential
building) and at least one service. Otherwise a `warning` with the reason is emitted.

What is checked is the composition of what was written, not the run profile: buildings are made residential
by the physical object type, and a non-residential profile can produce residential blocks in the zone
balance, and vice versa.

**Why `/teps` is polled.** TEPs are computed in the background after the schedule. Version 0.3.1 responds to
an unfinished computation with 500 "cache corrupted" instead of "in progress": the task is put into the cache
with a date in its identifier but looked up without it. Therefore any 5xx before
`SIRTEP_PROVISION_TIMEOUT_SECONDS` expires is treated as "not ready yet", while a 4xx is raised immediately.

The synchronous `POST /run` waits for TEPs just like the stream does: otherwise service provision would be
unreachable, since the caller has no token of their own for the service project. Waiting can be turned off
with the `sirtep_wait_provision` option, which works the same way in both paths.

The stage is non-fatal: if the schedule could not be computed, the zones, buildings and scenario remain with
the user. Globally, writing is **disabled by default** and is enabled only explicitly with
`PUBLISH_TO_URBAN=true`: the service is deployed to the dev cluster automatically, and writing to the shared
environment must not turn on by itself. For an individual run it is disabled with `publish: false` in the options.

**No new zone translation table was needed.** Urban API's `functional_zone_type_id` uses the same
identifier space as the GenPlanner profiles: 1 residential, 2 recreation, 7 business,
13 residential_multistorey. All 12 of our profiles exist in the environment's reference
(`tests/test_constants.py` guards this), so `properties.territory_zone` is written as is,
and the reference is needed only to make sure the environment knows the id. The name is a fallback:
in the reference the names are in English (`residential_multistorey`), the Russian one is in `zone_nickname`,
and GenPlanner returns its own ("жилая многоэтажная", multi-storey residential), which is translated by
`ZONE_NAME_TO_PROFILE`. A zone that cannot be identified is skipped and listed in `unknown_zone_names`.

Buildings are assigned physical object types: `residential` → "Жилой дом" (residential building), all others →
"Нежилое здание" (non-residential building). The constants hold names, not ids: every environment has its own ids.

## Chat: ChatStorage and vLLM

The request format has been checked against the GenPlanner and GenBuilder clients; both services talk
to the same shared services.

**ChatStorage.** Authentication uses a Keycloak service token (`client_credentials`);
the user is passed in the `X-User-Id` header, and requests without it are rejected.
In the `create_chat` body, `scenario_id` and `project_id` are **at the top level**,
not in `metadata`, otherwise the chat will not be linked to the scenario. `metadata` is non-nullable:
always send at least `{}`. Messages are multipart: `parts: [{kind, payload}]`.

**vLLM.** OpenAI-compatible `/v1/chat/completions`; the base URL is normalized
so that `/v1` is not doubled. Only `name` and `schema` are sent in `json_schema`;
neighbouring services do not send `strict`. vLLM reports errors via an `error` field in the body,
including with status `200` and inside the stream, so the body is checked separately.
A broken SSE frame is skipped with a warning rather than breaking an answer already in progress;
a structured answer is also parsed from a ```` ```json ```` wrapper.

## Text reports

After an operation completes, the chat additionally emits a `file` event with
`name: "report"`, `role: "report"`, `mime_type: "text/plain"`, `filename: "Отчёт.txt"`
("Report.txt"). The regular text answer stays in the stream. The link to the report is
stored next to it in the ChatStorage history as a `kind: "file"` part; the file contents
are not copied into the history.

The descriptor carries two durable links relative to the `source_service` base URL:

- `url`: `GET /buildplanner/reports/id` — UTF-8 text for viewing (`Content-Disposition: inline`).
- `download_url`: the same address with `?download=true` — download (`attachment; filename="report.txt"`).

The descriptor is located in the `content` of the `file` event.
The report includes the full answer, the parameters and the scenario, the territory indicators,
the results of each stage and the warnings.

The frontend should read the report as text, not as GeoJSON or HTML. To display it,
`fetch(url).text()` and a text component that preserves line breaks are enough.
Links go through the service API, so the browser never needs access to the internal MinIO.
If storage fails, a `warning` with `stage: store_report` is emitted; the answer and the operation
results are preserved.

The report is written in Russian, following the layout of a regulatory compliance check:
headings, a summary metrics table and separate detail sections. In PZZ (zoning
regulations) reports, objects are grouped by compliance and by whether they need manual
review; a classification result is never presented as a PZZ compliance check.
The text is saved as UTF-8 TXT with Markdown markup suitable for rendering on the
frontend. Internal API fields are omitted; parameter names, zone names and system
statuses are translated. Original zone codes, VRI (permitted use type) codes, document
codes and metric values are kept as is. These changes affect only how the report is
presented; the JSON and SSE schemas are unchanged.

For download, the Russian file name `Отчёт.txt` is sent via the `filename*` header
parameter; `filename` carries an ASCII-compatible fallback name.


## SSE reconnection

Chat SSE endpoints support replay and reconnection by `request_id` without restarting
the operation. See [client contract and storage limits](SSE_RECONNECTION.md).
