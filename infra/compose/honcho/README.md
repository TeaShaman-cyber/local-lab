# Honcho: локальные эмбеддинги

## Зачем

Honcho по умолчанию берёт `text-embedding-3-small` у внешнего провайдера: векторы считаются вне машины, а память зависит от чужого прайса и доступности. Для Hindsight мы этот класс уже проходили — там стоит `HINDSIGHT_API_EMBEDDINGS_PROVIDER=local` с мультиязычной моделью.

Здесь то же самое, но отдельным сервисом: у Honcho нет встроенного `local`, он умеет только OpenAI-совместимый endpoint.

## Состав

| Файл | Что |
|---|---|
| `embeddings.compose.yml` | TEI (text-embeddings-inference), CPU, порт `127.0.0.1:8080` |
| модель | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, **384** измерения |

Модель та же, что у Hindsight, — чтобы банки были сопоставимы и не пришлось объяснять расхождение метрик.

## Запуск

```bash
docker compose -f embeddings.compose.yml up -d
curl -s localhost:8080/health
curl -s localhost:8080/embed -H 'Content-Type: application/json' \
     -d '{"inputs":"тест мультиязычности"}' | python -c "import sys,json;print(len(json.load(sys.stdin)[0]))"
```

Первый старт скачивает ~470 МБ весов в volume `tei-data`.

## Подключение к Honcho

`config.toml` профиля:

```toml
[embedding]
VECTOR_DIMENSIONS = 384

[embedding.model_config]
transport = "openai"
model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

[embedding.model_config.overrides]
base_url = "http://host.docker.internal:8080/v1"
```

`host.docker.internal`, а не `127.0.0.1`: Honcho работает в контейнере.

## Грабля: `.env` перебивает `config.toml` (RULE-018)

Порядок приоритета у Honcho: **переменные окружения → `config.toml` → дефолт**. Ключ
`EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` в `.env` сильнее, чем
`[embedding.model_config.overrides] base_url` в `config.toml`.

Мы на это наступили: поправили `config.toml` на `:8080`, а запросы продолжали уходить
на старый `:8645`. Ошибка выглядела как проблема модели:

```
400 Model sentence-transformers/paraphrase-multilingual-minilm-l12-v2 does not exist
```

Хотя модель была, а неверным был маршрут.

Второй слой: `docker restart` **не перечитывает** `.env`. Нужно пересоздание:

```bash
docker exec local-test-api-1 env | grep EMBEDDING      # что реально загружено
docker compose up -d api deriver                       # пересоздать, не restart
docker exec local-test-api-1 env | grep EMBEDDING      # подтвердить :8080
```

Правило зафиксировано как `RULE-018` в `docs/reference/infrastructure.md`.

**Миграция размерности.** `documents.embedding` и `message_embeddings.embedding` объявлены как `vector(1536)`. При переходе на 384 нужно пересоздать колонки и пересчитать векторы; пока банк маленький — дешевле очистить и дать deriver пересобрать, чем писать backfill.

## Проверка

Признак того, что всё локально: в ответе на embed нет заголовков `x-railway-request-id` / `X-Nous-Token-Sharing` (они появлялись, когда запрос уходил через Nous).

## Ограничения

- TEI `cpu-1.8` использует ONNX-сборку; при первом старте качает `onnx/model.onnx`.
- Модель 384 измерения уступает `text-embedding-3-small` на длинных текстах; для памяти о разговоре этого достаточно, для поиска по коду — проверять отдельно.
- Порт `8080` локальный (`127.0.0.1`), наружу не публикуется.
