import asyncio
import os
import random
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Mock Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - MOCK - %(message)s')
logger = logging.getLogger("mock-bot")

load_dotenv()

# Configuration from env (or defaults)
TARGET_GROUP = "GDG Data and AI"
IDLE_THRESHOLD_MINUTES = 1 # Set to 1 minute for fast testing
RANDOM_REPLY_PROBABILITY = 0.5

SYSTEM_PROMPT = """You are the energetic, tech-obsessed soul of the GDG Data and AI community. 
You are NOT a helpful assistant. Be funny, use emojis, drop hot takes.
"""

class MockBot:
    def __init__(self):
        self.last_activity_time = datetime.now()
        self.message_history = []
        self.is_running = True

    async def get_ai_response(self, prompt):
        """Mocks AI response"""
        await asyncio.sleep(1) # Simulate network delay
        return f"[AI GENERATED REPLY to: '{prompt}' using persona]"

    async def proactive_loop(self):
        logger.info("Started Proactive Loop")
        while self.is_running:
            await asyncio.sleep(5) # Check every 5 seconds for test
            
            delta = datetime.now() - self.last_activity_time
            # Using 10 seconds threshold for test instead of minutes
            if delta > timedelta(seconds=30): 
                logger.info(f"Group silent for {delta.seconds}s. Sparking...")
                
                logger.info(">>> BOT: Hey everyone! Python or Mojo? Discuss!")
                self.last_activity_time = datetime.now()

    async def receive_message(self, text):
        """Simulate receiving a message from the group"""
        logger.info(f"Received in {TARGET_GROUP}: {text}")
        self.last_activity_time = datetime.now()
        self.message_history.append(text)

        # Logic from main.py
        should_reply = False
        if random.random() < RANDOM_REPLY_PROBABILITY:
            logger.info("Random reply trigger HIT!")
            should_reply = True
        else:
            logger.info("Random reply trigger MISS.")

        if "tag" in text.lower() or "@bot" in text.lower():
            logger.info("Bot was TAGGED!")
            should_reply = True

        if should_reply:
            response = await self.get_ai_response(text)
            logger.info(f">>> BOT: {response}")

    async def input_loop(self):
        print(f"\n--- MOCK BOT STARTED for group '{TARGET_GROUP}' ---")
        print("Type a message and press Enter to simulate a group message.")
        print("Don't type anything to test the IDLE SPARK logic (set to 30s).")
        print("Type 'exit' to quit.\n")
        
        while self.is_running:
            try:
                # Async input is tricky in some terminals, using thread executor for blocking input
                text = await asyncio.to_thread(input, "")
                if text.strip().lower() == 'exit':
                    self.is_running = False
                    break
                if text.strip():
                    await self.receive_message(text)
            except EOFError:
                break

    async def start(self):
        await asyncio.gather(
            self.proactive_loop(),
            self.input_loop()
        )

if __name__ == "__main__":
    bot = MockBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        pass
