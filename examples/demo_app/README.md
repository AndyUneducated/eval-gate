# examples/demo_app

A 50-line LLM pipeline that proves the OTel → EvalGate ingest path works end-to-end.

## What it does

1. Wires `opentelemetry-sdk` with an `OTLPSpanExporter` pointing at a local EvalGate (`http://localhost:8000/v1/otel/traces`).
2. Opens a parent `rag-pipeline` span with a child `retriever.search` and a child `llm.call`.
3. Calls `litellm.completion(..., mock_response="four")` — **no real API key required**, no credit burn.
4. Flushes the span processor so all spans land before the process exits.

## Run

```bash
make demo-trace            # one-shot: starts DB + API, runs this, curls back
# or, manually:
make db-up
uv run alembic upgrade head
uv run uvicorn evalgate.api.main:app --port 8000 &
uv run python -m examples.demo_app.pipeline
curl -s http://localhost:8000/v1/traces?limit=5 | python -m json.tool
```

## Real-model run

To exercise a real provider instead of the mock, drop `mock_response=` and pass an API key (e.g. `OPENAI_API_KEY`) — the OTel instrumentation around the call stays unchanged.
