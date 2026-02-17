import os
import time
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import requests
import json
import logging
import traceback
import sys
from contextlib import asynccontextmanager
from aiohttp import web
import random

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("whatsapp-gdg-bot")

load_dotenv()

# --- Configuration ---
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
QWEN_MODEL = os.getenv("QWEN_MODEL", "Qwen/Qwen1.5-72B-Chat")
TARGET_GROUP = os.getenv("TARGET_GROUP", "GDG Data and AI") 
IDLE_THRESHOLD_MINUTES = int(os.getenv("IDLE_THRESHOLD_MINUTES", "5")) # Lowered default to 5 minutes
RANDOM_REPLY_PROBABILITY = float(os.getenv("RANDOM_REPLY_PROBABILITY", "0.35")) # 35% chance to reply to ANY message
PORT = int(os.getenv("PORT", "10000"))

SYSTEM_PROMPT = """You are the energetic, tech-obsessed soul of the GDG Data and AI community. 
You are NOT a helpful assistant. You are a community member who loves Python, LLMs, and Data Science.
Your goal is to spark conversation, drop hot takes, and keep the group alive.
- Be funny, use emojis, and be slightly chaotic but moderate.
- If the group is quiet, users might be "sleeping". Wake them up with a fun fact or a controversial tech opinion (e.g., "Mojo > Python?").
- If someone tags you, reply with wit.
- Keep responses concise (under 3 sentences usually).
- Do NOT be formal. Be a "tech bro" but inclusive.
"""

class WhatsAppProactiveBot:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.is_authenticated = False
        self.processed_messages = set()
        self.start_time = datetime.now()
        self.message_history = {}
        self.last_activity_time = datetime.now()
        self.qr_code_path = "qr_code.png"
        
        # --- Stats Tracking ---
        self.stats = {
            "messages_seen": 0,
            "replies_sent": 0,
            "sparks_fired": 0,
            "random_hits": 0,
            "errors": 0,
        }
        self.activity_log = []  # Last 50 events
        
        # Web Server
        self.webapp = web.Application()
        self.webapp.router.add_get('/', self.handle_home)
        self.webapp.router.add_get('/qr', self.handle_qr)
        self.webapp.router.add_get('/check', self.handle_check)
        self.webapp.router.add_get('/health', self.handle_health)

    def log_activity(self, event):
        """Add event to activity log (keep last 50)"""
        entry = f"[{datetime.now().strftime('%H:%M:%S')}] {event}"
        self.activity_log.append(entry)
        if len(self.activity_log) > 50:
            self.activity_log.pop(0)
        logger.info(event)

    # --- Web Server Handlers ---
    async def handle_home(self, request):
        uptime = str(datetime.now() - self.start_time).split('.')[0]
        status_color = "#00ff88" if self.is_authenticated else "#ff4444"
        status_text = "CONNECTED ✅" if self.is_authenticated else "WAITING FOR QR SCAN ⏳"
        
        log_html = "".join(
            f"<div class='log-entry'>{entry}</div>" for entry in reversed(self.activity_log[-30:])
        ) or "<div class='log-entry' style='color:#666'>No activity yet...</div>"
        
        html = f"""<!DOCTYPE html>
<html><head>
<title>GDG Bot Dashboard</title>
<meta http-equiv="refresh" content="10">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0a0a0a; color:#e0e0e0; font-family:'Segoe UI',sans-serif; padding:20px; }}
  h1 {{ color:#00ff88; font-size:28px; margin-bottom:5px; }}
  .subtitle {{ color:#666; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:12px; margin:20px 0; }}
  .card {{ background:#1a1a2e; border-radius:10px; padding:16px; text-align:center; border:1px solid #2a2a4a; }}
  .card .value {{ font-size:28px; font-weight:bold; color:#00ff88; }}
  .card .label {{ font-size:12px; color:#888; margin-top:4px; }}
  .status {{ padding:8px 16px; border-radius:20px; display:inline-block; font-weight:bold; 
             background:{status_color}22; color:{status_color}; border:1px solid {status_color}; margin:10px 0; }}
  .log-box {{ background:#111; border:1px solid #333; border-radius:10px; padding:15px; 
              max-height:400px; overflow-y:auto; margin-top:15px; }}
  .log-entry {{ font-family:monospace; font-size:13px; padding:4px 0; border-bottom:1px solid #1a1a1a; color:#aaa; }}
  .actions {{ margin-top:15px; }}
  .actions a {{ color:#00ff88; text-decoration:none; margin-right:15px; padding:8px 16px; 
                border:1px solid #00ff88; border-radius:8px; font-size:14px; }}
  .actions a:hover {{ background:#00ff8822; }}
</style>
</head><body>
<h1>🤖 GDG Data & AI Bot</h1>
<p class="subtitle">WhatsApp Proactive Chatter</p>
<div class="status">{status_text}</div>

<div class="grid">
  <div class="card"><div class="value">{uptime}</div><div class="label">⏱️ Uptime</div></div>
  <div class="card"><div class="value">{self.stats['messages_seen']}</div><div class="label">👁️ Messages Seen</div></div>
  <div class="card"><div class="value">{self.stats['replies_sent']}</div><div class="label">💬 Replies Sent</div></div>
  <div class="card"><div class="value">{self.stats['sparks_fired']}</div><div class="label">🔥 Sparks Fired</div></div>
  <div class="card"><div class="value">{self.stats['random_hits']}</div><div class="label">🎲 Random Hits</div></div>
  <div class="card"><div class="value">{self.stats['errors']}</div><div class="label">❌ Errors</div></div>
</div>

<div class="actions">
  <a href="/qr">📱 View QR Code</a>
  <a href="/check">📊 JSON Stats</a>
  <a href="/health">💚 Health</a>
</div>

<h3 style="margin-top:20px; color:#888;">📜 Activity Log</h3>
<div class="log-box">{log_html}</div>

<p style="color:#333; margin-top:20px; font-size:11px;">Auto-refreshes every 10 seconds</p>
</body></html>"""
        return web.Response(text=html, content_type='text/html')

    async def handle_qr(self, request):
        if os.path.exists(self.qr_code_path):
            return web.FileResponse(self.qr_code_path)
        return web.Response(text="No QR code available yet. Check back in 10 seconds.")

    async def handle_check(self, request):
        uptime = str(datetime.now() - self.start_time).split('.')[0]
        data = {
            "status": "connected" if self.is_authenticated else "waiting_for_qr",
            "uptime": uptime,
            "target_group": TARGET_GROUP,
            "stats": self.stats,
            "last_activity": self.last_activity_time.isoformat(),
            "recent_log": self.activity_log[-20:],
        }
        return web.json_response(data)

    async def handle_health(self, request):
        return web.Response(text="OK", status=200)

    async def start_server(self):
        runner = web.AppRunner(self.webapp)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logger.info(f"Web server started on port {PORT}")

    # --- Browser Automation ---
    @asynccontextmanager
    async def browser_context(self):
        playwright = None
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    '--disable-notifications',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-gpu',
                    '--disable-dev-shm-usage',
                ]
            )
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            )
            self.context.set_default_navigation_timeout(60000)
            self.page = await self.context.new_page()
            yield
        finally:
            if self.context: await self.context.close()
            if self.browser: await self.browser.close()
            if playwright: await playwright.stop()

    async def capture_qr(self):
        try:
            # Try multiple known QR code selectors
            selectors = [
                "div[data-testid='qrcode']",
                "canvas",
                "div._akau",
                "[data-ref]",
            ]
            for selector in selectors:
                qr_element = await self.page.query_selector(selector)
                if qr_element:
                    await qr_element.screenshot(path=self.qr_code_path)
                    logger.info(f"QR Code captured using selector: {selector}")
                    return True
            
            # Fallback: take full page screenshot so user can at least see something
            await self.page.screenshot(path=self.qr_code_path)
            logger.info("QR element not found. Saved full page screenshot instead.")
            return True
        except Exception as e:
            logger.error(f"QR capture error: {e}")
            # Last resort: full page screenshot
            try:
                await self.page.screenshot(path=self.qr_code_path)
                logger.info("Saved fallback full page screenshot.")
            except:
                pass
            return False

    async def wait_for_login(self):
        logger.info("Waiting for login...")
        while not self.is_authenticated:
            try:
                if await self.page.query_selector("div[data-testid='chat-list']"):
                    self.is_authenticated = True
                    logger.info("Login Successful!")
                    return True
                
                await self.capture_qr()
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Login check error: {e}")
                await asyncio.sleep(5)
        return False

    async def initialize(self):
        await self.start_server()
        async with self.browser_context():
            # Retry navigation with backoff (DNS can be slow in containers)
            max_retries = 5
            for attempt in range(1, max_retries + 1):
                try:
                    logger.info(f"Navigating to WhatsApp Web (attempt {attempt}/{max_retries})...")
                    await self.page.goto("https://web.whatsapp.com/", timeout=60000)
                    logger.info("Navigation successful!")
                    break
                except Exception as e:
                    logger.error(f"Navigation failed (attempt {attempt}): {e}")
                    if attempt == max_retries:
                        logger.error("All navigation attempts failed. Exiting.")
                        return
                    wait_time = 10 * attempt  # 10s, 20s, 30s, 40s
                    logger.info(f"Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)

            if await self.wait_for_login():
                # Start combined loop
                await asyncio.gather(
                    self.message_loop(),
                    self.proactive_loop(),
                    self.keep_alive_loop()
                )

    # --- AI Logic ---
    async def get_ai_response(self, prompt, context=[]):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + context
        messages.append({"role": "user", "content": prompt})
        
        try:
            response = requests.post(
                "https://api.together.xyz/v1/chat/completions",
                headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
                json={
                    "model": QWEN_MODEL,
                    "max_tokens": 1024,
                    "temperature": 0.85,
                    "messages": messages
                },
                timeout=30
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
            else:
                logger.error(f"Together AI Error: {response.text}")
                return None
        except Exception as e:
            logger.error(f"AI/Network Error: {e}")
            return None

    # --- Message Handling ---
    async def message_loop(self):
        self.log_activity("Message listener started")
        
        # Inject observer
        await self.page.evaluate('''() => {
            window.lastMessageTimestamp = Date.now();
            window.newMessageArrived = false;
            const observer = new MutationObserver((mutations) => {
                window.newMessageArrived = true;
            });
            const app = document.querySelector('#app') || document.body;
            observer.observe(app, { childList: true, subtree: true });
        }''')

        while True:
            try:
                # Check for new messages
                new_msg = await self.page.evaluate('window.newMessageArrived')
                if new_msg:
                    await self.page.evaluate('window.newMessageArrived = false;')
                    await self.check_active_chat()
                
                # Check unread badges
                unread = await self.page.query_selector_all("span[data-testid='icon-unread']")
                for chat in unread:
                    await chat.click()
                    await asyncio.sleep(1) # Wait for load
                    await self.check_active_chat()
                
                await asyncio.sleep(2)
            except Exception as e:
                self.stats["errors"] += 1
                self.log_activity(f"❌ Loop error: {e}")
                await asyncio.sleep(5)

    async def check_active_chat(self):
        """Analyze the currently open chat"""
        try:
            # Get Chat Name
            chat_name = await self.get_chat_name()
            if not chat_name: return

            # Only care about TARGET_GROUP
            is_target_group = TARGET_GROUP.lower() in chat_name.lower()
            
            if is_target_group:
                self.last_activity_time = datetime.now()

            # Get Messages
            messages = await self.page.query_selector_all("div.message-in")
            if not messages: return

            last_msg = messages[-1]
            msg_text = await last_msg.inner_text()
            
            # Simple ID based on text + time roughly
            msg_id = f"{chat_name}-{len(messages)}-{msg_text[:10]}"
            
            if msg_id in self.processed_messages:
                return
            
            self.processed_messages.add(msg_id)
            self.stats["messages_seen"] += 1
            self.log_activity(f"👁️ Message in {chat_name}: {msg_text[:50]}")

            # Respond Logic
            if is_target_group:
                # Update History
                if chat_name not in self.message_history: self.message_history[chat_name] = []
                self.message_history[chat_name].append({"role": "user", "content": msg_text})
                if len(self.message_history[chat_name]) > 5: self.message_history[chat_name].pop(0)

                # DECIDE IF WE SHOULD REPLY
                should_reply = False
                
                # Random Chance ("Constantly sending messages" mode)
                if random.random() < RANDOM_REPLY_PROBABILITY:
                    self.stats["random_hits"] += 1
                    self.log_activity("🎲 Random reply trigger HIT!")
                    should_reply = True
                
                if should_reply:
                    reply = await self.get_ai_response(msg_text, self.message_history[chat_name])
                    if reply:
                        await self.send_text(reply)
                        self.stats["replies_sent"] += 1
                        self.log_activity(f"💬 Replied: {reply[:60]}")
                        self.message_history[chat_name].append({"role": "assistant", "content": reply})

        except Exception as e:
            self.stats["errors"] += 1
            self.log_activity(f"❌ Chat error: {e}")

    async def get_chat_name(self):
        try:
            el = await self.page.query_selector("header span[dir='auto']")
            return await el.inner_text() if el else None
        except:
            return None

    async def send_text(self, text):
        try:
            inp = await self.page.query_selector("footer div[contenteditable='true']")
            if inp:
                await inp.click()
                await self.page.keyboard.type(text)
                await self.page.keyboard.press("Enter")
                return True
        except Exception:
            return False

    async def proactive_loop(self):
        self.log_activity("Proactive Engine started")
        while True:
            await asyncio.sleep(60)
            
            delta = datetime.now() - self.last_activity_time
            if delta > timedelta(minutes=IDLE_THRESHOLD_MINUTES):
                self.log_activity(f"🔥 Group silent for {IDLE_THRESHOLD_MINUTES}min. Sparking...")
                
                prompt = "The group is silent. Generate a one-sentence fun/controversial tech question to wake them up."
                spark = await self.get_ai_response(prompt)
                
                if spark:
                    await self.navigate_to_group(TARGET_GROUP)
                    await self.send_text(spark)
                    self.stats["sparks_fired"] += 1
                    self.log_activity(f"🔥 Spark sent: {spark[:60]}")
                    self.last_activity_time = datetime.now()

    async def keep_alive_loop(self):
        self.log_activity("Keep-Alive Loop started")
        while True:
            await asyncio.sleep(300) # 5 minutes
            try:
                self.log_activity(f"💚 HEARTBEAT: Bot alive | Msgs: {self.stats['messages_seen']} | Replies: {self.stats['replies_sent']}")
            except Exception:
                pass

    async def navigate_to_group(self, group_name):
        try:
            search = await self.page.query_selector("div[contenteditable='true'][data-tab='3']")
            if search:
                await search.click()
                await self.page.keyboard.type(group_name)
                await asyncio.sleep(2)
                await self.page.keyboard.press("Enter")
        except:
            pass

if __name__ == "__main__":
    bot = WhatsAppProactiveBot()
    try:
        asyncio.run(bot.initialize())
    except KeyboardInterrupt:
        pass
