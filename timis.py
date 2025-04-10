# WhatsApp Bot powered by Qwen from Together AI
# This script uses the WhatsApp Web API through playwright and integrates with Together AI's Qwen model

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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("whatsapp_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("whatsapp-qwen-bot")

# Load environment variables
load_dotenv()
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
QWEN_MODEL = os.getenv("QWEN_MODEL", "Qwen/Qwen1.5-72B-Chat")  # Default to 72B model
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "30"))  # Retry delay in seconds
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))  # Max retries for API calls

class WhatsAppQwenBot:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.is_authenticated = False
        self.processed_messages = set()
        self.start_time = datetime.now()
        self.message_history = {}  # Store message history by chat
        self.rate_limit = {}  # Rate limiting by chat
        
    @asynccontextmanager
    async def browser_context(self):
        """Context manager for browser sessions with proper cleanup"""
        playwright = None
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(
                headless=False,  # Set to True for production
                args=['--disable-notifications']
            )
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            )
            # Enable navigation timeout of 60 seconds
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
            # Try multiple possible selectors that indicate successful login
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
            end_time = start_time + (timeout / 1000)  # Convert ms to seconds
            
            while time.time() < end_time:
                logger.info("Checking for successful login...")
                
                # Check for QR code - if it's present, we're not logged in yet
                qr_code = await self.page.query_selector("div[data-testid='qrcode']")
                if qr_code:
                    logger.info("QR code is still present. Waiting for scan...")
                    await asyncio.sleep(5)
                    continue
                
                # Try each selector to see if we're logged in
                for selector in selectors:
                    try:
                        element = await self.page.query_selector(selector)
                        if element:
                            logger.info(f"Login detected via selector: {selector}")
                            return True
                    except Exception:
                        pass
                
                # Also check for presence of chat or message elements
                try:
                    # Check if any chat is visible
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
                
                # Wait before checking again
                await asyncio.sleep(3)
            
            # Timeout reached
            logger.error("Timeout waiting for login")
            return False
            
        except Exception as e:
            logger.error(f"Error while waiting for login: {str(e)}")
            logger.error(traceback.format_exc())
            return False
    
    async def initialize(self):
        """Initialize the browser and navigate to WhatsApp Web with retry logic"""
        for attempt in range(1, 4):  # Try 3 times
            try:
                logger.info(f"Initialization attempt {attempt}...")
                async with self.browser_context():
                    # Navigate to WhatsApp Web
                    await self.page.goto("https://web.whatsapp.com/")
                    logger.info("Waiting for WhatsApp QR code scan...")
                    
                    # Wait for login with improved detection
                    login_success = await self.wait_for_login(timeout=300000)  # 5 minute timeout
                    
                    if login_success:
                        self.is_authenticated = True
                        logger.info("WhatsApp authentication successful!")
                        
                        # Give a moment for everything to load
                        await asyncio.sleep(3)
                        
                        # Start the message listener
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
            # First check if we're fully logged in by looking for a specific element
            try:
                await self.page.wait_for_selector("div[role='application']", timeout=10000)
                logger.info("WhatsApp interface is fully loaded")
            except PlaywrightTimeoutError:
                logger.warning("Could not detect WhatsApp interface elements, but continuing anyway")
            
            # Set up a mutation observer for new messages using JavaScript
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
            
            # Log that we're ready
            logger.info("Message observer set up successfully")
            
            while True:
                try:
                    # Check if we have a new message via JavaScript
                    new_message_arrived = await self.page.evaluate('''() => {
                        const result = window.newMessageArrived;
                        window.newMessageArrived = false;  // Reset the flag
                        return result;
                    }''')
                    
                    if new_message_arrived:
                        logger.info("New message detected, processing...")
                        await self.check_for_new_messages()
                    
                    # Regular check for unread chats as a backup method
                    unread_chats = await self.page.query_selector_all("div[data-testid='cell-frame-container'] span[data-testid='icon-unread']")
                    
                    for chat in unread_chats:
                        logger.info("Found unread chat, checking if it's new...")
                        await chat.click()
                        await asyncio.sleep(1)
                        await self.check_for_new_messages()
                    
                    # Wait a bit before checking again
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"Error in message loop: {str(e)}")
                    logger.error(traceback.format_exc())
                    await asyncio.sleep(5)  # Back off on errors
                    
        except Exception as e:
            logger.error(f"Fatal error in message listener: {str(e)}")
            logger.error(traceback.format_exc())
            raise
    
    async def check_for_new_messages(self):
        """Check for and process new messages in the current chat"""
        try:
            # Get the current chat name with expanded selector options
            chat_name = "Unknown Chat"  # Default value
        
        # Try multiple selectors for chat name
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
                # Alternative method: use JavaScript to find any element that might contain the chat name
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
                
            # Get message containers with expanded selector options
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
            
            # Process only the last message (most recent)
            latest_message = messages[-1]
            
            # Check if this message is incoming (not sent by us) with improved detection
            is_outgoing = False
            
            # Try multiple methods to detect outgoing messages
            outgoing_msg = await latest_message.query_selector("div[data-testid='msg-outgoing']")
            if outgoing_msg:
                is_outgoing = True
            else:
                # Use JavaScript for more reliable detection
                is_outgoing = await latest_message.evaluate("""(el) => {
                    return el.classList.contains('message-out') || 
                           el.querySelector('.message-out') !== null ||
                           el.getAttribute('data-testid')?.includes('outgoing') ||
                           el.getAttribute('class')?.includes('outgoing') ||
                           el.getAttribute('class')?.includes('out');
                }""")
            
            if is_outgoing:
                logger.info("Skipping outgoing message")
                return  # Skip our own messages
            
            # Extract message ID with fallback mechanisms
            msg_id = await latest_message.get_attribute("data-id")
            if not msg_id:
                # Try to extract message data attributes that might be unique
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
                    # Combine data attributes to create a pseudo ID
                    msg_id = json.dumps(data_attrs)
                else:
                    # Generate a pseudo ID from the message content and position
                    message_text_element = await latest_message.query_selector("span[data-testid='conversation-panel-message']")
                    if not message_text_element:
                        message_text_element = await latest_message.query_selector(".selectable-text")
                    
                    if message_text_element:
                        message_text = await message_text_element.inner_text()
                        msg_id = f"{chat_name}-{message_text}-{len(messages)}"
                    else:
                        # Last resort
                        msg_id = f"{chat_name}-{datetime.now().timestamp()}"
            
            if msg_id in self.processed_messages:
                logger.info(f"Skipping already processed message with ID: {msg_id[:30]}...")
                return
            
            # Get message timestamp if available
            timestamp_attr = await latest_message.get_attribute("data-pre-plain-text")
            current_time = datetime.now()
            message_time = current_time  # Default to current time
            
            if timestamp_attr:
                # Extract time from format like "[10:42, 3/18/2025]"
                try:
                    time_str = timestamp_attr.split("[")[1].split(",")[0].strip()
                    # Extract hours and minutes
                    hours, minutes = map(int, time_str.split(":"))
                    
                    # Create a datetime object for the message time
                    message_time = current_time.replace(hour=hours, minute=minutes)
                    
                    # If the calculated time is in the future, it's probably from yesterday
                    if message_time > current_time:
                        message_time = message_time - timedelta(days=1)
                    
                    # Check if message is older than when the bot started
                    if message_time < self.start_time:
                        logger.info(f"Skipping older message from {time_str}")
                        self.processed_messages.add(msg_id)
                        return
                except Exception as e:
                    logger.warning(f"Could not parse timestamp: {str(e)}")
            
            # Extract message text with multiple fallback methods
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
            
            # If still no text, try JavaScript as last resort
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
                # May be media message
                logger.info("Message appears to be media or contains no text")
                self.processed_messages.add(msg_id)
                return
            
            self.processed_messages.add(msg_id)
            logger.info(f"Processing new message from {chat_name}: {message_text}")
            
            # Apply rate limiting
            if chat_name in self.rate_limit:
                last_time, count = self.rate_limit[chat_name]
                if (current_time - last_time).total_seconds() < 60:  # 1-minute window
                    if count >= 10:  # Max 10 messages per minute
                        logger.info(f"Rate limiting chat with {chat_name}")
                        return
                    self.rate_limit[chat_name] = (last_time, count + 1)
                else:
                    # Reset counter for new time window
                    self.rate_limit[chat_name] = (current_time, 1)
            else:
                self.rate_limit[chat_name] = (current_time, 1)
            
            # Update message history for this chat
            if chat_name not in self.message_history:
                self.message_history[chat_name] = []
            
            # Maintain a history of 10 messages max
            if len(self.message_history[chat_name]) >= 10:
                self.message_history[chat_name].pop(0)
            
            self.message_history[chat_name].append({"role": "user", "content": message_text})
            
            # Generate response using Qwen
            response = await self.generate_qwen_response(message_text, chat_name)
            
            # Update message history with the response
            self.message_history[chat_name].append({"role": "assistant", "content": response})
            
            # Send the response
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
                
                # Build message history for more context
                messages = [
                    {"role": "system", "content": "You are a helpful assistant responding to WhatsApp messages. Keep responses concise and helpful."}
                ]
                
                # Add recent conversation history if available
                if chat_name in self.message_history:
                    # Add up to 4 previous messages for context
                    history = self.message_history[chat_name][-4:]
                    messages.extend(history)
                else:
                    # Just add the current message if no history
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
                    timeout=30  # 30 second timeout
                )
                
                if response.status_code == 200:
                    response_json = response.json()
                    return response_json["choices"][0]["message"]["content"]
                elif response.status_code == 429:  # Rate limiting
                    logger.warning(f"Rate limited by Together AI (attempt {attempt})")
                    await asyncio.sleep(RETRY_DELAY * attempt)  # Exponential backoff
                elif response.status_code >= 500:  # Server error
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
        for attempt in range(1, 4):  # Try 3 times
            try:
                # Wait a moment to ensure the UI has stabilized
                await asyncio.sleep(1)
                
                # Try various selectors to find the correct input box
                # Ordered from most specific to least specific
                input_selectors = [
                    "div[data-testid='conversation-compose-box-input']",  # Main selector
                    "footer div[contenteditable='true']",                 # Look in footer area
                    "#main footer div[contenteditable='true']",           # More specific with #main
                    "div.copyable-text.selectable-text[contenteditable='true'][data-tab='10']",  # Class-based selector
                    "div[role='textbox']",                                # Role-based selector
                    "div[title='Type a message']",                        # Title-based selector
                    "#main div[contenteditable='true']"                   # General selector within main
                ]
                
                input_box = None
                used_selector = None
                
                for selector in input_selectors:
                    try:
                        # Try to find the element
                        element = await self.page.query_selector(selector)
                        if element:
                            # Verify this is not the search box
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
                
                # If standard selectors failed, try JavaScript fallback
                if not input_box:
                    logger.info("Standard selectors failed, trying JavaScript fallback...")
                    try:
                        # Use JavaScript to find the input box more reliably
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
                            # Use the coordinates to click at the right position
                            await self.page.mouse.click(
                                input_box_data['right'] - 100,  # Click near the end but not at the edge
                                input_box_data['bottom'] - 10   # Click near the bottom but not at the edge
                            )
                            logger.info("Used JavaScript fallback to find and click input box")
                        else:
                            raise Exception("Could not find input box with JavaScript either")
                    except Exception as e:
                        logger.error(f"JavaScript fallback failed: {str(e)}")
                        raise Exception("Could not find message input box")
                else:
                    # Click in the middle of the found input box
                    await input_box.click()
                    
                # Clear any existing text
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Backspace")
                
                # Type the message in chunks to avoid issues with long messages
                chunk_size = 100
                for i in range(0, len(message), chunk_size):
                    chunk = message[i:i+chunk_size]
                    await self.page.keyboard.type(chunk)
                    await asyncio.sleep(0.2)  # Small delay between chunks
                
                # Find and click the send button (more reliable than pressing Enter)
                send_button = await self.page.query_selector("button[data-testid='send']")
                
                if send_button:
                    logger.info("Found send button, clicking it")
                    await send_button.click()
                else:
                    # Fallback to Enter key if button not found
                    logger.info("Send button not found, pressing Enter")
                    await self.page.keyboard.press("Enter")
                
                # Wait for message to send
                await asyncio.sleep(1)
                logger.info("Message sent successfully")
                return True
                    
            except Exception as e:
                logger.error(f"Error sending message (attempt {attempt}): {str(e)}")
                logger.error(traceback.format_exc())
                if attempt < 3:
                    logger.info(f"Retrying in {attempt * 2} seconds...")
                    await asyncio.sleep(attempt * 2)  # Progressive delay
        
        logger.error("Failed to send message after multiple attempts")
        return False
    
    async def reconnect(self):
        """Attempt to reconnect if the connection is lost"""
        logger.info("Attempting to reconnect...")
        self.is_authenticated = False
        
        # Close existing browser if any
        if self.browser:
            await self.browser.close()
            self.browser = None
        
        # Reinitialize
        return await self.initialize()

async def main():
    """Main function with error recovery"""
    bot = WhatsAppQwenBot()
    
    # Setup signal handlers for graceful shutdown
    if sys.platform != 'win32' and hasattr(asyncio, 'add_signal_handler'):
        import signal
        loop = asyncio.get_running_loop()
        for signal_name in ('SIGINT', 'SIGTERM'):
            loop.add_signal_handler(
                getattr(signal, signal_name),
                lambda: asyncio.create_task(shutdown(bot, loop))
            )
    
    retry_count = 0
    while retry_count < 3:  # Maximum 3 retries for the entire bot
        try:
            success = await bot.initialize()
            if not success:
                logger.error("Bot initialization failed")
                retry_count += 1
                await asyncio.sleep(60)  # Wait 1 minute before retrying
                continue
                
            # If we reach here, the bot has exited its main loop unexpectedly
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
            await asyncio.sleep(60)  # Wait 1 minute before retrying

    logger.info("Bot is shutting down")

async def shutdown(bot, loop):
    """Handle graceful shutdown"""
    logger.info("Shutdown signal received")
    if bot and bot.browser:
        await bot.browser.close()
    loop.stop()

if __name__ == "__main__":
    try:
        # Add signal handling for non-Windows platforms
        if sys.platform != 'win32':
            import signal
            signal.signal(signal.SIGINT, lambda s, f: None)
            
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot terminated by user")
    except Exception as e:
        print(f"Fatal error: {str(e)}")
        traceback.print_exc()