import a2s
import websockets
import time
import aiohttp
import mysql.connector
from twitchio.ext import commands
import json
import asyncio
import random
import os
import pyperclip
import keyboard
import re

#
#
# THIS CODE IS VERY GROSS AND NOT ORGANIZED INTO MULTIPLE FILES/FOLDER THIS WAS NOT MEANT TO BE PUBLIC, THERE'S A TON OF OLD CODE AND PROBABLY UNUSED CODE HAVE FUN
# THIS IS A MIX OF MY OWN CODE, RANDOM CODE FROM GOOGLE AND SOME CHATGPT CODE WHEN I GOT LAZY. SO SORRY FOR THE BAD LAZY CODE
# SINCE THIS IS COPY PASTING MESSAGES YOU NEED TO HAVE A MACHINE RUNNING 24/7 WITH THE DISCORD CHAT BOX SELECTED WITH THE CURSOR ON IT
# YOU WILL ALSO NEED A 2ND DISCORD ACCOUNT TO SEND THE MESSAGES FROM (I guess you could use your main account but I wouldn't recommend that)
#
#

# Replace with your Steam Web API key
STEAM_API_KEY = ""

# Replace these with your Twitch bot credentials
BOT_TOKEN = "" # oauth token for twitch account
BOT_NICKNAME = "" # twitch account username
DISCORD_PREFIX = "!" # don't change this
GLOBAL_COOLDOWN_SECONDS = 5 # also don't change this

# mysql db that holds map information , this isn't technically needed if you only want map name for map queries (this is where tier and style and whatever else may be added to map information in the future is stored)
DB_CONFIG = {
    "host":'',
    "user":'',
    "password":'',
    "database":'',
}

last_used_time = {}
GLOBAL_COOLDOWN_TIME = 60
COOLDOWN_FILE_GLOBAL = "cooldown_data.json"
COOLDOWN_FILE = "cooldowns.json"
CHANNEL_COOLDOWN_FILE = "channel_cooldowns.json"
STREAMERS_FILE = "streamers.json"
DISABLED_COMMANDS_FILE = "disabled_commands.json"
BOT_CREATOR = "kuiq"


class BotCooldownManager:
    def __init__(self):
        self.cooldowns = self.load_cooldowns()

    def save_cooldowns(self):
        """Save cooldowns to a file."""
        with open(COOLDOWN_FILE, "w") as f:
            serializable_cooldowns = {
                f"{command}|{channel}": expiry_time
                for (command, channel), expiry_time in self.cooldowns.items()
            }
            json.dump(serializable_cooldowns, f)

    def load_cooldowns(self):
        """Load cooldowns from a file."""
        try:
            with open(COOLDOWN_FILE, "r") as f:
                serializable_cooldowns = json.load(f)
            return {
                tuple(key.split("|")): expiry_time
                for key, expiry_time in serializable_cooldowns.items()
            }
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def is_on_cooldown(self, command_name, channel):
        """Check if a command is on cooldown for a specific channel."""
        key = f"{command_name}|{channel}"
        if key in self.cooldowns:
            remaining_time = self.cooldowns[key] - time.time()
            if remaining_time > 0:
                return remaining_time
            del self.cooldowns[key]  # Remove expired cooldown
        return 0

    def set_cooldown(self, command_name, channel, cooldown_seconds):
        """Set a cooldown for a command in a specific channel."""
        key = f"{command_name}|{channel}"
        self.cooldowns[key] = time.time() + cooldown_seconds

    def clear_cooldown(self, command_name, channel):
        key = f"{command_name}|{channel}"
        if key in self.cooldowns:
            del self.cooldowns[key]  # Remove the cooldown entry
            

class Bot(commands.Bot):
    def __init__(self, *args, **kwargs):
        # Load streamers' data, including their custom prefixes
        with open(STREAMERS_FILE, "r") as f:
            self.streamers = json.load(f)

        initial_channels = [streamer for streamer in self.streamers.keys()]

        kwargs.pop("command_prefix", None)  # Ensure it's not in kwargs


        super().__init__(
            token=BOT_TOKEN,
            prefix=self.get_prefix,
            initial_channels=initial_channels,
            *args,
            **kwargs
        )      

        self.channel_mapping = {}
        self.websocket = None  # WebSocket connection to the Discord bot
        self.discord_ws_url = "ws://localhost:3000"  # Discord WebSocket URL (Adjust if needed)
        self.channel_for_command = None  # To store the channel where the command was issued
        self.db_connection = None
        self.db_cursor = None
        self.json_file = "user_ids.json"
        self.global_cooldown_file = "cooldown_data.json"
        self.global_cooldown_duration = 5 # This is for the ksfbot commands, this is ABSOLUTELY NEEDED if you don't want things to break
        self.cooldown_data = self.load_global_cooldown()
        self.streamers = self.streamers
        self.cooldown_manager = BotCooldownManager()
        self.channel_cooldown_settings = self.load_channel_cooldowns()
        self.allowed_channels = ["kuiq", "mapfinder"]
        self.initial_channels = list(streamer for streamer in self.streamers.keys())
        self.disabled_commands = self.load_disabled_commands()

    # Function to copy message to clipboard and paste
    # KSF discord bot does not take input from other discord bots therefore I'm using this as a workaround using a real discord account to trigger the KSF discord bot
    def copy_to_clipboard_and_paste(self, message, channel_name):
        pyperclip.copy(message)  # Copy to clipboard
        time.sleep(0.2)  # Add a slight delay for stability
        keyboard.write(message)  # Paste the message
        time.sleep(0.2)  # Add a slight delay for stability
        keyboard.press_and_release('enter')  # Press Enter to send
        time.sleep(0.1)  # When mentioning a user another enter input is needed
        keyboard.press_and_release('enter')

        self.channel_mapping[message] = channel_name

    # Load the global cooldown data from a file
    def load_global_cooldown(self):
        try:
            with open(self.global_cooldown_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {'last_used': 0}  # Default to no cooldown

    # Save the global cooldown data to a file
    def save_global_cooldown(self):
        with open(self.global_cooldown_file, 'w') as f:
            json.dump(self.cooldown_data, f)

    # Check if the global cooldown has expired and calculate remaining time
    def check_global_cooldown(self):
        current_time = time.time()
        last_used = self.cooldown_data['last_used']
        time_left = self.global_cooldown_duration - (current_time - last_used)

        if time_left > 0:
            return False, time_left  # Cooldown still active, return remaining time
        else:
            # Update the cooldown timestamp to the current time
            self.cooldown_data['last_used'] = current_time
            self.save_global_cooldown()
            return True, 0  # Cooldown expired

    def is_channel_owner(self, ctx):
        """Check if the user is the channel owner."""
        channel = ctx.channel.name
        user = ctx.author.name
        return user == channel  # Assuming the owner is the same as the channel name

    # Function to check if the user is on cooldown for any of the commands
    def is_on_shared_cooldown(self):
        """Check if the global cooldown is active"""
        last_time = self.load_cooldown_data()  # Get last used time from the file
        now = time.time()  # Current time in seconds

        print(f"Last used time: {last_time}")  # Debugging line
        print(f"Current time: {now}")  # Debugging line

        if now - last_time < GLOBAL_COOLDOWN_TIME:
            remaining_time = GLOBAL_COOLDOWN_TIME - (now - last_time)
            print(f"Cooldown active. Remaining time: {remaining_time:.1f} seconds.")  # Debugging line
            return remaining_time  # Return remaining time if cooldown is active

        print("Cooldown not active.")  # Debugging line
        return 0

    def set_global_cooldown(self):
        """Set the global cooldown time by updating the file"""
        timestamp = time.time()  # Current time in seconds
        self.save_cooldown_data(timestamp)  # Save the new timestamp
        print(f"Cooldown set to: {timestamp}")  # Debugging line

    
    def replace_username_with_userID(self, input_text):
        try:
            with open(self.json_file, "r") as file:
                user_data = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"Error: {self.json_file} not found or invalid JSON.")
            return input_text  # If there's an error reading the file, return original text

        # idk shit about regex this is chatgpt x)
        # Regex to match @username in the command text
        match = re.match(r"@(\w+)", input_text)

        if match:
            username = match.group(1).lower()  # Get the username without '@'

            # Check if the username exists in the JSON data
            if username in user_data:
                user_id = user_data[username]["userID"]
                # Replace @username with the Discord-style user mention format <@userID>
                modified_text = input_text.replace(f"@{username}", f"<@{user_id}>")
                return modified_text
            else:
                # If no match is found, return None to indicate no link
                return None

        return input_text  # Return the original text if no match is found

    def load_disabled_commands(self):
        if os.path.exists("disabled_commands.json"):
            with open("disabled_commands.json", "r") as f:
                return json.load(f)
        else:
            return {}

    def save_disabled_commands(self):
        with open("disabled_commands.json", "w") as f:
            json.dump(self.disabled_commands, f, indent=4)

    def _add_command_to_registry(self, command_name: str):
        """Manually add the command back to the bot's registry."""
        # Check if the command exists
        command = self.get_command(command_name)
        
        # If the command exists, add it back to the bot
        if command:
            self.add_command(command)

    async def event_message(self, message):
        if not message.author:
            return  # Skip processing the message with no author

        channel_name = message.channel.name.lower()
        message.content = message.content.lower()
        user = message.author.name  # Author of the message (user who typed the message)

        # Load the custom prefix for the channel
        custom_prefix = await self.get_prefix(message)
        default_prefix = "_"  # Define the default prefix

        # Determine which prefix the message starts with
        if message.content.startswith(custom_prefix):
            prefix = custom_prefix
        elif message.content.startswith(default_prefix):
            prefix = default_prefix
        else:
            return  # Message does not start with a recognized prefix

        is_mod = message.author.is_mod  # Check if the user is a moderator
        is_owner = user == channel_name  # Check if the user is the channel owner
        is_bot_creator = user == BOT_CREATOR


###########################################################################################################################
#OWNER/MOD CHECKS

        if message.content.startswith(f"{prefix}setprefix"):
            if not (is_owner or is_mod or is_bot_creator):
#                await message.channel.send("This command is restricted to the channel owner or moderators.")
                return

        if message.content.startswith(f"{prefix}disable"):
            if not (is_owner or is_mod or is_bot_creator):
#                await message.channel.send("This command is restricted to the channel owner or moderators.")
                return

        if message.content.startswith(f"{prefix}enable"):
            if not (is_owner or is_mod or is_bot_creator):
#                await message.channel.send("This command is restricted to the channel owner or moderators.")
                return

        if message.content.startswith(f"{prefix}disable_all"):
            if not (is_owner or is_bot_creator):
#                await message.channel.send("This command is restricted to the channel owner or moderators.")
                return

        if message.content.startswith(f"{prefix}enable_all"):
            if not (is_owner or is_bot_creator):
#                await message.channel.send("This command is restricted to the channel owner or moderators.")
                return

        if message.content.startswith(f"{prefix}disabled_list"):
            if not (is_owner or is_mod or is_bot_creator):
#                await message.channel.send("This command is restricted to the channel owner or moderators.")
                return

        if message.content.startswith(f"{prefix}setcooldown"):
            if not (is_owner or is_mod or is_bot_creator):
#                await message.channel.send("This command is restricted to the channel owner or moderators.")
                return        


        # Check if the message starts with the correct prefix and that there's content after the prefix
        if message.content.startswith(prefix) and len(message.content) > len(prefix):
            command_message = message.content[len(prefix):].split()[0]
#            print(f"Command detected: {command_message}")

                

            # Now check for disabled commands
            if channel_name in self.disabled_commands:
                if self.disabled_commands[channel_name].get("all_disabled", False):
                    # Allow the channel owner to run "_enable_all" despite all_disabled
                    if message.content.startswith(prefix + "enable_all") and user == channel_name:
                        pass  # Let the command through
                    else:
                        await message.channel.send("All commands are currently disabled in this channel.")
                        return  # Prevent processing further messages

            if message.content.startswith(prefix):
                command_message = message.content[len(prefix):].split()[0]

                # Check if the specific command is disabled
                if channel_name in self.disabled_commands:
                    disabled_commands = self.disabled_commands[channel_name]["disabled_commands"]
                    if command_message in disabled_commands:
                        print(f"Command '{command_message}' is currently disabled in this channel.")
                        return

            await self.handle_commands(message)
                        # Log message information if needed (for debugging)
            print(f"Channel: {channel_name}, User: {user}, Message: {message.content}")

        try:
            with open("streamers.json", "r") as f:
                self.streamers = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.streamers = {}


    async def get_prefix(self, message):
        channel_name = message.channel.name.lower()

        try:
            with open("streamers.json", "r") as f:
                streamers_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            print("Error loading streamers.json")
            streamers_data = {}

        # Get the prefix for the channel from the loaded data
        prefix = streamers_data.get(channel_name, {}).get("prefix", "_")

        # Ensure the prefix is a string and not empty
        if not isinstance(prefix, str) or len(prefix) == 0:
            prefix = "_"

        return prefix

    async def save_streamers(self):
        """Save the streamers dictionary to a JSON file."""
        with open("streamers.json", "w") as f:
            json.dump(self.streamers, f, indent=4)

    def is_channel_allowed(self, channel_name):
        """Check if a channel is allowed to use restricted commands."""
        return channel_name.lower() in self.allowed_channels            

    def load_channel_cooldowns(self):
        """Load custom cooldowns for each channel from a file."""
        try:
            with open(CHANNEL_COOLDOWN_FILE, "r") as f:
                data = json.load(f)
                for channel, value in data.items():
                    if isinstance(value, int):
                        data[channel] = {"default": value}
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_channel_cooldowns(self):
        with open(CHANNEL_COOLDOWN_FILE, "w") as f:
            json.dump(self.channel_cooldown_settings, f)

    async def event_ready(self):
        asyncio.create_task(self.connect_to_discord_bot())  # Connect to the WebSocket server
        try:
            self.db_connection = mysql.connector.connect(**DB_CONFIG)
            self.db_cursor = self.db_connection.cursor(dictionary=True)
            print(f"Logged in as {self.nick}")
            print(f"Connected to {self.connected_channels}")
        except mysql.connector.Error as e:
            print(f"Error connecting to MySQL: {e}")

    async def connect_to_discord_bot(self):
        """Connect to the Discord bot's WebSocket server."""
        try:
            self.websocket = await websockets.connect(self.discord_ws_url)
            print("Connected to Discord bot WebSocket.")
            
            # Continuously listen for messages from Discord bot
            async for message in self.websocket:
                print(f"Received embed data from Discord bot: {message}")
                
                # Ensure channel is specified before sending back to Twitch
                if self.channel_for_command:
                    await self.send_to_twitch(message, self.channel_for_command)
                else:
                    print("Error: No channel specified to send message back to.")

        except Exception as e:
            print(f"Error connecting to Discord bot WebSocket: {e}")

    async def send_to_twitch(self, message, channel_name):
        # Ensure you're sending the message to the right channel in Twitch
        try:
            # Get the channel object using the name
            channel = self.get_channel(channel_name)
            if channel:
                await channel.send(message)
                print(f"Sent message to Twitch channel {channel_name}: {message}")
            else:
                print(f"Error: Channel {channel_name} not found.")
        except Exception as e:
            print(f"Error sending message to Twitch: {e}")

    async def close(self):
        if self.websocket:
            await self.websocket.close()
        await super().close()

    def query_mysql_for_map(self, map_name):
        # Query MySQL database for map details based on map_name.
        try:
            db_connection = mysql.connector.connect(**DB_CONFIG)
            cursor = db_connection.cursor(dictionary=True)

            query = """
            SELECT MapName, Tier, StageAmount, MapType
            FROM map_data
            WHERE MapName = %s
            """
            cursor.execute(query, (map_name,))
            result = cursor.fetchone()

            cursor.close()
            db_connection.close()

            return result
        except mysql.connector.Error as e:
            print(f"MySQL Error: {e}")
            return None  

    async def query_map(self, steam_id, channel_name, for_mrank=False, for_wr=False):
        # Fetch the current map for a given Steam ID and query MySQL for additional details.
        try:
            # Fetch server info using the Steam API
            server_ip = await self.fetch_server_info(steam_id)
            if server_ip:
                ip, port = server_ip.split(":")
                server_address = (ip, int(port))

                # Query the server for map information
                info = a2s.info(server_address)
                print(f"server info: {info}")
                current_map = info.map_name

                if for_mrank:
                    # If we're in the mrank context, just return the map name
                    return current_map
                
                if for_wr:
                    # If we're in the wr context, just return the map name
                    return current_map

                # Query MySQL for map details
                map_data = self.query_mysql_for_map(current_map)
                if not map_data:
                    return f"{current_map}"

                # Format the response based on the map type
                map_name = map_data["MapName"]
                tier = map_data["Tier"]
                map_type = map_data["MapType"]
                stage_amount = map_data["StageAmount"]

                if map_type.lower() == "linear":
                    return f"{map_name} | T{tier} | {map_type}"
                else:
                    return f"{map_name} | T{tier} | {stage_amount} {map_type}"
            else:
                return "Failed to retrieve server data."
        except Exception as e:
            print(f"Error fetching server info: {e}")
            return "Failed to retrieve server data."

    async def query_map_by_name(self, map_name: str):
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._query_map_by_name, map_name)
        return result
    

    def _query_map_by_name(self, map_name: str):
        map_name = map_name.lower()
        query = """
            SELECT * FROM map_data
            WHERE LOWER(MapName) LIKE %s
            ORDER BY 
                CASE 
                    WHEN LOWER(MapName) = %s THEN 1
                    ELSE 2
                END, 
                MapName ASC
            LIMIT 1
        """
        try:
            db_connection = mysql.connector.connect(**DB_CONFIG)
            cursor = db_connection.cursor(dictionary=True)

            # Execute query with both the pattern and the exact match
            cursor.execute(query, (f"%{map_name}%", map_name))
            result = cursor.fetchone()

            cursor.close()
            db_connection.close()

            return result
        except mysql.connector.Error as e:
            print(f"MySQL Error: {e}")
            return None

    def query_map_db_sync(self, map_name: str):
        query = "SELECT * FROM map_data WHERE LOWER(MapName) LIKE %s"
        db_connection = mysql.connector.connect(**DB_CONFIG)
        cursor = db_connection.cursor(dictionary=True)

        cursor.execute(query, (f"%{map_name}%",))
        result = cursor.fetchone()

        cursor.close()
        db_connection.close()

        return result

    async def run_in_executor(self, map_name: str):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self.query_map_db_sync, map_name)

    async def fetch_server_info(self, steam_id):
        #Use Steam Web API to get the server info for a player.
        url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={steam_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    data = await response.json()
                    players = data.get("response", {}).get("players", [])

                    if players:
                        player = players[0]
                        if "gameid" in player and player["gameid"] == "240":  # Check if playing CS: Source (I tried getting this to work with surfheaven on csgo legacy and could not so rip)
                            return player.get("gameserverip", None)
            return None
        except Exception as e:
            print(f"Error fetching Steam API data: {e}")
            return None
        
    def is_valid_steamid64(self, steam_id):
        #Validate if a given SteamID is in the correct SteamID64 format.
        return steam_id.isdigit() and len(steam_id) == 17  

#################################################################################################################

    @commands.command(name="map", aliases=["m"])
    async def map_command(self, ctx, map_search: str = None):
        channel = ctx.channel.name.lower()
        command_name = "map"

        # Check if the channel is in the streamers list
        if channel not in self.streamers:
            await ctx.send("No player data found for this channel.")
            return

        # Get the steam ID for the channel
        steam_id = self.streamers[channel]["steam_id"]

        # Ensure the channel settings are a dictionary and fetch cooldown
        channel_settings = self.channel_cooldown_settings.get(channel, {})
        if isinstance(channel_settings, int):
            channel_settings = {"default": channel_settings}

        cooldown_seconds = channel_settings.get(command_name, 60)  # Default to 60 seconds

        cooldown = 0

        if ctx.author.name != BOT_CREATOR:
            cooldown = self.cooldown_manager.is_on_cooldown(command_name, channel)

        if cooldown > 0:
            await ctx.send(f"Try again in {int(cooldown)} seconds.")
            return

        self.cooldown_manager.set_cooldown(command_name, channel, cooldown_seconds)

        # If map_search is provided, search for a map that contains the search term
        if map_search:
            # Query the database for a map name that matches the search term
            map_data = await self.query_map_by_name(map_search)
            
            if map_data:
                # Display map details if found
                map_name = map_data["MapName"]
                tier = map_data["Tier"]
                map_type = map_data["MapType"]
                stage_amount = map_data["StageAmount"]

                if map_type.lower() == "linear":
                    await ctx.send(f"{map_name} | Tier {tier} | {map_type}")
                else:
                    await ctx.send(f"{map_name} | Tier {tier} | {stage_amount} {map_type}")
            else:
                await ctx.send(f"No map found with the name '{map_search}'.")
            return

        # Query the map
        message = await self.query_map(steam_id, channel)

        print(f"Sending map information to {channel}: {message}")  # Logs to the console
        await ctx.send(message)

################################################################################################################# 
# KSF BOT COMMAND(S)

    @commands.command(name="wr", aliases=["wrb", "wrcp", "hswwr", "hswwrb", "hswwrcp", "swwr", "swwrb", "swwrcp", "bwwr", "bwwrb", "bwwrcp", "100twr", "100twrb", "100twrcp", "100thswwr", "100thswwrb", "100thswwrcp", "100tswwr", "100tswwrb", "100tswwrcp", "100tbwwr", "100tbwwrb", "100tbwwrcp"])
    async def wr_command(self, ctx: commands.Context):

        # Check if the global cooldown is active and get the remaining time
        cooldown_active, time_left = self.check_global_cooldown()

        if not cooldown_active:
            # If cooldown is active, show the remaining time before use
            remaining_time = int(time_left)  # Convert to an integer number of seconds
            await ctx.send(f"Try again in {remaining_time} seconds.")
            return

        channel = ctx.channel.name.lower()
        self.channel_for_command = channel

        args = ctx.message.content[1:].strip().split(" ", 1)

        if len(args) < 2 or not args[1].strip():
            try:
                if channel not in self.streamers:
                    await ctx.send("No player data found for this channel.")
                    return

                steam_id = self.streamers[channel]["steam_id"]
                mapname = await self.query_map(steam_id, channel, for_wr=True)

                if not mapname:
                    await ctx.send("Could not retrieve map information.")
                    return

                command_name = args[0]
                message = f"{DISCORD_PREFIX}{command_name} {mapname}"
                self.copy_to_clipboard_and_paste(message, channel)

            except Exception as e:
                await ctx.send(f"Error: {str(e)}")
                return

        else:
            input_text = args[1].strip()
            message = f"{DISCORD_PREFIX}{args[0]} {input_text}"
            self.copy_to_clipboard_and_paste(message, channel)

        await self.connect_to_discord_bot()

################################################################################################################# 
# KSF BOT COMMAND(S)

    @commands.command(name="pc")
    async def pc_command(self, ctx: commands.Context):

        # Check if the global cooldown is active and get the remaining time
        cooldown_active, time_left = self.check_global_cooldown()

        if not cooldown_active:
            # If cooldown is active, show the remaining time before use
            remaining_time = int(time_left)  # Convert to an integer number of seconds
            await ctx.send(f"Try again in {remaining_time} seconds.")
            return


        channel = ctx.channel.name.lower()
        self.channel_for_command = channel
        command_name = "pc"
        original_message = ctx.message.content[1:].strip()

        # Initialize flags for user linkage status
        user_linked = False
        mentioned_user_linked = False

        args = original_message.split(" ", 1)    
            
        try:
            with open(self.json_file, "r") as file:
                user_data = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        twitch_username = ctx.author.name.lower()

        # Check if the user is linked in the JSON file
        if twitch_username in user_data:
            user_linked = True
            user_id = user_data[twitch_username]["userID"]
        else:
            user_linked = False

        # If the command has a second argument (mentioned user)
        if len(args) > 1 and args[1].strip().startswith('@'):
            # Extract the mentioned user's name (remove '@' symbol)
            mentioned_user = args[1].strip()[1:].lower()

            # Check if the mentioned user is linked in the JSON file
            if mentioned_user in user_data:
                mentioned_user_linked = True
                mentioned_user_id = user_data[mentioned_user]["userID"]
            else:
                mentioned_user_linked = False

        # If no linked user or mentioned user, send appropriate message
        if not user_linked and not mentioned_user_linked:
            await ctx.send(f"{ctx.author.name}, your account is not linked.")
            return

        if not user_linked:
            await ctx.send(f"{ctx.author.name}, your account is not linked.")
            return

        if len(args) > 1 and not mentioned_user_linked:
            await ctx.send(f"{args[1]}, this user is not linked.")
            return

        # If the user is linked, proceed with the command
        if mentioned_user_linked:
            # Replace @username with <@userID> in the command
            message = f"{DISCORD_PREFIX}{args[0]} <@{mentioned_user_id}>"
        else:
            # If no mention, just use the original user_id for the command
            message = f"{DISCORD_PREFIX}{args[0]} <@{user_id}>"

        # Perform the required action, e.g., copying to clipboard
        self.copy_to_clipboard_and_paste(message, channel)

        # Start listening for Discord embed data
        await self.connect_to_discord_bot()



##################################################################################################################
# KSF BOT COMMAND(S)

    @commands.command(name="mrank", aliases=["hswmrank", "swmrank", "bwmrank", "100tmrank", "100thswmrank", "100tswmrank", "100tbwmrank", "prinfo"])
    async def mrank_command(self, ctx: commands.Context):

        # Check if the global cooldown is active and get the remaining time
        cooldown_active, time_left = self.check_global_cooldown()

        if not cooldown_active:
            # If cooldown is active, show the remaining time before use
            remaining_time = int(time_left)  # Convert to an integer number of seconds
            await ctx.send(f"Try again in {remaining_time} seconds.")
            return

        channel = ctx.channel.name.lower()
        self.channel_for_command = channel
        twitch_username = ctx.author.name.lower()

        original_message = ctx.message.content[1:].strip()
        args = original_message.split(" ", 1)

        try:
            with open(self.json_file, "r") as file:
                user_data = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        command_name = args[0]


        if len(args) < 2 or not args[1].strip():
            if channel not in self.streamers:
                await ctx.send("No player data found for this channel.")
                return

            steam_id = self.streamers[channel]["steam_id"]
            mapname = await self.query_map(steam_id, channel, for_mrank=True)

            if not mapname:
                await ctx.send("Could not retrieve map information.")
                return

            if twitch_username in user_data:
                user_id = user_data[twitch_username]["userID"]
                message = f"{DISCORD_PREFIX}{command_name} <@{user_id}> {mapname}"
                self.copy_to_clipboard_and_paste(message, channel)
            else:
                await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
                return

        # more regex that chatgpt did for me x)
        elif re.match(r"^(g\d+|\#\d+)$", args[1].strip().split(" ", 1)[0]):
            first_arg = args[1].strip().split(" ", 1)[0]
            second_arg = args[1].split(" ", 1)[1] if " " in args[1] else None

            if second_arg:
                message = f"{DISCORD_PREFIX}{command_name} {first_arg} {second_arg}"
                self.copy_to_clipboard_and_paste(message, channel)
            else:
                if channel not in self.streamers:
                    await ctx.send("No player data found for this channel.")
                    return

                steam_id = self.streamers[channel]["steam_id"]
                mapname = await self.query_map(steam_id, channel, for_mrank=True)

                if not mapname:
                    await ctx.send("Could not retrieve map information.")
                    return

                message = f"{DISCORD_PREFIX}{command_name} {first_arg} {mapname}"
                self.copy_to_clipboard_and_paste(message, channel)

        elif "@" in args[1]:
            if args[1].startswith("@"):
                username = args[1].split(" ", 1)[0][1:]
                mapname = args[1].split(" ", 1)[1] if " " in args[1] else None

                if username in user_data:
                    user_id = user_data[username]["userID"]
                    modified_input_text = f"<@{user_id}>"
                else:
                    await ctx.send(f"{username} is not linked in the system.")
                    return

                if mapname:
                    message = f"{DISCORD_PREFIX}{args[0]} {modified_input_text} {mapname}"
                    self.copy_to_clipboard_and_paste(message, channel)
                    return

                if channel not in self.streamers:
                    await ctx.send("No player data found for this channel.")
                    return

                steam_id = self.streamers[channel]["steam_id"]
                mapname = await self.query_map(steam_id, channel, for_mrank=True)

                if not mapname:
                    await ctx.send("Error: Could not retrieve map information.")
                    return

                message = f"{DISCORD_PREFIX}{args[0]} {modified_input_text} {mapname}"
                self.copy_to_clipboard_and_paste(message, channel)

            else:
                mapname = args[1].split(" ", 1)[0].strip()
                username = args[1].split(" ", 1)[1].strip()[1:]

                if username in user_data:
                    user_id = user_data[username]["userID"]
                    modified_input_text = f"<@{user_id}>"
                else:
                    await ctx.send(f"{username} is not linked in the system.")
                    return

                message = f"{DISCORD_PREFIX}{args[0]} {modified_input_text} {mapname}"
                self.copy_to_clipboard_and_paste(message, channel)

        else:
            if twitch_username in user_data:
                user_id = user_data[twitch_username]["userID"]
                message = f"{DISCORD_PREFIX}{command_name} <@{user_id}> {args[1].strip()}"
                self.copy_to_clipboard_and_paste(message, channel)
            else:
                await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
                return

        await self.connect_to_discord_bot()

#################################################################################################################
# KSF BOT COMMAND(S)

    @commands.command(name="crank", aliases=["rank", "hswrank", "swrank", "bwrank", "hswcrank", "swcrank", "bwcrank", "100trank", "100thswrank", "100tswrank", "100tbwrank", "100tcrank" "100thswcrank", "100tswcrank", "100tbwcrank"])
    async def crank_command(self, ctx: commands.Context):

        # Check if the global cooldown is active and get the remaining time
        cooldown_active, time_left = self.check_global_cooldown()

        if not cooldown_active:
            # If cooldown is active, show the remaining time before use
            remaining_time = int(time_left)  # Convert to an integer number of seconds
            await ctx.send(f"Try again in {remaining_time} seconds.")
            return

        original_message = ctx.message.content[1:].strip()

        channel = ctx.channel.name.lower()
        self.channel_for_command = channel

        args = original_message.split(" ", 1)

        if len(args) < 2 or not args[1].strip():
            twitch_username = ctx.author.name.lower()
            
            try:
                with open(self.json_file, "r") as file:
                    user_data = json.load(file)
            except (FileNotFoundError, json.JSONDecodeError):
                return

            if twitch_username in user_data:
                user_id = user_data[twitch_username]["userID"]
                input_text = f"<@{user_id}>"
                message = f"{DISCORD_PREFIX}{args[0]} {input_text}"
                self.copy_to_clipboard_and_paste(message, ctx.channel.name.lower())
                return

            await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
            return

        input_text = args[1].strip()
        modified_input_text = self.replace_username_with_userID(input_text)

        if modified_input_text is None:
            await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
            return

        message = f"{DISCORD_PREFIX}{args[0]} {modified_input_text}"
        self.copy_to_clipboard_and_paste(message, self.channel_for_command)

        print(f"Sending message to Twitch in channel: {self.channel_for_command}")

        await self.connect_to_discord_bot()


#################################################################################################################
# KSF BOT COMMAND(S)

    @commands.command(name="p", aliases=["hswp", "swp", "bwp", "100tp", "100thswp", "100tswp", "100tbwp"])
    async def p_command(self, ctx: commands.Context):

        # Check if the global cooldown is active and get the remaining time
        cooldown_active, time_left = self.check_global_cooldown()

        if not cooldown_active:
            # If cooldown is active, show the remaining time before use
            remaining_time = int(time_left)  # Convert to an integer number of seconds
            await ctx.send(f"Try again in {remaining_time} seconds.")
            return


        original_message = ctx.message.content[1:].strip()
        args = original_message.split(" ", 1)
        channel = ctx.channel.name.lower()
        self.channel_for_command = channel
        command_name = "p"


        if len(args) < 2 or not args[1].strip():
            twitch_username = ctx.author.name.lower()

            try:
                with open(self.json_file, "r") as file:
                    user_data = json.load(file)
            except (FileNotFoundError, json.JSONDecodeError):
                return

            if twitch_username in user_data:
                user_id = user_data[twitch_username]["userID"]
                input_text = f"<@{user_id}>"
                message = f"{DISCORD_PREFIX}{args[0]} {input_text}"
                self.copy_to_clipboard_and_paste(message, channel)
                return

            await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
            return


        input_text = args[1].strip()
        modified_input_text = self.replace_username_with_userID(input_text)

        if modified_input_text is None:
            await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
            return

        input_text = modified_input_text

        message = f"{DISCORD_PREFIX}{args[0]} {input_text}"
        self.copy_to_clipboard_and_paste(message, channel)

        await self.connect_to_discord_bot()


#################################################################################################################
# KSF BOT COMMAND(S)

    @commands.command(name="cp", aliases=["currentlyplaying"])
    #limited to mffns channel at his request otherwise It's disabled in other chats cause this command felt too stalkerish
    async def cp_command(self, ctx: commands.Context):

        allowed_channels = ["kuiq", "mffn"]

        # Check if the command is being used in one of the allowed channels
        if ctx.channel.name.lower() not in allowed_channels:
#            await ctx.send(f"test")
            return

        # Check if the global cooldown is active and get the remaining time
        cooldown_active, time_left = self.check_global_cooldown()

        if not cooldown_active:
            # If cooldown is active, show the remaining time before use
            remaining_time = int(time_left)  # Convert to an integer number of seconds
            await ctx.send(f"Try again in {remaining_time} seconds.")
            return


        original_message = ctx.message.content[1:].strip()
        args = original_message.split(" ", 1)
        channel = ctx.channel.name.lower()
        self.channel_for_command = channel
        command_name = "cp"


        if len(args) < 2 or not args[1].strip():
            twitch_username = ctx.author.name.lower()

            try:
                with open(self.json_file, "r") as file:
                    user_data = json.load(file)
            except (FileNotFoundError, json.JSONDecodeError):
                return

            if twitch_username in user_data:
                user_id = user_data[twitch_username]["userID"]
                input_text = f"<@{user_id}>"
                message = f"{DISCORD_PREFIX}{args[0]} {input_text}"
                self.copy_to_clipboard_and_paste(message, channel)
                return

            await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
            return


        input_text = args[1].strip()
        modified_input_text = self.replace_username_with_userID(input_text)

        if modified_input_text is None:
            await ctx.send(f"{ctx.author.name}, account not linked. use _link <Discord userID>")
            return

        input_text = modified_input_text

        message = f"{DISCORD_PREFIX}{args[0]} {input_text}"
        self.copy_to_clipboard_and_paste(message, channel)

        await self.connect_to_discord_bot()


#################################################################################################################


    @commands.command(name="r")
    async def random_map_command(self, ctx, tier: int = None):

        channel = ctx.channel.name.lower()
        command_name = "r"

        channel_settings = self.channel_cooldown_settings.get(channel, {})
        if isinstance(channel_settings, int):
            channel_settings = {"default": channel_settings}

        cooldown_seconds = channel_settings.get(command_name, 60)

        cooldown = 0

        if ctx.author.name != BOT_CREATOR:
            cooldown = self.cooldown_manager.is_on_cooldown(command_name, channel)

        if cooldown > 0:
            await ctx.send(f"Try again in {int(cooldown)} seconds.")
            return

        self.cooldown_manager.set_cooldown(command_name, channel, cooldown_seconds)


        try:
            db_connection = mysql.connector.connect(**DB_CONFIG)
            cursor = db_connection.cursor(dictionary=True)
            if tier is None:
                # Query all maps if no tier is provided
                query = "SELECT MapName, Tier, MapType, StageAmount FROM map_data"
                cursor.execute(query)
            elif 1 <= tier <= 8:
                # Query maps for the specific tier
                query = "SELECT MapName, Tier, MapType, StageAmount FROM map_data WHERE Tier = %s"
                cursor.execute(query, (tier,))
            else:
                # Invalid tier input
                await ctx.send("Please provide a valid tier between 1 and 8.")
                return

            results = cursor.fetchall()

            # If no maps are found
            if not results:
                if tier:
                    await ctx.send(f"No maps found for T{tier}.")
                else:
                    await ctx.send("No maps found in the database.")
                return

            # Pick a random map from the results
            random_map = random.choice(results)
            map_name = random_map["MapName"]
            tier = random_map["Tier"]
            map_type = random_map["MapType"]
            stage_amount = random_map["StageAmount"]

            if map_type.lower() == "linear":
                response = f"{map_name} | T{tier} | {map_type}"
            else:
                response = f"{map_name} | T{tier} | {stage_amount} {map_type}"

            print(f"Sending R map information to {channel}: {response}")  # Logs to the console
            await ctx.send(response)

        except mysql.connector.Error as err:
            print(f"Database query error: {err}")
            await ctx.send("An error occurred while fetching map data. Please try again.")
        finally:
            cursor.close()
            db_connection.close()

#################################################################################################################
# Allow streamers to add bot to their channel.

    @commands.command(name="add")
    async def addstreamer(self, ctx, steam_id: str = None):

        if not self.is_channel_allowed(ctx.channel.name):
            print(f"Add command sent in invalid channel")
#            await ctx.send("This command is not allowed in this channel.")
            return
        
        # Check if the Steam ID is valid
        if not self.is_valid_steamid64(steam_id):
            await ctx.send("Invalid SteamID64 format. Please provide a valid SteamID64.")
            return    


        streamer_name = ctx.author.name

        # Check if the streamer is already in the list
        if streamer_name in self.streamers:
            await ctx.send("You are already added!")
            return

        self.streamers[streamer_name] = {"steam_id": steam_id}
        await self.save_streamers()
        await ctx.send(f"Added {streamer_name} with SteamID: {steam_id}.")
        print(f"Successfully added {streamer_name}.")
        

        # Join the new channel dynamically
        await self.join_channels([streamer_name])
#        await ctx.send(f"Streamer {streamer_name} added successfully!")

#################################################################################################################

    @commands.command(name="connect", aliases=["link"])
    async def connect_command(self, ctx: commands.Context):
        args = ctx.message.content.strip().split(" ")

        # If there are less than 2 arguments, inform the user of the correct usage
        if len(args) < 2:
            await ctx.send("Usage: _link <discord userID> (This will be a 18~ digit number, not your @)")
            return

        # If the bot owner is issuing the command, they can manually input both twitch username and discord user ID
        if ctx.author.name.lower() == BOT_CREATOR:
            if len(args) != 3:
#                await ctx.send("Usage: _connect <twitch_username> <discord_user_id>")
                return
            twitch_username = args[1].lower()  # The first argument is the Twitch username
            discord_user_id = args[2]         # The second argument is the Discord user ID
        else:
            # Regular users will only provide the Discord user ID, and the bot uses their current Twitch username
            twitch_username = ctx.author.name.lower()
            discord_user_id = args[1]

        # Validate the Discord user ID (must be 17-19 digits long)
        if not discord_user_id.isdigit() or len(discord_user_id) not in range(17, 20):
            await ctx.send("Invalid Discord UserID (You need a 18~ digit number, not your @)")
            return

        try:
            with open(self.json_file, "r") as file:
                user_data = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            user_data = {}

        # If the bot owner is issuing the command, allow them to update the user even if it already exists
        if ctx.author.name.lower() == BOT_CREATOR:
            user_data[twitch_username] = {"userID": discord_user_id}
#            await ctx.send(f"{twitch_username}'s link has been updated to Discord ID {discord_user_id}.")
        else:
            # For regular users, check if the user is already linked
            if twitch_username in user_data:
                # If user is already linked, return a message without updating the JSON
                await ctx.send(f"{twitch_username} is already linked.")
                return

        # Add the new user (either the current Twitch username or the specified one)
        user_data[twitch_username] = {"userID": discord_user_id}
        await ctx.send(f"{twitch_username} is now linked with Discord ID {discord_user_id}.")

        try:
            with open(self.json_file, "w") as file:
                json.dump(user_data, file, indent=4)
        except IOError as e:
            print(f"Error saving data: {e}")

#################################################################################################################
# Allow streamers to remove the bot from their channels.

    @commands.command(name="remove")
    async def removestreamer(self, ctx):

        if not self.is_channel_allowed(ctx.channel.name):
            print(f"Remove command sent in invalid channel")
#            await ctx.send("This command is not allowed in this channel.")
            return

        twitch_username = ctx.author.name.lower()

        # Check if the user exists in the streamers list
        if twitch_username not in self.streamers:
            await ctx.send("You are not currently added.")
            return
        
        del self.streamers[twitch_username]
        await self.save_streamers()

        # Check if the bot is in the initial_channels list
        if twitch_username in self.initial_channels:
            self.initial_channels.remove(twitch_username)

        # Leave the channel
        await self.part_channels([twitch_username])

        await ctx.send(f"Successfully removed {twitch_username}.")
        print(f"Successfully removed {twitch_username}.")

################################################################################################################# 

    @commands.command(name="setcooldown")
    async def set_cooldown_command(self, ctx, command_name: str = None, cooldown_seconds: int = None):

        # Allow channel owners to adjust the cooldown for their channel and specific commands. (THIS ONLY APPLIES TO NON KSF DISCORD BOT COMMANDS)
        # Usage: _setcooldown <command_name> <cooldown_seconds>

        channel = ctx.channel.name
        user = ctx.author.name

        # Validate input
        if not command_name or cooldown_seconds is None:
            await ctx.send("Usage: _setcooldown <command_name> <cooldown_seconds>")
            return

        if cooldown_seconds < 0:
            await ctx.send("Cooldown must be a non-negative value.")
            return

        command = self.get_command(command_name)
        if not command:
            await ctx.send(f"Command '{command_name}' does not exist.")
            return        

        # Get all valid names for this command (primary name + aliases)
        valid_names = {command.name} | set(command.aliases or [])  # Default to empty set if no aliases

        if channel not in self.channel_cooldown_settings or isinstance(self.channel_cooldown_settings[channel], int):
            self.channel_cooldown_settings[channel] = {}

        # Update the cooldown for all valid names of the command
        for name in valid_names:
            self.channel_cooldown_settings[channel][name] = cooldown_seconds

            # Clear the cooldown timer for the command
            self.cooldown_manager.clear_cooldown(name, channel)

        self.save_channel_cooldowns()

        print(f"{channel}: {command_name} set to {cooldown_seconds} seconds")
        await ctx.send(f"Cooldown for '{command_name}' set to {cooldown_seconds} seconds.")

################################################################################################################# 

    @commands.command(name="disable")
    async def disable_command(self, ctx, command_name: str = None):

        # Disable a specific command in the current channel.
        # Usage: _disable <command_name>

        channel = ctx.channel.name
        user = ctx.author.name
        

        if command_name is None:
            print(f"Error: {user} tried to use _disable without specifying a command.")
            await ctx.send("Please specify a command to disable.")
            return
        
        command_name = command_name.lower()

        
        if channel not in self.disabled_commands:
            self.disabled_commands[channel] = {
                "all_disabled": False,
                "disabled_commands": [],
                "commands_functions": {}
            }


        command = self.get_command(command_name)    
        
        if not command:
            # If command is not found by name, try to find it using its aliases
            for cmd in self.commands:
                if command_name in cmd.aliases:
                    command = cmd
                    break

        if not command:
            await ctx.send(f"Command or alias '{command_name}' not found.")
            return

        commands_to_disable = set()

        commands_to_disable.add(command.name)
        if command.aliases:
            commands_to_disable.update(command.aliases)

        # Disable both the main command and its aliases
        for cmd in commands_to_disable:
            if cmd not in self.disabled_commands[channel]["disabled_commands"]:
                self.disabled_commands[channel]["disabled_commands"].append(cmd)
                self.disabled_commands[channel]["commands_functions"][cmd] = "disabled"

        self.save_disabled_commands()

        await ctx.send(f"Command(s) {', '.join(commands_to_disable)} have been disabled in this channel.")
                
################################################################################################################# 

    @commands.command(name="enable")
    async def enable_command(self, ctx, command_name: str = None):

        # Enable a previously disabled command in the current channel.
        # Usage: _enable <command_name>


        print(f"Command received: _enable {command_name}")  # Debugging line

        channel = ctx.channel.name
        user = ctx.author.name    

        if command_name is None:
            print(f"Error: {user} tried to use _enable without specifying a command.")
            await ctx.send("Please specify a command to enable.")
            return
        
        command_name = command_name.lower()
        

        # Skip disabled check for channel owner
        if self.is_channel_owner(ctx):
#            await ctx.send("As the channel owner, you can still use this command.")
            print(f"{channel} skipping enable/disable check")
        

        if channel not in self.disabled_commands:
            self.disabled_commands[channel] = {
                "all_disabled": False,
                "disabled_commands": [],
                "commands_functions": {}
            }

        command = self.get_command(command_name)

        if not command:
            for cmd in self.commands:
                if command_name in cmd.aliases:
                    command = cmd
                    break

        if not command:
            await ctx.send(f"Command or alias '{command_name}' not found.")
            return

 
        commands_to_enable = set()

        commands_to_enable.add(command.name)
        if command.aliases:
            commands_to_enable.update(command.aliases)

        for cmd in commands_to_enable:
            if cmd in self.disabled_commands[channel]["disabled_commands"]:
                self.disabled_commands[channel]["disabled_commands"].remove(cmd)
                self.disabled_commands[channel]["commands_functions"][cmd] = "enabled"

        self.save_disabled_commands()

        await ctx.send(f"Command(s) {', '.join(commands_to_enable)} have been enabled in this channel.")

################################################################################################################# 

    @commands.command(name="disabled_list")
    async def list_disabled_commands(self, ctx):

        # Show which commands are disabled for the current channel.

        channel = ctx.channel.name
        user = ctx.author.name

        if channel in self.disabled_commands:
            all_disabled = self.disabled_commands[channel].get("all_disabled", False)
            disabled_commands = self.disabled_commands[channel].get("disabled_commands", [])

            if all_disabled:
                await ctx.send("All commands are currently disabled in this channel.")
                return

            if disabled_commands:
                disabled_commands_list = ", ".join(disabled_commands)
                await ctx.send(f"The following commands are disabled: {disabled_commands_list}")
                return

        await ctx.send("No commands are currently disabled in this channel.")

################################################################################################################# 

    @commands.command(name="disable_all")
    async def disable_all_commands(self, ctx):

        # Disable all commands in the current channel.

        channel = ctx.channel.name
        user = ctx.author.name


        if self.is_channel_owner(ctx):
#            await ctx.send("As the channel owner, you can still use this command.")
            print(f"{channel} skipping enable/disable check")
        

        if channel not in self.disabled_commands:
            self.disabled_commands[channel] = {"all_disabled": False, "disabled_commands": []}

        self.disabled_commands[channel]["all_disabled"] = True
        self.save_disabled_commands()

        await ctx.send("All commands have been disabled in this channel.")

################################################################################################################# 

    @commands.command(name="enable_all")
    async def enable_all_commands(self, ctx):

        # Enable all commands in the current channel.

        channel = ctx.channel.name
        user = ctx.author.name

        if channel not in self.disabled_commands:
            self.disabled_commands[channel] = {
                "all_disabled": False,
                "disabled_commands": [],
                "commands_functions": {},
            }

        # Reset the all_disabled flag and clear disabled commands
        self.disabled_commands[channel]["all_disabled"] = False

        self.save_disabled_commands()
        await ctx.send("All commands have been enabled in this channel.")
        print(f"All commands enabled for {channel}. Updated state: {self.disabled_commands}")

################################################################################################################# 

    @commands.command()
    async def setprefix(self, ctx, new_prefix):
        # Change the prefix for the current streamer's channel.

        channel_name = ctx.channel.name.lower()
        user = ctx.author.name
        self.streamers[channel_name]["prefix"] = new_prefix

        with open(STREAMERS_FILE, "w") as f:
            json.dump(self.streamers, f, indent=4)
        
        await ctx.send(f"The command prefix has been changed to `{new_prefix}`.")

################################################################################################################# 

if __name__ == "__main__":
    bot = Bot()
    bot.run()