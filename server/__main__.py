from dotenv import load_dotenv

from scout.pipeline import ROOT

load_dotenv(ROOT / ".env")

import sys  # noqa: E402

if sys.argv[1:] == ["fees"]:
    # What the team is owed from the pool: python -m server fees
    from .credits import team_fees
    rows = team_fees()
    for r in rows:
        print(f"{r['team_address']}: {r['fees_usdc']:.2f} USDC in fees from {r['purchases']} purchases "
              f"({r['paid_usdc']:.2f} USDC paid in)")
    print("No credit purchases yet." if not rows else "")
else:
    from .app import serve
    serve()
