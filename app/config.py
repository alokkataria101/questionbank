"""Settings. Secrets are read from .env.secrets (git-ignored) or the environment."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# SECRETS_FILE override -> ./.env.secrets -> plain environment variables
for _f in (os.getenv("SECRETS_FILE", ""), str(ROOT / ".env.secrets")):
    if _f and Path(_f).is_file():
        load_dotenv(_f, override=False)
        break
load_dotenv(ROOT / ".env", override=True)

WATSONX_API_KEY = os.getenv("WATSONX_API_KEY", "")
WATSONX_PROJECT_ID = os.getenv("WATSONX_PROJECT_ID", "")
WATSONX_URL = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
# Must be a vision-capable model: handwriting arrives as an image.
WATSONX_VISION_MODEL = os.getenv(
    "WATSONX_VISION_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8"
)

STORAGE_DIR = Path(os.getenv("QB_STORAGE_DIR", ROOT / "storage"))
DATABASE_URL = os.getenv("QB_DATABASE_URL", f"sqlite:///{STORAGE_DIR / 'questionbank.db'}")

# A school can switch external AI off entirely.
ALLOW_EXTERNAL_AI = os.getenv("QB_ALLOW_EXTERNAL_AI", "true").lower() == "true"


def watsonx_configured() -> bool:
    return bool(WATSONX_API_KEY and WATSONX_PROJECT_ID)
