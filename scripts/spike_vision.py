"""Vision spike: extract structured fields from parcel screenshots.

Standalone validation. Run on Mac. No bot changes, no sheet writes.

What it does:
    For two screenshot types (warehouse-arrival notification and
    e-commerce order detail), prompt the Anthropic vision API to extract a
    JSON record and print it plus token usage. Lets us judge:
        - is the extraction accurate?
        - does the model return clean JSON or wrap it in prose?
        - what's the cost per image?

Run:
    python scripts/spike_vision.py
"""

import base64
import json
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"
SCREENSHOTS_DIR = Path.home() / "Desktop" / "parcel_order"


WAREHOUSE_NOTIFICATION_PROMPT = """The user sent this screenshot together with context saying it shows parcel warehouse-arrival notifications from their international forwarder.

Extract every notification card visible. Each card has these fields:
- 快递单号 (tracking number)
- 入库重量 (warehouse-recorded weight, kg)
- 入库时间 (arrival timestamp)
- 入库仓库 (warehouse name)

Respond with ONLY a JSON array (no prose, no markdown fence), one object per card:

[
  {
    "tracking_no": "...",
    "weight_kg": 1.02,
    "arrived_at": "2026-06-05 09:38",
    "warehouse": "..."
  }
]
"""


ORDER_DETAIL_PROMPT = """The user sent this screenshot together with context saying it is an e-commerce order detail page. The platform is: {platform}.

Extract each SKU as a separate item. For unit_price use 到手价 (actual paid per SKU after promotions), NOT 原价. Also extract the order date and total paid amount.

Respond with ONLY a JSON object (no prose, no markdown fence):

{{
  "platform": "{platform}",
  "order_date": "YYYY-MM-DD",
  "items": [
    {{"item_name": "...", "quantity": 1, "unit_price": 22.84}}
  ],
  "total_paid": 46.38
}}
"""


def encode_image(path: Path) -> dict:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": data,
        },
    }


def ask_vision(image_path: Path, prompt: str) -> tuple[str, anthropic.types.Usage]:
    resp = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    encode_image(image_path),
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    return text, resp.usage


def report(label: str, raw: str, usage: anthropic.types.Usage) -> None:
    print(f"\n=== {label} ===")
    print(f"tokens: in={usage.input_tokens} out={usage.output_tokens}")
    print("--- raw response ---")
    print(raw)
    print("--- parsed ---")
    try:
        parsed = json.loads(raw)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    except json.JSONDecodeError as exc:
        print(f"!!! invalid JSON: {exc}")


def main() -> None:
    print(f"model: {MODEL}")

    img = SCREENSHOTS_DIR / "IMG_4117.PNG"
    raw, usage = ask_vision(img, WAREHOUSE_NOTIFICATION_PROMPT)
    report(f"{img.name} — warehouse arrival notifications", raw, usage)

    img = SCREENSHOTS_DIR / "IMG_4118.PNG"
    raw, usage = ask_vision(img, ORDER_DETAIL_PROMPT.format(platform="jd"))
    report(f"{img.name} — order detail (jd)", raw, usage)


if __name__ == "__main__":
    main()
