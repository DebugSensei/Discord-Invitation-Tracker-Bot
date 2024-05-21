from datetime import datetime
import aiosqlite
import discord
import asyncio
from discord.ext import commands
import logging
from config import TOKEN, ROLE_IDS

logging.basicConfig(level=logging.INFO)
db_lock = asyncio.Lock()

intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)
if not intents.members:
    print("Warning: Server Members Intent is not enabled. Some features may not work as expected.")

@bot.event
async def on_ready():
    print(f"{bot.user.name} is ready.")
    await setup()
    await bot.tree.sync()

async def setup():
    bot.db = await aiosqlite.connect("inviteData.db")
    await bot.db.execute("CREATE TABLE IF NOT EXISTS totals (guild_id int, inviter_id int, normal int, left int, fake int, PRIMARY KEY (guild_id, inviter_id))")
    await bot.db.execute("CREATE TABLE IF NOT EXISTS invites (guild_id int, id string, uses int, PRIMARY KEY (guild_id, id))")
    await bot.db.execute("CREATE TABLE IF NOT EXISTS joined (guild_id int, inviter_id int, joiner_id int, PRIMARY KEY (guild_id, inviter_id, joiner_id))")
    await bot.db.commit()

async def main():
    bot.db = await aiosqlite.connect("inviteData.db")
    await bot.start(TOKEN)

async def update_totals(member):
    invites = await member.guild.invites()

    c = datetime.today().strftime("%Y-%m-%d").split("-")
    c_y = int(c[0])
    c_m = int(c[1])
    c_d = int(c[2])

    async with bot.db.execute("SELECT id, uses FROM invites WHERE guild_id = ?", (member.guild.id,)) as cursor: 
        async for invite_id, old_uses in cursor:
            for invite in invites:
                if invite.id == invite_id and invite.uses - old_uses > 0: 
                    if not (c_y == member.created_at.year and c_m == member.created_at.month and c_d - member.created_at.day < 7): 
                        print(invite.id)
                        await bot.db.execute("UPDATE invites SET uses = uses + 1 WHERE guild_id = ? AND id = ?", (invite.guild.id, invite.id))
                        await bot.db.execute("INSERT OR IGNORE INTO joined (guild_id, inviter_id, joiner_id) VALUES (?,?,?)", (invite.guild.id, invite.inviter.id, member.id))
                        await bot.db.execute("UPDATE totals SET normal = normal + 1 WHERE guild_id = ? AND inviter_id = ?", (invite.guild.id, invite.inviter.id))

                    else:
                        await bot.db.execute("UPDATE totals SET normal = normal + 1, fake = fake + 1 WHERE guild_id = ? and inviter_id = ?", (invite.guild.id, invite.inviter.id))

                    return

@bot.event
async def on_member_join(member):
    # Обновляем счётчик приглашений
    await update_totals(member)
    
    # Получаем общее количество приглашений из базы данных
    cur = await bot.db.execute("SELECT normal - left - fake AS total FROM totals WHERE guild_id = ? AND inviter_id = ?", (member.guild.id, member.id))
    row = await cur.fetchone()
    
    # Если данных нет, устанавливаем total в 0
    total = row[0] if row is not None else 0
    
    # Присваиваем роли на основе total
    await assign_roles_based_on_invites(member, total)
    
    await bot.db.commit()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Oops! This command doesn't exist 😞")
    else:
        raise error 
        
@bot.event
async def on_member_remove(member):
    cur = await bot.db.execute("SELECT inviter_id FROM joined WHERE guild_id = ? and joiner_id = ?", (member.guild.id, member.id))
    res = await cur.fetchone()
    if res is None:
        return
    
    inviter_id = res[0]
    
    await bot.db.execute("DELETE FROM joined WHERE guild_id = ? AND joiner_id = ?", (member.guild.id, member.id))
    await bot.db.execute("UPDATE totals SET left = left + 1 WHERE guild_id = ? AND inviter_id = ?", (member.guild.id, inviter_id))
    await bot.db.commit()

@bot.event
async def on_invite_create(invite):
    async with db_lock:
        await bot.db.execute("INSERT OR IGNORE INTO totals (guild_id, inviter_id, normal, left, fake) VALUES (?,?,?,?,?)", (invite.guild.id, invite.inviter.id, invite.uses, 0, 0))
        await bot.db.execute("INSERT OR IGNORE INTO invites (guild_id, id, uses) VALUES (?,?,?)", (invite.guild.id, invite.id, invite.uses))
        await bot.db.commit()
    
@bot.event
async def on_invite_delete(invite):
    await bot.db.execute("DELETE FROM invites WHERE guild_id = ? AND id = ?", (invite.guild.id, invite.id))
    await bot.db.commit()

@bot.event
async def on_guild_join(guild):
    for invite in await guild.invites():
        await bot.db.execute("INSERT OR IGNORE INTO invites (guild_id, id, uses) VALUES (?,?,?)", (guild.id, invite.id, invite.uses))
        
    await bot.db.commit()
    
@bot.event
async def on_guild_remove(guild):
    await bot.db.execute("DELETE FROM totals WHERE guild_id = ?", (guild.id,))
    await bot.db.execute("DELETE FROM invites WHERE guild_id = ?", (guild.id,))
    await bot.db.execute("DELETE FROM joined WHERE guild_id = ?", (guild.id,))

    await bot.db.commit()

async def assign_roles_based_on_invites(member, total):
    supporter_role = member.guild.get_role(int(ROLE_IDS['supporter']))
    helper_role = member.guild.get_role(int(ROLE_IDS['helper']))
    legend_role = member.guild.get_role(int(ROLE_IDS['legend']))

    # Назначение ролей на основе total
    if total >= 10:
        await member.add_roles(legend_role, reason="10+ invites")
        await member.remove_roles(helper_role, supporter_role, reason="Updating to legend role")
    elif total >= 5:
        await member.add_roles(helper_role, reason="5+ invites")
        await member.remove_roles(supporter_role, reason="Updating to helper role")
    elif total >= 1:
        await member.add_roles(supporter_role, reason="1+ invites")
    pass
@bot.tree.command(name="invites", description="Show users invites!")
async def invites(interaction: discord.Interaction, member: discord.Member = None):
    if member is None:
        member = interaction.user

    cur = await bot.db.execute("SELECT normal, left, fake FROM totals WHERE guild_id = ? AND inviter_id = ?", (interaction.guild_id, member.id))
    res = await cur.fetchone()
    if res is None:
        normal, left, fake = 0, 0, 0
    else:
        normal, left, fake = res

    total = normal - (left + fake)
    await assign_roles_based_on_invites(member, total)
    # Ответ во взаимодействие с информацией о приглашениях
    em = discord.Embed(
        title=f"Invites for {member.display_name}",
        description=f"{member.mention} currently has **{total}** invites. (**{normal}** Regular, **{left}** Left, **{fake}** Fake).",
        timestamp=datetime.now(),
        colour=discord.Colour.orange())
    await interaction.response.send_message(embed=em)

class LeaderboardEntry:
    def __init__(self, inviter_id, total_invites, nickname=None):
        self.inviter_id = inviter_id
        self.total_invites = total_invites
        self.nickname = nickname or "Unknown Member"

async def get_top_inviters(guild, limit=10):
    await guild.chunk() 
    async with bot.db.execute("SELECT inviter_id, SUM(normal) AS total FROM totals WHERE guild_id = ? GROUP BY inviter_id ORDER BY total DESC LIMIT ?", (guild.id, limit)) as cursor:
        leaderboard_entries = []
        async for row in cursor:
            inviter_id, total_invites = row
            print(f"Inviter ID: {inviter_id}, Total Invites: {total_invites}")  
            member = guild.get_member(inviter_id)
            if member: 
                leaderboard_entries.append(LeaderboardEntry(inviter_id, total_invites, member.display_name))
        return leaderboard_entries

@bot.tree.command(name="leaderstats", description="Top 10 inviters on the server!")
async def leaderstats(interaction: discord.Interaction):
    guild = interaction.guild
    await guild.chunk()
    
    top_inviters = []
    
    members = guild.members
    
    for member in members:
        await update_totals(member)
        
        cur = await bot.db.execute("SELECT normal, left, fake FROM totals WHERE guild_id = ? AND inviter_id = ?", (interaction.guild_id, member.id))
        res = await cur.fetchone()
        
        if res is None:
            continue

        normal, left, fake = res
        total = normal - (left + fake)
        top_inviters.append(LeaderboardEntry(member.id, total, member.display_name))
    
    top_inviters.sort(key=lambda x: x.total_invites, reverse=True)
    
    description_lines = []
    for index, entry in enumerate(top_inviters[:10], start=1):
        description_lines.append(f"{index}) {entry.nickname} ({entry.total_invites} invites)")
    
    description = "\n".join(description_lines) if description_lines else "No data available."
    embed = discord.Embed(title="Top 10 Inviters", description=description, color=discord.Color.orange())
    
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_disconnect():
    await bot.db.close()

bot.run(TOKEN)