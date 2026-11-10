"""Check the LLM key + model (Meta Model API) from your machine (reads local .env).

    python -m scripts.llm_smoke

Prints ok / model / latency only. On a 404 it lists the model ids the key can use.
Never prints the key.
"""

import sys

from backend.agent import llm


def main() -> int:
    result = llm.ping(retry=False)
    print(f"ok={result.ok} model={result.model} ms={result.ms}")
    if result.ok:
        return 0
    print(f"error: {result.error}")
    if result.error and result.error.startswith("HTTP 404"):
        try:
            print("models available to this key:")
            for model_id in llm.list_models():
                print(f"  {model_id}")
        except Exception as e:  # diagnostics only
            print(f"  (could not list models: {type(e).__name__})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
