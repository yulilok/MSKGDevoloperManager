# remove_commands.py
import asyncio
import os
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)

async def main():
    intents = discord.Intents.default()
    bot = discord.Client(intents=intents)
    tree = app_commands.CommandTree(bot)
    
    @bot.event
    async def on_ready():
        print(f"Bot ready as {bot.user} ({bot.user.id})")

        # Clear global commands
        await tree.sync()
        print("Global commands cleared")

        # Clear guild commands for configured guild, or all joined guilds.
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            tree.clear_commands(guild=guild_obj)
            print(f"Guild commands cleared for guild {GUILD_ID}")
        else:
            for g in bot.guilds:
                guild_obj = discord.Object(id=g.id)
                tree.clear_commands(guild=guild_obj)
                print(f"Guild commands cleared for guild {g.id}")

        await bot.close()
    
    await bot.start(TOKEN)

asyncio.run(main())