import os

# Tests never print logs to stderr; they still write JSONL into the sandbox.
os.environ.setdefault("LOG_STDERR", "0")
