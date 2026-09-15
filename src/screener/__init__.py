from pathlib import Path

from dotenv import load_dotenv

# Runs once, for any import of the package, so local secrets
# (DATAFORDELER_DAR_API_KEY, ANTHROPIC_API_KEY, ...) are available via
# os.environ without every module needing its own dotenv call. A real env
# var set by the shell/deploy environment always takes precedence over
# .env (override=False, the default).
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
