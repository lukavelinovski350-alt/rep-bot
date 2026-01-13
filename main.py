import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
import os
from typing import Optional, Dict, List
from dotenv import load_dotenv
import logging
import atexit
import signal
import psycopg2
from psycopg2.extras import Json
import json

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class Config:
    OWNER_ID = 1029438856069656576
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    DATABASE_URL = os.getenv('DATABASE_URL')  # ⭐ PostgreSQL URL from Render
    PREFIX = '!'
    PORT = int(os.getenv('PORT', 8080))
    
    # Reputation Settings
    VOUCH_REP_AMOUNT = 3
    VOUCH_COOLDOWN = 600
    LEADERBOARD_PER_PAGE = 10
    
    # Staff role IDs
    STAFF_ROLE_IDS = [
        1445260636618756198,  # MOD
        1445260631358836799,  # Head Mod
        1445260626904612944,  # ADMIN
        1445260616695681075,  # CO OWNER
        1445260607392714752   # OWNER
    ]
    
    # Feature settings
    HELPVOUCH_REP_MEMBER = 1
    HELPVOUCH_REP_STAFF = 2
    DUMMY_PER_DAY = 3
    DUMMY_REP_REMOVE = 3

class DatabaseManager:
    """PostgreSQL Database - DATA PERSISTS FOREVER"""
    
    def __init__(self):
        self.db_url = Config.DATABASE_URL
        
        if not self.db_url:
            logging.error("❌ DATABASE_URL not set!")
            raise ValueError("DATABASE_URL environment variable required")
        
        self.conn = None
        self.connect()
        self.init_database()
        logging.info("✅ Database connected")
    
    def connect(self):
        """Connect to PostgreSQL"""
        try:
            self.conn = psycopg2.connect(self.db_url)
            self.conn.autocommit = True
            logging.info("✅ PostgreSQL connected")
        except Exception as e:
            logging.error(f"❌ Connection failed: {e}")
            raise
    
    def init_database(self):
        """Create tables"""
        try:
            cursor = self.conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    reputation INTEGER DEFAULT 0,
                    is_blacklisted BOOLEAN DEFAULT FALSE
                )
            ''')
            
            # Vouches table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vouches (
                    id SERIAL PRIMARY KEY,
                    target_id BIGINT NOT NULL,
                    voucher_id BIGINT NOT NULL,
                    reason TEXT NOT NULL,
                    rep_amount INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Cooldowns table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_id BIGINT PRIMARY KEY,
                    last_vouch TIMESTAMP NOT NULL
                )
            ''')
            
            # Dummy usage table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS dummy_usage (
                    user_id BIGINT PRIMARY KEY,
                    usage_date DATE NOT NULL,
                    count INTEGER DEFAULT 0
                )
            ''')
            
            # Helpvouches table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS helpvouches (
                    id SERIAL PRIMARY KEY,
                    target_id BIGINT NOT NULL,
                    helper_id BIGINT NOT NULL,
                    amount INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # scam table
            cursor.execute('''
               CREATE TABLE IF NOT EXISTS scammer_reports (
                   id SERIAL PRIMARY KEY,
                   user_id BIGINT NOT NULL,
                   reporter_id BIGINT NOT NULL,
                   reason TEXT NOT NULL,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
              )
          ''')
            
            cursor.close()
            logging.info("✅ Database tables created")
            
        except Exception as e:
            logging.error(f"❌ Table creation failed: {e}")
            raise
    
    # ========================================
    # REPUTATION FUNCTIONS
    # ========================================
    
    def get_reputation(self, user_id: int) -> int:
        """Get user reputation"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('SELECT reputation FROM users WHERE user_id = %s', (user_id,))
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
        except Exception as e:
            logging.error(f"Error getting rep: {e}")
            return 0
    
    def add_reputation(self, user_id: int, amount: int):
        """Add reputation"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, reputation)
                VALUES (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET reputation = users.reputation + %s
            ''', (user_id, amount, amount))
            cursor.close()
        except Exception as e:
            logging.error(f"Error adding rep: {e}")
    
    def remove_reputation(self, user_id: int, amount: int):
        """Remove reputation"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, reputation)
                VALUES (%s, 0)
                ON CONFLICT (user_id)
                DO UPDATE SET reputation = GREATEST(0, users.reputation - %s)
            ''', (user_id, amount))
            cursor.close()
        except Exception as e:
            logging.error(f"Error removing rep: {e}")
    
    def set_reputation(self, user_id: int, amount: int):
        """Set reputation"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, reputation)
                VALUES (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET reputation = %s
            ''', (user_id, amount, amount))
            cursor.close()
        except Exception as e:
            logging.error(f"Error setting rep: {e}")
    
    def clear_reputation(self, user_id: int):
        """Clear user data"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM users WHERE user_id = %s', (user_id,))
            cursor.execute('DELETE FROM vouches WHERE target_id = %s', (user_id,))
            cursor.execute('DELETE FROM helpvouches WHERE target_id = %s', (user_id,))
            cursor.close()
        except Exception as e:
            logging.error(f"Error clearing rep: {e}")
    
    def get_leaderboard(self) -> List[tuple]:
        """Get leaderboard"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT user_id, reputation 
                FROM users 
                WHERE reputation > 0
                ORDER BY reputation DESC
            ''')
            result = cursor.fetchall()
            cursor.close()
            return result
        except Exception as e:
            logging.error(f"Error getting leaderboard: {e}")
            return []
    
    # ========================================
    # VOUCH FUNCTIONS
    # ========================================
    
    def add_vouch(self, target_id: int, voucher_id: int, reason: str):
        """Add vouch"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO vouches (target_id, voucher_id, reason, rep_amount)
                VALUES (%s, %s, %s, %s)
            ''', (target_id, voucher_id, reason, Config.VOUCH_REP_AMOUNT))
            
            # Update cooldown
            cursor.execute('''
                INSERT INTO cooldowns (user_id, last_vouch)
                VALUES (%s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id)
                DO UPDATE SET last_vouch = CURRENT_TIMESTAMP
            ''', (voucher_id,))
            
            cursor.close()
        except Exception as e:
            logging.error(f"Error adding vouch: {e}")
    
    def get_vouch_cooldown(self, user_id: int) -> Optional[float]:
        """Get cooldown in seconds"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_vouch))
                FROM cooldowns WHERE user_id = %s
            ''', (user_id,))
            result = cursor.fetchone()
            cursor.close()
            
            if not result:
                return None
            
            time_passed = result[0]
            if time_passed >= Config.VOUCH_COOLDOWN:
                return None
            
            return Config.VOUCH_COOLDOWN - time_passed
        except Exception as e:
            logging.error(f"Error getting cooldown: {e}")
            return None
    
    def get_vouch_history(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Get vouch history"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT voucher_id, reason, rep_amount, created_at
                FROM vouches
                WHERE target_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            ''', (user_id, limit))
            results = cursor.fetchall()
            cursor.close()
            
            return [{
                'voucher': row[0],
                'reason': row[1],
                'rep_amount': row[2],
                'timestamp': row[3].isoformat()
            } for row in results]
        except Exception as e:
            logging.error(f"Error getting history: {e}")
            return []

"""
SCAMMER SYSTEM - PART 1: Database Functions
Add this to your DatabaseManager class (after the helpvouch function
"""

# ========================================
# SCAMMER FUNCTIONS - Add these to DatabaseManager class
# ========================================

def add_scammer_report(self, user_id: int, reporter_id: int, reason: str):
    """Add a scammer report (Staff only)"""
    try:
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO scammer_reports (user_id, reporter_id, reason)
            VALUES (%s, %s, %s)
        ''', (user_id, reporter_id, reason))
        cursor.close()
        logging.info(f"Scammer report added: {reporter_id} -> {user_id}")
    except Exception as e:
        logging.error(f"Error adding scammer report: {e}")

def get_scammer_reports(self, user_id: int) -> List[Dict]:
    """Get all scammer reports for a user"""
    try:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, reporter_id, reason, created_at
            FROM scammer_reports
            WHERE user_id = %s
            ORDER BY created_at DESC
        ''', (user_id,))
        results = cursor.fetchall()
        cursor.close()
        
        return [{
            'id': row[0],
            'reporter': row[1],
            'reason': row[2],
            'timestamp': row[3].isoformat()
        } for row in results]
    except Exception as e:
        logging.error(f"Error getting scammer reports: {e}")
        return []

def remove_scammer_report(self, report_id: int):
    """Remove a specific scammer report by ID"""
    try:
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM scammer_reports WHERE id = %s
        ''', (report_id,))
        cursor.close()
        logging.info(f"Scammer report {report_id} removed")
    except Exception as e:
        logging.error(f"Error removing scammer report: {e}")

def clear_all_scammer_reports(self, user_id: int):
    """Clear all scammer reports for a user"""
    try:
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM scammer_reports WHERE user_id = %s
        ''', (user_id,))
        cursor.close()
        logging.info(f"All scammer reports cleared for user {user_id}")
    except Exception as e:
        logging.error(f"Error clearing scammer reports: {e}")

def get_all_scammers(self) -> List[tuple]:
    """Get all users who have scammer reports"""
    try:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT user_id, COUNT(*) as report_count
            FROM scammer_reports
            GROUP BY user_id
            ORDER BY report_count DESC
        ''')
        results = cursor.fetchall()
        cursor.close()
        return results
    except Exception as e:
        logging.error(f"Error getting all scammers: {e}")
        return []

def is_reported_scammer(self, user_id: int) -> bool:
    """Check if user has any scammer reports"""
    try:
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM scammer_reports WHERE user_id = %s
        ''', (user_id,))
        result = cursor.fetchone()
        cursor.close()
        return result[0] > 0 if result else False
    except Exception as e:
        logging.error(f"Error checking scammer status: {e}")
        return False

    
    # ========================================
    # BLACKLIST FUNCTIONS
    # ========================================
    
    def is_blacklisted(self, user_id: int) -> bool:
        """Check if blacklisted"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT is_blacklisted FROM users WHERE user_id = %s
            ''', (user_id,))
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else False
        except Exception as e:
            logging.error(f"Error checking blacklist: {e}")
            return False
    
    def add_to_blacklist(self, user_id: int):
        """Add to blacklist"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO users (user_id, is_blacklisted)
                VALUES (%s, TRUE)
                ON CONFLICT (user_id)
                DO UPDATE SET is_blacklisted = TRUE
            ''', (user_id,))
            cursor.close()
        except Exception as e:
            logging.error(f"Error adding to blacklist: {e}")
    
    def remove_from_blacklist(self, user_id: int):
        """Remove from blacklist"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                UPDATE users SET is_blacklisted = FALSE WHERE user_id = %s
            ''', (user_id,))
            cursor.close()
        except Exception as e:
            logging.error(f"Error removing from blacklist: {e}")
    
    def get_blacklist(self) -> List[int]:
        """Get all blacklisted users"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT user_id FROM users WHERE is_blacklisted = TRUE
            ''')
            results = cursor.fetchall()
            cursor.close()
            return [row[0] for row in results]
        except Exception as e:
            logging.error(f"Error getting blacklist: {e}")
            return []
    
    # ========================================
    # DUMMY FUNCTIONS
    # ========================================
    
    def can_use_dummy(self, user_id: int) -> tuple[bool, int]:
        """Check dummy usage"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT count, usage_date FROM dummy_usage WHERE user_id = %s
            ''', (user_id,))
            result = cursor.fetchone()
            cursor.close()
            
            today = datetime.utcnow().date()
            
            if not result:
                return True, Config.DUMMY_PER_DAY
            
            count, usage_date = result
            
            if usage_date != today:
                return True, Config.DUMMY_PER_DAY
            
            remaining = Config.DUMMY_PER_DAY - count
            return remaining > 0, remaining
            
        except Exception as e:
            logging.error(f"Error checking dummy: {e}")
            return True, Config.DUMMY_PER_DAY
    
    def use_dummy(self, user_id: int):
        """Record dummy usage"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO dummy_usage (user_id, usage_date, count)
                VALUES (%s, CURRENT_DATE, 1)
                ON CONFLICT (user_id)
                DO UPDATE SET 
                    count = CASE 
                        WHEN dummy_usage.usage_date = CURRENT_DATE 
                        THEN dummy_usage.count + 1 
                        ELSE 1 
                    END,
                    usage_date = CURRENT_DATE
            ''', (user_id,))
            cursor.close()
        except Exception as e:
            logging.error(f"Error using dummy: {e}")
    
    # ========================================
    # HELPVOUCH FUNCTIONS
    # ========================================
    
    def add_helpvouch(self, target_id: int, helper_id: int, amount: int):
        """Add helpvouch"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO helpvouches (target_id, helper_id, amount)
                VALUES (%s, %s, %s)
            ''', (target_id, helper_id, amount))
            cursor.close()
        except Exception as e:
            logging.error(f"Error adding helpvouch: {e}")

# Initialize database
db = DatabaseManager()
  
# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=Config.PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True
)

def is_owner():
    async def predicate(ctx):
        if ctx.author.id != Config.OWNER_ID:
            await ctx.send("Only the bot owner can use this command.")
            return False
        return True
    return commands.check(predicate)

def has_staff_role(member: discord.Member) -> bool:
    """Check if member has staff role"""
    return any(role.id in Config.STAFF_ROLE_IDS for role in member.roles)

def format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

@bot.event
async def on_ready():
    print('=' * 70)
    print(f'✅ Bot Online: {bot.user}')
    print(f'Servers: {len(bot.guilds)}')
    print(f'Prefix: {Config.PREFIX}')
    print(f'Database: PostgreSQL ✅')
    print('=' * 70)
    
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} slash commands')
    except Exception as e:
        logging.error(f'Sync failed: {e}')
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{Config.PREFIX}help | PostgreSQL"
        ),
        status=discord.Status.online
    )
    
    print('All systems active!')
    print('=' * 70)

# ========================================
# LEADERBOARD VIEW
# ========================================

class LeaderboardView(View):
    def __init__(self, ctx, pages: List[discord.Embed], timeout=180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.pages = pages
        self.current_page = 0
        self.message = None
        self.update_buttons()
    
    def update_buttons(self):
        self.first_page.disabled = self.current_page == 0
        self.prev_page.disabled = self.current_page == 0
        self.next_page.disabled = self.current_page == len(self.pages) - 1
        self.last_page.disabled = self.current_page == len(self.pages) - 1
    
    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.gray)
    async def first_page(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only command user can control this.", ephemeral=True)
            return
        self.current_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
    
    @discord.ui.button(label="◀️", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only command user can control this.", ephemeral=True)
            return
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
    
    @discord.ui.button(label="▶️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only command user can control this.", ephemeral=True)
            return
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
    
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.gray)
    async def last_page(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only command user can control this.", ephemeral=True)
            return
        self.current_page = len(self.pages) - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
    
    @discord.ui.button(label="🗑️", style=discord.ButtonStyle.danger)
    async def delete_message(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only command user can delete this.", ephemeral=True)
            return
        await interaction.message.delete()
        self.stop()
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except:
                pass

def create_leaderboard_pages(leaderboard: List[tuple], bot: commands.Bot) -> List[discord.Embed]:
    if not leaderboard:
        embed = discord.Embed(
            title="📊 Reputation Leaderboard",
            description="No reputation data yet. Start vouching!",
            color=discord.Color.blue()
        )
        return [embed]
    
    pages = []
    total_pages = (len(leaderboard) + Config.LEADERBOARD_PER_PAGE - 1) // Config.LEADERBOARD_PER_PAGE
    
    for page_num in range(total_pages):
        start_idx = page_num * Config.LEADERBOARD_PER_PAGE
        end_idx = start_idx + Config.LEADERBOARD_PER_PAGE
        page_data = leaderboard[start_idx:end_idx]
        
        embed = discord.Embed(
            title="📊 Reputation Leaderboard",
            description="Users ranked by reputation points",
            color=discord.Color.gold()
        )
        
        leaderboard_text = []
        for idx, (user_id, rep) in enumerate(page_data, start=start_idx + 1):
            user = bot.get_user(user_id)
            
            if idx == 1:
                medal = "🥇"
            elif idx == 2:
                medal = "🥈"
            elif idx == 3:
                medal = "🥉"
            else:
                medal = f"`#{idx}`"
            
            user_name = user.name if user else f"Unknown User"
            leaderboard_text.append(f"{medal} **{user_name}** - {rep} rep")
        
        embed.add_field(
            name=f"Rankings {start_idx + 1}-{start_idx + len(page_data)}",
            value="\n".join(leaderboard_text),
            inline=False
        )
        
        embed.set_footer(text=f"Page {page_num + 1}/{total_pages} | Total: {len(leaderboard)}")
        
        pages.append(embed)
    
    return pages

# ========================================
# BASIC COMMANDS
# ========================================

@bot.command(name='leaderboard', aliases=['lb', 'top'])
async def leaderboard_cmd(ctx):
    """View reputation leaderboard"""
    leaderboard = db.get_leaderboard()
    
    if not leaderboard:
        embed = discord.Embed(
            title="📊 Reputation Leaderboard",
            description="No reputation data yet. Use `!vouch @user reason` to give reputation!",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        return
    
    pages = create_leaderboard_pages(leaderboard, bot)
    
    if len(pages) == 1:
        await ctx.send(embed=pages[0])
    else:
        view = LeaderboardView(ctx, pages)
        view.message = await ctx.send(embed=pages[0], view=view)

@bot.command(name='rank', aliases=['rep', 'reputation'])
async def rank_cmd(ctx, member: discord.Member = None):
    """Check reputation"""
    member = member or ctx.author
    
    rep = db.get_reputation(member.id)
    leaderboard = db.get_leaderboard()
    
    rank = None
    for idx, (user_id, _) in enumerate(leaderboard, 1):
        if user_id == member.id:
            rank = idx
            break
    
    embed = discord.Embed(
        title=f"{member.display_name}'s Reputation",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    
    embed.add_field(name="Reputation", value=f"⭐ {rep}", inline=True)
    embed.add_field(name="Rank", value=f"#{rank}" if rank else "Unranked", inline=True)
    embed.add_field(name="Total Users", value=str(len(leaderboard)), inline=True)
    
    recent_vouches = db.get_vouch_history(member.id, limit=5)
    if recent_vouches:
        vouch_text = []
        for vouch in recent_vouches:
            voucher = bot.get_user(vouch['voucher'])
            voucher_name = voucher.name if voucher else "Unknown"
            vouch_text.append(f"**{voucher_name}**: {vouch['reason']}")
        
        embed.add_field(
            name="Recent Vouches (Last 5)",
            value="\n".join(vouch_text[:5]),
            inline=False
        )
    
    embed.set_footer(text=f"Requested by {ctx.author.name}")
    await ctx.send(embed=embed)

@bot.command(name='cooldown', aliases=['cd'])
async def cooldown_cmd(ctx):
    """Check vouch cooldown"""
    cooldown = db.get_vouch_cooldown(ctx.author.id)
    
    embed = discord.Embed(
        title="⏰ Vouch Cooldown",
        color=discord.Color.blue()
    )
    
    if cooldown is None:
        embed.description = "✅ You can vouch now!"
        embed.add_field(
            name="Usage",
            value=f"`{Config.PREFIX}vouch @user reason`",
            inline=False
        )
    else:
        time_remaining = format_time(cooldown)
        embed.description = f"You can vouch again in **{time_remaining}**"
        embed.add_field(
            name="Cooldown Duration",
            value=f"{Config.VOUCH_COOLDOWN // 60} minutes",
            inline=False
        )
    
    embed.set_footer(text=f"Requested by {ctx.author.name}")
    await ctx.send(embed=embed)

# ========================================
# VOUCH COMMAND
# ========================================

@bot.command(name='vouch')
async def vouch_cmd(ctx, member: discord.Member, *, reason: str = None):
    """Vouch for a user and give them reputation"""
    
    # Check if blacklisted
    if db.is_blacklisted(ctx.author.id):
        embed = discord.Embed(
            title="🚫 You Are Blacklisted",
            description="You have been blacklisted from using the vouch command.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    if not reason or len(reason.strip()) < 3:
        disclaimer_embed = discord.Embed(
            title="⚠️ Vouch Reason Required",
            description=(
                "You must provide a valid reason when vouching for someone.\n\n"
                "**Proper Usage:**\n"
                "`!vouch @user reason for vouching`\n\n"
                "**Examples:**\n"
                "✅ `!vouch @John Great trader, smooth deal!`\n"
                "✅ `!vouch @Sarah Trustworthy and fast service`\n"
                "❌ `!vouch @Mike`\n"
                "❌ `!vouch @Alex good`\n\n"
                "⚠️ **WARNING:** Vouching without a valid reason will result in punishment."
            ),
            color=discord.Color.red()
        )
        await ctx.send(embed=disclaimer_embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            title="❌ Cannot Vouch Yourself",
            description="You cannot vouch for yourself!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            title="❌ Cannot Vouch Bots",
            description="You cannot vouch for bots!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    cooldown = db.get_vouch_cooldown(ctx.author.id)
    if cooldown is not None:
        time_remaining = format_time(cooldown)
        
        embed = discord.Embed(
            title="⏰ Vouch Cooldown Active",
            description=f"You can vouch again in **{time_remaining}**",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Cooldown",
            value=f"You can vouch once every {Config.VOUCH_COOLDOWN // 60} minutes",
            inline=False
        )
        embed.set_footer(text=f"Requested by {ctx.author.name}")
        await ctx.send(embed=embed)
        return
    
    # Add reputation and vouch
    db.add_reputation(member.id, Config.VOUCH_REP_AMOUNT)
    db.add_vouch(member.id, ctx.author.id, reason)
    
    new_rep = db.get_reputation(member.id)
    
    embed = discord.Embed(
        title="✅ Vouch Successful",
        description=f"{ctx.author.mention} vouched for {member.mention}",
        color=discord.Color.green()
    )
    
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Reputation Given", value=f"+{Config.VOUCH_REP_AMOUNT} ⭐", inline=True)
    embed.add_field(name="New Total", value=f"{new_rep} ⭐", inline=True)
    embed.add_field(
        name="Next Vouch",
        value=f"Available in {Config.VOUCH_COOLDOWN // 60} minutes",
        inline=False
    )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Vouched by {ctx.author.name}")
    await ctx.send(embed=embed)
    
    # Try to DM the user
    try:
        dm_embed = discord.Embed(
            title="🎉 You Received a Vouch!",
            description=f"**{ctx.author.name}** vouched for you in **{ctx.guild.name}**",
            color=discord.Color.gold()
        )
        dm_embed.add_field(name="Reason", value=reason, inline=False)
        dm_embed.add_field(name="Reputation Gained", value=f"+{Config.VOUCH_REP_AMOUNT} ⭐", inline=True)
        dm_embed.add_field(name="Total Reputation", value=f"{new_rep} ⭐", inline=True)
        await member.send(embed=dm_embed)
    except:
        pass

@bot.command(name='vouchhistory', aliases=['vh', 'vouches'])
async def vouch_history_cmd(ctx, member: discord.Member = None):
    """View vouch history for a user"""
    member = member or ctx.author
    
    vouches = db.get_vouch_history(member.id, limit=10)
    
    if not vouches:
        embed = discord.Embed(
            title=f"{member.display_name}'s Vouch History",
            description="No vouches yet.",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title=f"{member.display_name}'s Vouch History",
        description=f"Showing last {len(vouches)} vouches",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    
    for idx, vouch in enumerate(vouches, 1):
        voucher = bot.get_user(vouch['voucher'])
        voucher_name = voucher.name if voucher else "Unknown User"
        
        embed.add_field(
            name=f"Vouch #{idx} - {voucher_name}",
            value=f"**Reason:** {vouch['reason']}\n**Rep Given:** +{vouch['rep_amount']} ⭐",
            inline=False
        )
    
    total_rep = db.get_reputation(member.id)
    embed.set_footer(text=f"Total Reputation: {total_rep} ⭐")
    await ctx.send(embed=embed)

# ========================================
# DUMMY COMMAND
# ========================================

@bot.command(name='dummy')
async def dummy_cmd(ctx, member: discord.Member):
    """Remove 3 rep from a user (3 times per day limit)"""
    
    can_use, remaining = db.can_use_dummy(ctx.author.id)
    
    if not can_use:
        embed = discord.Embed(
            title="❌ Daily Limit Reached",
            description=f"You have used all {Config.DUMMY_PER_DAY} dummy commands for today.",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Reset Time",
            value="Resets at 00:00 UTC",
            inline=False
        )
        embed.set_footer(text=f"Requested by {ctx.author.name}")
        await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            title="❌ Cannot Dummy Yourself",
            description="You cannot use dummy on yourself!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            title="❌ Cannot Dummy Bots",
            description="You cannot use dummy on bots!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    old_rep = db.get_reputation(member.id)
    db.remove_reputation(member.id, Config.DUMMY_REP_REMOVE)
    db.use_dummy(ctx.author.id)
    new_rep = db.get_reputation(member.id)
    
    embed = discord.Embed(
        title="💥 Dummy Used",
        description=f"{ctx.author.mention} used dummy on {member.mention}",
        color=discord.Color.orange()
    )
    
    embed.add_field(name="Previous Rep", value=f"{old_rep} ⭐", inline=True)
    embed.add_field(name="Removed", value=f"-{Config.DUMMY_REP_REMOVE} ⭐", inline=True)
    embed.add_field(name="New Total", value=f"{new_rep} ⭐", inline=True)
    embed.add_field(
        name="Remaining Uses Today",
        value=f"{remaining - 1}/{Config.DUMMY_PER_DAY}",
        inline=False
    )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Used by {ctx.author.name}")
    await ctx.send(embed=embed)

# ========================================
# HELPVOUCH COMMAND
# ========================================

@bot.command(name='helpvouch', aliases=['hv'])
async def helpvouch_cmd(ctx, member: discord.Member):
    """Give reputation (Staff: 2 rep | Members: 1 rep)"""
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            title="❌ Cannot Helpvouch Yourself",
            description="You cannot helpvouch yourself!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            title="❌ Cannot Helpvouch Bots",
            description="You cannot helpvouch bots!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    # Check if user has staff role
    is_staff = has_staff_role(ctx.author)
    rep_amount = Config.HELPVOUCH_REP_STAFF if is_staff else Config.HELPVOUCH_REP_MEMBER
    
    old_rep = db.get_reputation(member.id)
    db.add_reputation(member.id, rep_amount)
    db.add_helpvouch(member.id, ctx.author.id, rep_amount)
    new_rep = db.get_reputation(member.id)
    
    embed = discord.Embed(
        title="✅ Helpvouch Successful",
        description=f"{ctx.author.mention} helped {member.mention}",
        color=discord.Color.green()
    )
    
    if is_staff:
        embed.add_field(
            name="🛡️ Staff Bonus",
            value=f"Staff members give **{Config.HELPVOUCH_REP_STAFF} ⭐** (Regular members give {Config.HELPVOUCH_REP_MEMBER} ⭐)",
            inline=False
        )
    else:
        embed.add_field(
            name="ℹ️ Member Helpvouch",
            value=f"You gave **{Config.HELPVOUCH_REP_MEMBER} ⭐** (Staff members give {Config.HELPVOUCH_REP_STAFF} ⭐)",
            inline=False
        )
    
    embed.add_field(name="Previous Rep", value=f"{old_rep} ⭐", inline=True)
    embed.add_field(name="Added", value=f"+{rep_amount} ⭐", inline=True)
    embed.add_field(name="New Total", value=f"{new_rep} ⭐", inline=True)
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Helped by {ctx.author.name}")
    await ctx.send(embed=embed)

# ========================================
# BLACKLIST COMMAND
# ========================================

@bot.command(name='repblacklist', aliases=['blacklist', 'bl'])
@is_owner()
async def repblacklist_cmd(ctx, member: discord.Member):
    """Blacklist/unblacklist a user from using vouch (Owner only)"""
    
    if db.is_blacklisted(member.id):
        db.remove_from_blacklist(member.id)
        
        embed = discord.Embed(
            title="✅ User Unblacklisted",
            description=f"{member.mention} can now use the vouch command again.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Unblacklisted by {ctx.author.name}")
        
        await ctx.send(embed=embed)
        logging.info(f"Owner {ctx.author.name} unblacklisted {member.name}")
    else:
        db.add_to_blacklist(member.id)
        
        embed = discord.Embed(
            title="🚫 User Blacklisted",
            description=f"{member.mention} can no longer use the vouch command.",
            color=discord.Color.red()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Blacklisted by {ctx.author.name}")
        
        await ctx.send(embed=embed)
        logging.info(f"Owner {ctx.author.name} blacklisted {member.name}")

@bot.command(name='viewblacklist', aliases=['vbl'])
@is_owner()
async def viewblacklist_cmd(ctx):
    """View all blacklisted users (Owner only)"""
    blacklist = db.get_blacklist()
    
    if not blacklist:
        embed = discord.Embed(
            title="📋 Blacklist",
            description="No users are currently blacklisted.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="📋 Blacklisted Users",
        description=f"Total: {len(blacklist)} users",
        color=discord.Color.red()
    )
    
    blacklist_text = []
    for user_id in blacklist:
        user = bot.get_user(user_id)
        user_name = user.name if user else f"Unknown User ({user_id})"
        blacklist_text.append(f"• {user_name}")
    
    embed.add_field(
        name="Blacklisted Users",
        value="\n".join(blacklist_text[:25]),
        inline=False
    )
    
    if len(blacklist) > 25:
        embed.set_footer(text=f"Showing 25 of {len(blacklist)} users")
    
    await ctx.send(embed=embed)

"""
SCAMMER SYSTEM - PART 2: Commands
Add these commands to your bot (after the blacklist commands)
"""

# ========================================
# APPLY SCAMMER COMMAND (STAFF ONLY)
# ========================================

def is_staff():
    """Check if user has staff role"""
    async def predicate(ctx):
        if not has_staff_role(ctx.author):
            embed = discord.Embed(
                title="🚫 Staff Only",
                description="Only staff members can use this command.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return False
        return True
    return commands.check(predicate)

@bot.command(name='applyscammer', aliases=['reportscammer', 'addscammer'])
@is_staff()
async def applyscammer_cmd(ctx, member: discord.Member, *, reason: str = None):
    """Report a user as a scammer (STAFF ONLY)"""
    
    if not reason or len(reason.strip()) < 5:
        embed = discord.Embed(
            title="⚠️ Reason Required",
            description="You must provide a detailed reason when reporting a scammer.",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Usage",
            value=f"`{Config.PREFIX}applyscammer @user detailed reason here`",
            inline=False
        )
        embed.add_field(
            name="Example",
            value=f"`{Config.PREFIX}applyscammer @John He scammed me for $50 via fake PayPal`",
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    if member.id == ctx.author.id:
        embed = discord.Embed(
            title="❌ Cannot Report Yourself",
            description="You cannot report yourself as a scammer!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    if member.bot:
        embed = discord.Embed(
            title="❌ Cannot Report Bots",
            description="You cannot report bots as scammers!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    # Add scammer report to database
    db.add_scammer_report(member.id, ctx.author.id, reason)
    
    # Get total reports for this user
    reports = db.get_scammer_reports(member.id)
    total_reports = len(reports)
    
    embed = discord.Embed(
        title="🚨 Scammer Report Added",
        description=f"{member.mention} has been reported as a scammer",
        color=discord.Color.red()
    )
    
    embed.add_field(name="Reported By", value=ctx.author.mention, inline=True)
    embed.add_field(name="Total Reports", value=f"{total_reports} 🚩", inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Report ID: {reports[0]['id']} | Reported by {ctx.author.name}")
    
    await ctx.send(embed=embed)
    
    logging.info(f"Staff {ctx.author.name} reported {member.name} as scammer")

# ========================================
# SCAM COMMAND (ANYONE CAN USE)
# ========================================

@bot.command(name='scam', aliases=['scammer', 'checkscammer'])
async def scam_cmd(ctx, member: discord.Member):
    """Check if a user has been reported as a scammer (Anyone can use)"""
    
    reports = db.get_scammer_reports(member.id)
    
    if not reports:
        embed = discord.Embed(
            title="✅ No Scammer Reports",
            description=f"{member.mention} has **no scammer reports**.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Checked by {ctx.author.name}")
        await ctx.send(embed=embed)
        return
    
    # User has scammer reports - show them all
    embed = discord.Embed(
        title="🚨 SCAMMER ALERT 🚨",
        description=f"{member.mention} has been reported as a scammer!",
        color=discord.Color.dark_red()
    )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    
    # Add each report
    for idx, report in enumerate(reports, 1):
        reporter = bot.get_user(report['reporter'])
        reporter_name = reporter.name if reporter else "Unknown Staff"
        
        # Parse timestamp
        timestamp = datetime.fromisoformat(report['timestamp'])
        time_str = timestamp.strftime('%Y-%m-%d %H:%M UTC')
        
        embed.add_field(
            name=f"🚩 Report #{idx} - By {reporter_name}",
            value=f"**Reason:** {report['reason']}\n**Date:** {time_str}\n**Report ID:** `{report['id']}`",
            inline=False
        )
    
    embed.add_field(
        name="⚠️ WARNING",
        value=f"This user has **{len(reports)} scammer report(s)**. Exercise extreme caution!",
        inline=False
    )
    
    embed.set_footer(text=f"Checked by {ctx.author.name} | Total Reports: {len(reports)}")
    
    await ctx.send(embed=embed)

# ========================================
# REMOVE SCAMMER REPORT (STAFF ONLY)
# ========================================

@bot.command(name='removescammer', aliases=['deletescammer', 'clearscam'])
@is_staff()
async def removescammer_cmd(ctx, member: discord.Member, report_id: int = None):
    """Remove a scammer report (STAFF ONLY)"""
    
    reports = db.get_scammer_reports(member.id)
    
    if not reports:
        embed = discord.Embed(
            title="❌ No Reports Found",
            description=f"{member.mention} has no scammer reports.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    # If no report_id provided, show all reports and ask which one to delete
    if report_id is None:
        embed = discord.Embed(
            title=f"📋 Scammer Reports for {member.display_name}",
            description="Use the Report ID to remove a specific report",
            color=discord.Color.blue()
        )
        
        for idx, report in enumerate(reports, 1):
            reporter = bot.get_user(report['reporter'])
            reporter_name = reporter.name if reporter else "Unknown"
            
            embed.add_field(
                name=f"Report #{idx} - ID: {report['id']}",
                value=f"**By:** {reporter_name}\n**Reason:** {report['reason'][:100]}...",
                inline=False
            )
        
        embed.add_field(
            name="💡 How to Remove",
            value=f"`{Config.PREFIX}removescammer @{member.name} <Report_ID>`\n\n"
                  f"**Example:** `{Config.PREFIX}removescammer @{member.name} {reports[0]['id']}`\n\n"
                  f"Or use `{Config.PREFIX}clearallscam @{member.name}` to remove ALL reports",
            inline=False
        )
        
        await ctx.send(embed=embed)
        return
    
    # Check if report_id exists for this user
    report_exists = any(r['id'] == report_id for r in reports)
    
    if not report_exists:
        embed = discord.Embed(
            title="❌ Invalid Report ID",
            description=f"Report ID `{report_id}` not found for {member.mention}",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Available Report IDs",
            value=", ".join([f"`{r['id']}`" for r in reports]),
            inline=False
        )
        await ctx.send(embed=embed)
        return
    
    # Remove the report
    db.remove_scammer_report(report_id)
    
    remaining_reports = len(reports) - 1
    
    embed = discord.Embed(
        title="✅ Scammer Report Removed",
        description=f"Report ID `{report_id}` has been removed for {member.mention}",
        color=discord.Color.green()
    )
    
    embed.add_field(name="Remaining Reports", value=f"{remaining_reports} 🚩", inline=True)
    embed.add_field(name="Removed By", value=ctx.author.mention, inline=True)
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Removed by {ctx.author.name}")
    
    await ctx.send(embed=embed)
    
    logging.info(f"Staff {ctx.author.name} removed scammer report {report_id} for {member.name}")

@bot.command(name='clearallscam', aliases=['clearscammer'])
@is_staff()
async def clearallscam_cmd(ctx, member: discord.Member):
    """Clear ALL scammer reports for a user (STAFF ONLY)"""
    
    reports = db.get_scammer_reports(member.id)
    
    if not reports:
        embed = discord.Embed(
            title="❌ No Reports Found",
            description=f"{member.mention} has no scammer reports to clear.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    report_count = len(reports)
    
    # Confirmation
    confirm_embed = discord.Embed(
        title="⚠️ Confirm Clear All Reports",
        description=f"Are you sure you want to remove ALL scammer reports for {member.mention}?",
        color=discord.Color.orange()
    )
    confirm_embed.add_field(name="Reports to Clear", value=f"{report_count} 🚩", inline=True)
    confirm_embed.add_field(
        name="Confirmation",
        value="React with ✅ to confirm or ❌ to cancel",
        inline=False
    )
    
    msg = await ctx.send(embed=confirm_embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id
    
    try:
        reaction, user = await bot.wait_for('reaction_add', timeout=30.0, check=check)
        
        if str(reaction.emoji) == "✅":
            db.clear_all_scammer_reports(member.id)
            
            success_embed = discord.Embed(
                title="✅ All Reports Cleared",
                description=f"All {report_count} scammer reports cleared for {member.mention}",
                color=discord.Color.green()
            )
            success_embed.set_thumbnail(url=member.display_avatar.url)
            success_embed.set_footer(text=f"Cleared by {ctx.author.name}")
            
            await msg.edit(embed=success_embed)
            await msg.clear_reactions()
            
            logging.info(f"Staff {ctx.author.name} cleared all scammer reports for {member.name}")
        else:
            cancel_embed = discord.Embed(
                title="❌ Action Cancelled",
                description="Clear all reports cancelled",
                color=discord.Color.blue()
            )
            await msg.edit(embed=cancel_embed)
            await msg.clear_reactions()
    
    except asyncio.TimeoutError:
        timeout_embed = discord.Embed(
            title="⏰ Confirmation Timeout",
            description="Action cancelled due to timeout",
            color=discord.Color.orange()
        )
        await msg.edit(embed=timeout_embed)
        await msg.clear_reactions()

"""
SCAMMER SYSTEM - PART 3: List All Scammers
Add this command after Part 2 commands
"""

# ========================================
# LIST ALL SCAMMERS COMMAND
# ========================================

@bot.command(name='listscammers', aliases=['scammers', 'scamlist'])
async def listscammers_cmd(ctx):
    """View all users reported as scammers (Anyone can use)"""
    
    scammers = db.get_all_scammers()
    
    if not scammers:
        embed = discord.Embed(
            title="✅ No Scammer Reports",
            description="No users have been reported as scammers yet.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Requested by {ctx.author.name}")
        await ctx.send(embed=embed)
        return
    
    # Create embed with all scammers
    embed = discord.Embed(
        title="🚨 Reported Scammers List",
        description=f"Total users with scammer reports: **{len(scammers)}**",
        color=discord.Color.dark_red()
    )
    
    scammer_text = []
    for idx, (user_id, report_count) in enumerate(scammers[:25], 1):  # Limit to 25 to avoid embed limits
        user = bot.get_user(user_id)
        
        if user:
            user_name = f"{user.name}"
            user_mention = user.mention
        else:
            user_name = f"Unknown User"
            user_mention = f"ID: {user_id}"
        
        # Add warning emoji based on report count
        if report_count >= 5:
            warning = "🔴"  # High risk
        elif report_count >= 3:
            warning = "🟠"  # Medium risk
        else:
            warning = "🟡"  # Low risk
        
        scammer_text.append(f"{warning} **{idx}.** {user_mention} - **{report_count}** report(s)")
    
    embed.add_field(
        name="📋 Scammer List",
        value="\n".join(scammer_text),
        inline=False
    )
    
    if len(scammers) > 25:
        embed.add_field(
            name="ℹ️ Note",
            value=f"Showing top 25 of {len(scammers)} reported users",
            inline=False
        )
    
    embed.add_field(
        name="🔍 Check Individual Reports",
        value=f"Use `{Config.PREFIX}scam @user` to see detailed reports for a specific user",
        inline=False
    )
    
    embed.add_field(
        name="🚩 Warning Levels",
        value="🔴 High Risk (5+ reports)\n🟠 Medium Risk (3-4 reports)\n🟡 Low Risk (1-2 reports)",
        inline=False
    )
    
    embed.set_footer(text=f"Requested by {ctx.author.name}")
    
    await ctx.send(embed=embed)

# ========================================
# OWNER COMMANDS
# ========================================

@bot.command(name='addrep')
@is_owner()
async def addrep_cmd(ctx, member: discord.Member, amount: int):
    """Add reputation (Owner only)"""
    if amount <= 0:
        await ctx.send("Amount must be greater than 0")
        return
    
    old_rep = db.get_reputation(member.id)
    db.add_reputation(member.id, amount)
    new_rep = db.get_reputation(member.id)
    
    embed = discord.Embed(
        title="✅ Reputation Added",
        description=f"Added reputation to {member.mention}",
        color=discord.Color.green()
    )
    
    embed.add_field(name="Previous Rep", value=f"{old_rep} ⭐", inline=True)
    embed.add_field(name="Amount Added", value=f"+{amount} ⭐", inline=True)
    embed.add_field(name="New Total", value=f"{new_rep} ⭐", inline=True)
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Modified by {ctx.author.name}")
    await ctx.send(embed=embed)

@bot.command(name='removerep')
@is_owner()
async def removerep_cmd(ctx, member: discord.Member, amount: int):
    """Remove reputation (Owner only)"""
    if amount <= 0:
        await ctx.send("Amount must be greater than 0")
        return
    
    old_rep = db.get_reputation(member.id)
    db.remove_reputation(member.id, amount)
    new_rep = db.get_reputation(member.id)
    
    embed = discord.Embed(
        title="✅ Reputation Removed",
        description=f"Removed reputation from {member.mention}",
        color=discord.Color.orange()
    )
    
    embed.add_field(name="Previous Rep", value=f"{old_rep} ⭐", inline=True)
    embed.add_field(name="Amount Removed", value=f"-{amount} ⭐", inline=True)
    embed.add_field(name="New Total", value=f"{new_rep} ⭐", inline=True)
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Modified by {ctx.author.name}")
    await ctx.send(embed=embed)

@bot.command(name='setrep')
@is_owner()
async def setrep_cmd(ctx, member: discord.Member, amount: int):
    """Set reputation (Owner only)"""
    if amount < 0:
        await ctx.send("Amount cannot be negative")
        return
    
    old_rep = db.get_reputation(member.id)
    db.set_reputation(member.id, amount)
    
    embed = discord.Embed(
        title="✅ Reputation Set",
        description=f"Set reputation for {member.mention}",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Previous Rep", value=f"{old_rep} ⭐", inline=True)
    embed.add_field(name="New Rep", value=f"{amount} ⭐", inline=True)
    embed.add_field(name="Difference", value=f"{amount - old_rep:+d} ⭐", inline=True)
    
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Modified by {ctx.author.name}")
    await ctx.send(embed=embed)

@bot.command(name='clearrep')
@is_owner()
async def clearrep_cmd(ctx, member: discord.Member):
    """Clear all reputation data (Owner only)"""
    old_rep = db.get_reputation(member.id)
    vouch_count = len(db.get_vouch_history(member.id, limit=999))
    
    confirm_embed = discord.Embed(
        title="⚠️ Confirm Clear Reputation",
        description=f"Are you sure you want to clear all data for {member.mention}?",
        color=discord.Color.red()
    )
    confirm_embed.add_field(name="Reputation to Clear", value=f"{old_rep} ⭐", inline=True)
    confirm_embed.add_field(name="Vouches to Clear", value=str(vouch_count), inline=True)
    confirm_embed.add_field(
        name="Confirmation",
        value="React with ✅ to confirm or ❌ to cancel",
        inline=False
    )
    
    msg = await ctx.send(embed=confirm_embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id
    
    try:
        reaction, user = await bot.wait_for('reaction_add', timeout=30.0, check=check)
        
        if str(reaction.emoji) == "✅":
            db.clear_reputation(member.id)
            
            success_embed = discord.Embed(
                title="✅ Reputation Cleared",
                description=f"All reputation data cleared for {member.mention}",
                color=discord.Color.green()
            )
            success_embed.add_field(name="Reputation Cleared", value=f"{old_rep} ⭐", inline=True)
            success_embed.add_field(name="Vouches Cleared", value=str(vouch_count), inline=True)
            success_embed.set_footer(text=f"Cleared by {ctx.author.name}")
            
            await msg.edit(embed=success_embed)
            await msg.clear_reactions()
        else:
            cancel_embed = discord.Embed(
                title="❌ Action Cancelled",
                description="Reputation clear cancelled",
                color=discord.Color.blue()
            )
            await msg.edit(embed=cancel_embed)
            await msg.clear_reactions()
    
    except asyncio.TimeoutError:
        timeout_embed = discord.Embed(
            title="⏰ Confirmation Timeout",
            description="Action cancelled due to timeout",
            color=discord.Color.orange()
        )
        await msg.edit(embed=timeout_embed)
        await msg.clear_reactions()

@bot.command(name='repstats')
@is_owner()
async def repstats_cmd(ctx):
    """View system statistics (Owner only)"""
    leaderboard = db.get_leaderboard()
    total_users = len(leaderboard)
    total_rep = sum(rep for _, rep in leaderboard)
    blacklist = db.get_blacklist()
    
    top_user = None
    if leaderboard:
        top_user_id, top_rep = leaderboard[0]
        top_user = bot.get_user(top_user_id)
    
    embed = discord.Embed(
        title="📊 Reputation System Statistics",
        color=discord.Color.blue(),
    )
    
    embed.add_field(name="Total Users", value=str(total_users), inline=True)
    embed.add_field(name="Total Reputation", value=f"{total_rep} ⭐", inline=True)
    embed.add_field(name="Blacklisted Users", value=str(len(blacklist)), inline=True)
    
    embed.add_field(name="Vouch Cooldown", value=f"{Config.VOUCH_COOLDOWN // 60} min", inline=True)
    embed.add_field(name="Vouch Amount", value=f"{Config.VOUCH_REP_AMOUNT} ⭐", inline=True)
    embed.add_field(name="Dummy Per Day", value=f"{Config.DUMMY_PER_DAY}x", inline=True)
    
    if top_user:
        embed.add_field(
            name="Top User",
            value=f"{top_user.mention} - {leaderboard[0][1]} ⭐",
            inline=False
        )
    
    embed.add_field(
        name="Database",
        value="✅ PostgreSQL (Data persists forever)",
        inline=False
    )
    
    embed.set_footer(text=f"Requested by {ctx.author.name}")
    await ctx.send(embed=embed)

# ========================================
# HELP COMMAND
# ========================================

@bot.command(name='help')
async def help_cmd(ctx):
    """Display all commands"""
    is_owner_user = ctx.author.id == Config.OWNER_ID
    
    embed = discord.Embed(
        title="📖 Reputation Bot - Commands",
        description=f"Prefix: `{Config.PREFIX}` | Commands 👇",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="🌟 Reputation Commands",
        value=(
            f"`{Config.PREFIX}vouch @user reason` - Give {Config.VOUCH_REP_AMOUNT} rep\n"
            f"`{Config.PREFIX}scam @user` - Check scammer reports\n"
            f"`{Config.PREFIX}listscammers` - View all reported scammers\n"
            f"`{Config.PREFIX}helpvouch @user` - Staff - Give 2 rep per use| Members - Give 1 rep per use\n"
            f"`{Config.PREFIX}dummy @user` - Remove 3 rep (3x/day)\n"
            f"`{Config.PREFIX}leaderboard` - View leaderboard\n"
            f"`{Config.PREFIX}rank [@user]` - Check reputation\n"
            f"`{Config.PREFIX}vouchhistory [@user]` - View history\n"
            f"`{Config.PREFIX}cooldown` - Check cooldown"
        ),
        inline=False
    )

"""
# For public commands section:


# For staff commands section (if is_staff_user):
f"`{Config.PREFIX}applyscammer @user reason` - Report a scammer\n"
f"`{Config.PREFIX}removescammer @user [report_id]` - Remove report\n"
f"`{Config.PREFIX}clearallscam @user` - Clear all reports\n"
"""

    if is_owner_user:
        embed.add_field(
            name="👑 Owner Commands",
            value=(
                f"`{Config.PREFIX}repblacklist @user` - Toggle blacklist\n"
                f"`{Config.PREFIX}viewblacklist` - View blacklisted\n"
                f"`{Config.PREFIX}addrep @user amount` - Add rep\n"
                f"`{Config.PREFIX}removerep @user amount` - Remove rep\n"
                f"`{Config.PREFIX}setrep @user amount` - Set rep\n"
                f"`{Config.PREFIX}clearrep @user` - Clear all data\n"
                f"`{Config.PREFIX}repstats` - View statistics\n"
                f"`{Config.PREFIX}applyscammer @user reason` - Report a scammer\n"
                f"`{Config.PREFIX}removescammer @user [report_id]` - Remove report\n"
                f"`{Config.PREFIX}clearallscam @user` - Clear all reports"
            ),
            inline=False
        )
    
    embed.add_field(
        name="ℹ️ Information",
        value=(
            f"• Vouch: **{Config.VOUCH_REP_AMOUNT} rep** (cooldown: {Config.VOUCH_COOLDOWN // 60} min)\n"
            f"• Helpvouch: Staff Gives **2 rep per use**, Members Gives **1 rep per use**\n"
            f"• Dummy: Remove **3 rep** ({Config.DUMMY_PER_DAY}x limited uses per day)\n"
        ),
        inline=False
    )
    
    embed.set_footer(text=f"Requested by {ctx.author.name}")
    await ctx.send(embed=embed)

# ========================================
# ERROR HANDLER
# ========================================

@bot.event
async def on_command_error(ctx, error):
    """Handle errors"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❌ Missing Argument",
            description=f"Missing: `{error.param.name}`",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Help",
            value=f"Use `{Config.PREFIX}help` for command usage",
            inline=False
        )
        await ctx.send(embed=embed)
    
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. Mention a valid server member.")
    
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument. Check your command syntax.")
    
    elif isinstance(error, commands.CheckFailure):
        pass
    
    else:
        logging.error(f'Error in {ctx.command}: {error}')
        await ctx.send("An error occurred.")

# ========================================
# WEB SERVER FOR RENDER
# ========================================

async def start_keep_alive():
    """Web server for Render"""
    from aiohttp import web
    
    async def health(request):
        return web.Response(text='Bot Online!', status=200)
    
    async def status_page(request):
        leaderboard = db.get_leaderboard()
        total_rep = sum(rep for _, rep in leaderboard)
        blacklist = db.get_blacklist()
        
        html = f'''
<!DOCTYPE html>
<html>
<head>
    <title>Reputation Bot</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: monospace;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .container {{
            text-align: center;
            padding: 40px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 20px;
            backdrop-filter: blur(10px);
            max-width: 600px;
        }}
        h1 {{ font-size: 48px; margin: 0 0 20px 0; }}
        .status {{ font-size: 24px; margin: 20px 0; color: #0f0; }}
        .info {{ font-size: 18px; margin: 10px 0; }}
        .badge {{
            display: inline-block;
            padding: 8px 16px;
            background: rgba(255, 255, 255, 0.2);
            border-radius: 20px;
            margin: 5px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⭐ Reputation Bot</h1>
        <div class="status">✅ ONLINE</div>
        <div class="info">Servers: {len(bot.guilds)}</div>
        <div class="info">Total Users: {len(leaderboard)}</div>
        <div class="info">Total Rep: {total_rep}</div>
        <div class="info">Blacklisted: {len(blacklist)}</div>
        <div class="info">Database: PostgreSQL ✅</div>
        <div style="margin-top: 20px;">
            <span class="badge">Vouch: {Config.VOUCH_REP_AMOUNT}⭐</span>
            <span class="badge">Cooldown: {Config.VOUCH_COOLDOWN // 60}m</span>
            <span class="badge">Dummy: {Config.DUMMY_PER_DAY}x/day</span>
        </div>
    </div>
</body>
</html>
        '''
        return web.Response(text=html, content_type='text/html')
    
    app = web.Application()
    app.router.add_get('/', status_page)
    app.router.add_get('/health', health)
    app.router.add_get('/ping', health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
    await site.start()
    
    logging.info(f'🌐 Web server: port {Config.PORT}')

# ========================================
# MAIN STARTUP
# ========================================

async def main():
    """Main startup"""
    await start_keep_alive()
    
    try:
        await bot.start(Config.TOKEN)
    except KeyboardInterrupt:
        logging.info('Shutdown requested')
        await bot.close()
    except Exception as e:
        logging.error(f'Bot error: {e}')
        await bot.close()

if __name__ == '__main__':
    print('=' * 70)
    print('REPUTATION BOT - PostgreSQL Version')
    print('=' * 70)
    print(f'Owner ID: {Config.OWNER_ID}')
    print(f'Prefix: {Config.PREFIX}')
    print(f'Vouch: {Config.VOUCH_REP_AMOUNT}⭐ | Cooldown: {Config.VOUCH_COOLDOWN // 60}m')
    print(f'Helpvouch: Staff {Config.HELPVOUCH_REP_STAFF}⭐ | Member {Config.HELPVOUCH_REP_MEMBER}⭐')
    print(f'Dummy: Remove {Config.DUMMY_REP_REMOVE}⭐ ({Config.DUMMY_PER_DAY}x/day)')
    print(f'Database: PostgreSQL ✅')
    print(f'Port: {Config.PORT}')
    print('=' * 70)
    
    if not Config.TOKEN:
        print('\n❌ DISCORD_BOT_TOKEN not set!')
        exit(1)
    
    if not Config.DATABASE_URL:
        print('\n❌ DATABASE_URL not set!')
        print('Create PostgreSQL database on Render and add DATABASE_URL')
        exit(1)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Bot stopped')
    except Exception as e:
        print(f'Failed to start: {e}')
