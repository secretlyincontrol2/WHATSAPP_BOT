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
        
        # Web Server
        self.webapp = web.Application()
        self.webapp.router.add_get('/', self.handle_home)
        self.webapp.router.add_get('/qr', self.handle_qr)
        self.webapp.router.add_get('/health', self.handle_health)

    # --- Web Server Handlers ---
    async def handle_home(self, request):
        return web.Response(text="GDG Data & AI Bot Active. Go to /qr to scan login code.")

    async def handle_qr(self, request):
        if os.path.exists(self.qr_code_path):
            return web.FileResponse(self.qr_code_path)
        return web.Response(text="No QR code available yet. Check back in 10 seconds.")

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
        logger.info("Starting message listener...")
        
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
                logger.error(f"Loop error: {e}")
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
            logger.info(f"New message in {chat_name}: {msg_text}")

            # Respond Logic
            if is_target_group:
                # Update History
                if chat_name not in self.message_history: self.message_history[chat_name] = []
                self.message_history[chat_name].append({"role": "user", "content": msg_text})
                if len(self.message_history[chat_name]) > 5: self.message_history[chat_name].pop(0)

                # DECIDE IF WE SHOULD REPLY
                should_reply = False
                
                # 1. Reply if active tag (mentions us) - Hard to detect without parsing text for @BotName, 
                # but we can assume if they are talking we might want to chip in.
                
                # 2. Random Chance ("Constantly sending messages" mode)
                if random.random() < RANDOM_REPLY_PROBABILITY:
                    logger.info("Randomly decided to interject!")
                    should_reply = True
                
                if should_reply:
                    reply = await self.get_ai_response(msg_text, self.message_history[chat_name])
                    if reply:
                        await self.send_text(reply)
                        self.message_history[chat_name].append({"role": "assistant", "content": reply})

        except Exception as e:
            logger.error(f"Error checking chat: {e}")

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
        logger.info("Starting Proactive Engine...")
        while True:
            await asyncio.sleep(60)
            
            delta = datetime.now() - self.last_activity_time
            if delta > timedelta(minutes=IDLE_THRESHOLD_MINUTES):
                logger.info("Group silent. Sparking...")
                
                prompt = "The group is silent. Generate a one-sentence fun/controversial tech question to wake them up."
                spark = await self.get_ai_response(prompt)
                
                if spark:
                    # Navigate to group first to be safe
                    await self.navigate_to_group(TARGET_GROUP)
                    await self.send_text(spark)
                    self.last_activity_time = datetime.now() # Reset

    async def keep_alive_loop(self):
        """Pings the internal web server to keep the event loop moving/logging"""
        logger.info("Starting Keep-Alive Loop...")
        while True:
            await asyncio.sleep(300) # 5 minutes
            try:
                # We can't ping localhost inside container easily if we didn't expose it to self,
                # but we can just log a heartbeat
                logger.info(f"HEARTBEAT: Bot active at {datetime.now()}")
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
