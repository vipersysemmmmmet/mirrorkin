from dotenv import load_dotenv

from scout.pipeline import ROOT

load_dotenv(ROOT / ".env")

from .app import serve  # noqa: E402

serve()
