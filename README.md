# vLLM на CPU в Minikube

Qwen2.5-0.5B-Instruct обслуживается через vLLM 0.33.0. Веса скачивает отдельный
initContainer, затем сервер читает их с PVC без обращения к Hugging Face.
Оба контейнера работают с UID/GID 10001, без capabilities и с read-only корневой ФС.

Решение рассчитано на Linux ARM64 в Docker Desktop на Apple Silicon. Нужны
Docker Desktop, Python 3.11+, Minikube 1.39 и kubectl 1.37. Для Docker выделить
не менее 3 CPU и 6,5 ГиБ RAM; на диске оставить около 20 ГБ свободного места.
Скрипт запуска ограничивает Minikube тремя CPU и 5,5 ГиБ, сервер — двумя CPU
и 4,5 ГиБ. Нагрузочный тест в решение не входит.

## Запуск

Открыть Docker Desktop и дождаться запуска движка.

При первом запуске создать локальный файл секретов:

```sh
python3 - <<'PY'
import os, secrets
fd = os.open('k8s/.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write('HF_TOKEN=\nVLLM_API_KEY=' + secrets.token_urlsafe(32) + '\n')
PY
```

Для публичной Qwen `HF_TOKEN` остаётся пустым. Для gated-модели токен нужно
вписать в этот файл. Kustomize создаёт из него Secret; загрузчик получает только
HF-токен, сервер — только API-токен. Файл исключён из Git и Docker context.

```sh
docker build --platform linux/arm64 -t safeboard-vllm:cpu .
docker build --platform linux/arm64 -f downloader/Dockerfile -t safeboard-downloader:cpu .
python3 scripts/start-minikube.py
minikube -p safeboard image load safeboard-vllm:cpu safeboard-downloader:cpu
kubectl --context safeboard apply -k k8s
kubectl --context safeboard -n vllm-cpu rollout status deployment/vllm --timeout=300s
kubectl --context safeboard -n vllm-cpu port-forward service/vllm 8000:8000
```

В первый раз потребуется интернет для образов и модели. Последняя команда
занимает терминал. Запросы выполнять в другом терминале из корня репозитория.
Если внутри узла не проходит UDP DNS, скрипт запуска проверяет TCP и включает
его только в resolver и CoreDNS профиля `safeboard`. Настройки macOS не меняются.

## Запрос

```sh
API_KEY=$(sed -n 's/^VLLM_API_KEY=//p' k8s/.env)
curl --fail-with-body http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen","messages":[{"role":"user","content":"Сколько будет 2 + 2?"}],"max_tokens":32}'
```

Доступны `GET /health`, `GET /v1/models` и `POST /v1/chat/completions`.
Только health-check не требует токена. Остальные маршруты возвращают 404.
Для streaming добавить в тело запроса `"stream": true` и вызвать curl с `-N`.

## Настройки и кэш

Параметры инференса находятся в `k8s/serve.yaml`, модель и её commit hash —
в `k8s/kustomization.yaml`. После изменения достаточно повторить `kubectl apply -k k8s`:
Kustomize меняет имя ConfigMap и запускает новую ревизию Deployment. При изменении
кода или зависимостей образы нужно пересобрать, снова загрузить в Minikube и выполнить
`kubectl --context safeboard -n vllm-cpu rollout restart deployment/vllm`.

Используется BF16: процессор M3 поддерживает эту точность, а переход к float32
увеличивает расход памяти. Контекст ограничен 2048 токенами, один блок prefill —
256 токенами, KV-кэш — 256 МиБ, одновременно обрабатывается одна последовательность.

Загрузчик фиксирует SHA-256 скачанных файлов и атомарно записывает маркер готовности.
При повторном запуске проверяет файлы локально; незавершённое скачивание продолжает,
повреждённый готовый snapshot скачивает заново. Сервер монтирует PVC только для чтения.
Удаление пода и остановка Minikube сохраняют кэш; удаление PVC или профиля — удаляет его.

API пишет в журнал ID обращения, статус и длительность. Для ошибок vLLM остаются
уровень, источник, строка и тип исключения: сообщения и traceback отбрасываются,
поскольку могут содержать пользовательский текст. Тела, заголовки и query-параметры
не журналируются. Health-check не создаёт audit-записей.

CPU wheel 0.33.0 опубликован в официальном релизе под тегом
[v0.33.0rc3](https://github.com/vllm-project/vllm/releases/tag/v0.33.0rc3).
В requirements зафиксированы его URL, хеш и все транзитивные зависимости.
Для обновления lock-файлов используется uv:

```sh
uv pip compile requirements.in --python-platform aarch64-manylinux_2_40 --python-version 3.12 --torch-backend cpu --index-strategy unsafe-best-match --generate-hashes --emit-index-url --no-header --no-annotate --only-binary :all: -o requirements.txt
uv pip compile downloader/requirements.in --python-platform aarch64-manylinux_2_40 --python-version 3.12 --generate-hashes --emit-index-url --no-header --no-annotate --only-binary :all: -o downloader/requirements.txt
```

## Проверка и остановка

Проверено на MacBook Air M3 с 16 ГБ RAM: 17 модульных тестов, smoke-тест,
пересоздание пода, изменение ConfigMap без пересборки и запуск без сети.
Пик памяти сервера в Kubernetes — около 4,3 ГиБ; весь Minikube после smoke-теста
занимал около 4,4 ГиБ.

При запущенном port-forward:

```sh
python3 -m unittest discover -s tests -q
python3 tests/smoke.py
```

Smoke-тест проверяет авторизацию, закрытые маршруты, обычный ответ, streaming,
ошибки валидации и отсутствие входного текста, ответа и токена в логах.
Кэш после пересоздания пода можно проверить так:

```sh
kubectl --context safeboard -n vllm-cpu delete pod -l app=vllm
kubectl --context safeboard -n vllm-cpu rollout status deployment/vllm --timeout=300s
kubectl --context safeboard -n vllm-cpu logs deployment/vllm -c download
```

Ожидаемое событие загрузчика — `model_cache_hit`. Port-forward после удаления
пода нужно запустить заново. Offline-проверку выполнять отдельно от Kubernetes:

```sh
minikube stop -p safeboard
python3 tests/offline.py
```

Этот сценарий скачивает модель в Docker volume `safeboard-model`, повторяет запуск
загрузчика и делает запрос к серверу с `--network=none`. Контейнер сервера по окончании
останавливается. Volume остаётся для повторной проверки.

Чтобы освободить память, достаточно `minikube stop -p safeboard`. Для полного удаления
стенда и обоих кэшей:

```sh
minikube delete -p safeboard
docker volume rm safeboard-model
```
