# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════╗
║  🤖 CurlyBot — ربات کامل هایرایز                 ║
║  سازنده و مالک: @nima_curly                      ║
╚══════════════════════════════════════════════════╝

اجرا:
    pip install -r requirements.txt
    ROOM_ID=...  API_TOKEN=...  PUBLIC_URL=https://آدرس-سرور-تو  python curlybot.py

متغیرهای محیطی:
    ROOM_ID, API_TOKEN   (الزامی)  — یا فایل curlybot_secrets.json با کلیدهای room_id و api_token
    PUBLIC_URL           آدرس عمومی سرور برای رادیو (اگر هاست خودش ست نکند)
    PORT                 پورت وب‌سرور (پیش‌فرض 8080)
    MUSIC_SOURCES        ترتیب منبع آهنگ، پیش‌فرض: soundcloud,youtube
    YT_COOKIES_FILE      (اختیاری) فایل کوکی برای یوتیوب
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import random
import shutil
import time
import urllib.request
from asyncio import CancelledError, create_task, sleep
from datetime import datetime

from highrise import BaseBot, Position, User
from highrise.__main__ import BotDefinition
from highrise.models import RoomPermissions

try:
    import yt_dlp
except ImportError:  # فقط بخش موزیک غیرفعال می‌شود
    yt_dlp = None

# ═══════════════════════ لاگ ═══════════════════════
_handlers = [logging.StreamHandler()]
try:
    _handlers.insert(0, logging.FileHandler("curlybot.log", encoding="utf-8"))
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=_handlers)
logger = logging.getLogger("curlybot")

# ═══════════════════════ تنظیمات اصلی (اینجا عوض کن) ═══════════════════════
OWNER_USERNAME = "nima_curly"        # مالک اصلی ربات (بالاترین دسترسی)
PROTECTED_ADMINS = {"bad_qoq"}       # این افراد را نمی‌شود از ادمین‌ها حذف کرد (مثل ربات قبلی)

PUNCH_EMOTE = "emote-boxer"          # دنس «زدن» برای فرستنده‌ی !punch
FALL_EMOTE = "emote-death2"          # دنس «افتادن/غش» برای هدف !punch
PUNCH_COOLDOWN = 6.0                 # ثانیه

BOT_START_POSITION = (16.5, 0.25, 3.5)   # جای پیش‌فرض ربات هنگام ورود
DOWN_POSITION = (2.0, 0.5, 1.5)          # مقصد دستور !down

MAX_SONG_SECONDS = 600               # آهنگ بلندتر از این قبول نمی‌شود
MIN_SONG_SECONDS = 40                # کوتاه‌تر از این معمولاً پیش‌نمایش ۳۰ ثانیه‌ای است
DEFAULT_EMOTE_SECONDS = 15.0         # مدت دنس‌هایی که زمانشان ثبت نشده
MAX_AAC_LISTENERS = 5

CONFIG_FILE = "curlybot_config.json"
OLD_CONFIG_FILE = "bot_config.json"  # اگر از ربات قبلی مانده باشد، ادمین‌ها از آن وارد می‌شوند
DATA_FILE = "curlybot_data.json"     # امتیازها

# سطح دسترسی‌ها
USER, ADMIN, HOST, OWNER = 0, 1, 2, 3
START_TIME = time.time()

# ═══════════════════════ ابزارهای متن ═══════════════════════
_FA = "۰۱۲۳۴۵۶۷۸۹"
_AR = "٠١٢٣٤٥٦٧٨٩"
_TO_EN = {ord(c): str(i) for i, c in enumerate(_FA)}
_TO_EN.update({ord(c): str(i) for i, c in enumerate(_AR)})
_TO_FA = {ord(str(i)): c for i, c in enumerate(_FA)}


def normalize_digits(text: str) -> str:
    """اعداد فارسی/عربی را انگلیسی می‌کند."""
    return text.translate(_TO_EN)


def to_fa_digits(value) -> str:
    return str(value).translate(_TO_FA)


def chunk_text(text: str, limit: int = 240) -> list:
    """متن را برای محدودیت ۲۵۶ کاراکتری چت هایرایز تکه می‌کند (اول با خط، بعد با کلمه)."""
    out, cur = [], ""
    for line in str(text).split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            piece, line = line[:cut], line[cut:].lstrip()
            if cur:
                out.append(cur)
                cur = ""
            out.append(piece)
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += "\n" + line
        else:
            out.append(cur)
            cur = line
    if cur.strip():
        out.append(cur)
    return [c for c in out if c.strip()]


def fmt_duration(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{to_fa_digits(d)} روز")
    if h:
        parts.append(f"{to_fa_digits(h)} ساعت")
    parts.append(f"{to_fa_digits(m)} دقیقه")
    return " و ".join(parts)


# ═══════════════════════ لیست دنس‌ها (از فایل خودت، بدون تکراری) ═══════════════════════
# هر ردیف: (شناسه‌ی دنس, مدت به ثانیه, (اسم‌های انگلیسی مثل کد اول))
# شماره‌ی هر دنس = جایگاهش در لیست (۱ تا آخر). هر دنس با عدد انگلیسی، عدد فارسی،
# اسم انگلیسی و حتی شناسه‌ی کاملش قابل اجراست.
EMOTES = [
    ('idle_zombie', 28.75, ('zombie',)),
    ('idle_layingdown2', 21.55, ('relaxed',)),
    ('idle_layingdown', 24.59, ('attentive',)),
    ('idle-sleep', 22.62, ('sleepy',)),
    ('idle-sad', 24.38, ()),
    ('idle-posh', 21.85, ('posh',)),
    ('idle-loop-tired', 21.96, ('tiredloop',)),
    ('idle-loop-tapdance', 6.26, ('taploop', 'looptapdance')),
    ('idle-loop-sitfloor', 22.32, ('sit',)),
    ('idle-loop-shy', 16.47, ('shy',)),
    ('idle-loop-sad', 6.05, ('bummed',)),
    ('idle-loop-happy', 18.8, ("chillin'",)),
    ('idle-loop-annoyed', 17.06, ('annoyed',)),
    ('idle-loop-aerobics', 8.51, ('aerobics',)),
    ('idle-lookup', 22.34, ('ponder',)),
    ('idle-hero', 21.88, ('heropose',)),
    ('idle-floorsleeping', 13.94, ('cozynap',)),
    ('idle-enthusiastic', 15.94, ('enthused',)),
    ('idle-dance-swinging', 13.2, ('boogieswing',)),
    ('idle-dance-headbobbing', 25.37, ('feelthebeat',)),
    ('idle-angry', 25.43, ('irritated',)),
    ('emote-yes', 2.57, ('yes',)),
    ('emote-wings', 13.13, ('ibelieveicanfly',)),
    ('emote-wave', 2.69, ('thewave',)),
    ('emote-tired', 4.61, ('tired',)),
    ('emote-think', 3.69, ('think',)),
    ('emote-theatrical', 8.59, ('theatrical',)),
    ('emote-tapdance', 11.06, ('tapdance',)),
    ('emote-superrun', 6.27, ('superrun',)),
    ('emote-superpunch', 3.75, ('superpunch',)),
    ('emote-sumo', 10.87, ('sumofight',)),
    ('emote-suckthumb', 4.19, ('thumbsuck',)),
    ('emote-splitsdrop', 4.47, ('splitsdrop',)),
    ('emote-snowball', 5.23, ('snowballfight', 'snowball')),
    ('emote-snowangel', 6.22, ('snowangel',)),
    ('emote-shy', 4.48, ('shyemote',)),
    ('emote-secrethandshake', 3.88, ('secrehandshake',)),
    ('emote-sad', 5.41, ('poutyface',)),
    ('emote-ropepull', 8.77, ('ropepull',)),
    ('emote-roll', 3.56, ('roll',)),
    ('emote-rofl', 6.31, ('rofl',)),
    ('emote-robot', 7.61, ('robot',)),
    ('emote-rainbow', 2.81, ('rainbow',)),
    ('emote-proposing', 4.28, ('proposing',)),
    ('emote-peekaboo', 3.63, ('peekaboo',)),
    ('emote-peace', 5.76, ('peace',)),
    ('emote-panic', 2.85, ('panic',)),
    ('emote-no', 2.7, ('no',)),
    ('emote-ninjarun', 4.75, ('ninjarun',)),
    ('emote-nightfever', 15.0, ('nightfever',)),
    ('emote-monster_fail', 15.0, ('monsterfail',)),
    ('emote-model', 15.0, ('model',)),
    ('emote-lust', 15.0, ('flirtywave',)),
    ('emote-levelup', 15.0, ('levelup',)),
    ('emote-laughing2', 15.0, ('amused',)),
    ('emote-laughing', 15.0, ()),
    ('emote-kiss', 15.0, ('kiss',)),
    ('emote-kicking', 15.0, ('superkick',)),
    ('emote-jumpb', 15.0, ('jump',)),
    ('emote-gravity', 15.0, ('gravity',)),
    ('emote-judochop', 15.0, ('judochop',)),
    ('emote-jetpack', 15.0, ('imaginaryjetpack',)),
    ('emote-hugyourself', 15.0, ('hugyourself',)),
    ('emote-hot', 15.0, ('sweating',)),
    ('emote-hero', 15.0, ('heroentrance',)),
    ('emote-hello', 15.0, ('hello',)),
    ('emote-headball', 15.0, ('headball',)),
    ('emote-harlemshake', 15.0, ('harlemshake',)),
    ('emote-happy', 15.0, ('happy',)),
    ('emote-handstand', 15.0, ('handstand',)),
    ('emote-greedy', 15.0, ('greedyemote',)),
    ('emote-graceful', 15.0, ('graceful',)),
    ('emote-gordonshuffle', 15.0, ('moonwalk',)),
    ('emote-ghost-idle', 15.0, ('ghostfloat',)),
    ('emote-gangnam', 15.0, ('gangnamstyle',)),
    ('emote-frollicking', 15.0, ('frolic',)),
    ('emote-fainting', 15.0, ('faint',)),
    ('emote-fail2', 15.0, ('clumsy',)),
    ('emote-fail1', 15.0, ('fall',)),
    ('emote-exasperatedb', 15.0, ('facepalm',)),
    ('emote-exasperated', 15.0, ('exasperated',)),
    ('emote-elbowbump', 15.0, ('elbowbump',)),
    ('emote-disco', 15.0, ('disco',)),
    ('emote-disappear', 15.0, ('blastoff',)),
    ('emote-deathdrop', 15.0, ('faintdrop',)),
    ('emote-death2', 15.0, ('collapse',)),
    ('emote-death', 15.0, ('revival',)),
    ('emote-dab', 15.0, ('dab',)),
    ('emote-curtsy', 15.0, ('curtsy',)),
    ('emote-confused', 15.0, ('confusion',)),
    ('emote-cold', 15.0, ('cold',)),
    ('emote-charging', 15.0, ('charging',)),
    ('emote-bunnyhop', 15.0, ('bunnyhop',)),
    ('emote-bow', 15.0, ('bow',)),
    ('emote-boo', 15.0, ('boo',)),
    ('emote-baseball', 15.0, ('homerun',)),
    ('emote-apart', 15.0, ('fallingapart',)),
    ('emoji-thumbsup', 15.0, ('thumbsup',)),
    ('emoji-there', 15.0, ('point',)),
    ('emoji-sneeze', 15.0, ('sneeze',)),
    ('emoji-smirking', 15.0, ('smirk',)),
    ('emoji-sick', 15.0, ('sick',)),
    ('emoji-scared', 15.0, ('gasp',)),
    ('emoji-punch', 15.0, ('punch',)),
    ('emoji-pray', 15.0, ('pray',)),
    ('emoji-poop', 15.0, ('stinky',)),
    ('emoji-naughty', 15.0, ('naughty',)),
    ('emoji-mind-blown', 15.0, ('mindblown',)),
    ('emoji-lying', 15.0, ('lying',)),
    ('emoji-halo', 15.0, ('levitate', 'halo')),
    ('emoji-hadoken', 15.0, ('fireball lunge',)),
    ('emoji-give-up', 15.0, ('giveup',)),
    ('emoji-gagging', 15.0, ('tummy ache',)),
    ('emoji-flex', 15.0, ('flex',)),
    ('emoji-dizzy', 15.0, ('stunned', 'dizzy')),
    ('emoji-cursing', 15.0, ('cursing emote',)),
    ('emoji-crying', 15.0, ('sob',)),
    ('emoji-clapping', 15.0, ('clap',)),
    ('emoji-celebrate', 15.0, ('raisetheroof',)),
    ('emoji-arrogance', 15.0, ('arrogance',)),
    ('emoji-angry', 15.0, ('angry',)),
    ('dance-voguehands', 15.0, ('voguehands',)),
    ('dance-tiktok8', 15.0, ('savagedance',)),
    ('dance-tiktok2', 15.0, ('dontstartnow', 'tiktok2')),
    ('dance-spiritual', 15.0, ('yogaflow', 'spiritual')),
    ('dance-smoothwalk', 15.0, ('smoothwalk',)),
    ('dance-singleladies', 15.0, ('ringonit', 'singleladies')),
    ('dance-shoppingcart', 15.0, ("let's go shopping",)),
    ('dance-russian', 15.0, ('russian dance',)),
    ('dance-robotic', 15.0, ('tobotic', 'robotic')),
    ('dance-pennywise', 15.0, ("penny's dance",)),
    ('dance-orangejustice', 15.0, ('orange juice dance',)),
    ('dance-metal', 15.0, ('rockout',)),
    ('dance-martial-artist', 15.0, ('karate',)),
    ('dance-macarena', 15.0, ('macarena',)),
    ('dance-handsup', 15.0, ('handsintheair',)),
    ('dance-duckwalk', 15.0, ('duckealk',)),
    ('dance-breakdance', 15.0, ('breakdance',)),
    ('dance-blackpink', 15.0, ('kpop',)),
    ('dance-aerobics', 15.0, ('pushups',)),
    ('emote-hyped', 15.0, ('hyped',)),
    ('dance-jinglebell', 15.0, ('jinglebell',)),
    ('idle-nervous', 15.0, ('nervous',)),
    ('idle-toilet', 15.0, ('gottago',)),
    ('emote-attention', 15.0, ()),
    ('sit-open', 15.0, ()),
    ('emote-astronaut', 15.0, ('astronaut',)),
    ('dance-zombie', 15.0, ('dancezombie',)),
    ('emoji-ghost', 15.0, ('ghost',)),
    ('emote-hearteyes', 15.0, ('hearteyes',)),
    ('emote-swordfight', 15.0, ('swordfight',)),
    ('emote-timejump', 15.0, ('timejump',)),
    ('emote-snake', 15.0, ('snake',)),
    ('emote-heartfingers', 15.0, ('heartfingers',)),
    ('emote-heartshape', 15.0, ('heart shape', 'heartshape')),
    ('emote-hug', 15.0, ('hug',)),
    ('emote-lagughing', 15.0, ('laugh',)),
    ('emoji-eyeroll', 15.0, ('eyeroll',)),
    ('emote-embarrassed', 15.0, ('embarrassed',)),
    ('emote-float', 15.0, ('float',)),
    ('emote-telekinesis', 15.0, ('telekinesis',)),
    ('dance-sexy', 15.0, ('sexydance',)),
    ('emote-puppet', 15.0, ('puppet',)),
    ('idle-fighter', 15.0, ('fighter idle', 'fighter')),
    ('dance-pinguin', 15.0, ('penguindance',)),
    ('dance-creepypuppet', 15.0, ('creepypuppet',)),
    ('emote-sleigh', 15.0, ('sleigh',)),
    ('emote-maniac', 15.0, ('maniac',)),
    ('emote-energyball', 15.0, ('energyball',)),
    ('idle_singing', 15.0, ('singing', 'inging')),
    ('emote-frog', 15.0, ('frog',)),
    ('emote-superpose', 15.0, ('superpose',)),
    ('emote-cute', 15.0, ('cute',)),
    ('dance-tiktok9', 15.0, ('tiktok9',)),
    ('dance-weird', 15.0, ('weird',)),
    ('dance-tiktok10', 15.0, ('tiktok10',)),
    ('emote-pose7', 15.0, ('pose7',)),
    ('emote-pose8', 15.0, ('pose8',)),
    ('idle-dance-casual', 15.0, ('casualdance',)),
    ('emote-pose1', 15.0, ('pose1',)),
    ('emote-pose3', 15.0, ('pose3',)),
    ('emote-pose5', 15.0, ('pose5',)),
    ('emote-cutey', 15.0, ('cutey',)),
    ('emote-punkguitar', 15.0, ('punkguitar',)),
    ('emote-zombierun', 15.0, ('zombieru',)),
    ('dance-icecream', 15.0, ('icecream',)),
    ('dance-wrong', 15.0, ('wrong',)),
    ('idle-uwu', 15.0, ('uwu',)),
    ('idle-dance-tiktok4', 15.0, ('tiktok4',)),
    ('emote-shy2', 15.0, ('advancedshy', 'bashfulblush')),
    ('dance-anime', 15.0, ('anime',)),
    ('dance-kawai', 15.0, ('kawaii',)),
    ('idle-wild', 15.0, ('scritchy',)),
    ('emote-iceskating', 15.0, ('iceskating',)),
    ('emote-pose6', 15.0, ('surprisebig',)),
    ('emote-celebrationstep', 15.0, ('celebrationstep',)),
    ('emote-creepycute', 15.0, ('creepycute',)),
    ('emote-frustrated', 15.0, ('frustrated',)),
    ('emote-pose10', 15.0, ('pose10',)),
    ('sit-relaxed', None, ()),
    ('emote-stargaze', 15.0, ('stargazing',)),
    ('emote-slap', 15.0, ('slap',)),
    ('emote-boxer', 15.0, ('boxer',)),
    ('emote-headblowup', 15.0, ('headblowup',)),
    ('emote-kawaiigogo', 15.0, ('kawaiigogo',)),
    ('emote-repose', 15.0, ('repose',)),
    ('idle-dance-tiktok7', 15.0, ('tiktok7',)),
    ('emote-shrink', 15.0, ('shrink',)),
    ('emote-pose9', 15.0, ('ditzypose',)),
    ('emote-teleporting', 15.0, ('teleporting',)),
    ('dance-touch', 15.0, ('touch',)),
    ('idle-guitar', 15.0, ('airuitar',)),
    ('emote-gift', 15.0, ('thisis for you',)),
    ('dance-employee', 15.0, ('pushit',)),
    ('emote-kissing', 15.0, ()),
    ('dance-tiktok11', 15.0, ('tiktok11',)),
    ('emote-cutesalute', 15.0, ('cutesalute',)),
    ('emote-salute', 15.0, ('attention',)),
    ('idle-floorsleeping2', None, ('relaxing',)),
    ('dance-floss', 11.0, ('floss',)),
    ('dance-tiktok12', 15.0, ()),
    ('dance-tiktok13', 15.0, ()),
    ('emote-spiderman', None, ()),
    ('dance-twerk', None, ('twerkltout',)),
    ('dance-ballet', None, ('balletbliss',)),
    ('sit-idle-cute', None, ('rest',)),
    ('dance-true-heart', None, ()),
    ('dance-griddy', None, ('griddy',)),
    ('dance-freshprince', None, ('freshprince',)),
    ('emote-idle-daydreaming', None, ()),
    ('dance-popularvibe', None, ('popularvibe',)),
    ('sit-idle-laidBack', None, ('laidback',)),
    ('dance-swagbounce', None, ('swagbounce',)),
    ('dance-woah', None, ('woah',)),
    ('dance-mine', None, ('minedance',)),
    ('emote-blowkisses', None, ('blowkisses',)),
    ('dance-shuffle', None, ()),
    ('emote-knocking-screen', None, ()),
    ('emote-alice-shrink', 15.0, ('aliceshrink',)),
    ('emote-threadexchange-star', 15.0, ('threadexchangestar',)),
    ('emote-fairytwirl', None, ('fairytwirl',)),
    ('emote-sweetsmooch', None, ('sweetsmooch',)),
    ('idle-floating', None, ('floating',)),
    ('emote-launch', None, ('launch',)),
    ('idle-howl', None, ('howl',)),
    ('dance-fruity', None, ('fruity',)),
    ('dance-wild', None, ('wild',)),
    ('idle-laidBack', None, ()),
    ('dance-hipshake', None, ('hipshake',)),
    ('emote-fashionista', None, ('fashionista',)),
    ('idle-phone-camera', None, ('phonecamera',)),
    ('idle-laying-phone-talking', None, ('yapattack',)),
    ('emote-flexing', None, ('flexinghard',)),
    ('emote-collab-tea-left', None, ('spillingthetha',)),
    ('emote-scuba-dance', None, ('scubadance',)),
    ('emote-modelwalk', None, ('modelstrut',)),
    ('emote-meditate-idle', None, ('zenmode',)),
    ('emote-threadexchange-posing', None, ('silentlyjudging', 'divamoment')),
    ('idle-space', None, ('zerogravitychill',)),
]

# ═══════════════════════ نمایه‌ی دنس‌ها ═══════════════════════
EMOTE_IDS = [e[0] for e in EMOTES]
EMOTE_SECONDS = {e[0]: (e[1] or DEFAULT_EMOTE_SECONDS) for e in EMOTES}
EMOTE_LOOKUP = {}   # هر ورودی ممکن → شناسه‌ی دنس
EMOTE_NUMBER = {}   # شناسه → شماره
EMOTE_LABEL = {}    # شناسه → اسم نمایشی
for _i, (_eid, _dur, _aliases) in enumerate(EMOTES, start=1):
    EMOTE_NUMBER[_eid] = _i
    EMOTE_LABEL[_eid] = _aliases[0] if _aliases else _eid
    EMOTE_LOOKUP[str(_i)] = _eid               # عدد انگلیسی
    EMOTE_LOOKUP[to_fa_digits(_i)] = _eid      # عدد فارسی
    for _a in _aliases:                        # اسم‌های انگلیسی مثل کد اول
        EMOTE_LOOKUP[_a.lower()] = _eid
    EMOTE_LOOKUP[_eid.lower()] = _eid          # شناسه‌ی کامل
TOTAL_EMOTES = len(EMOTES)


def resolve_emote(token: str):
    """عدد انگلیسی/فارسی، اسم یا شناسه‌ی دنس → شناسه‌ی دنس (یا None)."""
    return EMOTE_LOOKUP.get(normalize_digits(token.strip().lower()))


# ═══════════════════════ تنظیمات پیش‌فرض ═══════════════════════
DEFAULT_CONFIG = {
    "host_usernames": [OWNER_USERNAME],
    "admin_usernames": [OWNER_USERNAME],
    "vip_usernames": [],
    "banned_users": [],
    "teleport_locations": {
        "vip": {"x": 14.5, "y": 16.75, "z": 5.5},
        "vip1": {"x": 14.5, "y": 16.75, "z": 5.5},
        "dj": {"x": 9.5, "y": 10.75, "z": 10.5},
    },
    "welcome_enabled": True,
    "welcome_message": (
        f"سلام {{username}} عزیز ✨ به روم خوش اومدی! 💃 با نوشتن عدد ۱ تا {to_fa_digits(TOTAL_EMOTES)} "
        "یا اسم دنس برقص، با -p اسم_آهنگ آهنگ بخواه 🎵 و با !help همه‌ی دستورها رو ببین. "
        f"ساخته‌شده توسط @{OWNER_USERNAME} 💜"
    ),
    "leave_message_enabled": False,
    "announcement_enabled": True,
    "announcement_interval": 300,
    "announcement_message": f"🤖 برای اجاره و ساخت ربات به @{OWNER_USERNAME} پیام بدید! 💜",
    "loopchat_interval": 10,
    "antiflood_enabled": False,
    "help_in_whisper": True,
    "bot_position": None,
    "emotebot_emote": None,
}


def as_position(pos):
    """موقعیت‌های روی صندلی (Anchor) x,y,z ندارند؛ فقط موقعیت واقعی را برمی‌گرداند."""
    if pos is not None and all(hasattr(pos, a) for a in ("x", "y", "z")):
        return Position(x=pos.x, y=pos.y, z=pos.z)
    return None


# ═══════════════════════ رادیوی داخلی ═══════════════════════
def _detect_public_url() -> str:
    url = os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or ""
    if not url:
        for env in ("RAILWAY_PUBLIC_DOMAIN", "KOYEB_PUBLIC_DOMAIN", "REPLIT_DEV_DOMAIN", "FLY_APP_NAME"):
            v = os.getenv(env)
            if v:
                url = v if v.startswith("http") else (f"https://{v}.fly.dev" if env == "FLY_APP_NAME" else f"https://{v}")
                break
    return url.rstrip("/")


PUBLIC_URL = _detect_public_url()


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


MP3_ARGS = ["-vn", "-map_metadata", "-1", "-ac", "2", "-ar", "44100", "-b:a", "128k",
            "-f", "mp3", "-write_xing", "0", "-id3v2_version", "0", "pipe:1"]
MP3_PATHS = ("/radio.mp3", "/radio", "/stream", "/stream.mp3", "/live.mp3", "/listen.mp3")


class RadioStation:
    """
    رادیوی داخلی ربات: یک استریم MP3 دائمی. آدرسش را یک بار در تنظیمات رادیوی روم می‌گذاری
    و ربات آهنگ‌های صف را روی آن پخش می‌کند. وقتی آهنگی نیست، سکوت پخش می‌شود تا استریم قطع نشود.

    آدرس‌ها:  /radio.mp3  (اصلی)   /radio.aac   /radio.m3u   /radio.pls   /nowplaying   /
    """
    CHUNK = 4096

    def __init__(self):
        self.listeners = set()
        self.recent = collections.deque(maxlen=12)   # حدود ۳ ثانیه برای شروع سریع شنونده جدید
        self.playing = False
        self.proc = None
        self.silence = b""
        self.server = None
        self._silence_task = None
        self.ffmpeg = find_ffmpeg()
        self.aac_count = 0
        self.now = None   # دیکشنری آهنگ فعلی برای /nowplaying

    # ---------- پخش ----------
    def broadcast(self, data: bytes):
        self.recent.append(data)
        for q in list(self.listeners):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:   # شنونده خیلی کند؛ قطعش می‌کنیم
                self.listeners.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)

    async def _make_silence(self):
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1",
                *MP3_ARGS,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            self.silence = out
        except Exception as e:
            logger.error(f"ساخت سکوت ناموفق بود: {e}")

    async def _silence_loop(self):
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        while True:
            nxt += 1.0
            if not self.playing and self.silence:
                self.broadcast(self.silence)
            await asyncio.sleep(max(0.0, nxt - loop.time()))

    async def play(self, source: str, headers=None, max_seconds=None, hls=False) -> int:
        """یک آهنگ را تا آخرش در استریم پخش می‌کند. تعداد بایت پخش‌شده را برمی‌گرداند."""
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-re"]
        if source.startswith("http"):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            if headers:
                cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
            if hls or ".m3u8" in source:
                cmd += ["-allowed_extensions", "ALL"]
        cmd += ["-i", source] + MP3_ARGS
        loop = asyncio.get_running_loop()
        started = loop.time()
        total = 0
        self.playing = True
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.proc = proc
        try:
            while True:
                data = await asyncio.wait_for(proc.stdout.read(self.CHUNK), timeout=30)
                if not data:
                    break
                total += len(data)
                self.broadcast(data)
                if max_seconds and loop.time() - started > max_seconds:
                    break
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                err = await asyncio.wait_for(proc.stderr.read(), timeout=2)
                if err:
                    logger.warning(f"ffmpeg: {err.decode(errors='ignore')[:300]}")
            except Exception:
                pass
            await proc.wait()
            self.proc = None
            self.playing = False
        return total

    def skip(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()

    # ---------- وب‌سرور ----------
    @staticmethod
    def base_url(headers: dict) -> str:
        if PUBLIC_URL:
            return PUBLIC_URL
        host = headers.get("host", "localhost")
        proto = headers.get("x-forwarded-proto", "http")
        return f"{proto}://{host}"

    async def _stream_mp3(self, writer):
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\n"
            b"Cache-Control: no-cache, no-store\r\nPragma: no-cache\r\n"
            b"Accept-Ranges: none\r\nicy-name: CurlyBot Radio\r\nicy-br: 128\r\n"
            b"Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n")
        await writer.drain()
        q = asyncio.Queue(maxsize=300)
        for c in self.recent:
            q.put_nowait(c)
        self.listeners.add(q)
        try:
            while True:
                data = await q.get()
                if data is None:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            self.listeners.discard(q)

    async def _stream_aac(self, writer):
        """نسخه‌ی AAC (ADTS) برای کلاینت‌هایی که MP3 را قبول نمی‌کنند؛ از همان استریم MP3 ترجمه می‌شود."""
        if not self.ffmpeg or self.aac_count >= MAX_AAC_LISTENERS:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        self.aac_count += 1
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0",
            "-vn", "-c:a", "aac", "-b:a", "96k", "-f", "adts", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        q = asyncio.Queue(maxsize=300)
        for c in self.recent:
            q.put_nowait(c)
        self.listeners.add(q)

        async def feed():
            try:
                while True:
                    data = await q.get()
                    if data is None:
                        break
                    proc.stdin.write(data)
                    await proc.stdin.drain()
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        feeder = create_task(feed())
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: audio/aac\r\nCache-Control: no-cache, no-store\r\n"
                b"Accept-Ranges: none\r\nicy-name: CurlyBot Radio\r\nConnection: close\r\n\r\n")
            await writer.drain()
            while True:
                out = await proc.stdout.read(4096)
                if not out:
                    break
                writer.write(out)
                await writer.drain()
        finally:
            self.aac_count -= 1
            self.listeners.discard(q)
            feeder.cancel()
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()

    @staticmethod
    def _simple(writer, body: bytes, ctype: str, head_only: bool):
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: " + ctype.encode() + b"\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n"
            + (b"" if head_only else body))

    async def _handle(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            headers = {}
            while True:
                h = await asyncio.wait_for(reader.readline(), 10)
                if h in (b"\r\n", b"\n", b""):
                    break
                if b":" in h:
                    k, v = h.decode(errors="ignore").split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            parts = line.decode(errors="ignore").split()
            method = parts[0].upper() if parts else ""
            path = (parts[1] if len(parts) > 1 else "/").split("?")[0].lower()
            head_only = method == "HEAD"
            base = self.base_url(headers)

            if path in MP3_PATHS:
                if head_only:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nConnection: close\r\n\r\n")
                else:
                    await self._stream_mp3(writer)
            elif path in ("/radio.aac", "/stream.aac"):
                if head_only:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: audio/aac\r\nConnection: close\r\n\r\n")
                else:
                    await self._stream_aac(writer)
            elif path == "/radio.m3u":
                self._simple(writer, f"#EXTM3U\n#EXTINF:-1,CurlyBot Radio\n{base}/radio.mp3\n".encode(),
                             "audio/x-mpegurl", head_only)
            elif path == "/radio.pls":
                self._simple(writer, (f"[playlist]\nNumberOfEntries=1\nFile1={base}/radio.mp3\n"
                                      "Title1=CurlyBot Radio\nLength1=-1\nVersion=2\n").encode(),
                             "audio/x-scpls", head_only)
            elif path == "/nowplaying":
                info = {"playing": self.playing, "listeners": len(self.listeners), "track": self.now}
                self._simple(writer, json.dumps(info, ensure_ascii=False).encode(),
                             "application/json; charset=utf-8", head_only)
            else:
                body = (f"CurlyBot is alive!\nRadio: {base}/radio.mp3\nPlaylist: {base}/radio.m3u\n"
                        f"Listeners: {len(self.listeners)}\n").encode()
                self._simple(writer, body, "text/plain; charset=utf-8", head_only)
            await writer.drain()
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            logger.error(f"خطا در وب‌سرور رادیو: {e}")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def start_http(self, port: int):
        if not self.ffmpeg:
            logger.error("ffmpeg پیدا نشد! pip install imageio-ffmpeg (یا ffmpeg سیستم) لازم است.")
        else:
            await self._make_silence()
            self._silence_task = asyncio.create_task(self._silence_loop())
        self.server = await asyncio.start_server(self._handle, "0.0.0.0", port)
        logger.info(f"وب‌سرور و رادیو روی پورت {port} فعال شد (/radio.mp3)")


RADIO = RadioStation()


# ═══════════════════════ منبع آهنگ (ساندکلود، یوتیوب، لینک مستقیم) ═══════════════════════
SOURCE_PREFIX = {"soundcloud": "scsearch", "youtube": "ytsearch"}


def music_sources() -> list:
    raw = os.getenv("MUSIC_SOURCES", "soundcloud,youtube")
    out = [s.strip().lower() for s in raw.split(",") if s.strip().lower() in SOURCE_PREFIX]
    return out or ["soundcloud", "youtube"]


def _ydl_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20}
    cookies = os.getenv("YT_COOKIES_FILE")
    if cookies and os.path.exists(cookies):
        opts["cookiefile"] = cookies
    return opts


def _guess_source(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "soundcloud.com" in u:
        return "soundcloud"
    return "link"


def _flat_search(query: str, source: str, n: int = 6) -> list:
    opts = _ydl_opts()
    opts["extract_flat"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{SOURCE_PREFIX[source]}{n}:{query}", download=False)
    results = []
    for e in (info.get("entries") or []):
        page = e.get("webpage_url") or e.get("url")
        if page:
            results.append({"title": e.get("title") or query, "page": page,
                            "duration": e.get("duration"), "source": source})
    return results


def search_song(query: str):
    """
    سرچ آهنگ به ترتیب منبع‌ها. خروجی: (آهنگ یا None, دلیل).
    دلیل: "ok" | "too_long" | "not_found"
    """
    too_long = False
    for source in music_sources():
        try:
            results = _flat_search(query, source)
        except Exception as e:
            logger.warning(f"سرچ در {source} ناموفق بود: {e}")
            continue
        for r in results:
            d = r["duration"]
            if d is not None and d > MAX_SONG_SECONDS:
                too_long = True
                continue
            if d is not None and d < MIN_SONG_SECONDS:
                continue   # معمولاً پیش‌نمایش ۳۰ ثانیه‌ای
            return r, "ok"
    return None, ("too_long" if too_long else "not_found")


def song_from_url(url: str):
    """لینک مستقیم (یوتیوب، ساندکلود یا فایل mp3 و ...)."""
    opts = _ydl_opts()
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("entries"):
        info = info["entries"][0]
    page = info.get("webpage_url") or url
    title = info.get("title") or url.rstrip("/").split("/")[-1]
    d = info.get("duration")
    if d is not None and d > MAX_SONG_SECONDS:
        return None, "too_long"
    return {"title": title, "page": page, "duration": d, "source": _guess_source(page)}, "ok"


def resolve_stream(page_url: str):
    """لینک مستقیم صدا + هدرها + hls (روی همین سرور مصرف می‌شود، پس قفل بودن IP مشکلی ندارد)."""
    is_yt = _guess_source(page_url) == "youtube"
    variants = [None, ["android_vr"], ["tv"], ["ios"]] if is_yt else [None]
    last = None
    for client in variants:
        try:
            opts = _ydl_opts()
            opts["format"] = "bestaudio/best"
            if client:
                opts["extractor_args"] = {"youtube": {"player_client": client}}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(page_url, download=False)
            if info.get("entries"):
                info = info["entries"][0]
            url = info.get("url") or (info.get("requested_formats") or [{}])[0].get("url")
            if not url:
                raise RuntimeError("لینک صدا پیدا نشد")
            headers = {k: v for k, v in (info.get("http_headers") or {}).items()
                       if k.lower() != "accept-encoding"}
            hls = str(info.get("protocol", "")).startswith("m3u8")
            return url, headers, hls
        except Exception as e:
            last = e
            logger.warning(f"resolve ({client or 'default'}) ناموفق: {e}")
    raise last


def resolve_with_fallback(song: dict):
    """اول لینک خود آهنگ؛ اگر نشد همان آهنگ را از منبع دیگر پیدا و پخش می‌کنیم."""
    try:
        return resolve_stream(song["page"])
    except Exception as e:
        last = e
    for source in [s for s in SOURCE_PREFIX if s != song.get("source")]:
        try:
            for r in _flat_search(song["title"], source, n=3):
                d = r["duration"]
                if d is None or MIN_SONG_SECONDS <= d <= MAX_SONG_SECONDS + 60:
                    logger.info(f"پخش از منبع جایگزین ({source}): {r['page']}")
                    return resolve_stream(r["page"])
        except Exception as e:
            logger.warning(f"منبع جایگزین {source} هم ناموفق: {e}")
    raise last


# ═══════════════════════ مدیریت صف و پخش ═══════════════════════
class MusicPlayer:
    """صف آهنگ‌ها. بین قطع و وصل مجدد ربات حفظ می‌شود."""

    def __init__(self):
        self.bot = None
        self.queue = []
        self.now = None
        self.searching = set()
        self.event = None
        self.task = None
        self.started_at = 0.0

    @property
    def ready(self) -> bool:
        return bool(yt_dlp and RADIO.ffmpeg)

    def bind(self, bot):
        self.bot = bot
        if self.event is None:
            self.event = asyncio.Event()
        if self.ready and (self.task is None or self.task.done()):
            self.task = create_task(self._loop())
        elif not self.ready:
            logger.error("بخش موزیک غیرفعال است (yt-dlp یا ffmpeg نصب نیست).")

    async def say(self, text):
        try:
            await self.bot.say(text)
        except Exception as e:
            logger.error(f"خطا در ارسال پیام موزیک: {e}")

    def busy(self, user_id: str) -> bool:
        if user_id in self.searching:
            return True
        if self.now and self.now["requester_id"] == user_id:
            return True
        return any(s["requester_id"] == user_id for s in self.queue)

    async def request(self, user: User, query: str):
        if self.busy(user.id):
            await self.say(f"@{user.username} شما هنوز آهنگی در صف یا در حال پخش دارید. 🎵")
            return
        self.searching.add(user.id)
        try:
            await self.say(f"🔍 در حال سرچ: {query}")
            try:
                if query.lower().startswith("http"):
                    song, why = await asyncio.to_thread(song_from_url, query)
                else:
                    song, why = await asyncio.to_thread(search_song, query)
            except Exception as e:
                logger.error(f"خطا در سرچ آهنگ: {e}")
                song, why = None, "not_found"
            if not song:
                if why == "too_long":
                    await self.say(f"❌ @{user.username} آهنگ بیشتر از {to_fa_digits(MAX_SONG_SECONDS // 60)} دقیقه است.")
                else:
                    await self.say(f"❌ @{user.username} آهنگی پیدا نشد. اسم را دقیق‌تر بنویس یا لینک بده.")
                return
            song["requester"] = user.username
            song["requester_id"] = user.id
            self.queue.append(song)
            self.event.set()
            dur = f" ⏱ {fmt_duration(song['duration'])}" if song.get("duration") else ""
            await self.say(
                f"✅ پیدا کردم: {song['title']}{dur}\n"
                f"👤 درخواست از: @{user.username}\n"
                f"🔢 جایگاه در صف: {to_fa_digits(len(self.queue))}")
        finally:
            self.searching.discard(user.id)

    async def remove(self, user: User):
        for s in self.queue:
            if s["requester_id"] == user.id:
                self.queue.remove(s)
                await self.say(f"🗑️ @{user.username} آهنگ شما ({s['title']}) از صف حذف شد.")
                return
        if self.now and self.now["requester_id"] == user.id:
            await self.say(f"@{user.username} آهنگ شما الان در حال پخش است و قابل حذف نیست.")
        else:
            await self.say(f"@{user.username} شما آهنگی در صف ندارید.")

    def skip(self) -> bool:
        if RADIO.playing:
            RADIO.skip()
            return True
        return False

    def clear(self) -> int:
        n = len(self.queue)
        self.queue.clear()
        return n

    def queue_text(self) -> str:
        if not self.now and not self.queue:
            return "📋 صف خالی است. با -p اسم_آهنگ اولین آهنگ را بخواه! 🎶"
        lines = []
        if self.now:
            lines.append(f"▶️ در حال پخش: {self.now['title']} — @{self.now['requester']}")
        if self.queue:
            lines.append("📋 صف آهنگ‌ها:")
            for i, s in enumerate(self.queue, start=1):
                lines.append(f"{to_fa_digits(i)}. {s['title']} — @{s['requester']}")
        return "\n".join(lines)

    def now_text(self) -> str:
        if not self.now:
            return "الان آهنگی پخش نمی‌شود. 🔇"
        left = ""
        if self.now.get("duration"):
            remain = max(0, self.now["duration"] - (time.time() - self.started_at))
            left = f"\n⏳ حدود {fmt_duration(remain)} مانده"
        return f"🎧 الان: {self.now['title']}\n👤 @{self.now['requester']}{left}"

    async def _loop(self):
        try:
            while True:
                if not self.queue:
                    self.now = None
                    RADIO.now = None
                    self.event.clear()
                    await self.event.wait()
                song = self.queue.pop(0)
                self.now = song
                try:
                    stream, hdrs, hls = await asyncio.to_thread(resolve_with_fallback, song)
                    self.started_at = time.time()
                    RADIO.now = {"title": song["title"], "requester": song["requester"],
                                 "duration": song.get("duration")}
                    await self.say(f"▶️ در حال پخش: {song['title']}\n👤 درخواست از: @{song['requester']}")
                    limit = (song.get("duration") or 900) + 30
                    sent = await RADIO.play(stream, hdrs, max_seconds=limit, hls=hls)
                    if sent < 20000:
                        raise RuntimeError("صدایی دریافت نشد")
                except CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"خطا در پخش آهنگ: {e}")
                    reason = " ".join(str(e).split())[:110]
                    await self.say(f"❌ مشکلی در پخش آهنگ @{song['requester']} پیش آمد، رد شد.\nسبب: {reason}")
                finally:
                    self.now = None
        except CancelledError:
            logger.info("حلقه‌ی پخش موزیک لغو شد.")


MUSIC = MusicPlayer()


# ═══════════════════════ راهنما ═══════════════════════
def build_help_pages() -> dict:
    n = to_fa_digits(TOTAL_EMOTES)
    return {
        "main": (
            "🌟✨ به CurlyBot خوش اومدی ✨🌟\n"
            f"🤖 ربات کامل روم، ساخته‌ی @{OWNER_USERNAME} 💜\n"
            "━━━━━━━━━━━━━━\n"
            "📚 بخش‌های راهنما (این‌ها رو بنویس):\n"
            "💃 !help dance ➜ دنس‌ها\n"
            "🎵 !help music ➜ آهنگ و صف\n"
            "🎮 !help fun ➜ سرگرمی و واکنش‌ها\n"
            "🛡️ !help admin ➜ مدیریت (ادمین‌ها)\n"
            "👑 !help owner ➜ ویژه‌ی مالک و Host\n"
            "━━━━━━━━━━━━━━\n"
            "🚀 شروع سریع: بنویس 12 تا برقصی، یا -p اسم_آهنگ تا آهنگ بخونم 🎶"),
        "dance": (
            "💃✨ راهنمای دنس‌ها\n"
            f"🔢 عدد ۱ تا {n}، انگلیسی یا فارسی. مثال: 12 یا ۱۲\n"
            "🔤 اسم انگلیسی دنس. مثال: zombie\n"
            "🎲 random یا رندوم ➜ یه دنس شانسی\n"
            "⛔ stop یا استوپ ➜ توقف دنس\n"
            "📜 !dances [صفحه] ➜ لیست دنس‌ها با شماره و اسم\n"
            "🔗 !dancechain ➜ زنجیره‌ی ۳ دنس پشت سر هم\n"
            "🔁 دنس تا وقتی stop نزنی تکرار می‌شه 💫\n"
            "🥊 !punch @آیدی ➜ تو دنس زدن، اون دنس افتادن 😂"),
        "music": (
            "🎵🎶 راهنمای آهنگ\n"
            "🎧 -p اسم_آهنگ ➜ درخواست آهنگ (لینک هم قبوله)\n"
            "📋 -q یا !صف ➜ دیدن صف (با شماره و آیدی)\n"
            "🗑️ -r یا !حذف ➜ حذف آهنگ خودت از صف\n"
            "🎼 -np ➜ آهنگ فعلی\n"
            "☝️ هر نفر هم‌زمان فقط یک آهنگ؛ بعد از پخش دوباره می‌تونی بخوای\n"
            f"⏱ حداکثر مدت آهنگ: {to_fa_digits(MAX_SONG_SECONDS // 60)} دقیقه\n"
            "📻 برای شنیدن، روم باید روی رادیوی ربات تنظیم باشه (مالک روم)"),
        "fun": (
            "🎮🎉 سرگرمی و واکنش‌ها\n"
            "💜 !heart [تعداد] @آیدی ➜ قلب بنفش\n"
            "👏 !clap  😉 !wink  👋 !wave  👍 !thumbs ➜ مثل بالا\n"
            "🥊 !punch @آیدی ➜ مشت‌بازی با دنس\n"
            "⬇️ !down ➜ رفتن به پایین\n"
            "🏆 !top ➜ برترین‌های روم\n"
            "👥 !users ➜ افراد روم   🏓 !ping   ℹ️ !info\n"
            "👑 بنویس «سازنده» تا سازنده‌ی ربات رو ببینی"),
        "admin": (
            "🛡️ راهنمای ادمین‌ها (۱/۳)\n"
            "🎵 -s اسکیپ  |  -clear خالی کردن صف  |  !radio آدرس رادیو\n"
            "🚀 !tele @آیدی مکان  |  !tele to @آیدی  |  !tele me @آیدی  |  !tele me all  |  !tele list\n"
            "📍 !vip  !vip1  !dj  |  !addtele نام  |  !deltele نام  |  !set (ربات میاد پیشت)\n"
            "🧊 !freeze @آیدی  |  !unfreeze @آیدی یا all\n"
            "🎉 !party @آیدی|all شماره  |  !partys @آیدی یا all\n"
            "━━━━━━━━━━━━━━\n"
            "🛡️ ادمین‌ها (۲/۳)\n"
            "🔨 !kick @آیدی  |  !mute @آیدی [دقیقه]  |  !ban @آیدی  |  !unban @آیدی  |  !banlist\n"
            "👑 !mod @آیدی ➜ ادمین روم کردن  |  !unmod @آیدی ➜ برداشتن\n"
            "💬 !loopchat پیام (یا stop)  |  !spam تعداد پیام\n"
            "🕺 !emotebot شماره|اسم (یا stop) ➜ دنس دائمی ربات\n"
            "🎭 !item set @آیدی ➜ ظاهر ربات مثل اون کاربر\n"
            "━━━━━━━━━━━━━━\n"
            "🛡️ ادمین‌ها (۳/۳)\n"
            "💰 !wallet  |  !tip all مقدار  |  !tip @آیدی مقدار (۱،۵،۱۰،۵۰،۱۰۰)\n"
            "💬 !welcome پیام (یا on/off)  |  !announce پیام  |  !interval ثانیه\n"
            "📜 !listadd لیست ادمین‌ها  |  !antiflood on/off ضد اسپم\n"
            "💃 @آیدی در پیام‌های !party و !tele با یا بدون @ کار می‌کنه"),
        "owner": (
            "👑 ویژه‌ی Host و مالک\n"
            "➕ !addadmin @آیدی  |  ➖ !removeadmin @آیدی  (Host)\n"
            f"🌟 !addhost @آیدی  |  !removehost @آیدی  (فقط مالک @{OWNER_USERNAME})\n"
            "📋 !hosts ➜ لیست Hostها\n"
            "👑 !mod / !unmod ➜ برای مالک و ادمین‌های ربات"),
    }


HELP_ALIASES = {
    "main": "main", "1": "main", "": "main",
    "dance": "dance", "dances": "dance", "دنس": "dance", "رقص": "dance",
    "music": "music", "song": "music", "آهنگ": "music", "موزیک": "music",
    "fun": "fun", "سرگرمی": "fun",
    "admin": "admin", "ادمین": "admin", "مدیریت": "admin",
    "owner": "owner", "host": "owner", "مالک": "owner",
}


# ═══════════════════════ سیستم دستورات ═══════════════════════
class Cmd:
    __slots__ = ("func", "level", "cooldown")

    def __init__(self, func, level, cooldown):
        self.func, self.level, self.cooldown = func, level, cooldown


COMMANDS = {}


def command(*names, level=USER, cooldown=0.0):
    def deco(fn):
        c = Cmd(fn, level, cooldown)
        for name in names:
            COMMANDS[name.lower()] = c
        return fn
    return deco


DENY = {
    ADMIN: "⛔ این دستور مخصوص ادمین‌های ربات است!",
    HOST: "⛔ این دستور مخصوص Host است!",
    OWNER: f"⛔ این دستور فقط مخصوص مالک ربات (@{OWNER_USERNAME}) است!",
}


class CurlyBot(BaseBot):
    def __init__(self):
        super().__init__()
        self.config = {}
        self.load_config()
        self.scores = self.load_data()
        self.user_id = None
        self.active_users = {}      # username(lower) -> User
        self.user_positions = {}    # username(lower) -> Position
        self.user_dances = {}       # username -> emote id
        self.dance_tasks = {}       # username(یا id ربات) -> Task
        self.party_dances = {}      # username -> (emote, forced)
        self.frozen = {}            # username -> (Task, Position)
        self.cooldowns = {}
        self.flood = {}
        self.flood_warns = {}
        self.announcement_task = None
        self.score_task = None
        self.loopchat_task = None
        self._chat_lock = asyncio.Lock()
        self.help_pages = build_help_pages()

    # ───────────── تنظیمات و داده ─────────────
    def load_config(self):
        cfg = {}
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            elif os.path.exists(OLD_CONFIG_FILE):   # وارد کردن از ربات قبلی
                with open(OLD_CONFIG_FILE, "r", encoding="utf-8") as f:
                    old = json.load(f)
                for k in ("host_usernames", "admin_usernames", "vip_usernames", "banned_users",
                          "teleport_locations", "announcement_message", "announcement_interval"):
                    if k in old:
                        cfg[k] = old[k]
                logger.info("تنظیمات ربات قبلی (ادمین‌ها، مکان‌ها، ...) وارد شد.")
        except Exception as e:
            logger.error(f"خطا در خواندن تنظیمات: {e}")
            cfg = {}
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, json.loads(json.dumps(v)))
        # مالک همیشه Host و ادمین است
        for lst in ("host_usernames", "admin_usernames"):
            cfg[lst] = [u.lower() for u in cfg[lst]]
            if OWNER_USERNAME.lower() not in cfg[lst]:
                cfg[lst].append(OWNER_USERNAME.lower())
        for h in cfg["host_usernames"]:      # Hostها خودکار ادمین هم هستند
            if h not in cfg["admin_usernames"]:
                cfg["admin_usernames"].append(h)
        cfg["banned_users"] = [u.lower() for u in cfg["banned_users"]]
        self.config = cfg
        self.save_config()

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"خطا در ذخیره‌ی تنظیمات: {e}")

    def load_data(self) -> dict:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return {k: int(v) for k, v in json.load(f).get("scores", {}).items()}
        except Exception as e:
            logger.error(f"خطا در خواندن داده‌ها: {e}")
        return {}

    def save_data(self):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump({"scores": self.scores}, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"خطا در ذخیره‌ی داده‌ها: {e}")

    # ───────────── نقش‌ها ─────────────
    def role_of(self, username: str) -> int:
        u = username.lower()
        if u == OWNER_USERNAME.lower():
            return OWNER
        if u in self.config["host_usernames"]:
            return HOST
        if u in self.config["admin_usernames"]:
            return ADMIN
        return USER

    def is_admin(self, user) -> bool:
        return self.role_of(user.username) >= ADMIN

    # ───────────── ابزارهای چت ─────────────
    async def say(self, text, delay: float = 0.3):
        for chunk in chunk_text(text, 250):
            async with self._chat_lock:
                try:
                    await self.highrise.chat(chunk)
                except Exception as e:
                    logger.error(f"خطا در ارسال چت: {e}")
                await sleep(delay)

    async def whisper(self, user: User, text, delay: float = 0.3):
        for chunk in chunk_text(text, 250):
            async with self._chat_lock:
                try:
                    await self.highrise.send_whisper(user.id, chunk)
                except Exception:
                    try:
                        await self.highrise.chat(f"@{user.username} {chunk}")
                    except Exception as e:
                        logger.error(f"خطا در ارسال ویسپر: {e}")
                await sleep(delay)

    def humans(self) -> list:
        return [u for u in self.active_users.values() if u.id != self.user_id]

    async def sync_room_users(self):
        try:
            resp = await self.highrise.get_room_users()
            content = getattr(resp, "content", None)
            if content is None:
                logger.error(f"پاسخ نامعتبر get_room_users: {resp}")
                return
            current = {u.username.lower(): (u, pos) for u, pos in content}
            for username in list(self.active_users):
                if username not in current:
                    self.forget_user(username)
            for username, (u, pos) in current.items():
                self.active_users[username] = u
                self.user_positions[username] = pos
            logger.info(f"همگام‌سازی انجام شد. تعداد: {len(self.active_users)}")
        except Exception as e:
            logger.error(f"خطا در همگام‌سازی کاربران: {e}")

    async def find_user(self, name: str):
        name = name.lstrip("@").strip().lower()
        u = self.active_users.get(name)
        if u is None:
            await self.sync_room_users()
            u = self.active_users.get(name)
        return u

    def forget_user(self, username: str):
        self.active_users.pop(username, None)
        self.user_positions.pop(username, None)
        self._cancel_dance(username)
        self.party_dances.pop(username, None)
        fz = self.frozen.pop(username, None)
        if fz:
            fz[0].cancel()
        self.flood.pop(username, None)

    # ───────────── دنس ─────────────
    def _cancel_dance(self, username: str):
        self.user_dances.pop(username, None)
        t = self.dance_tasks.pop(username, None)
        if t and not t.done():
            t.cancel()

    async def start_dance(self, user: User, emote_id: str, forced: bool = False):
        username = user.username.lower()
        party = self.party_dances.get(username)
        if party and party[1] and not forced:
            await self.say(f"🔒 @{user.username} در رقص اجباری هستی؛ فقط ادمین با !partys می‌تونه متوقفش کنه.")
            return
        self._cancel_dance(username)
        self.user_dances[username] = emote_id
        if not forced:
            self.party_dances.pop(username, None)
        pause = EMOTE_SECONDS.get(emote_id, DEFAULT_EMOTE_SECONDS) + 1.0
        uid = user.id

        async def loop():
            first = True
            try:
                while self.user_dances.get(username) == emote_id and username in self.active_users:
                    try:
                        await self.highrise.send_emote(emote_id, uid)
                    except Exception as e:
                        logger.error(f"خطا در ارسال دنس {emote_id} برای {username}: {e}")
                        if first:
                            await self.say(f"❌ @{user.username} این دنس الان اجرا نشد.")
                        break
                    first = False
                    await sleep(pause)
            except CancelledError:
                pass
            finally:
                if self.dance_tasks.get(username) is asyncio.current_task():
                    self.dance_tasks.pop(username, None)

        self.dance_tasks[username] = create_task(loop())

    async def stop_dance(self, user: User) -> bool:
        username = user.username.lower()
        party = self.party_dances.get(username)
        if party and party[1]:
            await self.say(f"🔒 @{user.username} رقص اجباری رو فقط ادمین با !partys می‌تونه متوقف کنه.")
            return False
        self._cancel_dance(username)
        self.party_dances.pop(username, None)
        return True

    # ───────────── وظایف پس‌زمینه ─────────────
    async def announcement_loop(self):
        try:
            while True:
                await sleep(max(30, int(self.config["announcement_interval"])))
                if self.config.get("announcement_enabled") and self.humans():
                    await self.say(self.config["announcement_message"])
        except CancelledError:
            pass
        except Exception as e:
            logger.error(f"خطا در حلقه‌ی اطلاع‌رسانی: {e}")

    async def score_loop(self):
        try:
            while True:
                await sleep(300)
                for username in self.active_users:
                    self.scores[username] = self.scores.get(username, 0) + 5
                self.save_data()
        except CancelledError:
            pass
        except Exception as e:
            logger.error(f"خطا در حلقه‌ی امتیاز: {e}")

    async def cleanup_tasks(self):
        tasks = list(self.dance_tasks.values()) + [fz[0] for fz in self.frozen.values()]
        tasks += [t for t in (self.announcement_task, self.score_task, self.loopchat_task) if t]
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except BaseException:
                pass
        self.dance_tasks.clear()
        self.user_dances.clear()
        self.party_dances.clear()
        self.frozen.clear()
        self.announcement_task = self.score_task = self.loopchat_task = None
        self.save_data()
        logger.info("همه‌ی وظایف پس‌زمینه لغو شدند.")

    # ───────────── رویدادها ─────────────
    async def on_start(self, session_metadata):
        logger.info("ربات به سرور هایرایز وصل شد.")
        self.user_id = getattr(session_metadata, "user_id", None)
        await self.cleanup_tasks()
        pos = self.config.get("bot_position") or list(BOT_START_POSITION)
        try:
            await self.highrise.teleport(user_id=self.user_id, dest=Position(x=pos[0], y=pos[1], z=pos[2]))
        except Exception as e:
            logger.error(f"خطا در تلپورت اولیه: {e}")
        await self.sync_room_users()
        self.announcement_task = create_task(self.announcement_loop())
        self.score_task = create_task(self.score_loop())
        MUSIC.bind(self)
        emo = self.config.get("emotebot_emote")
        if emo in EMOTE_SECONDS:
            self._start_emotebot(emo)
        if PUBLIC_URL:
            logger.info(f"📻 آدرس رادیو برای تنظیمات روم: {PUBLIC_URL}/radio.mp3")

    async def on_user_join(self, user: User, position):
        username = user.username.lower()
        if username in self.config["banned_users"]:
            try:
                await self.highrise.moderate_room(user.id, "kick")
            except Exception as e:
                logger.error(f"خطا در کیک کاربر بن‌شده {user.username}: {e}")
            return
        self.active_users[username] = user
        self.user_positions[username] = position
        self.scores[username] = self.scores.get(username, 0) + 10
        if self.config.get("welcome_enabled") and user.id != self.user_id:
            msg = self.config["welcome_message"]
            try:
                msg = msg.format(username=user.username)
            except Exception:
                pass
            await self.say(msg)

    async def on_user_leave(self, user: User, *args):
        username = user.username.lower()
        self.forget_user(username)
        if self.config.get("leave_message_enabled"):
            await self.say(f"👋 @{user.username} از روم رفت.")

    async def on_user_move(self, user: User, pos):
        username = user.username.lower()
        self.user_positions[username] = pos
        fz = self.frozen.get(username)
        if fz:
            target = fz[1]
            cur = as_position(pos)
            if cur and (abs(cur.x - target.x) > 0.3 or abs(cur.z - target.z) > 0.3):
                try:
                    await self.highrise.teleport(user_id=user.id, dest=target)
                except Exception as e:
                    logger.error(f"خطا در برگرداندن کاربر فریز {username}: {e}")

    async def on_message(self, user_id: str, *args, **kwargs):
        """پیام خصوصی (DM): به مکالمه‌های جدید یک معرفی خودکار می‌فرستد."""
        if user_id == self.user_id:
            return
        conversation_id = args[0] if args else kwargs.get("conversation_id", user_id)
        is_new = args[1] if len(args) > 1 else kwargs.get("is_new_conversation", True)
        if not is_new:
            return
        text = (
            "سلام عزیز! 💜\n\n"
            "🤖 من CurlyBot هستم؛ ربات کامل مدیریت و سرگرمی روم!\n"
            f"💃 {to_fa_digits(TOTAL_EMOTES)} دنس با عدد انگلیسی/فارسی و اسم\n"
            "🎵 پخش آهنگ درخواستی با صف و رادیوی اختصاصی\n"
            "🥊 !punch، 🛡️ مدیریت کامل، 🎉 پارتی، 🧊 فریز و کلی امکانات دیگه\n"
            "⚡ آنلاین ۲۴ ساعته\n\n"
            f"🤝 برای اجاره‌ی ربات به @{OWNER_USERNAME} پیام بدید 👑")
        try:
            await self.highrise.send_message(conversation_id, text)
        except Exception as e:
            logger.error(f"خطا در پاسخ خودکار دایرکت: {e}")

    async def on_tip(self, sender: User, receiver: User, tip):
        amount = getattr(tip, "amount", 0)
        if receiver.id == self.user_id:
            await self.say(f"💛 مرسی @{sender.username} بابت {amount} گلد! 🙏")
        self.scores[sender.username.lower()] = self.scores.get(sender.username.lower(), 0) + int(amount or 0)

    def flood_hit(self, user: User) -> bool:
        now = time.monotonic()
        dq = self.flood.setdefault(user.username.lower(), collections.deque(maxlen=6))
        dq.append(now)
        return len(dq) == 6 and now - dq[0] < 5.0

    async def on_chat(self, user: User, message: str):
        if user.id == self.user_id:
            return
        username = user.username.lower()
        msg = message.strip()
        if not msg:
            return
        try:
            self.scores[username] = self.scores.get(username, 0) + 2
            low = msg.lower()
            norm = normalize_digits(low)

            if (self.config.get("antiflood_enabled") and self.role_of(user.username) == USER
                    and self.flood_hit(user)):
                warns = self.flood_warns.get(username, 0) + 1
                self.flood_warns[username] = warns
                self.flood.pop(username, None)
                if warns >= 3:
                    self.flood_warns[username] = 0
                    try:
                        await self.highrise.moderate_room(user.id, "mute", 300)
                        await self.say(f"🔇 @{user.username} به‌خاطر اسپم ۵ دقیقه سکوت شد.")
                    except Exception as e:
                        logger.error(f"خطا در میوت اسپمر: {e}")
                else:
                    await self.say(f"⚠️ @{user.username} لطفاً اسپم نکن! ({to_fa_digits(warns)}/۳)")
                return

            emote = EMOTE_LOOKUP.get(norm)
            if emote:
                await self.start_dance(user, emote)
            elif low in ("stop", "استوپ"):
                await self.stop_dance(user)
            elif low in ("random", "رندوم", "شانسی"):
                await self.start_dance(user, random.choice(EMOTE_IDS))
            elif low in ("سازنده", "creature", "creator", "سازندت", "سازنده بات"):
                await self.say(f"👑 سازنده‌ی این ربات: @{OWNER_USERNAME} 👑")
            elif msg[0] in "!-":
                create_task(self.dispatch(user, msg))
        except Exception as e:
            logger.error(f"خطا در on_chat از {username}: {e}")

    async def dispatch(self, user: User, msg: str):
        tokens = msg.split()
        name = tokens[0].lower()
        args = tokens[1:]
        if name == "!item" and args and args[0].lower() == "set":
            name, args = "!item set", args[1:]
        cmd = COMMANDS.get(name)
        if cmd is None:
            if name.startswith("!"):
                await self.say(f"❌ دستور نامعلوم! برای دیدن دستورها !help بزن یا به @{OWNER_USERNAME} پیام بده.")
            return
        role = self.role_of(user.username)
        if role < cmd.level:
            await self.say(DENY[cmd.level])
            return
        if cmd.cooldown and role < ADMIN:
            key = (user.username.lower(), name)
            now = time.monotonic()
            if now - self.cooldowns.get(key, 0) < cmd.cooldown:
                return
            self.cooldowns[key] = now
        try:
            await cmd.func(self, user, args)
        except Exception as e:
            logger.error(f"خطا در دستور {name} از {user.username}: {e}", exc_info=True)
            await self.say(f"❌ خطا در اجرای دستور: {e}")

    # ═══════════════════════ دستورات ═══════════════════════
    async def need_target(self, user, args, usage, idx=0):
        if len(args) <= idx:
            await self.say(f"⚠️ فرمت درست: {usage}")
            return None
        target = await self.find_user(args[idx])
        if not target:
            await self.say(f"❌ کاربر {args[idx].lstrip('@')} الان در روم نیست.")
        return target

    async def tp(self, user_id, pos) -> bool:
        p = as_position(pos)
        if not p:
            return False
        await self.highrise.teleport(user_id=user_id, dest=p)
        return True

    # ───────────── راهنما و اطلاعات ─────────────
    @command("!help", "!راهنما", "!commands", cooldown=3)
    async def cmd_help(self, user, args):
        key = HELP_ALIASES.get(args[0].lower() if args else "", None)
        if key is None:
            await self.say(f"❓ بخش «{args[0]}» وجود ندارد. بنویس !help تا بخش‌ها را ببینی.")
            return
        text = self.help_pages[key]
        if self.config.get("help_in_whisper"):
            await self.whisper(user, text)
        else:
            await self.say(text)

    @command("!dances", "!دنس‌ها", "!emotes", cooldown=3)
    async def cmd_dances(self, user, args):
        per = 24
        pages = (TOTAL_EMOTES + per - 1) // per
        try:
            page = int(normalize_digits(args[0])) if args else 1
        except ValueError:
            page = 1
        page = max(1, min(page, pages))
        start = (page - 1) * per
        items = [f"{to_fa_digits(EMOTE_NUMBER[e])}.{EMOTE_LABEL[e]}" for e in EMOTE_IDS[start:start + per]]
        text = (f"💃 دنس‌ها (صفحه {to_fa_digits(page)} از {to_fa_digits(pages)})\n" + " | ".join(items)
                + f"\n➡️ صفحه‌ی بعد: !dances {to_fa_digits(min(page + 1, pages))}")
        await self.whisper(user, text) if self.config.get("help_in_whisper") else await self.say(text)

    @command("!dancechain", "!زنجیره", cooldown=20)
    async def cmd_dancechain(self, user, args):
        chain = ["dance-tiktok8", "dance-blackpink", "dance-tiktok2"]
        await self.say(f"🔗 زنجیره‌ی رقص برای @{user.username} شروع شد! 💃")
        for emote in chain:
            await self.highrise.send_emote(emote, user.id)
            await sleep(EMOTE_SECONDS.get(emote, DEFAULT_EMOTE_SECONDS) + 0.5)

    @command("!ping", cooldown=3)
    async def cmd_ping(self, user, args):
        await self.say(f"🏓 پونگ! آنلاین از {fmt_uptime(time.time() - START_TIME)} پیش ✅")

    @command("!info", "!about", cooldown=5)
    async def cmd_info(self, user, args):
        music = "فعال ✅" if MUSIC.ready else "غیرفعال ❌"
        await self.say(
            f"🤖 CurlyBot | سازنده @{OWNER_USERNAME} 💜\n"
            f"⏱ آنلاین: {fmt_uptime(time.time() - START_TIME)}\n"
            f"💃 دنس‌ها: {to_fa_digits(TOTAL_EMOTES)}   👥 افراد روم: {to_fa_digits(len(self.humans()))}\n"
            f"🎵 موزیک: {music}   📻 شنونده‌ها: {to_fa_digits(len(RADIO.listeners))}")

    @command("!users", "!count", cooldown=5)
    async def cmd_users(self, user, args):
        names = [f"@{u.username}" for u in self.humans()]
        shown = " ".join(names[:25]) + (f" و {to_fa_digits(len(names) - 25)} نفر دیگر" if len(names) > 25 else "")
        await self.say(f"👥 {to_fa_digits(len(names))} نفر در روم هستند:\n{shown}")

    @command("!top", "!برترین", cooldown=10)
    async def cmd_top(self, user, args):
        top = sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)[:5]
        if not top:
            await self.say("هنوز امتیازی ثبت نشده. 🏆")
            return
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        lines = [f"{medals[i]} @{n} — {to_fa_digits(s)}" for i, (n, s) in enumerate(top)]
        await self.say("🏆 برترین‌های روم:\n" + "\n".join(lines))

    # ───────────── موزیک ─────────────
    @command("-p", "!p", "!play", cooldown=2)
    async def cmd_play(self, user, args):
        if not MUSIC.ready:
            await self.say("❌ بخش موزیک فعال نیست (yt-dlp یا ffmpeg نصب نشده).")
            return
        query = " ".join(args).strip()
        if not query:
            await self.say("⚠️ فرمت درست: -p اسم_آهنگ  (یا -p لینک)")
            return
        await MUSIC.request(user, query)

    @command("-q", "-queue", "!q", "!queue", "!صف", cooldown=3)
    async def cmd_queue(self, user, args):
        await self.say(MUSIC.queue_text())

    @command("-r", "-remove", "!حذف", "!remove", cooldown=2)
    async def cmd_remove(self, user, args):
        await MUSIC.remove(user)

    @command("-np", "!np", "!nowplaying", "!الان", cooldown=3)
    async def cmd_np(self, user, args):
        await self.say(MUSIC.now_text())

    @command("-s", "-skip", "!skip", level=ADMIN)
    async def cmd_skip(self, user, args):
        if MUSIC.skip():
            await self.say(f"⏭️ آهنگ توسط @{user.username} رد شد.")
        else:
            await self.say("الان آهنگی در حال پخش نیست.")

    @command("-clear", "!clearqueue", level=ADMIN)
    async def cmd_clear(self, user, args):
        n = MUSIC.clear()
        await self.say(f"🧹 صف خالی شد ({to_fa_digits(n)} آهنگ حذف شد).")

    @command("!radio", "-radio", level=ADMIN)
    async def cmd_radio(self, user, args):
        if PUBLIC_URL:
            text = (f"📻 رادیوی ربات\n🔗 آدرس اصلی (MP3): {PUBLIC_URL}/radio.mp3\n"
                    f"📃 پلی‌لیست: {PUBLIC_URL}/radio.m3u\n🎚 AAC: {PUBLIC_URL}/radio.aac\n")
        else:
            text = ("📻 رادیوی ربات\n⚠️ PUBLIC_URL تنظیم نشده. آدرس سرور خودت را بگذار و بعدش /radio.mp3 را اضافه کن.\n")
        await self.say(text + f"👥 شنونده‌ها: {to_fa_digits(len(RADIO.listeners))}")

    # ───────────── سرگرمی ─────────────
    @command("!punch", cooldown=PUNCH_COOLDOWN)
    async def cmd_punch(self, user, args):
        target = await self.need_target(user, args, "!punch @آیدی")
        if not target:
            return
        if target.id == user.id:
            await self.say(f"@{user.username} نمی‌تونی خودت رو بزنی! 😅")
            return
        party = self.party_dances.get(target.username.lower())
        if party and party[1]:
            await self.say(f"🔒 @{target.username} در رقص اجباری است و نمی‌شه بهش !punch زد.")
            return
        for u in (user, target):          # رقص‌های حلقه‌ای دو طرف متوقف می‌شود تا روی هم نیفتند
            name = u.username.lower()
            if name not in self.party_dances:
                self._cancel_dance(name)
        results = await asyncio.gather(
            self.highrise.send_emote(PUNCH_EMOTE, user.id),
            self.highrise.send_emote(FALL_EMOTE, target.id),
            return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"خطا در !punch: {r}")
        await self.say(f"🥊 @{user.username} یه مشت حسابی به @{target.username} زد! 💥")

    async def _react_cmd(self, user, args, reactions, label, usage):
        count, target = None, None
        for a in args:
            n = normalize_digits(a)
            if a.startswith("@"):
                target = a.lstrip("@").lower()
            elif n.isdigit() and count is None:
                count = int(n)
            elif a.lower() == "all":
                target = "all"
            elif target is None:
                target = a.lower()
        count = count or 1
        if target is None:
            await self.say(f"⚠️ فرمت درست: {usage}")
            return

        async def send(uid):
            err = None
            for r in reactions:      # اگر اسم واکنش در نسخه‌ی SDK فرق داشت، اسم دوم امتحان می‌شود
                try:
                    await self.highrise.react(r, uid)
                    return None
                except Exception as e:
                    err = e
            return err

        if target == "all":
            if not self.is_admin(user):
                await self.say(DENY[ADMIN])
                return
            ok = 0
            for u in self.humans():
                if await send(u.id) is None:
                    ok += 1
                await sleep(0.5)
            await self.say(f"{label} برای {to_fa_digits(ok)} نفر ارسال شد! 🎉")
            return
        victim = await self.find_user(target)
        if not victim:
            await self.say(f"❌ کاربر {target} الان در روم نیست.")
            return
        limit = 100 if self.is_admin(user) else 20
        if not 1 <= count <= limit:
            await self.say(f"@{user.username} تعداد باید بین ۱ تا {to_fa_digits(limit)} باشد.")
            return
        sent = 0
        for _ in range(count):
            if victim.username.lower() not in self.active_users:
                break
            err = await send(victim.id)
            if err:
                await self.say(f"❌ خطا در ارسال: {err}")
                break
            sent += 1
            await sleep(0.5)
        if sent:
            await self.say(f"{label} ×{to_fa_digits(sent)} برای @{victim.username} ارسال شد!")

    @command("!heart", "!قلب", cooldown=3)
    async def cmd_heart(self, user, args):
        await self._react_cmd(user, args, ["heart"], "💜 قلب", "!heart [تعداد] @آیدی  یا  !heart all")

    @command("!clap", "!دست", cooldown=3)
    async def cmd_clap(self, user, args):
        await self._react_cmd(user, args, ["clap"], "👏 تشویق", "!clap [تعداد] @آیدی  یا  !clap all")

    @command("!wink", cooldown=3)
    async def cmd_wink(self, user, args):
        await self._react_cmd(user, args, ["wink"], "😉 چشمک", "!wink [تعداد] @آیدی  یا  !wink all")

    @command("!wave", cooldown=3)
    async def cmd_wave(self, user, args):
        await self._react_cmd(user, args, ["wave"], "👋 سلام", "!wave [تعداد] @آیدی  یا  !wave all")

    @command("!thumbs", "!like", cooldown=3)
    async def cmd_thumbs(self, user, args):
        await self._react_cmd(user, args, ["thumbs", "thumbs-up"], "👍 لایک", "!thumbs [تعداد] @آیدی  یا  !thumbs all")

    @command("!down", cooldown=5)
    async def cmd_down(self, user, args):
        await self.highrise.teleport(user_id=user.id, dest=Position(x=DOWN_POSITION[0], y=DOWN_POSITION[1], z=DOWN_POSITION[2]))
        await self.say(f"⬇️ @{user.username} به پایین رفت.")

    # ───────────── تلپورت ─────────────
    @command("!tele", level=ADMIN)
    async def cmd_tele(self, user, args):
        a = [x.lower() for x in args]
        locs = self.config["teleport_locations"]
        usage = "!tele @آیدی مکان  |  !tele to @آیدی  |  !tele me @آیدی  |  !tele me all  |  !tele list"
        if len(a) == 1 and a[0] in ("list", "لیست"):
            await self.say("📍 مکان‌ها: " + " ، ".join(locs.keys()))
        elif len(a) == 2 and a[0] == "to":
            target = await self.need_target(user, args, usage, 1)
            if target:
                ok = await self.tp(user.id, self.user_positions.get(target.username.lower()))
                await self.say(f"🚀 @{user.username} پیش @{target.username} رفت." if ok else "❌ موقعیت کاربر در دسترس نیست.")
        elif len(a) == 2 and a[0] == "me" and a[1] == "all":
            mine = as_position(self.user_positions.get(user.username.lower()))
            if not mine:
                await self.say("❌ موقعیت شما در دسترس نیست.")
                return
            ok = 0
            for u in self.humans():
                if u.id == user.id:
                    continue
                try:
                    await self.highrise.teleport(user_id=u.id, dest=mine)
                    ok += 1
                except Exception as e:
                    logger.error(f"خطا در تلپورت {u.username}: {e}")
                await sleep(0.5)
            await self.say(f"🚀 {to_fa_digits(ok)} نفر پیش @{user.username} آمدند.")
        elif len(a) == 2 and a[0] == "me":
            target = await self.need_target(user, args, usage, 1)
            if target:
                mine = self.user_positions.get(user.username.lower())
                ok = await self.tp(target.id, mine)
                await self.say(f"🚀 @{target.username} پیش @{user.username} آمد." if ok else "❌ موقعیت شما در دسترس نیست.")
        elif len(a) == 2:
            target = await self.need_target(user, args, usage, 0)
            if not target:
                return
            loc = locs.get(a[1])
            if not loc:
                await self.say(f"❌ مکان {a[1]} وجود ندارد. با !tele list ببین.")
                return
            await self.highrise.teleport(user_id=target.id, dest=Position(x=loc["x"], y=loc["y"], z=loc["z"]))
            await self.say(f"🚀 @{target.username} به {a[1].upper()} تلپورت شد!")
        else:
            await self.say(f"⚠️ فرمت درست:\n{usage}")

    async def _goto(self, user, name):
        loc = self.config["teleport_locations"].get(name)
        if not loc:
            await self.say(f"❌ مکان {name} تعریف نشده. با !addtele بسازش.")
            return
        await self.highrise.teleport(user_id=user.id, dest=Position(x=loc["x"], y=loc["y"], z=loc["z"]))
        await self.say(f"🚀 @{user.username} به {name.upper()} تلپورت شد!")

    @command("!vip", level=ADMIN)
    async def cmd_vip(self, user, args):
        await self._goto(user, "vip")

    @command("!vip1", level=ADMIN)
    async def cmd_vip1(self, user, args):
        await self._goto(user, "vip1")

    @command("!dj", level=ADMIN)
    async def cmd_dj(self, user, args):
        await self._goto(user, "dj")

    @command("!addtele", level=ADMIN)
    async def cmd_addtele(self, user, args):
        if len(args) != 1:
            await self.say("⚠️ فرمت درست: !addtele نام_مکان")
            return
        pos = as_position(self.user_positions.get(user.username.lower()))
        if not pos:
            await self.say("❌ موقعیت شما مشخص نیست (روی صندلی نباش).")
            return
        self.config["teleport_locations"][args[0].lower()] = {"x": pos.x, "y": pos.y, "z": pos.z}
        self.save_config()
        await self.say(f"📍 مکان {args[0].lower()} ذخیره شد!")

    @command("!deltele", level=ADMIN)
    async def cmd_deltele(self, user, args):
        if len(args) != 1:
            await self.say("⚠️ فرمت درست: !deltele نام_مکان")
            return
        name = args[0].lower()
        if name in ("vip", "vip1", "dj"):
            await self.say(f"❌ مکان پیش‌فرض {name} حذف نمی‌شود.")
        elif name not in self.config["teleport_locations"]:
            await self.say(f"❌ مکان {name} وجود ندارد.")
        else:
            del self.config["teleport_locations"][name]
            self.save_config()
            await self.say(f"🗑️ مکان {name} حذف شد.")

    @command("!set", level=ADMIN)
    async def cmd_set(self, user, args):
        pos = as_position(self.user_positions.get(user.username.lower()))
        if not pos:
            await self.say(f"@{user.username} موقعیت شما مشخص نیست.")
            return
        await self.highrise.teleport(user_id=self.user_id, dest=pos)
        self.config["bot_position"] = [pos.x, pos.y, pos.z]
        self.save_config()
        await self.say(f"📍 ربات به موقعیت @{user.username} آمد و جای جدیدش ذخیره شد.")

    @command("!item set", level=ADMIN)
    async def cmd_item_set(self, user, args):
        target = await self.need_target(user, args, "!item set @آیدی")
        if not target:
            return
        resp = await self.highrise.get_user_outfit(target.id)
        outfit = getattr(resp, "outfit", None)
        if not outfit:
            await self.say(f"❌ ظاهر @{target.username} در دسترس نیست.")
            return
        await self.highrise.set_outfit(outfit)
        await self.say(f"🎭 ظاهر ربات مثل @{target.username} شد!")

    # ───────────── مدیریت کاربران ─────────────
    def can_act_on(self, actor: User, target: User) -> bool:
        return self.role_of(actor.username) > self.role_of(target.username)

    @command("!kick", level=ADMIN)
    async def cmd_kick(self, user, args):
        target = await self.need_target(user, args, "!kick @آیدی")
        if not target:
            return
        if not self.can_act_on(user, target):
            await self.say("⛔ نمی‌تونی به همرتبه یا بالاتر از خودت اعمال کنی.")
            return
        await self.highrise.moderate_room(target.id, "kick")
        await self.say(f"👢 @{target.username} از روم اخراج شد.")

    @command("!mute", level=ADMIN)
    async def cmd_mute(self, user, args):
        target = await self.need_target(user, args, "!mute @آیدی [دقیقه]")
        if not target:
            return
        if not self.can_act_on(user, target):
            await self.say("⛔ نمی‌تونی به همرتبه یا بالاتر از خودت اعمال کنی.")
            return
        minutes = 10
        if len(args) > 1 and normalize_digits(args[1]).isdigit():
            minutes = max(1, min(int(normalize_digits(args[1])), 1440))
        await self.highrise.moderate_room(target.id, "mute", minutes * 60)
        await self.say(f"🔇 @{target.username} برای {to_fa_digits(minutes)} دقیقه سکوت شد.")

    @command("!ban", level=ADMIN)
    async def cmd_ban(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !ban @آیدی")
            return
        name = args[0].lstrip("@").lower()
        target = self.active_users.get(name)
        if target and not self.can_act_on(user, target):
            await self.say("⛔ نمی‌تونی به همرتبه یا بالاتر از خودت اعمال کنی.")
            return
        if name in self.config["banned_users"]:
            await self.say(f"@{name} از قبل بن است.")
            return
        self.config["banned_users"].append(name)
        self.save_config()
        if target:
            try:
                await self.highrise.moderate_room(target.id, "kick")
            except Exception as e:
                logger.error(f"خطا در کیک بعد از بن: {e}")
        await self.say(f"🔨 @{name} بن شد و دیگر نمی‌تواند وارد روم شود.")

    @command("!unban", level=ADMIN)
    async def cmd_unban(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !unban @آیدی")
            return
        name = args[0].lstrip("@").lower()
        if name not in self.config["banned_users"]:
            await self.say(f"@{name} در لیست بن نیست.")
            return
        self.config["banned_users"].remove(name)
        self.save_config()
        await self.say(f"✅ @{name} آنبن شد.")

    @command("!banlist", level=ADMIN)
    async def cmd_banlist(self, user, args):
        b = self.config["banned_users"]
        await self.say("🔨 لیست بن: " + (" ".join(f"@{n}" for n in b) if b else "خالی است"))

    @command("!freeze", level=ADMIN)
    async def cmd_freeze(self, user, args):
        target = await self.need_target(user, args, "!freeze @آیدی")
        if not target:
            return
        name = target.username.lower()
        if name in self.frozen:
            await self.say(f"@{name} از قبل فریز است.")
            return
        if not self.can_act_on(user, target):
            await self.say("⛔ نمی‌تونی به همرتبه یا بالاتر از خودت اعمال کنی.")
            return
        pos = as_position(self.user_positions.get(name))
        if not pos:
            await self.say(f"❌ موقعیت @{name} در دسترس نیست.")
            return

        async def loop():
            try:
                while name in self.frozen and name in self.active_users:
                    await sleep(2.0)
                    cur = as_position(self.user_positions.get(name))
                    if cur and (abs(cur.x - pos.x) > 0.3 or abs(cur.z - pos.z) > 0.3):
                        await self.highrise.teleport(user_id=target.id, dest=pos)
            except CancelledError:
                pass
            except Exception as e:
                logger.error(f"خطا در حلقه‌ی فریز {name}: {e}")

        self.frozen[name] = (create_task(loop()), pos)
        await self.say(f"🧊 @{target.username} فریز شد!")

    @command("!unfreeze", level=ADMIN)
    async def cmd_unfreeze(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !unfreeze @آیدی  یا  !unfreeze all")
            return
        if args[0].lower() == "all":
            n = len(self.frozen)
            for t, _ in self.frozen.values():
                t.cancel()
            self.frozen.clear()
            await self.say(f"🔥 {to_fa_digits(n)} نفر از فریز آزاد شدند.")
            return
        name = args[0].lstrip("@").lower()
        fz = self.frozen.pop(name, None)
        if not fz:
            await self.say(f"@{name} فریز نشده است.")
            return
        fz[0].cancel()
        await self.say(f"🔥 @{name} از فریز آزاد شد!")

    @command("!party", level=ADMIN)
    async def cmd_party(self, user, args):
        if len(args) < 2:
            await self.say("⚠️ فرمت درست: !party @آیدی شماره  یا  !party all شماره")
            return
        emote = resolve_emote(args[1])
        if not emote:
            await self.say(f"❌ دنس «{args[1]}» وجود ندارد! (۱ تا {to_fa_digits(TOTAL_EMOTES)} یا اسم)")
            return
        num = to_fa_digits(EMOTE_NUMBER[emote])
        if args[0].lower() == "all":
            ok = 0
            for u in self.humans():
                await self.start_dance(u, emote, forced=True)
                self.party_dances[u.username.lower()] = (emote, False)   # کاربر خودش می‌تواند stop بزند
                ok += 1
                await sleep(0.3)
            await self.say(f"🎉 دنس شماره‌ی {num} برای {to_fa_digits(ok)} نفر فعال شد!")
            return
        target = await self.need_target(user, args, "!party @آیدی شماره")
        if target:
            await self.start_dance(target, emote, forced=True)
            self.party_dances[target.username.lower()] = (emote, True)   # فقط ادمین متوقف می‌کند
            await self.say(f"🎉 دنس شماره‌ی {num} برای @{target.username} فعال شد! 🔒")

    @command("!partys", level=ADMIN)
    async def cmd_partys(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !partys @آیدی  یا  !partys all")
            return
        if args[0].lower() == "all":
            names = list(self.party_dances)
        else:
            names = [args[0].lstrip("@").lower()]
        n = 0
        for name in names:
            if name in self.party_dances:
                self.party_dances.pop(name, None)
                self._cancel_dance(name)
                n += 1
        await self.say(f"🛑 رقص اجباری {to_fa_digits(n)} نفر متوقف شد." if n else "کسی در رقص اجباری نیست.")

    # ───────────── ادمین کردن در روم ─────────────
    async def _room_privilege(self, user, args, grant: bool):
        label = "!mod" if grant else "!unmod"
        target = await self.need_target(user, args, f"{label} @آیدی")
        if not target:
            return
        if target.id == self.user_id:
            await self.say("🤖 این دستور برای خود ربات معنا ندارد.")
            return
        attempts = ([RoomPermissions(moderator=True, designer=True), RoomPermissions(moderator=True)]
                    if grant else [RoomPermissions(moderator=False, designer=False)])
        last_err = None
        for perms in attempts:
            try:
                result = await self.highrise.change_room_privilege(target.id, perms)
                if result is not None and "error" in type(result).__name__.lower():
                    raise RuntimeError(getattr(result, "message", str(result)))
                if grant:
                    await self.say(f"👑 @{target.username} حالا ادمین (مدیر) روم شد! 🎉")
                else:
                    await self.say(f"✅ دسترسی مدیریت @{target.username} برداشته شد.")
                logger.info(f"{label} {target.username} توسط {user.username} انجام شد.")
                return
            except Exception as e:
                last_err = e
                logger.error(f"خطا در {label} ({perms}): {e}")
        info = "نامشخص"
        try:
            bp = await self.highrise.get_room_privilege(self.user_id)
            info = f"moderator={getattr(bp, 'moderator', '?')}, designer={getattr(bp, 'designer', '?')}"
        except Exception:
            pass
        await self.say(
            f"❌ هایرایز اجازه نداد: {last_err}\n"
            f"🔐 دسترسی فعلی ربات در این روم: {info}\n"
            "💡 تغییر مدیرها فقط وقتی کار می‌کند که ربات با توکنِ اکانتِ مالک روم ساخته شده باشد. "
            "اگر این‌طور نیست، دستی از پروفایل کاربر مدیرش کن.")

    @command("!mod", level=ADMIN)
    async def cmd_mod(self, user, args):
        await self._room_privilege(user, args, True)

    @command("!unmod", level=ADMIN)
    async def cmd_unmod(self, user, args):
        await self._room_privilege(user, args, False)

    # ───────────── پیام‌ها و ظاهر ربات ─────────────
    @command("!spam", level=ADMIN)
    async def cmd_spam(self, user, args):
        if not args or not normalize_digits(args[0]).isdigit():
            await self.say("⚠️ فرمت درست: !spam تعداد پیام")
            return
        count = int(normalize_digits(args[0]))
        if not 1 <= count <= 100:
            await self.say("تعداد پیام‌ها باید بین ۱ تا ۱۰۰ باشد.")
            return
        text = " ".join(args[1:]) or "اسپم آزمایشی!"
        for _ in range(count):
            await self.say(text, delay=1.7)
        await self.say(f"✅ {to_fa_digits(count)} پیام ارسال شد!")

    @command("!loopchat", level=ADMIN)
    async def cmd_loopchat(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !loopchat پیام  (برای توقف: !loopchat stop)")
            return
        if self.loopchat_task and not self.loopchat_task.done():
            self.loopchat_task.cancel()
            self.loopchat_task = None
        if args[0].lower() in ("stop", "off", "توقف", "خاموش"):
            await self.say("🛑 پیام تکرارشونده متوقف شد.")
            return
        text = " ".join(args)
        interval = max(5, int(self.config.get("loopchat_interval", 10)))

        async def loop():
            try:
                while True:
                    await self.say(text)
                    await sleep(interval)
            except CancelledError:
                pass

        self.loopchat_task = create_task(loop())
        await self.say(f"🔁 حالت تکرار فعال شد (هر {to_fa_digits(interval)} ثانیه).")

    def _start_emotebot(self, emote_id: str):
        old = self.dance_tasks.pop(self.user_id, None)
        if old and not old.done():
            old.cancel()
        pause = EMOTE_SECONDS.get(emote_id, DEFAULT_EMOTE_SECONDS) + 1.0

        async def loop():
            try:
                while True:
                    await self.highrise.send_emote(emote_id, self.user_id)
                    await sleep(pause)
            except CancelledError:
                pass
            except Exception as e:
                logger.error(f"خطا در دنس دائمی ربات: {e}")

        self.dance_tasks[self.user_id] = create_task(loop())

    @command("!emotebot", level=ADMIN)
    async def cmd_emotebot(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !emotebot شماره_یا_اسم  (برای توقف: !emotebot stop)")
            return
        if args[0].lower() in ("stop", "off", "توقف"):
            t = self.dance_tasks.pop(self.user_id, None)
            if t:
                t.cancel()
            self.config["emotebot_emote"] = None
            self.save_config()
            await self.say("🛑 دنس دائمی ربات متوقف شد.")
            return
        emote = resolve_emote(args[0])
        if not emote:
            await self.say("❌ دنس یا شماره‌ی واردشده در لیست پیدا نشد!")
            return
        self._start_emotebot(emote)
        self.config["emotebot_emote"] = emote
        self.save_config()
        await self.say(f"🕺 دنس ربات روی حالت تکرار همیشگی تنظیم شد: [{EMOTE_LABEL[emote]}]")

    # ───────────── کیف پول و تیپ ─────────────
    async def get_gold(self) -> int:
        wallet = await self.highrise.get_wallet()
        for item in getattr(wallet, "content", None) or []:
            if getattr(item, "type", "") == "gold":
                return int(getattr(item, "amount", 0))
        return 0

    @command("!wallet", level=ADMIN)
    async def cmd_wallet(self, user, args):
        await self.say(f"💰 موجودی گلد ربات: {to_fa_digits(await self.get_gold())} گلد")

    @command("!tip", level=ADMIN)
    async def cmd_tip(self, user, args):
        usage = "!tip all مقدار  |  !tip @آیدی مقدار  (مقدار: ۱، ۵، ۱۰، ۵۰، ۱۰۰)"
        amount, target = None, None
        for a in args:
            n = normalize_digits(a)
            if n.isdigit():
                amount = int(n)
            else:
                target = a.lstrip("@").lower()
        bars = {1: "gold_bar_1", 5: "gold_bar_5", 10: "gold_bar_10", 50: "gold_bar_50", 100: "gold_bar_100"}
        if amount not in bars or not target:
            await self.say(f"⚠️ فرمت درست:\n{usage}")
            return
        if target == "all":
            victims = self.humans()
        else:
            v = await self.find_user(target)
            if not v:
                await self.say(f"❌ کاربر {target} در روم نیست.")
                return
            victims = [v]
        gold = await self.get_gold()
        if gold < amount * len(victims):
            await self.say(f"❌ موجودی ({to_fa_digits(gold)}) برای {to_fa_digits(len(victims))} تیپ {to_fa_digits(amount)} گلدی کافی نیست.")
            return
        ok = 0
        for v in victims:
            try:
                resp = await self.highrise.tip_user(v.id, bars[amount])
                if hasattr(resp, "error") or "error" in type(resp).__name__.lower():
                    raise RuntimeError(getattr(resp, "message", resp))
                ok += 1
            except Exception as e:
                await self.say(f"❌ خطا در تیپ به @{v.username}: {e}")
            await sleep(2.5)
        await self.say(f"💛 تیپ {to_fa_digits(amount)} گلدی به {to_fa_digits(ok)} نفر ارسال شد!")

    # ───────────── تنظیمات روم ─────────────
    @command("!welcome", level=ADMIN)
    async def cmd_welcome(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !welcome پیام  |  !welcome on  |  !welcome off\n(می‌تونی {username} بنویسی تا جای اسم فرد بشینه)")
            return
        if args[0].lower() in ("on", "off") and len(args) == 1:
            self.config["welcome_enabled"] = args[0].lower() == "on"
            self.save_config()
            await self.say("✅ پیام خوش‌آمدگویی " + ("روشن" if self.config["welcome_enabled"] else "خاموش") + " شد.")
            return
        self.config["welcome_message"] = " ".join(args)
        self.config["welcome_enabled"] = True
        self.save_config()
        await self.say("✅ پیام خوش‌آمدگویی تغییر کرد.")

    @command("!announce", level=ADMIN)
    async def cmd_announce(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !announce پیام  |  !announce on  |  !announce off")
            return
        if args[0].lower() in ("on", "off") and len(args) == 1:
            self.config["announcement_enabled"] = args[0].lower() == "on"
        else:
            self.config["announcement_message"] = " ".join(args)
            self.config["announcement_enabled"] = True
        self.save_config()
        await self.say("✅ اعلان خودکار به‌روز شد.")

    @command("!interval", level=ADMIN)
    async def cmd_interval(self, user, args):
        if not args or not normalize_digits(args[0]).isdigit():
            await self.say("⚠️ فرمت درست: !interval ثانیه  (حداقل ۳۰)")
            return
        self.config["announcement_interval"] = max(30, int(normalize_digits(args[0])))
        self.save_config()
        await self.say(f"✅ فاصله‌ی اعلان‌ها: {to_fa_digits(self.config['announcement_interval'])} ثانیه")

    @command("!antiflood", level=ADMIN)
    async def cmd_antiflood(self, user, args):
        if not args or args[0].lower() not in ("on", "off"):
            await self.say("⚠️ فرمت درست: !antiflood on  یا  !antiflood off")
            return
        self.config["antiflood_enabled"] = args[0].lower() == "on"
        self.save_config()
        await self.say("🛡️ ضد اسپم " + ("روشن ✅" if self.config["antiflood_enabled"] else "خاموش ❌") + " شد.")

    # ───────────── ادمین‌ها و Hostها ─────────────
    @command("!listadd", "!admins", level=ADMIN)
    async def cmd_listadd(self, user, args):
        admins = self.config["admin_usernames"]
        await self.say(f"🛡️ لیست ادمین‌ها ({to_fa_digits(len(admins))} نفر):\n" + "\n".join(f"@{a}" for a in admins))

    @command("!hosts", level=ADMIN)
    async def cmd_hosts(self, user, args):
        await self.say("👑 Hostها: " + " ".join(f"@{h}" for h in self.config["host_usernames"]))

    @command("!addadmin", level=HOST)
    async def cmd_addadmin(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !addadmin @آیدی")
            return
        name = args[0].lstrip("@").lower()
        if name in self.config["admin_usernames"]:
            await self.say(f"@{name} از قبل ادمین است!")
            return
        self.config["admin_usernames"].append(name)
        self.save_config()
        await self.say(f"✅ @{name} به ادمین‌ها اضافه شد!")

    @command("!removeadmin", level=HOST)
    async def cmd_removeadmin(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !removeadmin @آیدی")
            return
        name = args[0].lstrip("@").lower()
        if name not in self.config["admin_usernames"]:
            await self.say(f"@{name} در لیست ادمین‌ها نیست!")
        elif self.role_of(name) >= HOST or name in PROTECTED_ADMINS:
            await self.say(f"❌ @{name} رتبه‌ی محافظت‌شده دارد و حذف نمی‌شود!")
        else:
            self.config["admin_usernames"].remove(name)
            self.save_config()
            await self.say(f"✅ @{name} از ادمین‌ها حذف شد.")

    @command("!addhost", level=OWNER)
    async def cmd_addhost(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !addhost @آیدی")
            return
        name = args[0].lstrip("@").lower()
        if name in self.config["host_usernames"]:
            await self.say(f"@{name} از قبل Host است!")
            return
        self.config["host_usernames"].append(name)
        if name not in self.config["admin_usernames"]:
            self.config["admin_usernames"].append(name)
        self.save_config()
        await self.say(f"👑 @{name} حالا Host شد!")

    @command("!removehost", level=OWNER)
    async def cmd_removehost(self, user, args):
        if not args:
            await self.say("⚠️ فرمت درست: !removehost @آیدی")
            return
        name = args[0].lstrip("@").lower()
        if name == OWNER_USERNAME.lower():
            await self.say("❌ مالک اصلی را نمی‌شود از Host برداشت!")
        elif name not in self.config["host_usernames"]:
            await self.say(f"@{name} Host نیست!")
        else:
            self.config["host_usernames"].remove(name)
            self.save_config()
            await self.say(f"✅ رتبه‌ی Host از @{name} گرفته شد.")


# ═══════════════════════ اجرا ═══════════════════════
def load_credentials():
    room_id = os.getenv("ROOM_ID", "").strip()
    token = os.getenv("API_TOKEN", "").strip()
    if (not room_id or not token) and os.path.exists("curlybot_secrets.json"):
        try:
            with open("curlybot_secrets.json", "r", encoding="utf-8") as f:
                s = json.load(f)
            room_id = room_id or str(s.get("room_id", "")).strip()
            token = token or str(s.get("api_token", "")).strip()
        except Exception as e:
            logger.error(f"خطا در خواندن curlybot_secrets.json: {e}")
    return room_id, token


def _self_ping_loop():
    """هر چند دقیقه به آدرس عمومی سرور سر می‌زند تا هاست‌های رایگان نخوابند."""
    import threading

    def run():
        while True:
            time.sleep(480)
            try:
                urllib.request.urlopen(PUBLIC_URL + "/", timeout=15).read(64)
            except Exception:
                pass
    if PUBLIC_URL:
        threading.Thread(target=run, daemon=True).start()


async def main():
    room_id, token = load_credentials()
    if not room_id or not token:
        logger.error("ROOM_ID یا API_TOKEN تنظیم نشده! آن‌ها را به‌صورت متغیر محیطی بگذار "
                     "(یا فایل curlybot_secrets.json با کلیدهای room_id و api_token بساز).")
        return
    logger.info(f"ROOM_ID: {room_id}")

    try:
        await RADIO.start_http(int(os.getenv("PORT", 8080)))
        if PUBLIC_URL:
            logger.info(f"📻 آدرس رادیو: {PUBLIC_URL}/radio.mp3")
            _self_ping_loop()
        else:
            logger.warning("PUBLIC_URL تنظیم نشده؛ آدرس رادیو = <آدرس سرور>/radio.mp3")
    except Exception as e:
        logger.error(f"خطا در اجرای وب‌سرور/رادیو: {e}")

    def handle_exception(loop, context):
        logger.error(f"خطای مهارشده در تسک پس‌زمینه: {context.get('exception', context.get('message'))}")
    try:
        asyncio.get_running_loop().set_exception_handler(handle_exception)
    except Exception:
        pass

    from highrise.__main__ import main as highrise_main
    attempt = 0
    while True:
        bot = CurlyBot()
        try:
            logger.info(f"اتصال به هایرایز... روم: {room_id}")
            await highrise_main([BotDefinition(room_id=room_id, api_token=token, bot=bot)])
        except Exception as e:
            logger.error(f"اتصال قطع شد یا خطا داد: {e}")
        try:
            await bot.cleanup_tasks()
        except Exception:
            pass
        attempt += 1
        wait = min(60, 5 + attempt * 3)
        logger.info(f"اتصال دوباره تا {wait} ثانیه‌ی دیگر (تلاش {attempt})...")
        await asyncio.sleep(wait)


if __name__ == "__main__":
    asyncio.run(main())
