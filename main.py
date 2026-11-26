import os
from typing import List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

load_dotenv()

TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WABA_ID = os.getenv("WHATSAPP_WABA_ID")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")

GRAPH = f"https://graph.facebook.com/{VERSION}"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

app = FastAPI(title="WhatsApp Template API")

# Demo-only in-memory stores (replace with your database later)
template_status = {}   # template_name -> {"status": ..., "reason": ...}
message_status = {}    # wa_message_id -> {"to": ..., "status": ..., "errors": ...}


class TemplateCreate(BaseModel):
    name: str                       # lowercase letters, digits, underscores: "happy_diwali_test"
    category: str = "MARKETING"     # MARKETING | UTILITY | AUTHENTICATION
    language: str = "en"
    body: str                       # must not start or end with a variable
    body_examples: List[str] = []   # one sample value per variable, in order
    footer: Optional[str] = None


class SendTemplate(BaseModel):
    to: str                              # country code + number, no + or spaces: "919876543210"
    template_name: str = "hello_world"   # Meta's pre-approved template, no approval needed
    language: str = "en_US"              # hello_world uses en_US
    variables: List[str] = []            # leave empty for hello_world





@app.get("/test")
def test():
    return {
        "status":"Working"
    }




# ---------- Your APIs ----------
@app.post("/whatsapp/templates")
async def create_template(t: TemplateCreate):
    body_component = {"type": "BODY", "text": t.body}
    if t.body_examples:
        body_component["example"] = {"body_text": [t.body_examples]}

    components = [body_component]
    if t.footer:
        components.append({"type": "FOOTER", "text": t.footer})

    payload = {
        "name": t.name,
        "category": t.category,
        "language": t.language,
        "components": components,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.post(
            f"{GRAPH}/{WABA_ID}/message_templates", json=payload, headers=HEADERS
        )

    if res.status_code >= 400:
        raise HTTPException(status_code=res.status_code, detail=res.json())

    template_status[t.name] = {"status": "PENDING", "reason": None}
    return res.json()  # id, status (usually PENDING), category


@app.get("/whatsapp/templates")
async def list_templates(name: Optional[str] = None):
    params = {"limit": 100, "fields": "id,name,status,category,language,rejected_reason"}
    if name:
        params["name"] = name

    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(
            f"{GRAPH}/{WABA_ID}/message_templates", headers=HEADERS, params=params
        )

    if res.status_code >= 400:
        raise HTTPException(status_code=res.status_code, detail=res.json())

    return res.json()


@app.post("/whatsapp/send-template")
async def send_template(msg: SendTemplate):
    template = {
        "name": msg.template_name,
        "language": {"code": msg.language},
    }
    if msg.variables:
        template["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": v} for v in msg.variables],
            }
        ]

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": msg.to,
        "type": "template",
        "template": template,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.post(f"{GRAPH}/{PHONE_ID}/messages", json=payload, headers=HEADERS)

    if res.status_code >= 400:
        raise HTTPException(status_code=res.status_code, detail=res.json())

    data = res.json()
    wa_id = data["messages"][0]["id"]
    message_status[wa_id] = {"to": msg.to, "status": "accepted", "errors": None}
    return data


@app.get("/whatsapp/template-status")
async def view_template_status():
    """Shows what the webhook has told you about templates."""
    return template_status


@app.get("/whatsapp/message-status")
async def view_message_status():
    """Shows sent / delivered / read / failed as reported by the webhook."""
    return message_status


# ---------- Webhook (called by Meta) ----------
@app.get("/whatsapp/webhook")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    # Meta's one-time handshake: the token must match your WHATSAPP_VERIFY_TOKEN
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/whatsapp/webhook")
async def receive_webhook(request: Request):
    data = await request.json()

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})

            if field == "message_template_status_update":
                name = value.get("message_template_name")
                template_status[name] = {
                    "status": value.get("event"),    # APPROVED | REJECTED | PAUSED | DISABLED
                    "reason": value.get("reason"),
                }
                print("TEMPLATE:", name, template_status[name])

            elif field == "messages":
                for st in value.get("statuses", []):
                    message_status[st["id"]] = {
                        "to": st.get("recipient_id"),
                        "status": st.get("status"),  # sent | delivered | read | failed
                        "errors": st.get("errors"),
                    }
                    print("STATUS:", st["id"], st.get("status"))
                for m in value.get("messages", []):
                    print("REPLY:", m.get("from"), m.get("text", {}).get("body"))

            else:
                print("EVENT:", field, value)

    return {"status": "ok"}  # always reply 200 quickly