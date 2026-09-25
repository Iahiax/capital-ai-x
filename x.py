import os
import io
import re
import ast
import sys
import gc
import json
import time
import base64
import asyncio
import threading
import collections
import tempfile
import traceback
import subprocess
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

# تفعيل دعم ألوان ANSI في تيرمينال Windows
if os.name == 'nt':
    os.system('')

# إعداد Matplotlib النقي للخلفية بدون الاعتماد على حالة pyplot العامة (100% Thread-Safe)
import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

# مستودع البيانات المحلي ومراقبة النظام
import duckdb
import psutil

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# التحقق من توفر مكتبات التعلم الآلي
try:
    import lightgbm as lgb
except ImportError:
    lgb = None

try:
    from catboost import CatBoostClassifier
except ImportError:
    CatBoostClassifier = None

try:
    from hmmlearn.hmm import GaussianHMM
except ImportError:
    GaussianHMM = None

# ==============================================================================
# نظام الطباعة والمراقبة الحية للأخطاء في التيرمينال (Live Terminal Logger)
# ==============================================================================
class TerminalLogger:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def _now(cls) -> str:
        return datetime.now().strftime("%H:%M:%S")

    @classmethod
    def info(cls, msg: str):
        print(f"[{cls._now()}] {cls.CYAN}ℹ️ [INFO]{cls.RESET} {msg}")

    @classmethod
    def scan(cls, msg: str):
        print(f"[{cls._now()}] {cls.BLUE}🔍 [SCAN]{cls.RESET} {msg}")

    @classmethod
    def filter(cls, msg: str):
        print(f"[{cls._now()}] {cls.YELLOW}⚠️ [FILTER]{cls.RESET} {msg}")

    @classmethod
    def success(cls, msg: str):
        print(f"[{cls._now()}] {cls.GREEN}✅ [SUCCESS]{cls.RESET} {cls.BOLD}{msg}{cls.RESET}")

    @classmethod
    def error(cls, component: str, err_msg: str, tb: str = None):
        print(f"\n{cls.RED}{cls.BOLD}🚨 [{cls._now()}] [ERROR IN {component.upper()}]:{cls.RESET}")
        print(f"{cls.RED}↳ السبب: {err_msg}{cls.RESET}")
        if tb:
            print(f"{cls.RED}↳ تفاصيل الخطأ البرمجي:\n{tb.strip()}{cls.RESET}\n")

# ==============================================================================
# 1. إعدادات النظام وقائمة أزواج العملات
# ==============================================================================
class Config:
    # Capital.com API
    CAPITAL_EMAIL = "yahia.x@outlook.sa"
    CAPITAL_API_KEY = "ut2RpxSbx6fiDdHv"
    CAPITAL_PASSWORD = "Yahia@1411"
    DEMO_MODE = True
    DEMO_SERVER = "https://demo-api-capital.backend-capital.com"
    LIVE_SERVER = "https://api-capital.backend-capital.com"
    
    # سلة أزواج العملات المعتمدة للتدوير اللحظي
    ACTIVE_EPICS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]

    # إدارة المخاطر والرافعة المالية
    LEVERAGE = 100.0
    KELLY_FRACTION = 0.25
    MAX_MARGIN_USAGE_PCT = 0.80
    MAX_OPEN_POSITIONS = 2         # السماح بصفقتين كحد أقصى لأزواج مختلفة
    
    # المعاملات الديناميكية المستندة لـ ATR
    MIN_STOP_PIPS = 3.5
    MIN_PROFIT_PIPS = 6.0
    ATR_SL_MULTIPLIER = 1.5
    ATR_TP_MULTIPLIER = 3.0
    
    # مراقبة السيولة، السبريد، والسمية والانزلاق
    MAX_DAILY_DRAWDOWN_PCT = 3.0       # قاطع الهبوط اليومي (3%)
    MAX_FRICTION_RATIO_PCT = 25.0      # أقصى نسبة سبريد إلى مدى الشمعة اللحظي
    SPREAD_EXPANSION_VELOCITY_MAX = 1.5  # حظر التنفيذ إذا تضاعف السبريد فجأة بـ 50%
    PSI_DRIFT_THRESHOLD = 0.25         # سقف استقرار التوزيع الإحصائي PSI لمنع فرط التخصيص
    MAX_ALLOWED_SLIPPAGE_PIPS = 0.5    # أقصى انزلاق مسموح قبل تعليق الزوج مؤقتاً
    PRE_TRADE_MAX_SLIPPAGE_PIPS = 0.4  # أقصى حركة مسموحة للسعر بين لحظة الرصد والتنفيذ
    MAX_API_LATENCY_MS = 600.0         # عتبة زمن الاستجابة لتخفيض اللوت
    HALT_API_LATENCY_MS = 1000.0       # عتبة تعليق التداول عند بطء السيرفر الحاد
    COOLDOWN_MINUTES = 20              # فترة التهدئة الإلزامية بعد خسارتين متتاليتين
    WATCHDOG_TIMEOUT_SECONDS = 180     # مهلة خيط اليقظة الداخلي لإعادة تشغيل الخدمة
    
    # المعاملات المتقدمة المؤسسية
    MACRO_SHOCK_CORRELATION_MAX = 0.85 # عتبة تجميد التداول عند صدمة الارتباط الكلي
    VOL_OF_VOL_HIGH_THRESHOLD = 0.35   # عتبة تقلب التقلب لتخفيض الحجم وتوسيع الوقف
    MAX_TIME_UNDER_WATER_SEC = 14400   # حد مدة التراجع (4 ساعات) لتخفيض كيلي
    CANARY_FORWARD_TEST_MINUTES = 30   # مدة اختبار الظل للنماذج قبل تفعيلها

    # Telegram Bot
    TELEGRAM_BOT_TOKEN = "8893700308:AAE5ahpKtEenHs_Q5kVGC6zDhfb832X66YI"
    TELEGRAM_CHAT_ID = "-1004325895118"

    # Google Gemini API
    GEMINI_API_KEY = "AQ.Ab8RN6JevzXfPTK6MPgLXSeePc5ERTi0eONlQdTNqSv5P0McGA"
    GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"

    # المفكرة الاقتصادية اللحظية والماكرو
    FRED_API_KEY = "d295bdec801d4faf6f55e5a5ea34eb07"
    FMP_API_KEY = "Jh4bsnzdHnXSVvou5ehHKBbffdrEfrYa"
    FINNHUB_API_KEY = "d96hs19r01qr77dkhss0d96hs19r01qr77dkhssg"

    # مسار مستودع DuckDB المحلي
    DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_warehouse.duckdb")

# ==============================================================================
# 2. هندسة استقرار ويندوز ومعالج الانهيارات (Core Affinity & Crash Dump)
# ==============================================================================
def tune_windows_process_affinity_and_priority():
    try:
        proc = psutil.Process(os.getpid())
        if hasattr(psutil, "HIGH_PRIORITY_CLASS"):
            proc.nice(psutil.HIGH_PRIORITY_CLASS)
        cpu_count = psutil.cpu_count(logical=False) or psutil.cpu_count() or 1
        if cpu_count > 1:
            proc.cpu_affinity(list(range(cpu_count)))
        TerminalLogger.info(f"تم ضبط أولوية العملية إلى HIGH_PRIORITY وتوزيع الأنوية على {cpu_count} مسار في ويندوز.")
    except Exception as e:
        TerminalLogger.filter(f"تعذر ضبط أولوية الأنوية: {e}")

def setup_crash_dump_handler():
    def global_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        TerminalLogger.error("CRITICAL_SYSTEM_CRASH", str(exc_value), tb_str)
        dump_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exception_type": str(exc_type.__name__),
            "exception_message": str(exc_value),
            "traceback": tb_str,
            "system_metrics": InternalSystemWatchdog.get_metrics()
        }
        dump_filename = f"crash_dump_{int(time.time())}.json"
        try:
            with open(dump_filename, "w", encoding="utf-8") as f:
                json.dump(dump_data, f, ensure_ascii=False, indent=2)
            TerminalLogger.info(f"تم حفظ تقرير الانهيار في {dump_filename}")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = global_excepthook

# ==============================================================================
# 3. مستودع البيانات المحلي فائق السرعة عبر DuckDB (قفل متبادل آمن على ويندوز)
# ==============================================================================
class DuckDBWarehouse:
    _lock = threading.RLock()

    @classmethod
    def init_database(cls):
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS m1_candles (
                        epic VARCHAR,
                        timestamp VARCHAR,
                        open DOUBLE,
                        high DOUBLE,
                        low DOUBLE,
                        close DOUBLE,
                        ask_close DOUBLE,
                        volume DOUBLE,
                        PRIMARY KEY (epic, timestamp)
                    );
                    CREATE TABLE IF NOT EXISTS closed_trades (
                        deal_id VARCHAR PRIMARY KEY,
                        epic VARCHAR,
                        direction VARCHAR,
                        pnl DOUBLE,
                        close_time VARCHAR
                    );
                    CREATE TABLE IF NOT EXISTS tca_metrics (
                        deal_id VARCHAR PRIMARY KEY,
                        epic VARCHAR,
                        spread_cost_usd DOUBLE,
                        slippage_pips DOUBLE,
                        total_friction_usd DOUBLE,
                        friction_ratio_pct DOUBLE,
                        timestamp VARCHAR
                    );
                """)
            finally:
                con.close()

    @classmethod
    def upsert_candles(cls, epic: str, df: pd.DataFrame):
        if df.empty:
            return
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                data_to_insert = df[['timestamp', 'open', 'high', 'low', 'close', 'ask_close', 'volume']].copy()
                data_to_insert['epic'] = epic
                con.register('df_view', data_to_insert)
                con.execute("""
                    INSERT INTO m1_candles (epic, timestamp, open, high, low, close, ask_close, volume)
                    SELECT epic, timestamp, open, high, low, close, ask_close, volume FROM df_view
                    ON CONFLICT (epic, timestamp) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        ask_close = EXCLUDED.ask_close,
                        volume = EXCLUDED.volume
                """)
            finally:
                con.close()

    @classmethod
    def log_closed_trade(cls, deal_id: str, epic: str, direction: str, pnl: float):
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                con.execute("""
                    INSERT INTO closed_trades (deal_id, epic, direction, pnl, close_time)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (deal_id) DO NOTHING
                """, [deal_id, epic, direction, pnl, now_str])
            finally:
                con.close()

    @classmethod
    def log_tca_metric(cls, deal_id: str, epic: str, spread_cost_usd: float, slippage_pips: float, total_friction_usd: float, friction_ratio_pct: float):
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                now_str = datetime.now(timezone.utc).isoformat()
                con.execute("""
                    INSERT INTO tca_metrics (deal_id, epic, spread_cost_usd, slippage_pips, total_friction_usd, friction_ratio_pct, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (deal_id) DO UPDATE SET
                        spread_cost_usd = EXCLUDED.spread_cost_usd,
                        slippage_pips = EXCLUDED.slippage_pips,
                        total_friction_usd = EXCLUDED.total_friction_usd,
                        friction_ratio_pct = EXCLUDED.friction_ratio_pct
                """, [deal_id, epic, spread_cost_usd, slippage_pips, total_friction_usd, friction_ratio_pct, now_str])
            finally:
                con.close()

    @classmethod
    def get_recent_closed_trades(cls, limit=50) -> pd.DataFrame:
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                df = con.execute(f"SELECT * FROM closed_trades ORDER BY close_time DESC LIMIT {limit}").df()
            finally:
                con.close()
            return df

    @classmethod
    def get_tca_summary(cls, limit=50) -> dict:
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                df = con.execute(f"SELECT * FROM tca_metrics ORDER BY timestamp DESC LIMIT {limit}").df()
            finally:
                con.close()
            if df.empty:
                return {"avg_slippage": 0.0, "total_friction": 0.0, "avg_friction_pct": 0.0}
            return {
                "avg_slippage": round(float(df['slippage_pips'].mean()), 2),
                "total_friction": round(float(df['total_friction_usd'].sum()), 2),
                "avg_friction_pct": round(float(df['friction_ratio_pct'].mean()), 1)
            }

    @classmethod
    def get_cached_candles(cls, epic: str, limit=1000) -> pd.DataFrame:
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                query = f"""
                    SELECT timestamp, open, high, low, close, ask_close, volume
                    FROM m1_candles
                    WHERE epic = '{epic}'
                    ORDER BY timestamp DESC
                    LIMIT {limit}
                """
                df = con.execute(query).df()
            finally:
                con.close()
            if not df.empty:
                return df.sort_values("timestamp").reset_index(drop=True)
            return pd.DataFrame()

    @classmethod
    def get_asian_range(cls, epic: str) -> tuple[float, float]:
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.strftime("%Y-%m-%d 00:00:00")
        today_asian_end = now_utc.strftime("%Y-%m-%d 07:00:00")
        
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                # معالجة تنسيق T في مقارنة السلاسل الزمنية بدقة في DuckDB
                query = f"""
                    SELECT MAX(high) as asian_high, MIN(low) as asian_low
                    FROM m1_candles
                    WHERE epic = '{epic}'
                      AND REPLACE(timestamp, 'T', ' ') >= '{today_start}'
                      AND REPLACE(timestamp, 'T', ' ') <= '{today_asian_end}'
                """
                res = con.execute(query).fetchone()
                if res and res[0] is not None and res[1] is not None:
                    return float(res[0]), float(res[1])
            except Exception as e:
                TerminalLogger.error("DUCKDB_ASIAN_RANGE", str(e))
            finally:
                con.close()
            return 0.0, 0.0

    @classmethod
    def perform_maintenance(cls):
        with cls._lock:
            con = duckdb.connect(Config.DB_FILE)
            try:
                con.execute("CHECKPOINT;")
                con.execute("VACUUM;")
                TerminalLogger.success("تم تنفيذ صيانة CHECKPOINT و VACUUM لمستودع DuckDB بنجاح.")
            except Exception as e:
                TerminalLogger.error("DUCKDB_MAINTENANCE", str(e))
            finally:
                con.close()

# ==============================================================================
# 4. النماذج الرياضية والإحصائية المتقدمة (Advanced Mathematical Models)
# ==============================================================================
class AdvancedQuantMath:
    @staticmethod
    def haar_wavelet_denoise(signal: np.ndarray) -> np.ndarray:
        if len(signal) < 8:
            return signal
        n = len(signal)
        padded_len = 1 << (n - 1).bit_length()
        padded = np.pad(signal, (0, padded_len - n), mode='edge')
        
        approx = padded.copy()
        details = []
        curr = approx
        for _ in range(2):
            half = len(curr) // 2
            a = (curr[0::2] + curr[1::2]) / np.sqrt(2.0)
            d = (curr[0::2] - curr[1::2]) / np.sqrt(2.0)
            threshold = 1.4826 * np.median(np.abs(d)) * np.sqrt(2.0 * max(1e-5, np.log(len(d))))
            d = np.sign(d) * np.maximum(0.0, np.abs(d) - threshold)
            details.append(d)
            curr = a

        for d in reversed(details):
            rec = np.empty(len(curr) * 2, dtype=float)
            rec[0::2] = (curr + d) / np.sqrt(2.0)
            rec[1::2] = (curr - d) / np.sqrt(2.0)
            curr = rec

        return curr[:n]

    @staticmethod
    def fractional_differentiation(series: pd.Series, d=0.4, threshold=1e-4) -> pd.Series:
        max_k = min(len(series) // 2, 80)
        weights = [1.0]
        k = 1
        while k < max_k:
            w_k = -weights[-1] / k * (d - k + 1)
            if abs(w_k) < threshold:
                break
            weights.append(w_k)
            k += 1
        weights = np.array(weights)
        res = np.convolve(series.values, weights, mode='valid')
        pad_len = len(series) - len(res)
        return pd.Series(np.pad(res, (pad_len, 0), mode='edge'), index=series.index)

    @staticmethod
    def kalman_filter_price_velocity(prices: np.ndarray) -> tuple[float, float]:
        if len(prices) < 5:
            return float(prices[-1]), 0.0
        x = np.array([prices[0], 0.0])
        P = np.eye(2) * 1.0
        F = np.array([[1.0, 1.0], [0.0, 1.0]])
        Q = np.array([[1e-4, 1e-4], [1e-4, 1e-3]])
        H = np.array([[1.0, 0.0]])
        R = np.array([[1e-3]])

        for z in prices:
            x = F @ x
            P = F @ P @ F.T + Q
            y = z - (H @ x)
            S = H @ P @ H.T + R
            K = P @ H.T * (1.0 / (S[0, 0] + 1e-8))
            x = x + (K * y[0]).flatten()
            P = (np.eye(2) - K @ H) @ P

        return float(x[0]), float(x[1])

    @staticmethod
    def calculate_rolling_hurst(series: np.ndarray) -> float:
        if len(series) < 30:
            return 0.50
        try:
            rets_1 = np.diff(np.log(series + 1e-8))
            var_1 = np.var(rets_1) + 1e-8
            rets_2 = (np.log(series[2:] + 1e-8) - np.log(series[:-2] + 1e-8))
            var_2 = np.var(rets_2) + 1e-8
            variance_ratio = var_2 / (2.0 * var_1)
            hurst = 0.5 + 0.5 * (np.log2(max(1e-4, variance_ratio)))
            return float(np.clip(hurst, 0.05, 0.95))
        except Exception:
            return 0.50

    @staticmethod
    def calculate_ou_half_life(series: np.ndarray) -> float:
        if len(series) < 20:
            return 15.0
        y = series[1:]
        x = series[:-1]
        dx = y - x
        X = np.column_stack([x, np.ones(len(x))])
        try:
            sol = np.linalg.lstsq(X, dx, rcond=None)[0]
            slope = sol[0]
            if slope < -1e-5:
                half_life = -np.log(2.0) / slope
                return float(np.clip(half_life, 8.0, 120.0))
        except Exception:
            pass
        return 15.0

    @staticmethod
    def predict_garch_volatility(returns: np.ndarray) -> float:
        """التنبؤ بالتقلب المسبق مع تطبيق استهداف التباين Variance Targeting"""
        if len(returns) < 20:
            return float(np.std(returns) if len(returns) > 0 else 0.0005)
        alpha = 0.10
        beta = 0.85
        long_run_var = float(np.var(returns))
        omega = long_run_var * (1.0 - alpha - beta)
        sigma2 = long_run_var
        for r in returns:
            sigma2 = omega + alpha * (r**2) + beta * sigma2
        next_sigma2 = omega + alpha * (returns[-1]**2) + beta * sigma2
        return float(np.sqrt(max(1e-10, next_sigma2)))

    @staticmethod
    def triple_barrier_labeling(df: pd.DataFrame, pt_mult=2.0, sl_mult=1.0, max_bars=10) -> pd.Series:
        labels = []
        n = len(df)
        closes = df['close'].values
        tr = np.maximum(df['high'] - df['low'], 1e-5).values
        for i in range(n):
            if i + max_bars >= n:
                labels.append(0)
                continue
            entry_p = closes[i]
            vol = tr[i]
            upper_barrier = entry_p + (vol * pt_mult)
            lower_barrier = entry_p - (vol * sl_mult)
            
            future_slice = closes[i+1 : i+max_bars+1]
            hit = 0
            for p in future_slice:
                if p >= upper_barrier:
                    hit = 1
                    break
                elif p <= lower_barrier:
                    hit = -1
                    break
            labels.append(hit)
        return pd.Series(labels, index=df.index)

# ==============================================================================
# 5. مؤقت اليقظة وقفل الأوامر المتزامن (Watchdog & Mutex Order Lock)
# ==============================================================================
class OrderExecutionMutex:
    _lock = threading.Lock()
    _last_order_timestamp = 0.0

    @classmethod
    def acquire_order_slot(cls, min_spacing_sec=1.5):
        with cls._lock:
            now = time.time()
            diff = now - cls._last_order_timestamp
            if diff < min_spacing_sec:
                time.sleep(min_spacing_sec - diff)
            cls._last_order_timestamp = time.time()

class InternalSystemWatchdog:
    last_heartbeat_time = time.time()
    _running = True

    @classmethod
    def beat(cls):
        cls.last_heartbeat_time = time.time()

    @classmethod
    def _watchdog_loop(cls):
        while cls._running:
            time.sleep(10)
            elapsed = time.time() - cls.last_heartbeat_time
            if elapsed > Config.WATCHDOG_TIMEOUT_SECONDS:
                msg = f"تجمدت دورة الأحداث لـ {elapsed:.0f} ثانية! إعادة تشغيل الخدمة عبر NSSM..."
                TerminalLogger.error("INTERNAL_WATCHDOG", msg)
                try:
                    with open("watchdog_crash.log", "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")
                except Exception:
                    pass
                os._exit(1)

    @classmethod
    def start(cls):
        t = threading.Thread(target=cls._watchdog_loop, daemon=True)
        t.start()
        TerminalLogger.info("تم تفعيل مؤقت اليقظة والتعافي الذاتي الداخلي (180s Watchdog).")

    @staticmethod
    def get_metrics() -> dict:
        proc = psutil.Process(os.getpid())
        ram_mb = proc.memory_info().rss / (1024 * 1024)
        cpu_pct = proc.cpu_percent(interval=None)
        uptime_sec = time.time() - proc.create_time()
        hours, remainder = divmod(uptime_sec, 3600)
        minutes, _ = divmod(remainder, 60)
        
        return {
            "ram_mb": round(ram_mb, 2),
            "cpu_pct": round(cpu_pct, 1),
            "uptime": f"{int(hours)}h {int(minutes)}m",
            "threads": proc.num_threads(),
            "disk_free_gb": round(psutil.disk_usage('.').free / (1024**3), 2)
        }

# ==============================================================================
# 6. وسيط Capital.com مع التجديد المسبق للتوكن وقياس زمن الاستجابة
# ==============================================================================
class FastCapitalBroker:
    def __init__(self):
        self.demo = Config.DEMO_MODE
        self.cst = None
        self.xst = None
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=retries)
        self.session.mount("https://", adapter)
        self.latencies = collections.deque(maxlen=15)
        self.last_login_time = 0.0
        self._login_lock = threading.Lock()
        self.login()

    @staticmethod
    def get_pip_multiplier(epic: str) -> float:
        return 0.01 if "JPY" in epic.upper() else 0.0001

    @classmethod
    def get_pip_value_usd(cls, epic: str, current_price: float) -> float:
        if "JPY" in epic.upper():
            return 1000.0 / (current_price if current_price > 0 else 150.0)
        return 10.0

    def get_server(self):
        return Config.DEMO_SERVER if self.demo else Config.LIVE_SERVER

    def get_average_latency(self) -> float:
        return float(np.mean(self.latencies)) if len(self.latencies) > 0 else 150.0

    def record_latency(self, start_time: float):
        elapsed = (time.time() - start_time) * 1000.0
        self.latencies.append(elapsed)

    def login(self):
        with self._login_lock:
            if time.time() - self.last_login_time < 2.0:
                return True
            url = f"{self.get_server()}/api/v1/session"
            headers = {"X-CAP-API-KEY": Config.CAPITAL_API_KEY, "Content-Type": "application/json"}
            payload = {"identifier": Config.CAPITAL_EMAIL, "password": Config.CAPITAL_PASSWORD, "encryptedPassword": False}
            t0 = time.time()
            try:
                r = self.session.post(url, headers=headers, json=payload, timeout=8)
                self.record_latency(t0)
                if r.status_code == 200:
                    self.cst = r.headers.get("CST")
                    self.xst = r.headers.get("X-SECURITY-TOKEN")
                    self.last_login_time = time.time()
                    TerminalLogger.success("تم تسجيل الدخول إلى وسيط Capital.com بنجاح وتحديث رموز الجلسة.")
                    return True
                else:
                    TerminalLogger.error("BROKER_LOGIN", f"فشل تسجيل الدخول (رمز {r.status_code}): {r.text}")
            except Exception as e:
                TerminalLogger.error("BROKER_LOGIN_EXCEPTION", str(e), traceback.format_exc())
            return False

    def keep_alive_session(self):
        url = f"{self.get_server()}/api/v1/accounts"
        try:
            r = self.session.get(url, headers=self.get_headers(), timeout=5)
            if r.status_code == 401 or (time.time() - self.last_login_time > 500):
                TerminalLogger.info("تجديد استباقي لجلسة الوسيط لتفادي انتهاء الصلاحية...")
                self.login()
        except Exception as e:
            TerminalLogger.error("SESSION_KEEPALIVE", str(e))
            self.login()

    def get_headers(self):
        return {"X-SECURITY-TOKEN": self.xst, "CST": self.cst, "Content-Type": "application/json"}

    def get_account_details(self) -> dict:
        url = f"{self.get_server()}/api/v1/accounts"
        t0 = time.time()
        try:
            r = self.session.get(url, headers=self.get_headers(), timeout=6)
            self.record_latency(t0)
            if r.status_code == 401:
                self.login()
                r = self.session.get(url, headers=self.get_headers(), timeout=6)
            accounts = r.json().get("accounts", [])
            if accounts:
                bal = accounts[0].get("balance", {})
                return {
                    "balance": float(bal.get("balance") or 1000.0),
                    "available": float(bal.get("available") or 1000.0)
                }
        except Exception as e:
            TerminalLogger.error("GET_ACCOUNT_DETAILS", str(e))
        return {"balance": 1000.0, "available": 1000.0}

    def fetch_live_candles(self, epic: str, resolution="MINUTE", max_bars=300) -> pd.DataFrame:
        url = f"{self.get_server()}/api/v1/prices/{epic}"
        params = {"resolution": resolution, "max": max_bars}
        t0 = time.time()
        try:
            r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            self.record_latency(t0)
            if r.status_code == 401:
                self.login()
                r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            data = r.json().get("prices", [])
            rows = []
            for p in data:
                o_bid = (p.get("openPrice") or {}).get("bid")
                h_bid = (p.get("highPrice") or {}).get("bid")
                l_bid = (p.get("lowPrice") or {}).get("bid")
                c_bid = (p.get("closePrice") or {}).get("bid")
                c_ask = (p.get("closePrice") or {}).get("ask") or c_bid
                vol_val = p.get("lastTradedVolume")

                if None in (o_bid, h_bid, l_bid, c_bid):
                    continue

                rows.append({
                    "timestamp": p.get("snapshotTime", ""),
                    "open": float(o_bid),
                    "high": float(h_bid),
                    "low": float(l_bid),
                    "close": float(c_bid),
                    "ask_close": float(c_ask if c_ask is not None else c_bid),
                    "volume": float(vol_val if vol_val is not None else 1.0)
                })
            df = pd.DataFrame(rows)
            if not df.empty and resolution == "MINUTE":
                DuckDBWarehouse.upsert_candles(epic, df)
            return df
        except Exception as e:
            TerminalLogger.error(f"FETCH_CANDLES_{epic}", str(e))
            return pd.DataFrame()

    def get_latest_quote(self, epic: str) -> tuple[float, float]:
        url = f"{self.get_server()}/api/v1/markets/{epic}"
        try:
            r = self.session.get(url, headers=self.get_headers(), timeout=3)
            if r.status_code == 200:
                snap = r.json().get("snapshot", {})
                bid = float(snap.get("bid") or 0.0)
                offer = float(snap.get("offer") or 0.0)
                return bid, offer
        except Exception as e:
            TerminalLogger.error(f"GET_LATEST_QUOTE_{epic}", str(e))
        return 0.0, 0.0

    def get_deal_confirmation(self, deal_ref: str) -> dict:
        url = f"{self.get_server()}/api/v1/confirms/{deal_ref}"
        try:
            r = self.session.get(url, headers=self.get_headers(), timeout=4)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {}

    def execute_order_server_trailing(self, epic: str, direction: str, size: float, current_price: float, stop_pips: float, profit_pips: float) -> dict:
        OrderExecutionMutex.acquire_order_slot(min_spacing_sec=1.5)
        url = f"{self.get_server()}/api/v1/positions"
        pip_mult = self.get_pip_multiplier(epic)
        stop_dist_price = round(stop_pips * pip_mult, 3 if "JPY" in epic else 5)

        if direction == "BUY":
            profit_level = round(current_price + (profit_pips * pip_mult), 3 if "JPY" in epic else 5)
        else:
            profit_level = round(current_price - (profit_pips * pip_mult), 3 if "JPY" in epic else 5)

        payload = {
            "epic": epic,
            "direction": direction,
            "size": size,
            "guaranteedStop": False,
            "trailingStop": True,
            "stopDistance": stop_dist_price,
            "profitLevel": profit_level
        }
        t0 = time.time()
        try:
            r = self.session.post(url, headers=self.get_headers(), json=payload, timeout=8)
            self.record_latency(t0)
            if r.status_code == 401:
                self.login()
                r = self.session.post(url, headers=self.get_headers(), json=payload, timeout=8)
            res = r.json()
            if "dealReference" in res:
                time.sleep(0.3)
                conf = self.get_deal_confirmation(res["dealReference"])
                if conf.get("dealStatus") == "REJECTED" or conf.get("status") == "REJECTED":
                    err_reason = conf.get("rejectReason", "REJECTED_BY_EXCHANGE")
                    TerminalLogger.error("ORDER_REJECTED_ASYNC", err_reason)
                    return {"errorCode": err_reason}
                if "level" in conf:
                    res["level"] = conf["level"]
            return res
        except Exception as e:
            TerminalLogger.error("EXECUTE_ORDER", str(e), traceback.format_exc())
            return {"error": str(e)}

    def update_position_stop(self, deal_id: str, new_stop_level: float) -> bool:
        url = f"{self.get_server()}/api/v1/positions/{deal_id}"
        payload = {"stopLevel": new_stop_level, "trailingStop": False}
        t0 = time.time()
        try:
            r = self.session.put(url, headers=self.get_headers(), json=payload, timeout=6)
            self.record_latency(t0)
            if r.status_code == 401:
                self.login()
                r = self.session.put(url, headers=self.get_headers(), json=payload, timeout=6)
            if r.status_code not in [200, 204]:
                TerminalLogger.error(f"UPDATE_STOP_{deal_id}", f"Status {r.status_code}: {r.text}")
                return False
            return True
        except Exception as e:
            TerminalLogger.error(f"UPDATE_STOP_{deal_id}", str(e))
            return False

    def close_position_fully(self, deal_id: str) -> bool:
        url = f"{self.get_server()}/api/v1/positions/{deal_id}"
        t0 = time.time()
        try:
            r = self.session.delete(url, headers=self.get_headers(), timeout=6)
            self.record_latency(t0)
            if r.status_code == 401:
                self.login()
                r = self.session.delete(url, headers=self.get_headers(), timeout=6)
            return r.status_code in [200, 204]
        except Exception as e:
            TerminalLogger.error(f"CLOSE_POSITION_{deal_id}", str(e))
            return False

    def get_open_positions(self) -> list:
        url = f"{self.get_server()}/api/v1/positions"
        t0 = time.time()
        try:
            r = self.session.get(url, headers=self.get_headers(), timeout=6)
            self.record_latency(t0)
            if r.status_code == 401:
                self.login()
                r = self.session.get(url, headers=self.get_headers(), timeout=6)
            if r.status_code == 200:
                return r.json().get("positions", [])
        except Exception as e:
            TerminalLogger.error("GET_OPEN_POSITIONS", str(e))
        return []

    def fetch_recent_closed_trades(self, limit=5) -> list:
        url = f"{self.get_server()}/api/v1/history/activity"
        params = {"limit": limit}
        t0 = time.time()
        try:
            r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            self.record_latency(t0)
            if r.status_code == 401:
                self.login()
                r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            if r.status_code == 200:
                return r.json().get("activities", [])
        except Exception as e:
            TerminalLogger.error("FETCH_RECENT_CLOSED_TRADES", str(e))
        return []

# ==============================================================================
# 7. بنية السوق الدقيقة والتنفيذ (Market Microstructure & Spreads)
# ==============================================================================
class MarketMicrostructureEngine:
    @staticmethod
    def calculate_net_liquidity_void_ratio(df: pd.DataFrame) -> tuple[bool, str]:
        if len(df) < 5:
            return True, "بيانات غير كافية"
        last = df.iloc[-1]
        candle_range = abs(last['high'] - last['low']) + 1e-8
        body = abs(last['close'] - last['open'])
        if (body / candle_range) > 0.85 and last['volume'] > df['volume'].tail(10).mean() * 1.8:
            return False, "تجميد لحظي: عجز سيولة حاد (Marubozu Void) بانتظار إعادة التوازن 50%"
        return True, "توازن السيولة طبيعي"

    @staticmethod
    def check_glosten_harris_adverse_selection(df: pd.DataFrame, pip_mult: float) -> tuple[bool, float]:
        if len(df) < 15:
            return True, 0.0
        last_spread_pips = (df['ask_close'].iloc[-1] - df['close'].iloc[-1]) / pip_mult
        avg_spread_pips = ((df['ask_close'] - df['close']).rolling(14).mean().iloc[-1]) / pip_mult
        if avg_spread_pips <= 0:
            avg_spread_pips = last_spread_pips

        spread_inflation = last_spread_pips / (avg_spread_pips + 1e-5)
        if spread_inflation > 1.70:
            return False, float(spread_inflation)
        return True, float(spread_inflation)

    @staticmethod
    def detect_micro_volume_burst(df: pd.DataFrame) -> tuple[bool, float]:
        if len(df) < 10:
            return False, 1.0
        rolling_mean = df['volume'].tail(10).mean() + 1e-8
        burst_ratio = float(df['volume'].iloc[-1] / rolling_mean)
        return (burst_ratio > 2.5), burst_ratio

    @staticmethod
    def get_asymmetric_slippage_cap(epic: str, atr_pips: float) -> float:
        if "GBP" in epic:
            return min(Config.MAX_ALLOWED_SLIPPAGE_PIPS, atr_pips * 0.10)
        return min(Config.MAX_ALLOWED_SLIPPAGE_PIPS, atr_pips * 0.08)

# ==============================================================================
# 8. مؤشر الدولار ومصفوفة القوة
# ==============================================================================
class CurrencyStrengthMatrix:
    @classmethod
    def evaluate_currency_strength(cls) -> dict:
        strengths = {"USD": 0.0, "EUR": 0.0, "GBP": 0.0, "JPY": 0.0, "AUD": 0.0}
        try:
            e_df = DuckDBWarehouse.get_cached_candles("EURUSD", limit=20)
            g_df = DuckDBWarehouse.get_cached_candles("GBPUSD", limit=20)
            j_df = DuckDBWarehouse.get_cached_candles("USDJPY", limit=20)
            a_df = DuckDBWarehouse.get_cached_candles("AUDUSD", limit=20)

            if len(e_df) >= 15 and len(g_df) >= 15 and len(j_df) >= 15 and len(a_df) >= 15:
                r_eur = (e_df['close'].iloc[-1] - e_df['close'].iloc[-15]) / e_df['close'].iloc[-15]
                r_gbp = (g_df['close'].iloc[-1] - g_df['close'].iloc[-15]) / g_df['close'].iloc[-15]
                r_aud = (a_df['close'].iloc[-1] - a_df['close'].iloc[-15]) / a_df['close'].iloc[-15]
                r_jpy = -((j_df['close'].iloc[-1] - j_df['close'].iloc[-15]) / j_df['close'].iloc[-15])

                s_usd = (-r_eur - r_gbp - r_aud - r_jpy) / 4.0
                strengths["USD"] = round(s_usd, 5)
                strengths["EUR"] = round(r_eur + s_usd, 5)
                strengths["GBP"] = round(r_gbp + s_usd, 5)
                strengths["AUD"] = round(r_aud + s_usd, 5)
                strengths["JPY"] = round(r_jpy + s_usd, 5)
        except Exception as e:
            TerminalLogger.error("CURRENCY_STRENGTH_EVAL", str(e))
        return strengths

    @classmethod
    def is_strength_aligned(cls, epic: str, action: str, strengths: dict) -> tuple[bool, str]:
        base = epic[:3]
        quote = epic[3:]
        base_s = strengths.get(base, 0.0)
        quote_s = strengths.get(quote, 0.0)
        diff = base_s - quote_s

        if action == "BUY" and diff >= 0:
            return True, f"قوة العملة متوافقة ({base} أقوى من {quote} بفارق {diff:+.4f})"
        elif action == "SELL" and diff <= 0:
            return True, f"قوة العملة متوافقة ({base} أضعف من {quote} بفارق {diff:+.4f})"

        return False, f"تعارض القوة النسبية ({base}: {base_s:+.4f} مقابل {quote}: {quote_s:+.4f})"

class SyntheticDXYEngine:
    @classmethod
    def get_synthetic_dxy_trend(cls) -> tuple[int, str, pd.Series]:
        try:
            e = DuckDBWarehouse.get_cached_candles("EURUSD", limit=30)
            j = DuckDBWarehouse.get_cached_candles("USDJPY", limit=30)
            g = DuckDBWarehouse.get_cached_candles("GBPUSD", limit=30)
            a = DuckDBWarehouse.get_cached_candles("AUDUSD", limit=30)

            if min(len(e), len(j), len(g), len(a)) >= 15:
                # المحاذاة الزمنية المتزامنة للمؤشر التجميعي بدقة الدقيقة
                e['t'] = e['timestamp'].astype(str).str.replace('T', ' ').str.slice(0, 16)
                j['t'] = j['timestamp'].astype(str).str.replace('T', ' ').str.slice(0, 16)
                g['t'] = g['timestamp'].astype(str).str.replace('T', ' ').str.slice(0, 16)
                a['t'] = a['timestamp'].astype(str).str.replace('T', ' ').str.slice(0, 16)

                merged = e[['t', 'close']].rename(columns={'close': 'e'})\
                    .merge(j[['t', 'close']].rename(columns={'close': 'j'}), on='t')\
                    .merge(g[['t', 'close']].rename(columns={'close': 'g'}), on='t')\
                    .merge(a[['t', 'close']].rename(columns={'close': 'a'}), on='t')

                if len(merged) >= 15:
                    syn_dxy = 100.0 * (
                        (merged['e'].values ** -0.576) *
                        (merged['j'].values ** 0.136) *
                        (merged['g'].values ** -0.119) *
                        (merged['a'].values ** -0.05)
                    )
                    dxy_series = pd.Series(syn_dxy)
                    ema_dxy = dxy_series.ewm(span=10).mean().iloc[-1]
                    curr_dxy = dxy_series.iloc[-1]

                    if curr_dxy > ema_dxy * 1.00002:
                        return 1, "الدولار التجميعي صاعد (Bullish USD Flow)", dxy_series
                    elif curr_dxy < ema_dxy * 0.99998:
                        return -1, "الدولار التجميعي هابط (Bearish USD Flow)", dxy_series
                    return 0, "الدولار التجميعي محايد", dxy_series
        except Exception as e:
            TerminalLogger.error("SYNTHETIC_DXY_CALC", str(e))
        return 0, "مؤشر الدولار غير متوفر", pd.Series(dtype=float)

    @classmethod
    def is_dxy_confluent(cls, epic: str, action: str, dxy_trend: int) -> bool:
        if dxy_trend == 0:
            return True
        usd_dir = CurrencyCorrelationManager.get_usd_direction(epic, action)
        if usd_dir == "LONG_USD" and dxy_trend > 0:
            return True
        elif usd_dir == "SHORT_USD" and dxy_trend < 0:
            return True
        return False

class CrossAssetMacroShockFilter:
    @classmethod
    def detect_macro_shock(cls) -> tuple[bool, float]:
        returns_dict = {}
        for epic in Config.ACTIVE_EPICS:
            df = DuckDBWarehouse.get_cached_candles(epic, limit=25)
            if len(df) >= 15:
                returns_dict[epic] = df['close'].pct_change().dropna().tail(12).values

        if len(returns_dict) == len(Config.ACTIVE_EPICS):
            try:
                min_len = min(len(v) for v in returns_dict.values())
                aligned = {k: v[-min_len:] for k, v in returns_dict.items()}
                df_ret = pd.DataFrame(aligned)
                corr = df_ret.corr().abs()
                triu_idx = np.triu_indices_from(corr.values, k=1)
                mean_corr = float(np.mean(corr.values[triu_idx]))
                if mean_corr >= Config.MACRO_SHOCK_CORRELATION_MAX:
                    return True, mean_corr
                return False, mean_corr
            except Exception as e:
                TerminalLogger.error("MACRO_SHOCK_FILTER", str(e))
        return False, 0.0

class CurrencyCorrelationManager:
    @staticmethod
    def get_usd_direction(epic: str, action: str) -> str:
        if epic.upper().endswith("USD"):
            return "SHORT_USD" if action == "BUY" else "LONG_USD"
        elif epic.upper().startswith("USD"):
            return "LONG_USD" if action == "BUY" else "SHORT_USD"
        return "NEUTRAL"

    @classmethod
    def calculate_usd_beta(cls, asset_returns: pd.Series, dxy_series: pd.Series) -> float:
        if len(asset_returns) < 15 or len(dxy_series) < 15:
            return 1.0
        try:
            dxy_ret = dxy_series.pct_change().dropna()
            min_l = min(len(asset_returns), len(dxy_ret))
            if min_l < 10:
                return 1.0
            a_sub = asset_returns.values[-min_l:]
            d_sub = dxy_ret.values[-min_l:]
            var_dxy = np.var(d_sub)
            if var_dxy < 1e-8 or np.std(d_sub) < 1e-5:
                return 1.0
            cov = np.cov(a_sub, d_sub)[0][1]
            return float(cov / (var_dxy + 1e-8))
        except Exception:
            return 1.0

    @classmethod
    def can_open_position(cls, open_positions: list, proposed_epic: str, proposed_action: str) -> tuple[bool, str]:
        proposed_usd_dir = cls.get_usd_direction(proposed_epic, proposed_action)
        
        for pos in open_positions:
            pos_epic = pos.get("market", {}).get("epic") or pos.get("epic", "")
            pos_dir = pos.get("position", {}).get("direction") or pos.get("direction", "")
            
            if pos_epic == proposed_epic:
                return False, f"يوجد مركز مفتوح بالفعل على الزوج {pos_epic}."

            existing_usd_dir = cls.get_usd_direction(pos_epic, pos_dir)
            if proposed_usd_dir != "NEUTRAL" and existing_usd_dir == proposed_usd_dir:
                return False, f"حظر ارتباط: المركز المفتوح على {pos_epic} يحمل نفس اتجاه الدولار ({proposed_usd_dir})."

        return True, "انكشاف العملة متوازن"

# ==============================================================================
# 9. صمام أمان السوق، تثبيت لندن 4 PM Fix، والأخبار
# ==============================================================================
class MarketShield:
    RELEVANT_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD"]

    @staticmethod
    def check_market_hours() -> tuple[bool, str]:
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()
        hour = now_utc.hour
        if weekday == 4 and hour >= 20:
            return False, "السوق يقترب من الإغلاق الأسبوعي (الجمعة بعد 20:00 UTC)."
        if weekday == 5:
            return False, "سوق الفوركس مغلق (عطلة نهاية الأسبوع - السبت)."
        if weekday == 6 and hour < 21:
            return False, "سوق الفوركس مغلق (بانتظار افتتاح الأحد 21:00 UTC)."
        return True, "السوق مفتوح والسيولة متاحة."

    @staticmethod
    def check_overnight_swap_window() -> tuple[bool, str]:
        now_utc = datetime.now(timezone.utc)
        minute_of_day = now_utc.hour * 60 + now_utc.minute
        if 1290 <= minute_of_day <= 1335:
            return False, "حظر التبييت: تجنب فتح صفقات خلال فترة احتساب رسوم التمويل الليلي (21:30 - 22:15 UTC)."
        return True, "خارج نافذة رسوم التبييت."

    @staticmethod
    def check_london_fix_window() -> tuple[bool, str]:
        now_utc = datetime.now(timezone.utc)
        minute_of_day = now_utc.hour * 60 + now_utc.minute
        if 955 <= minute_of_day <= 965:
            return False, "حظر نافذة تثبيت لندن (London 4 PM Fix Window: 15:55 - 16:05 UTC)."
        return True, "خارج نافذة تثبيت لندن."

    @staticmethod
    def check_volatility_spillover(basket_atrs: dict) -> tuple[bool, str]:
        for epic, current_atr in basket_atrs.items():
            df_c = DuckDBWarehouse.get_cached_candles(epic, limit=60)
            if len(df_c) >= 30:
                p_mult = FastCapitalBroker.get_pip_multiplier(epic)
                tr_vals = np.maximum(df_c['high'] - df_c['low'], 1e-5) / p_mult
                rolled = tr_vals.rolling(14).mean().dropna()
                if len(rolled) >= 15:
                    hist_mean = rolled.iloc[-10]
                    if hist_mean > 0 and current_atr > (hist_mean * 2.2):
                        return True, f"انتقال تقلب عابر: طفرة مفاجئة على {epic} ({current_atr:.1f} Pips > {hist_mean:.1f} Pips)"
        return False, "التقلب متزن عبر السلة"

    @classmethod
    def check_live_news(cls) -> tuple[bool, str]:
        now_utc = datetime.now(timezone.utc)
        from_str = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
        to_str = (now_utc + timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            url_fmp = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={from_str}&to={to_str}&apikey={Config.FMP_API_KEY}"
            resp = requests.get(url_fmp, timeout=4)
            if resp.status_code == 200:
                events = resp.json()
                for ev in events:
                    curr = str(ev.get("currency", "")).upper()
                    impact = str(ev.get("impact", "")).capitalize()
                    if curr in cls.RELEVANT_CURRENCIES and impact in ["High", "Very high"]:
                        date_raw = str(ev.get("date", "")).replace("T", " ")
                        if len(date_raw) >= 19:
                            ev_dt = datetime.strptime(date_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            diff = (ev_dt - now_utc).total_seconds() / 60.0
                            if -15.0 <= diff <= 15.0:
                                return True, f"حظر إخباري (FMP): {ev.get('event')} ({curr}) خلال {diff:.1f} دقيقة."
        except Exception as e:
            TerminalLogger.error("NEWS_FMP_CHECK", str(e))

        try:
            url_fh = f"https://finnhub.io/api/v1/calendar/economic?from={from_str}&to={to_str}&token={Config.FINNHUB_API_KEY}"
            resp = requests.get(url_fh, timeout=4)
            if resp.status_code == 200:
                events = resp.json().get("economicCalendar", [])
                for ev in events:
                    country = str(ev.get("country", "")).upper()
                    impact = str(ev.get("impact", "")).lower()
                    if country in ["US", "EU", "DE", "GB", "JP", "AU"] and impact in ["high", "3"]:
                        time_raw = str(ev.get("time", "")).replace("T", " ")
                        if len(time_raw) >= 19:
                            ev_dt = datetime.strptime(time_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            diff = (ev_dt - now_utc).total_seconds() / 60.0
                            if -15.0 <= diff <= 15.0:
                                return True, f"حظر إخباري (Finnhub): {ev.get('event')} ({country}) خلال {diff:.1f} دقيقة."
        except Exception as e:
            TerminalLogger.error("NEWS_FINNHUB_CHECK", str(e))

        return False, "لا توجد أخبار عالية التأثير في النافذة اللحظية."

# ==============================================================================
# 10. تدفق الأوامر، دايفرجنس CVD، مصائد السيولة، وفجوات FVG
# ==============================================================================
class OrderFlowEngine:
    @staticmethod
    def calculate_volume_delta(df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        range_hl = (d['high'] - d['low']) + 1e-8
        clv = ((d['close'] - d['low']) - (d['high'] - d['close'])) / range_hl
        d['bar_delta'] = d['volume'] * clv
        d['cvd'] = d['bar_delta'].cumsum()
        d['cvd_zscore'] = ((d['cvd'] - d['cvd'].rolling(30).mean()) / (d['cvd'].rolling(30).std() + 1e-8)).fillna(0.0)
        
        body = abs(d['close'] - d['open'])
        wick_lower = np.minimum(d['open'], d['close']) - d['low']
        wick_upper = d['high'] - np.maximum(d['open'], d['close'])
        
        d['bullish_absorption'] = (d['volume'] > d['volume'].rolling(20).mean() * 1.4) & (wick_lower > body * 1.5)
        d['bearish_absorption'] = (d['volume'] > d['volume'].rolling(20).mean() * 1.4) & (wick_upper > body * 1.5)
        return d

    @staticmethod
    def detect_cvd_divergence(df: pd.DataFrame) -> tuple[int, str]:
        if len(df) < 15:
            return 0, "بيانات غير كافية لدايفرجنس CVD"

        recent = df.tail(12)
        if recent['low'].iloc[-1] <= recent['low'].min() and recent['cvd'].iloc[-1] > recent['cvd'].min():
            if recent['bullish_absorption'].any() or recent['cvd_zscore'].iloc[-1] > -0.5:
                return 1, "دايفرجنس شرائي صريح (Bullish CVD Divergence)"

        if recent['high'].iloc[-1] >= recent['high'].max() and recent['cvd'].iloc[-1] < recent['cvd'].max():
            if recent['bearish_absorption'].any() or recent['cvd_zscore'].iloc[-1] < 0.5:
                return -1, "دايفرجنس بيعي صريح (Bearish CVD Divergence)"

        return 0, "لا يوجد دايفرجنس CVD"

    @staticmethod
    def check_order_flow_toxicity(df: pd.DataFrame, action: str) -> tuple[bool, float]:
        if len(df) < 5:
            return False, 0.0
        recent = df.tail(2)
        tot_vol = recent['volume'].sum() + 1e-8
        net_delta = recent['bar_delta'].sum()
        toxicity = abs(net_delta) / tot_vol

        if action == "BUY" and net_delta < 0 and toxicity > 0.70:
            return True, float(toxicity)
        elif action == "SELL" and net_delta > 0 and toxicity > 0.70:
            return True, float(toxicity)

        return False, float(toxicity)

    @staticmethod
    def detect_eqh_eql_sweep(df: pd.DataFrame, pip_mult: float) -> tuple[int, str]:
        if len(df) < 30:
            return 0, "بيانات غير كافية لـ EQH/EQL"

        recent_highs = df['high'].iloc[-25:-4]
        recent_lows = df['low'].iloc[-25:-4]
        last_close = df['close'].iloc[-1]
        last_high = df['high'].iloc[-1]
        last_low = df['low'].iloc[-1]

        sorted_highs = recent_highs.nlargest(2)
        if len(sorted_highs) == 2 and abs(sorted_highs.iloc[0] - sorted_highs.iloc[1]) <= (0.6 * pip_mult):
            pos_0 = df.index.get_loc(sorted_highs.index[0])
            pos_1 = df.index.get_loc(sorted_highs.index[1])
            if abs(pos_0 - pos_1) >= 3:
                eqh_level = max(sorted_highs.iloc[0], sorted_highs.iloc[1])
                if last_high > eqh_level and last_close < eqh_level:
                    if df['bearish_absorption'].iloc[-1] or df['cvd_zscore'].iloc[-1] < -0.5:
                        return -1, f"اقتناص قمم متساوية (EQH Sweep at {eqh_level:.5f})"

        sorted_lows = recent_lows.nsmallest(2)
        if len(sorted_lows) == 2 and abs(sorted_lows.iloc[0] - sorted_lows.iloc[1]) <= (0.6 * pip_mult):
            pos_0 = df.index.get_loc(sorted_lows.index[0])
            pos_1 = df.index.get_loc(sorted_lows.index[1])
            if abs(pos_0 - pos_1) >= 3:
                eql_level = min(sorted_lows.iloc[0], sorted_lows.iloc[1])
                if last_low < eql_level and last_close > eql_level:
                    if df['bullish_absorption'].iloc[-1] or df['cvd_zscore'].iloc[-1] > 0.5:
                        return 1, f"اقتناص قيعان متساوية (EQL Sweep at {eql_level:.5f})"

        return 0, "لا توجد مصيدة EQH/EQL"

    @staticmethod
    def detect_asian_liquidity_sweep(df: pd.DataFrame, asian_high: float, asian_low: float, pip_mult: float) -> tuple[int, str]:
        if asian_high <= 0 or asian_low <= 0 or len(df) < 5:
            return 0, "مدى آسيا غير متوفر"

        now_utc = datetime.now(timezone.utc)
        if not (7 <= now_utc.hour < 16):
            return 0, "خارج نافذة اقتناص سيولة آسيا"

        last_bars = df.tail(3)
        swept_low = (last_bars['low'].min() < asian_low) and (last_bars['close'].iloc[-1] > asian_low)
        if swept_low and (last_bars['bullish_absorption'].any() or last_bars['cvd_zscore'].iloc[-1] > 0.5):
            return 1, f"اقتناص سيولة قاع آسيا (Bullish Sweep under {asian_low:.5f})"

        swept_high = (last_bars['high'].max() > asian_high) and (last_bars['close'].iloc[-1] < asian_high)
        if swept_high and (last_bars['bearish_absorption'].any() or last_bars['cvd_zscore'].iloc[-1] < -0.5):
            return -1, f"اقتناص سيولة قمة آسيا (Bearish Sweep above {asian_high:.5f})"

        return 0, "لا توجد مصيدة سيولة حالية"

    @staticmethod
    def detect_fvg_confluence(df: pd.DataFrame, action: str, current_price: float, pip_mult: float) -> tuple[bool, str]:
        if len(df) < 10:
            return False, "بيانات غير كافية لـ FVG"
        
        for i in range(len(df) - 1, max(len(df) - 10, 2), -1):
            if action == "BUY":
                gap_low = df['high'].iloc[i - 2]
                gap_high = df['low'].iloc[i]
                if gap_high > gap_low:
                    gap_size_pips = (gap_high - gap_low) / pip_mult
                    if gap_size_pips >= 1.0 and (gap_low <= current_price <= (gap_high + (0.5 * pip_mult))):
                        return True, f"إعادة اختبار فجوة شرائية FVG ({gap_size_pips:.1f} نقطة)"
            elif action == "SELL":
                gap_high = df['low'].iloc[i - 2]
                gap_low = df['high'].iloc[i]
                if gap_high > gap_low:
                    gap_size_pips = (gap_high - gap_low) / pip_mult
                    if gap_size_pips >= 1.0 and ((gap_low - (0.5 * pip_mult)) <= current_price <= gap_high):
                        return True, f"إعادة اختبار فجوة بيعية FVG ({gap_size_pips:.1f} نقطة)"
        return False, "لا توجد فجوة FVG نشطة حالياً"

class HMMRegimeClassifier:
    def __init__(self):
        self.model = None

    def fit_predict_regime(self, df: pd.DataFrame) -> int:
        if len(df) < 60:
            return 1
        returns = df['close'].pct_change().dropna().values.reshape(-1, 1)
        vol = df['close'].rolling(10).std().dropna().values.reshape(-1, 1)
        min_len = min(len(returns), len(vol))
        X = np.column_stack([returns[-min_len:], vol[-min_len:]])

        if GaussianHMM is not None:
            try:
                self.model = GaussianHMM(n_components=3, covariance_type="diag", n_iter=40, random_state=42)
                self.model.fit(X)
                pred_states = self.model.predict(X)
                state_vars = [np.var(X[pred_states == i, 0]) if np.sum(pred_states == i) > 0 else 0 for i in range(3)]
                sorted_rank = np.argsort(state_vars)
                rank_map = {sorted_rank[0]: 0, sorted_rank[1]: 1, sorted_rank[2]: 2}
                return rank_map.get(pred_states[-1], 1)
            except Exception as e:
                TerminalLogger.error("HMM_FIT_PREDICT", str(e))

        if X[-1, 1] > np.mean(X[:, 1]) * 2.0:
            return 2
        elif abs(X[-1, 0]) > np.std(X[:, 0]) * 1.1:
            return 0
        return 1

class PopulationStabilityIndex:
    @staticmethod
    def calculate_psi(expected: np.ndarray, actual: np.ndarray, num_bins=5) -> float:
        try:
            if len(expected) < 30 or len(actual) < 30:
                return 0.0
            quantiles = np.linspace(0, 100, num_bins + 1)
            bin_edges = np.percentile(expected, quantiles)
            bin_edges = np.unique(bin_edges)
            if len(bin_edges) < 2:
                return 0.0
            bin_edges[0] -= 1e-5
            bin_edges[-1] += 1e-5

            exp_counts = np.histogram(expected, bins=bin_edges)[0] + 1e-4
            act_counts = np.histogram(actual, bins=bin_edges)[0] + 1e-4

            exp_pct = exp_counts / np.sum(exp_counts)
            act_pct = act_counts / np.sum(act_counts)

            psi = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
            return float(psi)
        except Exception:
            return 0.0

# ==============================================================================
# 11. النماذج الهجينة والمصادقة الإحصائية (Conformal & Online Pruning & Canary)
# ==============================================================================
class HybridMetaLabeler:
    FEATURE_NAMES = ["close", "vol", "mom", "cvd", "frac_diff", "kalman_v"]

    def __init__(self):
        self.lgb_model = None
        self.cb_model = None
        self.active_features = list(self.FEATURE_NAMES)
        self.calibration_errors_y1 = []
        self.is_trained = False

    def train_ensemble_with_purged_cv(self, df_m1: pd.DataFrame, pip_mult: float) -> tuple[bool, str]:
        if len(df_m1) < 180:
            return False, "بيانات غير كافية للتجزئة النظيفة"
        
        d = df_m1.copy().dropna()
        d['frac_diff'] = AdvancedQuantMath.fractional_differentiation(d['close'], d=0.4)
        
        v_list = []
        c_vals = d['close'].values
        for idx in range(len(c_vals)):
            if idx < 5:
                v_list.append(0.0)
            else:
                _, v = AdvancedQuantMath.kalman_filter_price_velocity(c_vals[max(0, idx-15): idx+1])
                v_list.append(v)
        d['kalman_v'] = v_list
        
        y_barrier = AdvancedQuantMath.triple_barrier_labeling(d, pt_mult=2.0, sl_mult=1.0, max_bars=10)
        y = (y_barrier > 0).astype(int).iloc[:-10]

        features = pd.DataFrame({
            "close": d['close'],
            "vol": d['volume'],
            "mom": d['close'].pct_change(10),
            "cvd": d.get('cvd_zscore', pd.Series(0, index=d.index)),
            "frac_diff": d['frac_diff'],
            "kalman_v": d['kalman_v']
        }).iloc[:-10].fillna(0.0)

        candidate_features = []
        for col in self.FEATURE_NAMES:
            if features[col].std() > 1e-6:
                candidate_features.append(col)
        if len(candidate_features) < 2:
            candidate_features = list(self.FEATURE_NAMES)

        features = features[candidate_features]

        split_idx = int(len(features) * 0.70)
        ref_features = features['mom'].iloc[:split_idx].values
        curr_features = features['mom'].iloc[split_idx:].values
        psi_score = PopulationStabilityIndex.calculate_psi(ref_features, curr_features)

        if psi_score > Config.PSI_DRIFT_THRESHOLD and self.is_trained:
            TerminalLogger.filter(f"تجميد تدريب النماذج: رصد انجراف إحصائي (PSI = {psi_score:.3f} > {Config.PSI_DRIFT_THRESHOLD})")
            return False, f"تجميد التدريب: رصد انجراف إحصائي (PSI = {psi_score:.3f} > {Config.PSI_DRIFT_THRESHOLD})"

        embargo = 10
        train_end = split_idx
        val_start = split_idx + embargo

        if val_start >= len(features) - 20:
            return False, "فجوة العزل تتجاوز البيانات المتاحة"

        X_train = features.iloc[:train_end]
        y_train = y.iloc[:train_end]
        X_val = features.iloc[val_start:]
        y_val = y.iloc[val_start:]

        if len(np.unique(y_train)) < 2:
            return False, "تنوع الإشارات التدريبية غير كافٍ"

        temp_lgb = None
        temp_cb = None

        if lgb is not None:
            try:
                temp_lgb = lgb.LGBMClassifier(n_estimators=35, learning_rate=0.04, max_depth=3, verbose=-1, random_state=42)
                temp_lgb.fit(X_train, y_train)
            except Exception as e:
                TerminalLogger.error("LGB_TRAIN", str(e))

        if CatBoostClassifier is not None:
            try:
                temp_cb = CatBoostClassifier(iterations=35, learning_rate=0.04, depth=3, verbose=False, random_seed=42)
                temp_cb.fit(X_train, y_train)
            except Exception as e:
                TerminalLogger.error("CATBOOST_TRAIN", str(e))

        if temp_lgb is not None or temp_cb is not None:
            self.lgb_model = temp_lgb
            self.cb_model = temp_cb
            self.active_features = candidate_features
            self.is_trained = True

            if len(X_val) > 10:
                val_probs = []
                if self.lgb_model: val_probs.append(self.lgb_model.predict_proba(X_val)[:, 1])
                if self.cb_model: val_probs.append(self.cb_model.predict_proba(X_val)[:, 1])
                if val_probs:
                    ensemble_val = np.mean(val_probs, axis=0)
                    winning_mask = (y_val.values == 1)
                    if np.sum(winning_mask) >= 3:
                        errs = 1.0 - ensemble_val[winning_mask]
                    else:
                        errs = np.abs(y_val.values - ensemble_val)
                    self.calibration_errors_y1 = [float(e) for e in errs if not np.isnan(e)]

            TerminalLogger.success(f"اكتمل تدريب النماذج الهجينة بنجاح بالميزات: {', '.join(self.active_features)}")
            return True, f"اكتمل التدريب النظيف بالميزات النشطة {len(self.active_features)} (PSI = {psi_score:.3f})"
        
        return False, "تعذر تهيئة نماذج التعلم الآلي"

    def predict_conformal_probability(self, feature_row: dict) -> tuple[float, bool]:
        if not self.is_trained:
            return 0.70, True

        probs = []
        X_df = pd.DataFrame([[feature_row.get(k, 0.0) for k in self.active_features]], columns=self.active_features).fillna(0.0)

        if self.lgb_model is not None:
            try:
                probs.append(float(self.lgb_model.predict_proba(X_df)[0][1]))
            except Exception:
                pass
        if self.cb_model is not None:
            try:
                probs.append(float(self.cb_model.predict_proba(X_df)[0][1]))
            except Exception:
                pass

        final_prob = float(np.mean(probs)) if probs else 0.70
        is_conformal_safe = True
        clean_calib = [e for e in self.calibration_errors_y1 if not np.isnan(e)]
        if len(clean_calib) >= 3:
            q90 = float(np.percentile(clean_calib, 90))
            if not np.isnan(q90):
                is_conformal_safe = ((1.0 - final_prob) <= q90)

        return final_prob, is_conformal_safe

class CanaryShadowTester:
    def __init__(self):
        self.shadow_predictions = []
        self.deployment_time = time.time()

    def record_shadow_prediction(self, prob: float, realized_pnl: float):
        self.shadow_predictions.append({"prob": prob, "pnl": realized_pnl, "time": time.time()})
        if len(self.shadow_predictions) > 20:
            self.shadow_predictions.pop(0)

    def is_canary_healthy(self) -> bool:
        if (time.time() - self.deployment_time) < (Config.CANARY_FORWARD_TEST_MINUTES * 60):
            return True
        if len(self.shadow_predictions) < 5:
            return True
        wins = [p['pnl'] > 0 for p in self.shadow_predictions]
        win_rate = sum(wins) / len(wins)
        if win_rate < 0.45:
            TerminalLogger.filter("نموذج الظل حقق فوزاً أقل من 45%! إعادة تدريب النماذج فورياً...")
            self.deployment_time = time.time()
            self.shadow_predictions.clear()
            return False
        return True

# ==============================================================================
# 12. إدارة المخاطر، التهدئة، تدرج التراجع، وتكافؤ المخاطر (Risk Parity & CVaR)
# ==============================================================================
class AdvancedRiskAndSessionManager:
    def __init__(self):
        self.last_reset_day = datetime.now(timezone.utc).day
        self.day_start_equity = 1000.0
        self.high_water_mark = 0.0
        self.hwm_timestamp = time.time()
        self.daily_circuit_breaker_active = False
        self.consecutive_losses = 0
        self.cooldown_until = 0.0

    def trigger_cooldown(self, minutes=Config.COOLDOWN_MINUTES):
        self.cooldown_until = time.time() + (minutes * 60)
        TerminalLogger.filter(f"تم تفعيل فترة التهدئة الإلزامية لمدة {minutes} دقيقة بعد تكرار الخسائر.")

    def is_in_cooldown(self) -> tuple[bool, int]:
        rem = int(self.cooldown_until - time.time())
        if rem > 0:
            return True, int(rem / 60) + 1
        return False, 0

    def update_daily_equity_baseline(self, current_equity: float) -> bool:
        now_utc = datetime.now(timezone.utc)
        if self.high_water_mark <= 0.0 or current_equity > self.high_water_mark:
            self.high_water_mark = current_equity
            self.hwm_timestamp = time.time()

        if now_utc.day != self.last_reset_day:
            self.day_start_equity = current_equity
            self.last_reset_day = now_utc.day
            self.daily_circuit_breaker_active = False
            TerminalLogger.info(f"بدء يوم تداول جديد! خط الأساس اليومي لرأس المال: ${current_equity:,.2f}")
            return True
        return False

    def check_circuit_breakers(self, current_equity: float) -> tuple[bool, str]:
        self.update_daily_equity_baseline(current_equity)

        if self.daily_circuit_breaker_active:
            return False, "قاطع الهبوط اليومي نشط (حظر التداول مؤقتاً لحماية الحساب)."

        drawdown_pct = ((self.day_start_equity - current_equity) / self.day_start_equity) * 100.0
        if drawdown_pct >= Config.MAX_DAILY_DRAWDOWN_PCT:
            self.daily_circuit_breaker_active = True
            TerminalLogger.error("CIRCUIT_BREAKER", f"تجاوزت الخسائر اليومية {drawdown_pct:.2f}% (الحد الأقصى {Config.MAX_DAILY_DRAWDOWN_PCT}%)")
            return False, f"🚨 قاطع الهبوط: تجاوزت الخسائر {drawdown_pct:.2f}% (الحد الأقصى {Config.MAX_DAILY_DRAWDOWN_PCT}%)."

        time_under_water = time.time() - self.hwm_timestamp
        if (current_equity < self.high_water_mark * 0.985) and (time_under_water > Config.MAX_TIME_UNDER_WATER_SEC):
            TerminalLogger.filter("حظر مدة التراجع: الحساب غارق أسفل أعلى قمة لأكثر من 4 ساعات متواصلة.")
            return False, f"حظر مدة التراجع: الحساب غارق أسفل القمة لأكثر من 4 ساعات ({time_under_water/3600:.1f}h)"

        return True, "إدارة المخاطر اليومية مستقرة."

    def calculate_cvar_99(self) -> float:
        df_trades = DuckDBWarehouse.get_recent_closed_trades(limit=50)
        if len(df_trades) < 15:
            return 25.0
        pnls = df_trades['pnl'].values
        losses = -pnls[pnls < 0]
        if len(losses) < 3:
            return 25.0
        var_99 = np.percentile(losses, 90)
        cvar = np.mean(losses[losses >= var_99])
        return float(cvar)

    def get_drawdown_throttle(self, current_equity: float) -> float:
        dd_pct = max(0.0, ((self.day_start_equity - current_equity) / (self.day_start_equity + 1e-8)) * 100.0)
        throttle = max(0.25, 1.0 - (dd_pct / Config.MAX_DAILY_DRAWDOWN_PCT))
        return float(throttle)

    def get_seasonality_filter(self) -> tuple[float, float, str]:
        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()
        hour = now_utc.hour

        if weekday == 0 and hour < 10:
            return 0.70, 0.72, "Monday Open (Reduced Risk)"
        elif weekday == 4 and hour >= 14:
            return 0.70, 0.72, "Friday De-Risking (Conservative)"
        elif weekday in [1, 2, 3]:
            return 1.0, 0.65, "Midweek Prime Liquidity"

        return 0.85, 0.68, "Standard Session"

    def get_dynamic_session_weights(self) -> dict:
        hour = datetime.now(timezone.utc).hour
        if 0 <= hour < 7:
            return {
                "SMC": 0.20, "VWAP": 0.60, "MOM": 0.20, 
                "session": "Asian Range",
                "sl_mult": 1.4, "tp_mult": 2.0,
                "mode": "MEAN_REVERSION"
            }
        elif 7 <= hour < 12:
            return {
                "SMC": 0.55, "VWAP": 0.15, "MOM": 0.30, 
                "session": "London Expansion",
                "sl_mult": 1.5, "tp_mult": 3.0,
                "mode": "BREAKOUT_EXPANSION"
            }
        elif 12 <= hour < 16:
            return {
                "SMC": 0.30, "VWAP": 0.20, "MOM": 0.50, 
                "session": "Overlap Momentum",
                "sl_mult": 1.6, "tp_mult": 3.5,
                "mode": "TREND_CONTINUATION"
            }
        else:
            return {
                "SMC": 0.25, "VWAP": 0.50, "MOM": 0.25, 
                "session": "Late NY",
                "sl_mult": 1.4, "tp_mult": 2.2,
                "mode": "MEAN_REVERSION"
            }

    @staticmethod
    def calculate_vol_of_vol(df: pd.DataFrame, pip_mult: float) -> tuple[float, float, float]:
        if len(df) < 30:
            return 0.15, 1.0, 1.0

        tr = np.maximum(df['high'] - df['low'], 
                        np.maximum(abs(df['high'] - df['close'].shift(1)), 
                                   abs(df['low'] - df['close'].shift(1))))
        atr_window = tr.rolling(14).mean().dropna().tail(20)
        if len(atr_window) < 5:
            return 0.15, 1.0, 1.0
        mean_atr = atr_window.mean() + 1e-8
        std_atr = atr_window.std()
        if np.isnan(std_atr):
            return 0.15, 1.0, 1.0
        vol_of_vol = float(std_atr / mean_atr)

        size_penalty = 1.0
        sl_buffer = 1.0
        if vol_of_vol > Config.VOL_OF_VOL_HIGH_THRESHOLD:
            size_penalty = max(0.60, 1.0 / (1.0 + (vol_of_vol - Config.VOL_OF_VOL_HIGH_THRESHOLD) * 2.0))
            sl_buffer = min(1.30, 1.0 + (vol_of_vol * 0.4))

        return vol_of_vol, size_penalty, sl_buffer

    @staticmethod
    def calculate_risk_parity_weight(asset_atr_pips: float, basket_atrs: dict) -> float:
        if not basket_atrs or asset_atr_pips <= 0:
            return 1.0
        valid_atrs = [v for v in basket_atrs.values() if not np.isnan(v) and v > 0]
        if not valid_atrs:
            return 1.0
        avg_basket_atr = float(np.mean(valid_atrs))
        parity_ratio = avg_basket_atr / asset_atr_pips
        if np.isnan(parity_ratio):
            return 1.0
        return max(0.65, min(1.40, parity_ratio))

    def get_bayesian_rolling_kelly(self) -> float:
        df_trades = DuckDBWarehouse.get_recent_closed_trades(limit=30)
        if len(df_trades) < 10:
            return max(0.05, (0.58 * 2.5 - 0.42) / 2.5)

        pnls = df_trades['pnl'].values
        wins = pnls[pnls > 0]
        losses = abs(pnls[pnls < 0])

        total_count = len(pnls)
        p_bayesian = (len(wins) + 6.0) / (total_count + 10.0)
        
        avg_win = np.mean(wins) if len(wins) > 0 else 1.0
        avg_loss = np.mean(losses) if len(losses) > 0 else 1.0
        b_ratio = max(1.2, avg_win / (avg_loss + 1e-8))

        full_kelly = max(0.02, min(0.40, (p_bayesian * b_ratio - (1.0 - p_bayesian)) / b_ratio))
        return full_kelly

    def calculate_lot_size(self, epic: str, equity: float, current_price: float, sl_pips: float, avg_latency: float, vol_penalty: float, risk_parity_mult: float) -> float:
        if current_price <= 0 or np.isnan(current_price):
            current_price = 150.0 if "JPY" in epic.upper() else 1.0850

        throttle = self.get_drawdown_throttle(equity)
        season_mult, _, _ = self.get_seasonality_filter()
        safe_parity = 1.0 if np.isnan(risk_parity_mult) else risk_parity_mult
        safe_vol_pen = 1.0 if np.isnan(vol_penalty) else vol_penalty
        active_kelly = Config.KELLY_FRACTION * throttle * season_mult * safe_vol_pen * safe_parity

        if self.consecutive_losses >= 2:
            active_kelly *= 0.5

        if avg_latency > Config.MAX_API_LATENCY_MS:
            active_kelly *= 0.5

        bayesian_kelly = self.get_bayesian_rolling_kelly()
        risk_capital = equity * (bayesian_kelly * active_kelly)
        
        pip_val_usd = FastCapitalBroker.get_pip_value_usd(epic, current_price)
        pip_risk = sl_pips * pip_val_usd
        calculated_lots = risk_capital / (pip_risk if pip_risk > 0 else 40.0)
        
        buying_power = equity * Config.LEVERAGE * Config.MAX_MARGIN_USAGE_PCT
        if epic.upper().startswith("USD"):
            max_possible_lots = buying_power / 100000.0
        else:
            max_possible_lots = buying_power / (100000.0 * current_price)

        if np.isnan(calculated_lots) or calculated_lots <= 0:
            calculated_lots = 0.01

        final_lot = max(0.01, min(calculated_lots, max_possible_lots))
        return round(float(final_lot), 2)

    @staticmethod
    def calculate_midnight_performance(current_equity: float, day_start_equity: float) -> dict:
        df_trades = DuckDBWarehouse.get_recent_closed_trades(limit=50)
        tca = DuckDBWarehouse.get_tca_summary(limit=50)
        pnl_usd = current_equity - day_start_equity
        return_pct = (pnl_usd / (day_start_equity + 1e-8)) * 100.0

        if df_trades.empty:
            return {
                "pnl_usd": round(pnl_usd, 2),
                "return_pct": round(return_pct, 2),
                "trades": 0,
                "win_rate": 0.0,
                "profit_factor": 1.0,
                "sortino": 0.0,
                "best_asset": "لا صفقات",
                "tca": tca
            }

        pnls = df_trades['pnl'].values
        wins = pnls[pnls > 0]
        losses = abs(pnls[pnls < 0])

        gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_loss = float(np.sum(losses)) if len(losses) > 0 else 1e-5
        profit_factor = round(gross_profit / gross_loss, 2)
        win_rate = round((len(wins) / max(1, len(pnls))) * 100.0, 1)

        downside = pnls[pnls < 0]
        downside_std = float(np.std(downside)) if len(downside) > 1 else 1e-4
        sortino = round(float(np.mean(pnls)) / (downside_std + 1e-8), 2)

        asset_perf = df_trades.groupby("epic")["pnl"].sum()
        best_asset = asset_perf.idxmax() if not asset_perf.empty else "N/A"

        return {
            "pnl_usd": round(pnl_usd, 2),
            "return_pct": round(return_pct, 2),
            "trades": len(pnls),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "sortino": sortino,
            "best_asset": best_asset,
            "tca": tca
        }

class MultiTimeframeAnalyzer:
    @staticmethod
    def get_m15_bias_from_duckdb(epic: str) -> tuple[int, str]:
        df_m1 = DuckDBWarehouse.get_cached_candles(epic, limit=1000)
        if len(df_m1) < 45:
            return 0, "بيانات DuckDB غير كافية لـ M15"

        try:
            d = df_m1.copy()
            d['dt'] = pd.to_datetime(d['timestamp'], utc=True)
            d.set_index('dt', inplace=True)
            d.sort_index(inplace=True)
            
            df_m15 = d.resample('15min').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna().reset_index()

            if len(df_m15) < 15:
                return 0, "شمعات M15 المجمعة قيد الاكتمال"

            ema20 = df_m15['close'].ewm(span=min(20, len(df_m15)), adjust=False).mean().iloc[-1]
            ema50 = df_m15['close'].ewm(span=min(50, len(df_m15)), adjust=False).mean().iloc[-1]

            if ema20 > ema50:
                return 1, "الاتجاه صاعد على M15 (DuckDB)"
            elif ema20 < ema50:
                return -1, "الاتجاه هابط على M15 (DuckDB)"
            return 0, "اتجاه M15 محايد"
        except Exception as e:
            TerminalLogger.error(f"MTF_M15_{epic}", str(e))
            return 0, f"خطأ تجميع M15: {e}"

# ==============================================================================
# 13. المصادقة البصرية للشارت عبر Gemini Vision مع تنقية المويجات Haar
# ==============================================================================
class GeminiChartVisionValidator:
    @staticmethod
    def generate_chart_image_b64(df_m1: pd.DataFrame, vwap_series: pd.Series) -> str:
        d = df_m1.tail(40).copy().reset_index(drop=True)
        if len(d) < 10:
            return ""

        denoised_close = AdvancedQuantMath.haar_wavelet_denoise(d['close'].values)
        v = vwap_series.tail(40).values

        fig = Figure(figsize=(6, 3), dpi=100, facecolor='#0E1117')
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111, facecolor='#0E1117')

        try:
            for i, row in d.iterrows():
                color = '#00FF7F' if row['close'] >= row['open'] else '#FF3B30'
                ax.plot([i, i], [row['low'], row['high']], color=color, linewidth=1)
                ax.plot([i, i], [row['open'], row['close']], color=color, linewidth=3)

            ax.plot(range(len(denoised_close)), denoised_close, color='#FFD700', linestyle=':', linewidth=1.2, label="Denoised Trend")
            ax.plot(range(len(v)), v, color='#00BFFF', linestyle='--', linewidth=1.5, label="VWAP")
            ax.axis('off')
            fig.tight_layout()

            buf = io.BytesIO()
            canvas.print_png(buf)
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode('utf-8')
            buf.close()
            return img_b64
        finally:
            fig.clf()
            del ax, canvas, fig
            gc.collect()

    @classmethod
    def validate_setup_with_vision(cls, epic: str, df_m1: pd.DataFrame, vwap_series: pd.Series, action: str, price: float) -> tuple[bool, str]:
        try:
            b64_chart = cls.generate_chart_image_b64(df_m1, vwap_series)
            if not b64_chart:
                return True, "تخطي الفحص (بيانات بصرية قليلة)"

            url = f"{Config.GEMINI_ENDPOINT}?key={Config.GEMINI_API_KEY}"
            headers = {"Content-Type": "application/json"}

            prompt = (
                f"You are a quant execution risk controller. We want to enter '{action}' on {epic} at {price}. "
                f"Examine this 40-bar chart with VWAP and Denoised Trend. Look for hostile counter-trend wicks or fakeouts. "
                f"Respond STRICTLY in JSON without markdown code blocks: {{\"decision\": \"APPROVE\" or \"REJECT\", \"reason\": \"brief explanation in Arabic\"}}"
            )

            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inlineData": {"mimeType": "image/png", "data": b64_chart}}
                        ]
                    }
                ]
            }

            r = requests.post(url, headers=headers, json=payload, timeout=8)
            if r.status_code == 200:
                res_data = r.json()
                candidates = res_data.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    parts = candidates[0]["content"].get("parts", [])
                    if parts and "text" in parts[0]:
                        raw_text = parts[0]["text"].strip()
                        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                        if json_match:
                            data = json.loads(json_match.group(0))
                            decision = (data.get("decision") == "APPROVE")
                            reason = data.get("reason", "موافقة بصرية")
                            if not decision:
                                TerminalLogger.filter(f"رفض بصري من Gemini Vision لزوج {epic}: {reason}")
                            return decision, reason
            else:
                TerminalLogger.error("GEMINI_VISION_API", f"HTTP {r.status_code}: {r.text}")
        except Exception as e:
            TerminalLogger.error("GEMINI_VISION_CALL", str(e))
        return True, "تم تخطي الفحص البصري"

# ==============================================================================
# 14. محرك Google Gemini مع Function Calling المحدث
# ==============================================================================
GEMINI_FUNCTION_TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "update_trading_risk",
                "description": "تعديل إعدادات مضاعفات الوقف والهدف المشتقة من ATR ونسبة صيغة كيلي",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "atr_sl_mult": {"type": "NUMBER", "description": "مضاعف الوقف بالنسبة لـ ATR (مثلاً 1.5)"},
                        "atr_tp_mult": {"type": "NUMBER", "description": "مضاعف الهدف بالنسبة لـ ATR (مثلاً 3.0)"},
                        "kelly_fraction": {"type": "NUMBER", "description": "نسبة كسر كيلي (بين 0.05 و 0.50)"}
                    }
                }
            },
            {
                "name": "force_recalibrate_models",
                "description": "إعادة تدريب نماذج التعلم الآلي CatBoost/LightGBM والباكتيست الذاتي لجميع الأزواج فورياً",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "toggle_autotrade",
                "description": "تشغيل أو إيقاف التداول الآلي اللحظي في النظام",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "enabled": {"type": "BOOLEAN", "description": "True للتفعيل أو False للإيقاف"}
                    },
                    "required": ["enabled"]
                }
            },
            {
                "name": "switch_trading_mode",
                "description": "التبديل بين الحساب التجريبي Demo والحساب الحقيقي Live",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "mode": {"type": "STRING", "description": "'demo' أو 'live'"}
                    },
                    "required": ["mode"]
                }
            }
        ]
    }
]

class GeminiConversationalAgent:
    @staticmethod
    def ask_and_execute(user_message: str, system_instance, system_context: dict) -> str:
        url = f"{Config.GEMINI_ENDPOINT}?key={Config.GEMINI_API_KEY}"
        headers = {"Content-Type": "application/json"}

        system_instruction = f"""
أنت المهندس الكمي والمشرف الرئيسي على نظام التداول الآلي المؤسسي الشامل لـ CFDs على فريم M1.
أنت تتحدث باللغة العربية بطلاقة وبأسلوب مالي واثق ومحترف وموجز.

بيانات السوق والمحفظة اللحظية الحقيقية:
- الرصيد الإجمالي: ${system_context.get('balance', 0):,.2f}
- الرصيد المتاح: ${system_context.get('available', 0):,.2f}
- أزواج العملات النشطة: {', '.join(Config.ACTIVE_EPICS)}
- جلسة التداول الحالية: {system_context.get('session', 'N/A')}
- فلتر الموسمية: {system_context.get('season_desc', 'عادي')}
- حالة قاطع الهبوط اليومي: {'مفعل وحظر التداول' if system_context.get('circuit_breaker') else 'طبيعي ومستقر'}
- حالة فترة التهدئة: {system_context.get('cooldown_msg', 'غير مفعلة')}
- صحة النظام (RAM / CPU): {system_context.get('health', {}).get('ram_mb', 0)}MB / {system_context.get('health', {}).get('cpu_pct', 0)}%
- الصفقات المفتوحة: {system_context.get('open_trades', 0)}
- التداول الآلي: {'نشط' if system_context.get('autotrade') else 'متوقف مؤقتاً'}
- وضع الحساب: {'تجريبي (DEMO)' if system_context.get('is_demo') else 'حقيقي (LIVE)'}
- متوسط استجابة الوسيط: {system_context.get('latency', 0):.0f}ms
- مضاعفات ATR الحالية: الوقف={Config.ATR_SL_MULTIPLIER}x | الهدف={Config.ATR_TP_MULTIPLIER}x

المهمة:
1. إذا طلب المتداول تعديل أي إعداد، استدعِ الأداة المناسبة فوراً (Function Calling).
2. إذا كان السؤال تحليلياً، أجب بذكاء واحترافية استناداً إلى الأرقام الحقيقية المذكورة أعلاه.
"""
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": user_message}]}],
            "tools": GEMINI_FUNCTION_TOOLS
        }

        try:
            r = requests.post(url, headers=headers, json=payload, timeout=12)
            if r.status_code != 200:
                TerminalLogger.error("GEMINI_CHAT_API", f"HTTP {r.status_code}: {r.text}")
                return f"عذراً، حدث خطأ في الاتصال بمحرك Gemini (رمز {r.status_code})."

            res_json = r.json()
            candidates = res_json.get("candidates", [])
            if not candidates:
                return "لم يتم استلام رد من محرك Gemini."

            parts = candidates[0].get("content", {}).get("parts", [])
            text_response = ""
            func_call = None

            for p in parts:
                if "functionCall" in p:
                    func_call = p["functionCall"]
                if "text" in p:
                    text_response += p["text"]

            if func_call:
                func_name = func_call.get("name")
                args = func_call.get("args", {})

                if func_name == "update_trading_risk":
                    if "atr_sl_mult" in args:
                        Config.ATR_SL_MULTIPLIER = float(args["atr_sl_mult"])
                    if "atr_tp_mult" in args:
                        Config.ATR_TP_MULTIPLIER = float(args["atr_tp_mult"])
                    if "kelly_fraction" in args:
                        Config.KELLY_FRACTION = float(args["kelly_fraction"])
                    TerminalLogger.success(f"تعديل إعدادات المخاطر عبر Gemini: SL={Config.ATR_SL_MULTIPLIER}x, TP={Config.ATR_TP_MULTIPLIER}x, Kelly={Config.KELLY_FRACTION}")
                    return (
                        f"✅ **[تم تطبيق التعديل ذاتياً بواسطة Gemini]**\n\n"
                        f"• مضاعف وقف ATR: `{Config.ATR_SL_MULTIPLIER}x`\n"
                        f"• مضاعف هدف ATR: `{Config.ATR_TP_MULTIPLIER}x`\n"
                        f"• نسبة كسر كيلي للمخاطرة: `{Config.KELLY_FRACTION}`"
                    )

                elif func_name == "force_recalibrate_models":
                    evo_res = system_instance.train_and_evolve()
                    TerminalLogger.success("تمت إعادة المعايرة والتدريب والباكتيست عبر طلب Gemini.")
                    return (
                        f"🧠 **[تمت إعادة المعايرة والتدريب والباكتيست الذاتي لجميع الأزواج]**\n\n"
                        f"• الحالة: {evo_res.get('status')}"
                    )

                elif func_name == "toggle_autotrade":
                    system_instance.autotrade_active = bool(args.get("enabled", False))
                    status_str = "مُفعل بنجاح 🚀" if system_instance.autotrade_active else "مُعطل مؤقتاً 🛑"
                    TerminalLogger.info(f"تم تغيير حالة التداول الآلي إلى: {status_str}")
                    return f"🔄 **تم تغيير حالة التداول الآلي:** {status_str}"

                elif func_name == "switch_trading_mode":
                    mode_choice = str(args.get("mode", "demo")).lower()
                    if mode_choice == "live":
                        system_instance.broker.demo = False
                        Config.DEMO_MODE = False
                        system_instance.broker.login()
                        TerminalLogger.filter("⚠️ تم التحويل إلى وضع الحساب الحقيقي (LIVE)!")
                        return "⚠️ **تحذير: تم التبديل للحساب الحقيقي (LIVE)!**"
                    else:
                        system_instance.broker.demo = True
                        Config.DEMO_MODE = True
                        system_instance.broker.login()
                        TerminalLogger.info("تم التحويل إلى وضع الحساب التجريبي (DEMO).")
                        return "✅ **تم التبديل للحساب التجريبي (DEMO).**"

            return text_response if text_response else "تم استلام رسالتك وتحديث الحالة."

        except Exception as e:
            TerminalLogger.error("GEMINI_AGENT_ERROR", str(e), traceback.format_exc())
            return f"تعذر استكمال المعالجة عبر Gemini: {e}"

# ==============================================================================
# 15. محرك النظام الرئيسي وتدوير العملات والباكتيست الموحد
# ==============================================================================
class MasterQuantSystem:
    def __init__(self):
        DuckDBWarehouse.init_database()
        self.broker = FastCapitalBroker()
        self.order_flow = OrderFlowEngine()
        self.hmm = HMMRegimeClassifier()
        self.meta_labelers = {epic: HybridMetaLabeler() for epic in Config.ACTIVE_EPICS}
        self.canary_tester = CanaryShadowTester()
        self.risk_mgr = AdvancedRiskAndSessionManager()
        self.autotrade_active = False
        self.evolution_log = []
        self.processed_deal_ids = set()
        
        self.breakeven_applied_deals = set()
        self.entry_timestamps = {}
        self.slippage_history = {epic: [] for epic in Config.ACTIVE_EPICS}
        self.suspended_epics = {}

    def reconcile_closed_trades(self):
        activities = self.broker.fetch_recent_closed_trades(limit=5)
        for act in activities:
            details = act.get("details") if isinstance(act.get("details"), dict) else {}
            deal_id = act.get("dealId") or details.get("dealId")
            
            if deal_id and str(deal_id).strip() not in self.processed_deal_ids:
                pnl_val = None
                for k in ["profitAndLoss", "pnl", "profit"]:
                    if k in act and act[k] is not None:
                        pnl_val = float(act[k])
                        break
                    if k in details and details[k] is not None:
                        pnl_val = float(details[k])
                        break

                epic = act.get("epic") or details.get("epic") or "EURUSD"
                direction = act.get("direction") or details.get("direction") or "BUY"
                
                # إضافة المعرف فقط عند التأكد من إغلاق الصفقة ووجود قيمة PnL (Fix 2)
                if pnl_val is not None:
                    pnl = float(pnl_val)
                    if pnl < 0:
                        self.risk_mgr.consecutive_losses += 1
                        TerminalLogger.filter(f"تسجيل خسارة على الصفقة {deal_id} ({epic}) بمبلغ ${pnl:.2f} (خسائر متتالية: {self.risk_mgr.consecutive_losses})")
                        if self.risk_mgr.consecutive_losses >= 2:
                            self.risk_mgr.trigger_cooldown()
                    else:
                        self.risk_mgr.consecutive_losses = 0
                        TerminalLogger.success(f"تسجيل ربح على الصفقة {deal_id} ({epic}) بمبلغ +${pnl:.2f}")
                    
                    clean_id_str = str(deal_id).strip()
                    DuckDBWarehouse.log_closed_trade(clean_id_str, str(epic), str(direction), pnl)

                    recent_slips = self.slippage_history.get(str(epic), [0.0])
                    slip = recent_slips[-1] if recent_slips else 0.0
                    ref_price = 150.0 if "JPY" in str(epic).upper() else 1.0850
                    pip_val = FastCapitalBroker.get_pip_value_usd(str(epic), ref_price)
                    
                    # استخراج حجم العقد بدقة لحساب التكلفة الحقيقية للصفقة (Fix 3)
                    pos_size = float(act.get("size") or details.get("size") or 0.02)
                    spread_cost = round(0.8 * pip_val * pos_size, 2)
                    friction_usd = round((0.8 + slip) * pip_val * pos_size, 2)
                    f_ratio = round((friction_usd / (abs(pnl) + friction_usd + 1e-5)) * 100.0, 1)
                    DuckDBWarehouse.log_tca_metric(clean_id_str, str(epic), spread_cost, slip, friction_usd, f_ratio)
                    self.canary_tester.record_shadow_prediction(0.70, pnl)

                    self.processed_deal_ids.add(clean_id_str)
                    self.breakeven_applied_deals.discard(clean_id_str)
                    self.entry_timestamps.pop(clean_id_str, None)

    def enforce_dynamic_trade_management(self) -> list:
        events = []
        open_pos = self.broker.get_open_positions()
        open_deal_ids = set()

        for p in open_pos:
            pos_info = p.get("position", {})
            deal_id = str(pos_info.get("dealId", "")).strip()
            epic = p.get("market", {}).get("epic") or p.get("epic", "")
            direction = pos_info.get("direction")
            entry_price = float(pos_info.get("level") or 0.0)
            current_size = float(pos_info.get("size") or 0.0)
            current_sl = float(pos_info.get("stopLevel") or 0.0)
            
            if not deal_id or not epic or entry_price <= 0:
                continue

            open_deal_ids.add(deal_id)

            if deal_id not in self.entry_timestamps:
                self.entry_timestamps[deal_id] = time.time()
                
            pip_mult = self.broker.get_pip_multiplier(epic)
            current_bid = float(p.get("market", {}).get("bid") or 0.0)
            current_offer = float(p.get("market", {}).get("offer") or 0.0)
            
            # جلب الأسعار اللحظية كبديل فوري إذا كانت القيم غير متوفرة في لقطة المركز
            if current_bid <= 0 or current_offer <= 0:
                current_bid, current_offer = self.broker.get_latest_quote(epic)

            spread_pips = (current_offer - current_bid) / pip_mult if current_bid > 0 and current_offer > 0 else 0.8
            
            df_m1 = DuckDBWarehouse.get_cached_candles(epic, limit=50)
            if len(df_m1) < 15:
                atr_pips = 4.0
            else:
                tr = np.maximum(df_m1['high'] - df_m1['low'], 
                                np.maximum(abs(df_m1['high'] - df_m1['close'].shift(1)), 
                                           abs(df_m1['low'] - df_m1['close'].shift(1))))
                atr_pips = (tr.rolling(14).mean().iloc[-1]) / pip_mult
                if np.isnan(atr_pips) or atr_pips <= 0:
                    atr_pips = 4.0

            profit_pips = 0.0
            if direction == "BUY" and current_bid > 0:
                profit_pips = (current_bid - entry_price) / pip_mult
            elif direction == "SELL" and current_offer > 0:
                profit_pips = (entry_price - current_offer) / pip_mult

            # الخروج الزمني وفق عمر النصف لنموذج أورنشتاين-أولينبيك (Feature 8)
            ou_half_life = AdvancedQuantMath.calculate_ou_half_life(df_m1['close'].values)
            elapsed_minutes = (time.time() - self.entry_timestamps[deal_id]) / 60.0
            if elapsed_minutes > (ou_half_life * 2.0) and profit_pips < 1.0:
                if self.broker.close_position_fully(deal_id):
                    TerminalLogger.filter(f"خروج زمني (OU Timeout) على {epic} بعد مرور {elapsed_minutes:.1f} دقيقة (الربح: {profit_pips:+.1f} Pips)")
                    events.append(("OU_TIMEOUT_EXIT", epic, direction, entry_price, round(profit_pips, 1)))
                    continue

            # 1. نقل الوقف لنقطة الدخول (Breakeven) عند وصول الأرباح إلى 1.0 ATR
            be_threshold = max(Config.MIN_STOP_PIPS, atr_pips * 1.0)
            if profit_pips >= be_threshold and deal_id not in self.breakeven_applied_deals:
                if direction == "BUY":
                    new_sl = round(entry_price + ((spread_pips + 0.3) * pip_mult), 3 if "JPY" in epic else 5)
                else:
                    new_sl = round(entry_price - ((spread_pips + 0.3) * pip_mult), 3 if "JPY" in epic else 5)

                success_be = self.broker.update_position_stop(deal_id, new_sl)
                if success_be:
                    self.breakeven_applied_deals.add(deal_id)
                    current_sl = new_sl
                    TerminalLogger.success(f"تأمين الأرباح (Risk-Free Breakeven) على {epic}: نقل الوقف إلى {new_sl}")
                    events.append(("BREAKEVEN", epic, direction, new_sl, round(profit_pips, 1)))

            # 2. الوقف الهيكلي المتحرك (Structural Swing-Trailing Stop) خلف الشمعة السابقة
            elif deal_id in self.breakeven_applied_deals and len(df_m1) >= 3:
                prev_low = df_m1['low'].iloc[-2]
                prev_high = df_m1['high'].iloc[-2]

                if direction == "BUY":
                    structural_sl = round(prev_low - ((spread_pips + 0.3) * pip_mult), 3 if "JPY" in epic else 5)
                    if structural_sl > max(current_sl, entry_price) and (current_bid - structural_sl) >= (Config.MIN_STOP_PIPS * pip_mult):
                        if self.broker.update_position_stop(deal_id, structural_sl):
                            TerminalLogger.info(f"رفع الوقف الهيكلي لزوج {epic} إلى {structural_sl}")
                            events.append(("STRUCTURAL_TRAIL", epic, direction, structural_sl, round(profit_pips, 1)))
                elif direction == "SELL":
                    structural_sl = round(prev_high + ((spread_pips + 0.3) * pip_mult), 3 if "JPY" in epic else 5)
                    if (current_sl <= 0 or structural_sl < current_sl) and (structural_sl < entry_price) and (structural_sl - current_offer) >= (Config.MIN_STOP_PIPS * pip_mult):
                        if self.broker.update_position_stop(deal_id, structural_sl):
                            TerminalLogger.info(f"خفض الوقف الهيكلي لزوج {epic} إلى {structural_sl}")
                            events.append(("STRUCTURAL_TRAIL", epic, direction, structural_sl, round(profit_pips, 1)))

        stale_ids = [d for d in self.entry_timestamps if d not in open_deal_ids]
        for sid in stale_ids:
            self.entry_timestamps.pop(sid, None)
            self.breakeven_applied_deals.discard(sid)

        return events

    def enforce_swap_closure_if_needed(self):
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour == 21 and 50 <= now_utc.minute <= 55:
            open_pos = self.broker.get_open_positions()
            for p in open_pos:
                pos_info = p.get("position", {})
                deal_id = pos_info.get("dealId")
                upl = float(pos_info.get("upl") or pos_info.get("profitAndLoss") or 0.0)
                if deal_id and upl > 0:
                    self.broker.close_position_fully(str(deal_id))
                    TerminalLogger.success(f"[Swap Guard]: إغلاق صفقة رابحة {deal_id} بربح ${upl:.2f} لتفادي رسوم التبييت الليلية.")

    def run_single_asset_backtest(self, epic: str, df: pd.DataFrame) -> dict:
        if len(df) < 60:
            return {"status": "بيانات غير كافية"}

        d = df.copy()
        pip_mult = self.broker.get_pip_multiplier(epic)
        
        tp = (d['high'] + d['low'] + d['close']) / 3
        vwap = (tp * d['volume']).cumsum() / (d['volume'].cumsum() + 1e-8)
        std = (tp - vwap).rolling(30).std()
        
        vwap_sig = pd.Series(0, index=d.index)
        vwap_sig[d['close'] < (vwap - 2.0 * std)] = 1
        vwap_sig[d['close'] > (vwap + 2.0 * std)] = -1

        mom_sig = np.sign(d['close'].pct_change(10)).fillna(0.0)
        smc_sig = pd.Series(0, index=d.index)
        smc_sig[d['close'] < d['close'].shift(20).rolling(20).min()] = 1
        smc_sig[d['close'] > d['close'].shift(20).rolling(20).max()] = -1

        signals = (smc_sig * 0.40) + (vwap_sig * 0.35) + (mom_sig * 0.25)
        pos = pd.Series(0, index=d.index)
        pos[signals > 0.40] = 1
        pos[signals < -0.40] = -1

        trades = []
        current_pos = 0
        entry_price = 0.0

        spread = 0.8 * pip_mult
        equity_curve = [1000.0]

        for i in range(len(d) - 1):
            p = pos.iloc[i]
            price = d['close'].iloc[i]
            
            if current_pos == 0 and p != 0:
                current_pos = p
                entry_price = price
            elif current_pos != 0 and (p != current_pos or i == len(d) - 2):
                exit_price = price
                if current_pos == 1:
                    raw_pips = (exit_price - entry_price - spread) / pip_mult
                else:
                    raw_pips = (entry_price - exit_price - spread) / pip_mult

                trades.append(raw_pips > 0)
                equity_curve.append(equity_curve[-1] * (1.0 + (raw_pips * 0.001)))
                current_pos = p
                entry_price = price

        eq_series = pd.Series(equity_curve)
        peak = eq_series.cummax()
        dd = (eq_series - peak) / peak
        max_dd = abs(float(dd.min())) * 100

        total_trades = len(trades)
        win_rate = (sum(trades) / max(1, total_trades)) * 100
        return {
            "return_pct": round(float((eq_series.iloc[-1] - 1000.0) / 10.0), 2),
            "max_dd": round(max_dd, 2),
            "win_rate": round(win_rate, 1),
            "trades": total_trades
        }

    def scan_and_rotate_assets(self) -> dict:
        acc = self.broker.get_account_details()

        # 1. فحص قاطع الهبوط اليومي، التهدئة، ومدة التراجع
        cb_ok, cb_msg = self.risk_mgr.check_circuit_breakers(acc["balance"])
        if not cb_ok:
            TerminalLogger.filter(f"حظر قاطع الهبوط: {cb_msg}")
            return {"action": "CIRCUIT_BREAKER", "reason": cb_msg}

        in_cooldown, rem_minutes = self.risk_mgr.is_in_cooldown()
        if in_cooldown:
            TerminalLogger.filter(f"فترة تهدئة نشطة: باقي {rem_minutes} دقيقة")
            return {"action": "COOLDOWN", "reason": f"فترة تهدئة نشطة بعد خسارتين متتاليتين (باقي {rem_minutes} دقيقة)"}

        # 2. فحص سرعة استجابة خادم الوسيط
        avg_latency = self.broker.get_average_latency()
        if avg_latency > Config.HALT_API_LATENCY_MS:
            TerminalLogger.error("LATENCY_HALT", f"استجابة وسيط التداول بطيئة جداً ({avg_latency:.0f}ms > 1000ms)")
            return {"action": "LATENCY_HALT", "reason": f"تعليق التداول: خوادم الوسيط بطيئة جداً ({avg_latency:.0f}ms > 1000ms)"}

        # 3. فحص صدمات الارتباط الجماعي المفاجئ (Macro Shock)
        shock_detected, shock_corr = CrossAssetMacroShockFilter.detect_macro_shock()
        if shock_detected:
            TerminalLogger.filter(f"حظر صدمة الارتباط الكلي: متوسط ارتباط السلة ({shock_corr:.2f} > {Config.MACRO_SHOCK_CORRELATION_MAX})")
            return {"action": "MACRO_SHOCK_HALT", "reason": f"حظر صدمة الارتباط الكلي للسلة (متوسط الارتباط {shock_corr:.2f} > {Config.MACRO_SHOCK_CORRELATION_MAX})"}

        # 4. فحص مواعيد السوق، الأخبار، تثبيت لندن 4PM Fix، وحماية رسوم التبييت
        market_ok, market_msg = MarketShield.check_market_hours()
        if not market_ok:
            TerminalLogger.filter(f"السوق مغلق: {market_msg}")
            return {"action": "HALT", "reason": market_msg}

        fix_ok, fix_msg = MarketShield.check_london_fix_window()
        if not fix_ok:
            TerminalLogger.filter(f"نافذة تثبيت لندن: {fix_msg}")
            return {"action": "LONDON_FIX_BLOCK", "reason": fix_msg}

        swap_ok, swap_msg = MarketShield.check_overnight_swap_window()
        if not swap_ok:
            TerminalLogger.filter(f"نافذة رسوم التبييت: {swap_msg}")
            return {"action": "SWAP_BLOCK", "reason": swap_msg}

        news_block, news_msg = MarketShield.check_live_news()
        if news_block:
            TerminalLogger.filter(f"حظر إخباري لحظي: {news_msg}")
            return {"action": "NEWS_BLOCK", "reason": news_msg}

        # 5. فحص سقف أزواج العملات المنفردة المفتوحة (Fix 4)
        open_pos = self.broker.get_open_positions()
        distinct_open_epics = set(
            ep for ep in (p.get("market", {}).get("epic") or p.get("epic", "") for p in open_pos) if ep
        )
        if len(distinct_open_epics) >= Config.MAX_OPEN_POSITIONS:
            TerminalLogger.filter(f"بلوغ سقف أزواج العملات المفتوحة: {len(distinct_open_epics)} / {Config.MAX_OPEN_POSITIONS}")
            return {"action": "IN_POSITION", "reason": f"تم بلوغ الحد الأقصى لأزواج العملات المتزامنة ({len(distinct_open_epics)})"}

        # 6. فحص صحة نموذج الظل التجريبي (Canary Shadow Test) وإعادة التدريب الذاتي عند التراجع
        if not self.canary_tester.is_canary_healthy():
            self.train_and_evolve()
            return {"action": "CANARY_RECALIBRATED", "reason": "تمت إعادة تدريب النماذج بعد تراجع أداء نموذج الظل الاستباقي"}

        session_info = self.risk_mgr.get_dynamic_session_weights()
        _, min_prob_required, _ = self.risk_mgr.get_seasonality_filter()
        curr_strengths = CurrencyStrengthMatrix.evaluate_currency_strength()
        dxy_trend, dxy_desc, dxy_series = SyntheticDXYEngine.get_synthetic_dxy_trend()
        
        basket_atrs = {}
        for ep in Config.ACTIVE_EPICS:
            c_df = DuckDBWarehouse.get_cached_candles(ep, limit=20)
            if len(c_df) >= 15:
                p_mult = self.broker.get_pip_multiplier(ep)
                tr_val = np.maximum(c_df['high'] - c_df['low'], 
                                    np.maximum(abs(c_df['high'] - c_df['close'].shift(1)), 
                                               abs(c_df['low'] - c_df['close'].shift(1))))
                basket_atrs[ep] = float((tr_val.rolling(14).mean().iloc[-1]) / p_mult)

        spillover_detected, spill_msg = MarketShield.check_volatility_spillover(basket_atrs)
        if spillover_detected:
            TerminalLogger.filter(f"حظر انتقال تقلب عابر: {spill_msg}")
            return {"action": "VOLATILITY_SPILLOVER_HALT", "reason": spill_msg}

        qualified_opportunities = []

        # 7. مسح سلة العملات بالكامل
        for epic in Config.ACTIVE_EPICS:
            if time.time() < self.suspended_epics.get(epic, 0):
                continue

            pip_mult = self.broker.get_pip_multiplier(epic)
            df_m1 = self.broker.fetch_live_candles(epic, resolution="MINUTE", max_bars=300)
            if len(df_m1) < 50:
                continue

            void_ok, void_msg = MarketMicrostructureEngine.calculate_net_liquidity_void_ratio(df_m1)
            if not void_ok:
                TerminalLogger.filter(f"تخطي {epic}: {void_msg}")
                continue

            gh_ok, gh_ratio = MarketMicrostructureEngine.check_glosten_harris_adverse_selection(df_m1, pip_mult)
            if not gh_ok:
                TerminalLogger.filter(f"تخطي {epic}: تضخم السبريد بسبب ضغط الانتقاء العكسي ({gh_ratio:.2f})")
                continue

            last = df_m1.iloc[-1]
            live_spread_pips = (last['ask_close'] - last['close']) / pip_mult

            rolling_spread = ((df_m1['ask_close'] - df_m1['close']).rolling(10).mean().iloc[-1]) / pip_mult
            if live_spread_pips > (rolling_spread * Config.SPREAD_EXPANSION_VELOCITY_MAX):
                TerminalLogger.filter(f"تخطي {epic}: قفزة في سرعة السبريد ({live_spread_pips:.1f} Pips)")
                continue

            tr_base = np.maximum(df_m1['high'] - df_m1['low'], 
                                np.maximum(abs(df_m1['high'] - df_m1['close'].shift(1)), 
                                           abs(df_m1['low'] - df_m1['close'].shift(1))))
            base_atr_pips = (tr_base.rolling(14).mean().iloc[-1]) / pip_mult
            if np.isnan(base_atr_pips) or base_atr_pips <= 0:
                base_atr_pips = 4.0

            clean_returns = df_m1['close'].pct_change().replace([np.inf, -np.inf], np.nan).dropna().values
            garch_sigma = AdvancedQuantMath.predict_garch_volatility(clean_returns)
            std_sigma = (float(np.std(clean_returns)) + 1e-8) if len(clean_returns) > 0 else 1.0
            garch_factor = np.clip(garch_sigma / std_sigma, 0.8, 1.4)
            atr_pips = max(Config.MIN_STOP_PIPS, base_atr_pips * garch_factor)

            if ((live_spread_pips / atr_pips) * 100.0) > Config.MAX_FRICTION_RATIO_PCT:
                continue

            hurst = AdvancedQuantMath.calculate_rolling_hurst(df_m1['close'].values)
            if np.isnan(hurst) or (0.47 <= hurst <= 0.53):
                continue

            df_m1 = self.order_flow.calculate_volume_delta(df_m1)
            regime = self.hmm.fit_predict_regime(df_m1)
            if regime == 2:
                continue

            mtf_bias, _ = MultiTimeframeAnalyzer.get_m15_bias_from_duckdb(epic)

            current_price = last['close']
            tp = (df_m1['high'] + df_m1['low'] + df_m1['close']) / 3
            vwap = (tp * df_m1['volume']).cumsum() / (df_m1['volume'].cumsum() + 1e-8)
            std = (tp - vwap).rolling(30).std()
            vwap_val = vwap.iloc[-1]
            vwap_lower = vwap_val - (2.0 * std.iloc[-1])
            vwap_upper = vwap_val + (2.0 * std.iloc[-1])

            smc_sig = 1 if last['bullish_absorption'] else (-1 if last['bearish_absorption'] else 0)
            vwap_sig = 1 if current_price < vwap_lower else (-1 if current_price > vwap_upper else 0)
            mom_sig = np.sign(df_m1['close'].pct_change(10).iloc[-1])

            asian_h, asian_l = DuckDBWarehouse.get_asian_range(epic)
            sweep_dir, sweep_detail = OrderFlowEngine.detect_asian_liquidity_sweep(df_m1, asian_h, asian_l, pip_mult)
            if sweep_dir != 0:
                smc_sig += sweep_dir * 1.5

            eq_dir, eq_detail = OrderFlowEngine.detect_eqh_eql_sweep(df_m1, pip_mult)
            if eq_dir != 0:
                smc_sig += eq_dir * 1.5

            cvd_dir, cvd_desc = OrderFlowEngine.detect_cvd_divergence(df_m1)
            if cvd_dir != 0:
                mom_sig += cvd_dir * 1.5

            burst_hit, burst_mag = MarketMicrostructureEngine.detect_micro_volume_burst(df_m1)
            if burst_hit:
                mom_sig *= 1.3

            score = (smc_sig * session_info["SMC"]) + (vwap_sig * session_info["VWAP"]) + (mom_sig * session_info["MOM"])

            action = None
            if score > 0.40 and mtf_bias > 0:
                if current_price <= (vwap_val + 1.8 * atr_pips * pip_mult):
                    action = "BUY"
            elif score < -0.40 and mtf_bias < 0:
                if current_price >= (vwap_val - 1.8 * atr_pips * pip_mult):
                    action = "SELL"

            if action:
                can_open, corr_msg = CurrencyCorrelationManager.can_open_position(open_pos, epic, action)
                if not can_open:
                    TerminalLogger.filter(f"حظر ارتباط: {corr_msg}")
                    continue

                if not dxy_series.empty:
                    usd_beta = CurrencyCorrelationManager.calculate_usd_beta(df_m1['close'].pct_change().dropna().tail(15), dxy_series)
                    if abs(usd_beta) > 2.5:
                        continue

                if not SyntheticDXYEngine.is_dxy_confluent(epic, action, dxy_trend):
                    TerminalLogger.filter(f"تعارض مع مؤشر الدولار التجميعي على {epic}")
                    continue

                strength_ok, strength_detail = CurrencyStrengthMatrix.is_strength_aligned(epic, action, curr_strengths)
                if not strength_ok:
                    TerminalLogger.filter(f"تعارض مصفوفة القوة على {epic}: {strength_detail}")
                    continue

                toxic, tox_score = OrderFlowEngine.check_order_flow_toxicity(df_m1, action)
                if toxic:
                    TerminalLogger.filter(f"ارتفاع سمية تدفق الأوامر المعاكسة على {epic} ({tox_score:.2f})")
                    continue

                fvg_detected, fvg_detail = OrderFlowEngine.detect_fvg_confluence(df_m1, action, current_price, pip_mult)
                if fvg_detected:
                    score += (0.15 if action == "BUY" else -0.15)

                frac_d_val = float(AdvancedQuantMath.fractional_differentiation(df_m1['close'], d=0.4).iloc[-1])
                _, kalman_vel = AdvancedQuantMath.kalman_filter_price_velocity(df_m1['close'].tail(15).values)

                meta_dict = {
                    "close": current_price,
                    "vol": last['volume'],
                    "mom": df_m1['close'].pct_change(10).iloc[-1],
                    "cvd": last['cvd_zscore'],
                    "frac_diff": frac_d_val,
                    "kalman_v": kalman_vel
                }

                prob_success, is_conformal = self.meta_labelers[epic].predict_conformal_probability(meta_dict)
                if not is_conformal:
                    TerminalLogger.filter(f"رفض عدم التيقن الإحصائي (Conformal Rejection) لزوج {epic}")
                    continue

                if prob_success >= min_prob_required:
                    vov, vov_penalty, vov_sl_buf = AdvancedRiskAndSessionManager.calculate_vol_of_vol(df_m1, pip_mult)
                    risk_parity_mult = AdvancedRiskAndSessionManager.calculate_risk_parity_weight(atr_pips, basket_atrs)
                    dyn_sl = max(Config.MIN_STOP_PIPS, round(atr_pips * session_info["sl_mult"] * vov_sl_buf, 1))
                    dyn_tp = max(Config.MIN_PROFIT_PIPS, round(atr_pips * session_info["tp_mult"], 1))

                    qualified_opportunities.append({
                        "epic": epic,
                        "action": action,
                        "score": abs(score),
                        "prob": prob_success,
                        "price": current_price,
                        "ask_price": last['ask_close'],
                        "sl_pips": dyn_sl,
                        "tp_pips": dyn_tp,
                        "hurst": round(hurst, 2),
                        "atr_pips": atr_pips,
                        "fvg_note": fvg_detail if fvg_detected else "لا توجد فجوة",
                        "sweep_note": sweep_detail if sweep_dir != 0 else (eq_detail if eq_dir != 0 else "طبيعي"),
                        "cvd_note": cvd_desc if cvd_dir != 0 else "متسق",
                        "strength_note": strength_detail,
                        "dxy_note": dxy_desc,
                        "vov_penalty": vov_penalty,
                        "risk_parity_mult": risk_parity_mult,
                        "pip_mult": pip_mult,
                        "df": df_m1,
                        "vwap": vwap
                    })

        if qualified_opportunities:
            best_opp = max(qualified_opportunities, key=lambda x: (x["prob"], x["score"]))
            
            vision_ok, vision_reason = GeminiChartVisionValidator.validate_setup_with_vision(
                best_opp["epic"], best_opp["df"], best_opp["vwap"], best_opp["action"], best_opp["price"]
            )
            
            if not vision_ok:
                TerminalLogger.filter(f"رفض بصري لزوج {best_opp['epic']}: {vision_reason}")
                return {"action": "VISION_REJECTED", "reason": f"رفض بصري لزوج {best_opp['epic']}: {vision_reason}"}

            if self.autotrade_active:
                slip_cap = MarketMicrostructureEngine.get_asymmetric_slippage_cap(best_opp["epic"], best_opp["atr_pips"])
                live_bid, live_offer = self.broker.get_latest_quote(best_opp["epic"])
                
                # عزل السبريد ومطابقة Ask مع Ask و Bid مع Bid لمنع الرفض الكاذب لصفقات الشراء (Fix 1)
                if best_opp["action"] == "BUY":
                    target_exec_price = live_offer if live_offer > 0 else best_opp["ask_price"]
                    drift_pips = abs(target_exec_price - best_opp["ask_price"]) / best_opp["pip_mult"]
                else:
                    target_exec_price = live_bid if live_bid > 0 else best_opp["price"]
                    drift_pips = abs(target_exec_price - best_opp["price"]) / best_opp["pip_mult"]

                if drift_pips > slip_cap:
                    TerminalLogger.filter(f"انزلاق ما قبل التنفيذ لـ {best_opp['epic']} ({drift_pips:.2f} > {slip_cap:.2f} Pips)")
                    return {"action": "PRE_TRADE_SLIPPAGE_REJECTED", "reason": f"انزلاق ما قبل التنفيذ {drift_pips:.2f} > السقف غير المتماثل {slip_cap:.2f} Pips"}

                total_lots = self.risk_mgr.calculate_lot_size(
                    best_opp["epic"], 
                    acc["available"], 
                    target_exec_price,
                    best_opp["sl_pips"],
                    avg_latency,
                    best_opp["vov_penalty"],
                    best_opp["risk_parity_mult"]
                )

                deal_refs = []
                actual_prices = []
                
                if total_lots >= 0.02:
                    child_lots_1 = round(total_lots / 2.0, 2)
                    child_lots_2 = round(total_lots - child_lots_1, 2)

                    res_1 = self.broker.execute_order_server_trailing(
                        epic=best_opp["epic"],
                        direction=best_opp["action"],
                        size=child_lots_1,
                        current_price=target_exec_price,
                        stop_pips=best_opp["sl_pips"],
                        profit_pips=max(Config.MIN_PROFIT_PIPS, best_opp["atr_pips"] * 1.5)
                    )
                    res_2 = self.broker.execute_order_server_trailing(
                        epic=best_opp["epic"],
                        direction=best_opp["action"],
                        size=child_lots_2,
                        current_price=target_exec_price,
                        stop_pips=best_opp["sl_pips"],
                        profit_pips=best_opp["tp_pips"]
                    )
                    res = res_1 if "dealReference" in res_1 else res_2
                    for r_c in [res_1, res_2]:
                        if "dealReference" in r_c:
                            deal_refs.append(str(r_c["dealReference"]))
                        if "level" in r_c:
                            actual_prices.append(float(r_c["level"]))
                else:
                    res = self.broker.execute_order_server_trailing(
                        epic=best_opp["epic"],
                        direction=best_opp["action"],
                        size=total_lots,
                        current_price=target_exec_price,
                        stop_pips=best_opp["sl_pips"],
                        profit_pips=best_opp["tp_pips"]
                    )
                    if "dealReference" in res:
                        deal_refs.append(str(res["dealReference"]))
                    if "level" in res:
                        actual_prices.append(float(res["level"]))

                if not deal_refs:
                    TerminalLogger.error("ORDER_REJECTED_BY_BROKER", str(res.get("errorCode", res)))
                    return {"action": "ORDER_FAILED", "reason": str(res.get("errorCode", "فشل استلام مرجع التنفيذ من الوسيط"))}

                actual_price = float(np.mean(actual_prices)) if actual_prices else target_exec_price
                base_ref = best_opp["ask_price"] if best_opp["action"] == "BUY" else best_opp["price"]
                slippage_pips = round(abs(actual_price - base_ref) / best_opp["pip_mult"], 2)
                self.slippage_history[best_opp["epic"]].append(slippage_pips)
                
                slip_alert = ""
                recent_slips = self.slippage_history[best_opp["epic"]][-2:]
                if len(recent_slips) >= 2 and np.mean(recent_slips) > Config.MAX_ALLOWED_SLIPPAGE_PIPS:
                    self.suspended_epics[best_opp["epic"]] = time.time() + 3600
                    slip_alert = f"\n⚠️ **تحذير:** تكرر انزلاق التنفيذ ({np.mean(recent_slips):.2f} Pips). تم تعليق الزوج لمدة ساعة."
                    TerminalLogger.filter(f"تعليق التداول على {best_opp['epic']} لمدة ساعة لتكرار الانزلاق.")

                clean_ref = "-".join([re.sub(r'[^a-zA-Z0-9-]', '-', d_r) for d_r in deal_refs])

                TerminalLogger.success(
                    f"تنفيذ صفقة: {best_opp['action']} {total_lots} Lot على {best_opp['epic']} بسعر {actual_price} "
                    f"(SL: {best_opp['sl_pips']} Pips, TP: {best_opp['tp_pips']} Pips, Deals: {clean_ref})"
                )

                return {
                    "action": best_opp["action"],
                    "epic": best_opp["epic"],
                    "price": actual_price,
                    "lots": total_lots,
                    "sl_pips": best_opp["sl_pips"],
                    "tp_pips": best_opp["tp_pips"],
                    "prob": round(best_opp["prob"], 2),
                    "hurst": best_opp["hurst"],
                    "session": session_info["session"],
                    "deal_ref": clean_ref,
                    "fvg": best_opp["fvg_note"],
                    "sweep": best_opp["sweep_note"],
                    "cvd": best_opp["cvd_note"],
                    "strength": best_opp["strength_note"],
                    "dxy": best_opp["dxy_note"],
                    "latency": round(avg_latency, 0),
                    "slippage": slippage_pips,
                    "slip_alert": slip_alert,
                    "reason": f"اقتناص فرصة رابحة ({best_opp['epic']}) بوقف ديناميكي {best_opp['sl_pips']} نقطة"
                }
            else:
                TerminalLogger.info(f"رصد إشارة دخول في وضع المراقبة: {best_opp['action']} على {best_opp['epic']} (الاحتمالية {best_opp['prob']*100:.1f}%)")
                return {
                    "action": "SIGNAL_DETECTED",
                    "epic": best_opp["epic"],
                    "price": best_opp["price"],
                    "sl_pips": best_opp["sl_pips"],
                    "tp_pips": best_opp["tp_pips"],
                    "prob": round(best_opp["prob"], 2),
                    "reason": f"فرصة ممتازة على {best_opp['epic']} ولكن التداول الآلي معطل (المراقبة فقط)"
                }

        TerminalLogger.scan("لا توجد فرص متوافقة ومستوفية للشروط اللحظية.")
        return {"action": "HOLD", "reason": "استقرار محايد عبر كافة العملات النشطة"}

    def run_full_backtest_duckdb(self) -> dict:
        results = {}
        for epic in Config.ACTIVE_EPICS:
            df = DuckDBWarehouse.get_cached_candles(epic, limit=1000)
            if len(df) >= 60:
                results[epic] = self.run_single_asset_backtest(epic, df)
        return results

    def train_and_evolve(self) -> dict:
        trained_epics = []
        backtest_results = {}
        
        for epic in Config.ACTIVE_EPICS:
            df = DuckDBWarehouse.get_cached_candles(epic, limit=1000)
            if len(df) >= 60:
                df_features = self.order_flow.calculate_volume_delta(df)
                success, msg = self.meta_labelers[epic].train_ensemble_with_purged_cv(df_features, self.broker.get_pip_multiplier(epic))
                if success:
                    trained_epics.append(epic)
                bt_metrics = self.run_single_asset_backtest(epic, df)
                backtest_results[epic] = bt_metrics
        
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "status": f"تم التدريب النظيف (Purged CV + Triple Barrier) لـ: {', '.join(trained_epics) if trained_epics else 'تجميد الأوزان'}",
            "results": backtest_results
        }
        self.evolution_log.append(entry)
        TerminalLogger.success(f"اكتملت دورة التطور الساعية والباكتيست الذاتي لـ {len(trained_epics)} زوج.")
        return entry

# ==============================================================================
# 16. أوامر التيليجرام التفاعلية المباشرة وتصنيف الأولويات
# ==============================================================================
system = MasterQuantSystem()

async def send_priority_message(context: ContextTypes.DEFAULT_TYPE, text: str, urgent: bool = False):
    try:
        safe_text = text.replace("_", "-")
        await context.bot.send_message(
            chat_id=Config.TELEGRAM_CHAT_ID,
            text=safe_text,
            parse_mode="Markdown",
            disable_notification=not urgent
        )
    except Exception:
        try:
            clean_plain = re.sub(r'[*_`\[\]]', '', text)
            await context.bot.send_message(
                chat_id=Config.TELEGRAM_CHAT_ID,
                text=clean_plain,
                parse_mode=None,
                disable_notification=not urgent
            )
        except Exception as e:
            TerminalLogger.error("TELEGRAM_SEND_MESSAGE", str(e))

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    in_cd, rem_cd = system.risk_mgr.is_in_cooldown()
    cd_status = f"نشطة ({rem_cd} دقيقة متبقية) ⏳" if in_cd else "مستقرة وجاهزة ✅"
    _, _, season_txt = system.risk_mgr.get_seasonality_filter()
    cvar_val = system.risk_mgr.calculate_cvar_99()

    msg = (
        "👑 **نظام التداول الكمي المؤسسي الشامل لـ CFDs (M1 Institutional Quant)**\n\n"
        f"• محرك الذكاء الاصطناعي: **Google Gemini Flash (Vision + Tools)**\n"
        f"• أزواج العملات النشطة: **{', '.join(Config.ACTIVE_EPICS)}**\n"
        f"• إدارة المخاطر: **Risk Parity + CVaR 99% (${cvar_val:.1f}) + GARCH(1,1)**\n"
        f"• النماذج الرياضية: **Kalman Filter + Hurst Exp + Fractional Diff (d=0.4)**\n"
        f"• التحقق الإحصائي: **Triple Barrier + Conformal Prediction + Wavelets**\n"
        f"• صائد السيولة: **Judas Swing + CVD Divergence + EQH/EQL + NLVR**\n"
        f"• حماية الأسواق: **Macro Shock + London 4PM Fix + Glosten-Harris**\n"
        f"• انزلاق متكيف: **Asymmetric Slippage Modeling + Pre-Trade Cap**\n"
        f"• بيئة التشغيل: **Windows High Priority + CPU Core Affinity + NSSM OK**\n"
        f"• مراقبة حية: **Live Terminal Diagnostics Engine مفعّل 🖥️**\n"
        f"• خيط اليقظة الداخلي: **Internal Watchdog Active (180s) 🛡️**\n"
        f"• فترة التهدئة: **{cd_status}**\n"
        f"• حساب التداول: **{'تجريبي (DEMO)' if system.broker.demo else 'حقيقي (LIVE)'}**\n"
        f"• حالة التداول الآلي: **{'نشط ✅' if system.autotrade_active else 'معطل ⏸'}**\n\n"
        "**الأوامر السريعة المتاحة:**\n"
        "• `/autotrade_on` - تشغيل التداول الآلي اللحظي\n"
        "• `/autotrade_off` - إيقاف التداول الآلي مؤقتاً\n"
        "• `/mode_demo` - التبديل للحساب التجريبي\n"
        "• `/mode_live` - التبديل للحساب الحقيقي\n"
        "• `/backtest` - باكتيست فوري من مستودع DuckDB\n"
        "• `/status` - فحص المحفظة ومراكز التداول الحالية\n"
        "• `/health` - مراقبة صحة الخادم، الرام ومقاييس TCA\n"
        "• `/evolution` - تقرير تدريب وباكتيست نماذج التعلم الآلي\n"
        "• `/news` - فحص مفكرة الأخبار وتثبيت لندن\n\n"
        "💬 *يمكنك محادثة Gemini بالعربية أو توجيه أوامر مثل: 'عدل مضاعف وقف ATR إلى 2' أو 'أعد تدريب النماذج'!*"
    )
    try:
        await update.message.reply_text(msg.replace("_", "-"), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(re.sub(r'[*_`\[\]]', '', msg), parse_mode=None)

async def autotrade_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system.autotrade_active = True
    TerminalLogger.success("تم تفعيل التداول الآلي عبر تيليجرام.")
    await update.message.reply_text("🚀 **تم تفعيل التداول الآلي المؤسسي متعدد العملات.**", parse_mode="Markdown")

async def autotrade_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system.autotrade_active = False
    TerminalLogger.info("تم إيقاف التداول الآلي عبر تيليجرام.")
    await update.message.reply_text("🛑 **تم إيقاف التداول الآلي مؤقتاً.**", parse_mode="Markdown")

async def mode_demo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system.broker.demo = True
    Config.DEMO_MODE = True
    system.broker.login()
    await update.message.reply_text("✅ **تم التبديل للحساب التجريبي (DEMO).**", parse_mode="Markdown")

async def mode_live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system.broker.demo = False
    Config.DEMO_MODE = False
    system.broker.login()
    await update.message.reply_text("⚠️ **تحذير: تم التبديل للحساب الحقيقي (LIVE)! سيتم استخدام أموال حقيقية.**", parse_mode="Markdown")

async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ جاري إجراء الباكتيست الفوري من مستودع DuckDB لكافة الأزواج...")
    res = await asyncio.to_thread(system.run_full_backtest_duckdb)
    if not res:
        await update.message.reply_text("لا توجد شموع كافية في مستودع DuckDB بعد. اترك البوت يعمل لدقائق لتجميع البيانات.")
        return
    msg = "📊 **[نتائج الباكتيست من مستودع DuckDB المحلي]**\n\n"
    for epic, r in res.items():
        msg += (
            f"**• {epic}:**\n"
            f"  ↳ العائد: `{r.get('return_pct')}%` | أقصى هبوط: `{r.get('max_dd')}%`\n"
            f"  ↳ نسبة الفوز: `{r.get('win_rate')}%` | الصفقات: `{r.get('trades')}`\n\n"
        )
    try:
        await update.message.reply_text(msg.replace("_", "-"), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(re.sub(r'[*_`\[\]]', '', msg), parse_mode=None)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acc = system.broker.get_account_details()
    open_p = system.broker.get_open_positions()
    distinct_open_epics = set(
        ep for ep in (p.get("market", {}).get("epic") or p.get("epic", "") for p in open_p) if ep
    )
    sess = system.risk_mgr.get_dynamic_session_weights()
    current_kelly = system.risk_mgr.get_bayesian_rolling_kelly()
    latency = system.broker.get_average_latency()
    throttle = system.risk_mgr.get_drawdown_throttle(acc["balance"])
    in_cd, rem_cd = system.risk_mgr.is_in_cooldown()
    _, _, season_txt = system.risk_mgr.get_seasonality_filter()
    strengths = CurrencyStrengthMatrix.evaluate_currency_strength()
    _, dxy_desc, _ = SyntheticDXYEngine.get_synthetic_dxy_trend()
    cvar_val = system.risk_mgr.calculate_cvar_99()
    
    msg = (
        "💼 **[الحالة اللحظية والمحفظة]**\n\n"
        f"• الرصيد الإجمالي: `${acc['balance']:,.2f}`\n"
        f"• الرصيد المتاح: `${acc['available']:,.2f}`\n"
        f"• جلسة التداول: `{sess['session']}` (نمط `{sess['mode']}`)\n"
        f"• فلتر الموسمية: `{season_txt}`\n"
        f"• مؤشر CVaR 99%: `${cvar_val:.1f}`\n"
        f"• استجابة الوسيط (API Latency): `{latency:.0f} ms`\n"
        f"• مؤشر الدولار التجميعي: `{dxy_desc}`\n"
        f"• مصفوفة القوة: `EUR:{strengths['EUR']:+.3f} | USD:{strengths['USD']:+.3f} | GBP:{strengths['GBP']:+.3f}`\n"
        f"• معامل تدرج التراجع (Throttle): `{throttle*100:.0f}%`\n"
        f"• فترة التهدئة: **{'نشطة (' + str(rem_cd) + ' دقيقة) ⏳' if in_cd else 'غير مفعلة ✅'}**\n"
        f"• معامل كيلي البايزي الفعلي: `{current_kelly:.3f}`\n"
        f"• قاطع الهبوط اليومي (3%): **{'حظر تداول 🚫' if system.risk_mgr.daily_circuit_breaker_active else 'طبيعي ومستقر ✅'}**\n"
        f"• المراكز المفتوحة: `{len(open_p)} عقد ({len(distinct_open_epics)}/{Config.MAX_OPEN_POSITIONS} زوج)`\n"
        f"• التداول الآلي: **{'نشط ✅' if system.autotrade_active else 'معطل ⏸'}**"
    )
    try:
        await update.message.reply_text(msg.replace("_", "-"), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(re.sub(r'[*_`\[\]]', '', msg), parse_mode=None)

async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    h = InternalSystemWatchdog.get_metrics()
    latency = system.broker.get_average_latency()
    tca = DuckDBWarehouse.get_tca_summary(limit=30)
    msg = (
        "🖥 **[تقرير صحة النظام ومقاييس TCA - Windows]**\n\n"
        f"• استهلاك الذاكرة (RAM): `{h['ram_mb']} MB`\n"
        f"• استهلاك المعالج (CPU): `{h['cpu_pct']}%`\n"
        f"• استجابة وسيط التداول: `{latency:.0f} ms`\n"
        f"• متوسط الانزلاق السعري (TCA): `{tca['avg_slippage']} Pips`\n"
        f"• إجمالي تكلفة الاحتكاك: `${tca['total_friction']}`\n"
        f"• وقت التشغيل المتواصل: `{h['uptime']}`\n"
        f"• مؤقت اليقظة الذاتي: **نشط ويراقب الخدمة (Watchdog OK) 🛡️**\n"
        f"• مستودع DuckDB: **قفل المعالجة المتبادل نشط (Thread-Safe Lock) ✅**"
    )
    try:
        await update.message.reply_text(msg.replace("_", "-"), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(re.sub(r'[*_`\[\]]', '', msg), parse_mode=None)

async def evolution_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not system.evolution_log:
        await update.message.reply_text("جاري تدريب النماذج والباكتيست الذاتي لأول مرة من مستودع DuckDB...")
        await asyncio.to_thread(system.train_and_evolve)
    last = system.evolution_log[-1]
    
    msg = (
        "🧬 **[تقرير التدريب النظيف والباكتيست الذاتي الساعي]**\n\n"
        f"• التوقيت: `{last['timestamp']}`\n"
        f"• الحالة: {last['status']}\n\n"
        "**نتائج الباكتيست اللحظي للعملات:**\n"
    )
    results = last.get("results", {})
    for epic, r in results.items():
        msg += (
            f"• **{epic}:** ربح `{r.get('return_pct')}%` | فوز `{r.get('win_rate')}%` | هبوط `{r.get('max_dd')}%`\n"
        )
    try:
        await update.message.reply_text(msg.replace("_", "-"), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(re.sub(r'[*_`\[\]]', '', msg), parse_mode=None)

async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocked, news_msg = MarketShield.check_live_news()
    market_ok, market_msg = MarketShield.check_market_hours()
    fix_ok, fix_msg = MarketShield.check_london_fix_window()
    swap_ok, swap_msg = MarketShield.check_overnight_swap_window()
    msg = (
        "🌐 **[فحص السيولة، تثبيت لندن، والأخبار الحية]**\n\n"
        f"• حالة الأخبار: **{'حظر تداول 🚫' if blocked else 'آمن للتداول ✅'}**\n"
        f"  ↳ {news_msg}\n\n"
        f"• نافذة تثبيت لندن (4 PM Fix): **{'حظر تداول 🚫' if not fix_ok else 'آمن للتداول ✅'}**\n"
        f"  ↳ {fix_msg}\n\n"
        f"• حماية رسوم التبييت: **{'حظر تبييت 🚫' if not swap_ok else 'آمن للتداول ✅'}**\n"
        f"  ↳ {swap_msg}\n\n"
        f"• حالة جلسة السوق: **{'مفتوح ✅' if market_ok else 'مغلق ⏸'}**\n"
        f"  ↳ {market_msg}"
    )
    try:
        await update.message.reply_text(msg.replace("_", "-"), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(re.sub(r'[*_`\[\]]', '', msg), parse_mode=None)

async def gemini_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_chat_action("typing")

    acc = system.broker.get_account_details()
    open_pos = system.broker.get_open_positions()
    sess = system.risk_mgr.get_dynamic_session_weights()
    h = InternalSystemWatchdog.get_metrics()
    in_cd, rem_cd = system.risk_mgr.is_in_cooldown()
    _, _, season_txt = system.risk_mgr.get_seasonality_filter()

    system_ctx = {
        "balance": acc["balance"],
        "available": acc["available"],
        "session": sess["session"],
        "season_desc": season_txt,
        "health": h,
        "circuit_breaker": system.risk_mgr.daily_circuit_breaker_active,
        "cooldown_msg": f"نشطة ({rem_cd} دقيقة متبقية)" if in_cd else "مستقرة",
        "open_trades": len(open_pos),
        "autotrade": system.autotrade_active,
        "latency": system.broker.get_average_latency(),
        "is_demo": system.broker.demo
    }

    reply = await asyncio.to_thread(GeminiConversationalAgent.ask_and_execute, user_text, system, system_ctx)
    try:
        await update.message.reply_text(reply.replace("_", "-"), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(re.sub(r'[*_`\[\]]', '', reply), parse_mode=None)

# ==============================================================================
# 17. مهام الجدولة اللحظية والساعية وتصنيف الإشعارات
# ==============================================================================
async def job_scanner_minute(context: ContextTypes.DEFAULT_TYPE):
    try:
        # تسجيل نبضة اليقظة فورياً في أول سطر
        InternalSystemWatchdog.beat()

        # الاصطفاف الزمني المحكم دون أي نوم مفرط يضيع الدورات
        now_sec = datetime.now(timezone.utc).second
        if now_sec < 42:
            await asyncio.sleep(42 - now_sec)
        elif now_sec > 55:
            await asyncio.sleep((60 - now_sec) + 42)

        TerminalLogger.scan(f"بدء دورة مسح سلة العملات {Config.ACTIVE_EPICS} (الثانية: {datetime.now(timezone.utc).second})...")

        await asyncio.to_thread(system.reconcile_closed_trades)
        await asyncio.to_thread(system.enforce_swap_closure_if_needed)

        # إدارة الصفقات المفتوحة
        mgmt_events = await asyncio.to_thread(system.enforce_dynamic_trade_management)
        for event_type, epic, direction, val, pips in mgmt_events:
            if event_type == "STRUCTURAL_TRAIL":
                msg = (
                    f"📈 **[وقف هيكلي متحرك - Structural Swing-Trail]**\n\n"
                    f"• الزوج: `{epic}` ({direction})\n"
                    f"• الأرباح المحققة: `+{pips} Pips`\n"
                    f"• الوقف الجديد: تم رفعه خلف الشمعة السابقة `{val}` لحماية الأرباح المتصاعدة."
                )
                await send_priority_message(context, msg, urgent=False)
            elif event_type == "OU_TIMEOUT_EXIT":
                msg = (
                    f"⏱️ **[خروج زمني - Ornstein-Uhlenbeck Timeout]**\n\n"
                    f"• الزوج: `{epic}` ({direction})\n"
                    f"• النتيجة: `+{pips} Pips`\n"
                    f"تم إغلاق الصفقة لتجاوز عمر النصف المقدر للارتداد نحو VWAP."
                )
                await send_priority_message(context, msg, urgent=False)
            else:
                msg = (
                    f"🛡️ **[تأمين الأرباح - Risk-Free Breakeven]**\n\n"
                    f"• الزوج: `{epic}` ({direction})\n"
                    f"• الأرباح الحالية: `+{pips} Pips` (تجاوزت 1.0 ATR)\n"
                    f"• الوقف الجديد: تم نقله لنقطة الدخول `{val}` لحماية رأس المال."
                )
                await send_priority_message(context, msg, urgent=False)

        # فحص منتصف الليل والصيانة الذاتية
        acc = system.broker.get_account_details()
        is_new_day = system.risk_mgr.update_daily_equity_baseline(acc["balance"])
        if is_new_day:
            await asyncio.to_thread(DuckDBWarehouse.perform_maintenance)
            perf = AdvancedRiskAndSessionManager.calculate_midnight_performance(
                acc["balance"], system.risk_mgr.day_start_equity
            )
            tca = perf.get("tca", {})
            digest_msg = (
                "🌙 **[تقرير الأداء وإدارة المخاطر اليومي - Midnight Digest]**\n\n"
                f"• صافي الربح/الخسارة اليومية: `${perf['pnl_usd']:,.2f}` (`{perf['return_pct']:+.2f}%`)\n"
                f"• إجمالي صفقات اليوم: `{perf['trades']}` | نسبة الفوز: `{perf['win_rate']}%`\n"
                f"• معامل الربحية (Profit Factor): `{perf['profit_factor']}`\n"
                f"• معامل سورينتو (Sortino Ratio): `{perf['sortino']}`\n"
                f"• أفضل أصل مساهمة: `{perf['best_asset']}`\n\n"
                "**ملخص تكلفة المعاملات (TCA):**\n"
                f"• متوسط الانزلاق السعري: `{tca.get('avg_slippage')} Pips`\n"
                f"• إجمالي الاحتكاك المدفوع للوسيط: `${tca.get('total_friction')}`\n"
                f"• نسبة تكلفة التنفيذ من الأرباح: `{tca.get('avg_friction_pct')}%`\n\n"
                "تمت صيانة وضغط مستودع DuckDB بنجاح وإعادة ضبط خطوط الأساس لليوم الجديد."
            )
            await send_priority_message(context, digest_msg, urgent=False)

        # مسح السوق وتدوير العملات اللحظي
        res = await asyncio.to_thread(system.scan_and_rotate_assets)

        if res.get("action") in ["BUY", "SELL"]:
            msg = (
                f"⚡ **[تنفيذ صفقة مؤسسية - تدوير العملات]**\n\n"
                f"• الزوج المقتنص: `{res['epic']}`\n"
                f"• الاتجاه: `{res['action']}`\n"
                f"• السعر: `{res['price']}`\n"
                f"• حجم العقد: `{res['lots']} Lot`\n"
                f"• مؤشر هيرست (Hurst): `{res.get('hurst')}` (Trending)\n"
                f"• مصفوفة القوة: `{res.get('strength')}`\n"
                f"• مؤشر الدولار: `{res.get('dxy')}`\n"
                f"• صائد السيولة: `{res.get('sweep')}`\n"
                f"• فجوة السيولة (FVG): `{res.get('fvg')}`\n"
                f"• وقف الخسارة الديناميكي: `{res.get('sl_pips')} Pips`\n"
                f"• جني الأرباح الديناميكي: `{res.get('tp_pips')} Pips`\n"
                f"• استجابة الوسيط: `{res.get('latency')} ms`\n"
                f"• انزلاق التنفيذ: `{res.get('slippage')} Pips`\n"
                f"• احتمالية النجاح (Conformal): `{res['prob']*100:.1f}%`\n"
                f"• المصادقة البصرية: **معتمدة عبر Gemini Vision**\n"
                f"• المرجع: `{res.get('deal_ref', 'OK')}`"
                f"{res.get('slip_alert', '')}"
            )
            await send_priority_message(context, msg, urgent=True)

        elif res.get("action") == "SIGNAL_DETECTED":
            msg = (
                f"📡 **[رصد إشارة دخول - وضع المراقبة]**\n\n"
                f"• الزوج: `{res['epic']}`\n"
                f"• السعر: `{res['price']}`\n"
                f"• الوقف/الهدف المقترح: `{res.get('sl_pips')} / {res.get('tp_pips')} Pips`\n"
                f"• الاحتمالية: `{res['prob']*100:.1f}%`\n"
                f"• الحالة: {res['reason']}"
            )
            await send_priority_message(context, msg, urgent=False)

        elif res.get("action") == "ORDER_FAILED":
            await send_priority_message(context, f"❌ **[فشل تنفيذ الصفقة من الوسيط]**: {res['reason']}", urgent=True)

        elif res.get("action") in ["CIRCUIT_BREAKER", "LATENCY_HALT", "COOLDOWN", "PRE_TRADE_SLIPPAGE_REJECTED", "MACRO_SHOCK_HALT", "LONDON_FIX_BLOCK", "VOLATILITY_SPILLOVER_HALT", "CANARY_RECALIBRATED"]:
            if res.get("action") in ["CIRCUIT_BREAKER", "LATENCY_HALT", "MACRO_SHOCK_HALT", "VOLATILITY_SPILLOVER_HALT"]:
                await send_priority_message(context, f"🚨 {res['reason']}", urgent=True)
            elif res.get("action") not in ["COOLDOWN", "PRE_TRADE_SLIPPAGE_REJECTED", "LONDON_FIX_BLOCK"]:
                await send_priority_message(context, f"ℹ️ {res['reason']}", urgent=False)

    except Exception as e:
        TerminalLogger.error("JOB_SCANNER_MINUTE", str(e), traceback.format_exc())

async def job_evolution_hourly(context: ContextTypes.DEFAULT_TYPE):
    try:
        TerminalLogger.info("بدء مهمة التدريب والباكتيست الذاتي الساعية...")
        evo = await asyncio.to_thread(system.train_and_evolve)
        results = evo.get("results", {})
        
        msg = (
            "🧬 **[تقرير دوري آلي: التدريب النظيف (Purged CV + Triple Barrier)]**\n\n"
            f"• التوقيت: `{evo['timestamp']}`\n"
            f"• الحالة: {evo['status']}\n\n"
            "**أداء سلة العملات في الباكتيست الأخير:**\n"
        )
        for epic, r in results.items():
            msg += (
                f"**• {epic}:** عائـد `{r.get('return_pct')}%` | فـوز `{r.get('win_rate')}%` | هبـوط `{r.get('max_dd')}%`\n"
            )
        
        await send_priority_message(context, msg, urgent=False)
    except Exception as e:
        TerminalLogger.error("JOB_EVOLUTION_HOURLY", str(e), traceback.format_exc())

async def job_session_keepalive(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(system.broker.keep_alive_session)
    except Exception as e:
        TerminalLogger.error("JOB_SESSION_KEEPALIVE", str(e))

async def job_health_heartbeat(context: ContextTypes.DEFAULT_TYPE):
    try:
        h = InternalSystemWatchdog.get_metrics()
        latency = system.broker.get_average_latency()
        tca = DuckDBWarehouse.get_tca_summary(limit=30)
        msg = (
            "💓 **[نبضة صحة واستجابة النظام الدورية - 12 ساعة]**\n\n"
            f"• استهلاك الذاكرة (RAM): `{h['ram_mb']} MB`\n"
            f"• استهلاك المعالج (CPU): `{h['cpu_pct']}%`\n"
            f"• استجابة الوسيط (API Latency): `{latency:.0f} ms`\n"
            f"• متوسط الانزلاق السعري (TCA): `{tca['avg_slippage']} Pips`\n"
            f"• إجمالي تكلفة الاحتكاك: `${tca['total_friction']}`\n"
            f"• وقت التشغيل المتواصل: `{h['uptime']}`\n"
            f"• مؤقت اليقظة الذاتي: **نشط ويراقب الخدمة (Watchdog OK) 🛡️**\n"
            f"• مستودع DuckDB: **قفل المعالجة المتبادل نشط (Thread-Safe Lock) ✅**"
        )
        await send_priority_message(context, msg, urgent=False)
        TerminalLogger.info(f"نبضة صحة النظام: RAM={h['ram_mb']}MB, CPU={h['cpu_pct']}%, Latency={latency:.0f}ms")
    except Exception as e:
        TerminalLogger.error("JOB_HEALTH_HEARTBEAT", str(e))

# ==============================================================================
# 18. نقطة التشغيل الرئيسية لـ Windows وخدمة NSSM
# ==============================================================================
if __name__ == "__main__":
    print("\n" + "="*80)
    print(" 🚀 Quant Institutional Multi-Asset Trading Engine (v5.5 Fully Hardened)")
    print(" 🛡️ Active Safety: Conformal ML | GARCH | Kalman | Hurst | Watchdog | Live Diagnostics")
    print("="*80 + "\n")
    
    setup_crash_dump_handler()
    tune_windows_process_affinity_and_priority()
    InternalSystemWatchdog.start()

    app = ApplicationBuilder().token(Config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("autotrade_on", autotrade_on_cmd))
    app.add_handler(CommandHandler("autotrade_off", autotrade_off_cmd))
    app.add_handler(CommandHandler("mode_demo", mode_demo_cmd))
    app.add_handler(CommandHandler("mode_live", mode_live_cmd))
    app.add_handler(CommandHandler("backtest", backtest_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(CommandHandler("evolution", evolution_cmd))
    app.add_handler(CommandHandler("news", news_cmd))

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), gemini_chat_handler))

    jq = app.job_queue
    jq.run_repeating(job_scanner_minute, interval=60, first=10)
    jq.run_repeating(job_session_keepalive, interval=480, first=480)
    jq.run_repeating(job_evolution_hourly, interval=3600, first=30)
    jq.run_repeating(job_health_heartbeat, interval=43200, first=3600)

    TerminalLogger.success("بدء تشغيل استقبال أوامر تيليجرام ومراقبة السوق المباشرة...")
    app.run_polling()
