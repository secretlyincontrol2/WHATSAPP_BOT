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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("whatsapp_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("whatsapp-qwen-bot")

load_dotenv()
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
QWEN_MODEL = os.getenv("QWEN_MODEL", "Qwen/Qwen1.5-72B-Chat") 
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "30"))  
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))  

class WhatsAppQwenBot:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.is_authenticated = False
        self.processed_messages = set()
        self.start_time = datetime.now()
        self.message_history = {}  
        self.rate_limit = {}  
        
    @asynccontextmanager
    async def browser_context(self):
        """Context manager for browser sessions with proper cleanup"""
        playwright = None
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(
                headless=False,  
                args=['--disable-notifications']
            )
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            )
            self.context.set_default_navigation_timeout(60000)
            self.context.set_default_timeout(30000)
            self.page = await self.context.new_page()
            
            yield
            
        finally:
            logger.info("Cleaning up browser resources...")
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if playwright:
                await playwright.stop()
    
    async def wait_for_login(self, timeout=300000):
        """Wait for WhatsApp Web login with multiple selector options"""
        try:
            selectors = [
                "div[data-testid='chat-list']",
                "div[data-testid='chatlist-panel']",
                "#side div[role='grid']",
                "#pane-side",
                "div[role='navigation']",
                "div[role='application'] div[role='grid']",
                "div[data-testid='conversation-panel-messages']"
            ]
            
            start_time = time.time()
            end_time = start_time + (timeout / 1000)  
            
            while time.time() < end_time:
                logger.info("Checking for successful login...")
                
                qr_code = await self.page.query_selector("div[data-testid='qrcode']")
                if qr_code:
                    logger.info("QR code is still present. Waiting for scan...")
                    await asyncio.sleep(5)
                    continue
                
                for selector in selectors:
                    try:
                        element = await self.page.query_selector(selector)
                        if element:
                            logger.info(f"Login detected via selector: {selector}")
                            return True
                    except Exception:
                        pass
                
                try:
                    chat_present = await self.page.evaluate('''() => {
                        return document.querySelectorAll('.chat').length > 0 || 
                               document.querySelectorAll('[data-testid*="cell-frame"]').length > 0 ||
                               document.querySelectorAll('[data-testid*="conversation"]').length > 0;
                    }''')
                    
                    if chat_present:
                        logger.info("Login detected via JavaScript evaluation")
                        return True
                except Exception:
                    pass
                
                await asyncio.sleep(3)
            
            logger.error("Timeout waiting for login")
            return False
            
        except Exception as e:
            logger.error(f"Error while waiting for login: {str(e)}")
            logger.error(traceback.format_exc())
            return False
    
    async def initialize(self):
        """Initialize the browser and navigate to WhatsApp Web with retry logic"""
        for attempt in range(1, 4): 
            try:
                logger.info(f"Initialization attempt {attempt}...")
                async with self.browser_context():
                    await self.page.goto("https://web.whatsapp.com/")
                    logger.info("Waiting for WhatsApp QR code scan...")
                    
                    login_success = await self.wait_for_login(timeout=300000) 
                    
                    if login_success:
                        self.is_authenticated = True
                        logger.info("WhatsApp authentication successful!")
                        
                        await asyncio.sleep(3)
                        
                        await self.listen_for_messages()
                        
                        return True
                    else:
                        logger.error("Failed to detect successful login")
                        if attempt < 3:
                            logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                            await asyncio.sleep(RETRY_DELAY)
            except PlaywrightTimeoutError:
                logger.error(f"Timeout during initialization (attempt {attempt})")
                if attempt < 3:
                    logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                    await asyncio.sleep(RETRY_DELAY)
            except Exception as e:
                logger.error(f"Initialization error (attempt {attempt}): {str(e)}")
                logger.error(traceback.format_exc())
                if attempt < 3:
                    logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                    await asyncio.sleep(RETRY_DELAY)
        
        logger.critical("Failed to initialize after multiple attempts")
        return False

    async def listen_for_messages(self):
        """Listen for incoming new messages and respond using Qwen"""
        if not self.is_authenticated:
            logger.error("Not authenticated to WhatsApp Web")
            return
            
        logger.info("Listening for new messages...")
        logger.info("Bot is now active and will respond to new incoming messages only")
        
        try:
            try:
                await self.page.wait_for_selector("div[role='application']", timeout=10000)
                logger.info("WhatsApp interface is fully loaded")
            except PlaywrightTimeoutError:
                logger.warning("Could not detect WhatsApp interface elements, but continuing anyway")
            
            await self.page.evaluate('''() => {
                window.lastMessageTimestamp = Date.now();
                window.newMessageArrived = false;
                
                // Function to find the message container regardless of its structure
                function findMessageContainer() {
                    // Try various selectors that might contain messages
                    const selectors = [
                        'div[role="application"] div[role="region"]',
                        'div[data-testid="conversation-panel-messages"]',
                        '#main div[role="region"]',
                        'div[data-testid="msg-container"]',
                        'div.message-in',
                        '#main'
                    ];
                    
                    for (const selector of selectors) {
                        const element = document.querySelector(selector);
                        if (element) return element;
                    }
                    
                    // Fallback: look for any element that might contain messages
                    return document.querySelector('#app') || document.body;
                }
                
                // Set up the observer on the best container we can find
                const messageContainer = findMessageContainer();
                console.log("Setting up observer on", messageContainer);
                
                if (messageContainer) {
                    const observer = new MutationObserver((mutations) => {
                        for (const mutation of mutations) {
                            if (mutation.addedNodes.length) {
                                window.lastMessageTimestamp = Date.now();
                                window.newMessageArrived = true;
                                console.log("New message detected");
                            }
                        }
                    });
                    
                    observer.observe(messageContainer, {
                        childList: true,
                        subtree: true
                    });
                    
                    console.log("Observer setup complete");
                }
            }''')
            
            logger.info("Message observer set up successfully")
            
            while True:
                try:
                    new_message_arrived = await self.page.evaluate('''() => {
                        const result = window.newMessageArrived;
                        window.newMessageArrived = false;  // Reset the flag
                        return result;
                    }''')
                    
                    if new_message_arrived:
                        logger.info("New message detected, processing...")
                        await self.check_for_new_messages()
                    
                    unread_chats = await self.page.query_selector_all("div[data-testid='cell-frame-container'] span[data-testid='icon-unread']")
                    
                    for chat in unread_chats:
                        logger.info("Found unread chat, checking if it's new...")
                        await chat.click()
                        await asyncio.sleep(1)
                        await self.check_for_new_messages()
                    
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error in message loop: {str(e)}")
                    logger.error(traceback.format_exc())
                    await asyncio.sleep(5)  
                    
        except Exception as e:
            logger.error(f"Fatal error in message listener: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
    async def check_for_new_messages(self):
        """Check for and process new messages in the current chat"""
        try:
            chat_name = "Unknown Chat" 
        
            selectors = [
            "div[data-testid='conversation-header'] span[data-testid='conversation-info-header-chat-title']",
            "#main header span[title]",
            "header span[dir='auto']",
            "header span.emoji-texttt",
            "div.chat-title",
            "div[data-testid='chat-subtitle'] span"
            ]
        
            for selector in selectors:
                chat_name_element = await self.page.query_selector(selector)
                if chat_name_element:
                    try:
                        chat_name = await chat_name_element.inner_text()
                        if chat_name:
                            logger.info(f"Found chat name: {chat_name} using selector: {selector}")
                            break
                    except Exception as e:
                        logger.debug(f"Error getting text from selector {selector}: {str(e)}")
                        continue
        
            if chat_name == "Unknown Chat":
                try:
                    chat_name = await self.page.evaluate('''() => {
                        // Look for any element in the header that might contain the chat name
                        const headerElements = document.querySelectorAll('header span');
                        for (const el of headerElements) {
                            if (el.textContent && 
                                !el.textContent.includes('+') && 
                                el.textContent.length > 1 && 
                                el.textContent.length < 50) {
                                return el.textContent.trim();
                            }
                    }
                        return "Unknown Chat";
                    }''')
                    logger.info(f"Found chat name via JavaScript: {chat_name}")
                except Exception as e:
                    logger.warning(f"JavaScript chat name extraction failed: {str(e)}")
                
            messages = []
            message_selectors = [
                "div[data-testid='msg-container']",
                "div.message-in",
                ".message",
                "div[role='row']"
            ]
            
            for selector in message_selectors:
                messages = await self.page.query_selector_all(selector)
                if messages and len(messages) > 0:
                    logger.info(f"Found {len(messages)} messages using selector: {selector}")
                    break
                    
            if not messages or len(messages) == 0:
                logger.warning("No messages found in current chat")
                return
            
            latest_message = messages[-1]
            
            is_outgoing = False
            
            outgoing_msg = await latest_message.query_selector("div[data-testid='msg-outgoing']")
            if outgoing_msg:
                is_outgoing = True
            else:
                is_outgoing = await latest_message.evaluate("""(el) => {
                    return el.classList.contains('message-out') || 
                           el.querySelector('.message-out') !== null ||
                           el.getAttribute('data-testid')?.includes('outgoing') ||
                           el.getAttribute('class')?.includes('outgoing') ||
                           el.getAttribute('class')?.includes('out');
                }""")
            
            if is_outgoing:
                logger.info("Skipping outgoing message")
                return  
            
            msg_id = await latest_message.get_attribute("data-id")
            if not msg_id:
                data_attrs = await latest_message.evaluate("""(el) => {
                    const attrs = {};
                    for (const attr of el.attributes) {
                        if (attr.name.startsWith('data-')) {
                            attrs[attr.name] = attr.value;
                        }
                    }
                    return attrs;
                }""")
                
                if data_attrs and len(data_attrs) > 0:
                    msg_id = json.dumps(data_attrs)
                else:
                    message_text_element = await latest_message.query_selector("span[data-testid='conversation-panel-message']")
                    if not message_text_element:
                        message_text_element = await latest_message.query_selector(".selectable-text")
                    
                    if message_text_element:
                        message_text = await message_text_element.inner_text()
                        msg_id = f"{chat_name}-{message_text}-{len(messages)}"
                    else:
                        msg_id = f"{chat_name}-{datetime.now().timestamp()}"
            
            if msg_id in self.processed_messages:
                logger.info(f"Skipping already processed message with ID: {msg_id[:30]}...")
                return
            
            timestamp_attr = await latest_message.get_attribute("data-pre-plain-text")
            current_time = datetime.now()
            message_time = current_time 
            
            if timestamp_attr:
                try:
                    time_str = timestamp_attr.split("[")[1].split(",")[0].strip()
                    hours, minutes = map(int, time_str.split(":"))
                    
                    message_time = current_time.replace(hour=hours, minute=minutes)
                    
                    if message_time > current_time:
                        message_time = message_time - timedelta(days=1)
                    
                    if message_time < self.start_time:
                        logger.info(f"Skipping older message from {time_str}")
                        self.processed_messages.add(msg_id)
                        return
                except Exception as e:
                    logger.warning(f"Could not parse timestamp: {str(e)}")
            
            message_text = ""
            message_selectors = [
                "span[data-testid='conversation-panel-message']",
                ".selectable-text",
                "span.quoted-mention",
                "div.text-message"
            ]
            
            for selector in message_selectors:
                message_text_element = await latest_message.query_selector(selector)
                if message_text_element:
                    try:
                        message_text = await message_text_element.inner_text()
                        if message_text:
                            break
                    except Exception:
                        continue
            
            if not message_text:
                try:
                    message_text = await latest_message.evaluate("""(el) => {
                        // Try to find any text content in the message
                        const textNodes = Array.from(el.querySelectorAll('*'))
                            .filter(node => node.textContent && node.textContent.trim().length > 0);
                        
                        if (textNodes.length > 0) {
                            return textNodes[0].textContent.trim();
                        }
                        return "";
                    }""")
                except Exception as e:
                    logger.warning(f"JavaScript message extraction failed: {str(e)}")
            
            if not message_text:
                logger.info("Message appears to be media or contains no text")
                self.processed_messages.add(msg_id)
                return
            
            self.processed_messages.add(msg_id)
            logger.info(f"Processing new message from {chat_name}: {message_text}")
            
            if chat_name in self.rate_limit:
                last_time, count = self.rate_limit[chat_name]
                if (current_time - last_time).total_seconds() < 60: 
                    if count >= 10:  
                        logger.info(f"Rate limiting chat with {chat_name}")
                        return
                    self.rate_limit[chat_name] = (last_time, count + 1)
                else:
                    self.rate_limit[chat_name] = (current_time, 1)
            else:
                self.rate_limit[chat_name] = (current_time, 1)
            
            if chat_name not in self.message_history:
                self.message_history[chat_name] = []
            
            if len(self.message_history[chat_name]) >= 10:
                self.message_history[chat_name].pop(0)
            
            self.message_history[chat_name].append({"role": "user", "content": message_text})
            
            response = await self.generate_qwen_response(message_text, chat_name)
            
            self.message_history[chat_name].append({"role": "assistant", "content": response})
            
            await self.send_message(response)
            logger.info(f"Sent response to {chat_name}: {response}")
            
        except Exception as e:
            logger.error(f"Error processing new message: {str(e)}")
            logger.error(traceback.format_exc())
    
    async def generate_qwen_response(self, message, chat_name):
        """Generate a response using Qwen model from Together AI with retry logic"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                headers = {
                    "Authorization": f"Bearer {TOGETHER_API_KEY}",
                    "Content-Type": "application/json"
                }
                
                messages = [
                    {"role": "system", "content": "You are a helpful assistant responding to WhatsApp messages. Keep responses concise and helpful."}
                ]
                
                if chat_name in self.message_history:
                    history = self.message_history[chat_name][-4:]
                    messages.extend(history)
                else:
                    messages.append({"role": "user", "content": message})
                
                payload = {
                    "model": QWEN_MODEL,
                    "max_tokens": 1024,
                    "temperature": 0.7,
                    "messages": messages
                }
                
                response = requests.post(
                    "https://api.together.xyz/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=30  
                )
                
                if response.status_code == 200:
                    response_json = response.json()
                    return response_json["choices"][0]["message"]["content"]
                elif response.status_code == 429: 
                    logger.warning(f"Rate limited by Together AI (attempt {attempt})")
                    await asyncio.sleep(RETRY_DELAY * attempt)  
                elif response.status_code >= 500:  
                    logger.error(f"Together AI server error: {response.status_code} (attempt {attempt})")
                    await asyncio.sleep(RETRY_DELAY * attempt)
                else:
                    logger.error(f"Error from Together AI API ({response.status_code}): {response.text}")
                    return "Sorry, I couldn't process your request at the moment."
                    
            except requests.exceptions.Timeout:
                logger.error(f"Timeout connecting to Together AI (attempt {attempt})")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error to Together AI: {str(e)} (attempt {attempt})")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
            except Exception as e:
                logger.error(f"Error generating response: {str(e)}")
                return "Sorry, I'm having trouble connecting to my AI service."
        
        return "I'm sorry, but I'm experiencing technical difficulties right now. Please try again later."
    
    async def send_message(self, message):
        """Send a message in the currently open chat with improved input detection"""
        for attempt in range(1, 4): 
            try:
                await asyncio.sleep(1)
                
                
                input_selectors = [
                    "div[data-testid='conversation-compose-box-input']", 
                    "footer div[contenteditable='true']",                 
                    "#main footer div[contenteditable='true']",           
                    "div.copyable-text.selectable-text[contenteditable='true'][data-tab='10']",  
                    "div[role='textbox']",                               
                    "div[title='Type a message']",                      
                    "#main div[contenteditable='true']"                
                ]
                
                input_box = None
                used_selector = None
                
                for selector in input_selectors:
                    try:
                        element = await self.page.query_selector(selector)
                        if element:
                            is_search = await element.evaluate("""(el) => {
                                // Check if this element is in or near a search area
                                const parent = el.closest('div[role="search"]') || el.closest('div[data-testid*="search"]');
                                return !!parent;
                            }""")
                            
                            if not is_search:
                                input_box = element
                                used_selector = selector
                                logger.info(f"Found chat input using selector: {selector}")
                                break
                    except Exception as e:
                        logger.debug(f"Error with selector {selector}: {str(e)}")
                        continue
                
                if not input_box:
                    logger.info("Standard selectors failed, trying JavaScript fallback...")
                    try:
                        input_box_data = await self.page.evaluate("""() => {
                            // Find the main chat area first
                            const mainChat = document.querySelector('#main') || 
                                             document.querySelector('div[data-testid="conversation-panel"]');
                            
                            if (!mainChat) return null;
                            
                            // Look for input area in the footer
                            const footer = mainChat.querySelector('footer');
                            if (!footer) return null;
                            
                            // Find any contenteditable div in the footer
                            const input = footer.querySelector('div[contenteditable="true"]');
                            if (!input) return null;
                            
                            // Get its position for verification
                            const rect = input.getBoundingClientRect();
                            
                            return {
                                found: true,
                                bottom: rect.bottom,
                                right: rect.right
                            };
                        }""")
                        
                        if input_box_data and input_box_data.get('found'):
                            await self.page.mouse.click(
                                input_box_data['right'] - 100, 
                                input_box_data['bottom'] - 10   
                            )
                            logger.info("Used JavaScript fallback to find and click input box")
                        else:
                            raise Exception("Could not find input box with JavaScript either")
                    except Exception as e:
                        logger.error(f"JavaScript fallback failed: {str(e)}")
                        raise Exception("Could not find message input box")
                else:
                    await input_box.click()
                    
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Backspace")
                
                chunk_size = 100
                for i in range(0, len(message), chunk_size):
                    chunk = message[i:i+chunk_size]
                    await self.page.keyboard.type(chunk)
                    await asyncio.sleep(0.2) 
                
                send_button = await self.page.query_selector("button[data-testid='send']")
                
                if send_button:
                    logger.info("Found send button, clicking it")
                    await send_button.click()
                else:
                    logger.info("Send button not found, pressing Enter")
                    await self.page.keyboard.press("Enter")
                
                await asyncio.sleep(1)
                logger.info("Message sent successfully")
                return True
                    
            except Exception as e:
                logger.error(f"Error sending message (attempt {attempt}): {str(e)}")
                logger.error(traceback.format_exc())
                if attempt < 3:
                    logger.info(f"Retrying in {attempt * 2} seconds...")
                    await asyncio.sleep(attempt * 2)  
        
        logger.error("Failed to send message after multiple attempts")
        return False
    
    async def reconnect(self):
        """Attempt to reconnect if the connection is lost"""
        logger.info("Attempting to reconnect...")
        self.is_authenticated = False
        
        if self.browser:
            await self.browser.close()
            self.browser = None
        
        return await self.initialize()

async def main():
    """Main function with error recovery"""
    bot = WhatsAppQwenBot()
    
    if sys.platform != 'win32' and hasattr(asyncio, 'add_signal_handler'):
        import signal
        loop = asyncio.get_running_loop()
        for signal_name in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(
                getattr(signal, signal_name),
                lambda: asyncio.create_task(shutdown(bot, loop))
            )
    
    retry_count = 0
    while retry_count < 3:  
        try:
            success = await bot.initialize()
            if not success:
                logger.error("Bot initialization failed")
                retry_count += 1
                await asyncio.sleep(60)  
                continue
                
            logger.warning("Bot exited main loop unexpectedly. Restarting...")
            retry_count += 1
            await asyncio.sleep(10)
            
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt. Shutting down...")
            break
        except Exception as e:
            logger.critical(f"Critical bot error: {str(e)}")
            logger.critical(traceback.format_exc())
            retry_count += 1
            await asyncio.sleep(60) 

    logger.info("Bot is shutting down")

async def shutdown(bot, loop):
    """Handle graceful shutdown"""
    logger.info("Shutdown signal received")
    if bot and bot.browser:
        await bot.browser.close()
    loop.stop()

if __name__ == "__main__":
    try:
        if sys.platform != 'win32':
            import signal
            signal.signal(signal.SIGINT, lambda s, f: None)
            
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot terminated by user")
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        traceback.print_exc()
