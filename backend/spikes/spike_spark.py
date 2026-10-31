"""M0.1: Muse Spark returns schema-constrained extraction results."""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(base_url=os.environ["MODEL_BASE_URL"], api_key=os.environ["MODEL_API_KEY"])

TRANSCRIPT = """Dana: I'll send the revised hardware specifications to Facilities by Friday.
Sam: We could consider contacting the vendor about the rack mounts.
Priya: I sent the cabling plan to Facilities yesterday, they have everything they need."""

schema = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["commitment", "request", "decision", "progress", "completion_claim", "blocker", "suggestion"]},
                    "speaker": {"type": "string"},
                    "deliverable": {"type": "string"},
                    "deadline_text": {"type": "string"},
                    "excerpt": {"type": "string"},
                },
                "required": ["type", "speaker", "deliverable", "deadline_text", "excerpt"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}

resp = client.chat.completions.create(
    model=os.environ["REASONING_MODEL"],
    messages=[
        {"role": "system", "content": "Extract commitments, requests, decisions, progress updates, completion claims, blockers and suggestions from the meeting transcript. A suggestion is not a commitment."},
        {"role": "user", "content": TRANSCRIPT},
    ],
    response_format={"type": "json_schema", "json_schema": {"name": "Events", "strict": True, "schema": schema}},
)
print(json.dumps(json.loads(resp.choices[0].message.content), indent=2))
print("usage:", resp.usage)
