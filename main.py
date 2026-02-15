from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from pathlib import Path
import openai
try:
    # New OpenAI python client (v1+)
    from openai import OpenAI as OpenAIClient
except Exception:
    OpenAIClient = None
from fastapi.middleware.cors import CORSMiddleware
import random
import time
import httpx

# Load .env located next to this file (backend/.env), fallback to cwd
dot_env_path = Path(__file__).resolve().parent / '.env'
if dot_env_path.exists():
    load_dotenv(dotenv_path=dot_env_path)
else:
    load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise RuntimeError('Set OPENAI_API_KEY in environment')

# Initialize OpenAI client for v1+ if available, otherwise configure legacy `openai` module
if OpenAIClient is not None:
    openai_client = OpenAIClient(api_key=OPENAI_API_KEY)
else:
    openai.api_key = OPENAI_API_KEY

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*']
)


@app.get('/')
async def root():
    return {'status': 'ok', 'service': 'SkinPilot backend', 'endpoints': ['/chat','/send-otp','/verify-otp','/docs','/redoc']}

class ChatRequest(BaseModel):
    message: str


class OTPRequest(BaseModel):
    phone: str


class OTPVerifyRequest(BaseModel):
    phone: str
    code: str

@app.post('/chat')
async def chat(req: ChatRequest):
    try:
        # Prefer the new client interface if available
        if OpenAIClient is not None:
            resp = openai_client.chat.completions.create(
                model='gpt-3.5-turbo',
                messages=[{"role": "user", "content": req.message}],
                max_tokens=800
            )
            # response message may be a dict-like or object
            msg = resp.choices[0].message
            if isinstance(msg, dict):
                text = msg.get('content')
            else:
                text = getattr(msg, 'content', None)
        else:
            # legacy openai python client (pre-1.0)
            resp = openai.ChatCompletion.create(
                model='gpt-3.5-turbo',
                messages=[{"role":"user","content": req.message}],
                max_tokens=800
            )
            text = resp.choices[0].message.content

        return {"reply": text}
    except Exception as e:
        # Log full exception server-side, but return a safer client-facing message.
        import traceback
        traceback.print_exc()
        # If the error is from OpenAI and indicates an auth issue, map to 502 with a generic message.
        msg = str(e)
        if 'invalid_api_key' in msg or 'Incorrect API key' in msg or '401' in msg:
            raise HTTPException(status_code=502, detail='OpenAI authentication error (check server API key)')
        raise HTTPException(status_code=502, detail='OpenAI service error')


# Simple in-memory OTP store for demo purposes: { phone: (code, expiry_ts) }
otp_store = {}

TWILIO_ACCOUNT = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_FROM = os.getenv('TWILIO_FROM_NUMBER')


@app.post('/send-otp')
async def send_otp(req: OTPRequest):
    phone = req.phone
    if not phone:
        raise HTTPException(status_code=400, detail='phone required')
    code = f"{random.randint(0,999999):06d}"
    expiry = time.time() + 5*60
    otp_store[phone] = (code, expiry)

    # If Twilio credentials present, attempt to send SMS
    if TWILIO_ACCOUNT and TWILIO_TOKEN and TWILIO_FROM:
        try:
            async with httpx.AsyncClient() as client:
                url = f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT}/Messages.json'
                data = {'To': phone, 'From': TWILIO_FROM, 'Body': f'Your SkinPilot OTP is {code}'}
                resp = await client.post(url, data=data, auth=(TWILIO_ACCOUNT, TWILIO_TOKEN))
                resp.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Twilio send error: {e}')
        return {'sent': True}

    # No Twilio: return the code for testing (do not use in production)
    return {'sent': True, 'code': code}


@app.post('/verify-otp')
async def verify_otp(req: OTPVerifyRequest):
    phone = req.phone
    code = req.code
    rec = otp_store.get(phone)
    if not rec:
        raise HTTPException(status_code=400, detail='no otp for phone')
    saved_code, expiry = rec
    if time.time() > expiry:
        del otp_store[phone]
        raise HTTPException(status_code=400, detail='otp expired')
    if code != saved_code:
        raise HTTPException(status_code=400, detail='invalid code')
    # success; remove entry
    del otp_store[phone]
    return {'verified': True}
