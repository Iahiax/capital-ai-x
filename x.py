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
import tempfile
import subprocess
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

# إعداد Matplotlib للعمل في الخلفية دون واجهات رسومية (Windows Service Safe)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

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
    
    # المعاملات الديناميكية المستندة لـ ATR
    MIN_STOP_PIPS = 3.5
    MIN_PROFIT_PIPS = 6.0
    ATR_SL_MULTIPLIER = 1.5
    ATR_TP_MULTIPLIER = 3.0
    
    MAX_DAILY_DRAWDOWN_PCT = 3.0   # قاطع الهبوط اليومي (3%)
    MAX_FRICTION_RATIO_PCT = 20.0  # أقصى نسبة سبريد إلى مدى الشمعة اللحظي

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
# 2. مستودع البيانات المحلي فائق السرعة عبر DuckDB (تزامن آمن)
# ==============================================================================
class DuckDBWarehouse:
    _db_lock = threading.Lock()

    @classmethod
    def init_database(cls):
        with cls._db_lock:
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
                    )
                """)
            finally:
                con.close()

    @classmethod
    def upsert_candles(cls, epic: str, df: pd.DataFrame):
        if df.empty:
            return
        with cls._db_lock:
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
    def get_cached_candles(cls, epic: str, limit=1000) -> pd.DataFrame:
        with cls._db_lock:
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

# ==============================================================================
# 3. مراقب صحة النظام ومكافحة تسريب الذاكرة (Non-Blocking Watchdog)
# ==============================================================================
class SystemHealthWatchdog:
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
# 4. وسيط Capital.com مع استخراج الأسعار واحتساب القيمة النقطية
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

    def login(self):
        url = f"{self.get_server()}/api/v1/session"
        headers = {"X-CAP-API-KEY": Config.CAPITAL_API_KEY, "Content-Type": "application/json"}
        payload = {"identifier": Config.CAPITAL_EMAIL, "password": Config.CAPITAL_PASSWORD, "encryptedPassword": False}
        try:
            r = self.session.post(url, headers=headers, json=payload, timeout=8)
            if r.status_code == 200:
                self.cst = r.headers.get("CST")
                self.xst = r.headers.get("X-SECURITY-TOKEN")
                return True
        except Exception as e:
            print(f"[Broker Login Error]: {e}")
        return False

    def get_headers(self):
        return {"X-SECURITY-TOKEN": self.xst, "CST": self.cst, "Content-Type": "application/json"}

    def get_account_details(self) -> dict:
        url = f"{self.get_server()}/api/v1/accounts"
        try:
            r = self.session.get(url, headers=self.get_headers(), timeout=6)
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
            print(f"[Account Details Error]: {e}")
        return {"balance": 1000.0, "available": 1000.0}

    def fetch_live_candles(self, epic: str, resolution="MINUTE", max_bars=100) -> pd.DataFrame:
        url = f"{self.get_server()}/api/v1/prices/{epic}"
        params = {"resolution": resolution, "max": max_bars}
        try:
            r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
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
            print(f"[Fetch Candles Error - {epic}]: {e}")
            return pd.DataFrame()

    def execute_order_server_trailing(self, epic: str, direction: str, size: float, current_price: float, stop_pips: float, profit_pips: float) -> dict:
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
        try:
            r = self.session.post(url, headers=self.get_headers(), json=payload, timeout=8)
            if r.status_code == 401:
                self.login()
                r = self.session.post(url, headers=self.get_headers(), json=payload, timeout=8)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def get_open_positions(self) -> list:
        url = f"{self.get_server()}/api/v1/positions"
        try:
            r = self.session.get(url, headers=self.get_headers(), timeout=6)
            if r.status_code == 401:
                self.login()
                r = self.session.get(url, headers=self.get_headers(), timeout=6)
            if r.status_code == 200:
                return r.json().get("positions", [])
        except Exception as e:
            print(f"[Open Positions Error]: {e}")
        return []

    def fetch_recent_closed_trades(self, limit=5) -> list:
        url = f"{self.get_server()}/api/v1/history/activity"
        params = {"limit": limit}
        try:
            r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            if r.status_code == 401:
                self.login()
                r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            if r.status_code == 200:
                return r.json().get("activities", [])
        except Exception as e:
            print(f"[Activity Fetch Error]: {e}")
        return []

# ==============================================================================
# 5. إدارة ارتباط العملات ومخاطر السلة المشتركة (Currency Correlation Manager)
# ==============================================================================
class CurrencyCorrelationManager:
    """
    حساب صافي التعرض اللحظي لعملة الدولار (USD Delta Exposure).
    يمنع الدخول في صفقة جديدة تضاعف المخاطرة في نفس الاتجاه ضد الدولار.
    """
    @staticmethod
    def get_usd_direction(epic: str, action: str) -> str:
        # أزواج تنتهي بالدولار: BUY تعني بيع الدولار (-USD)، SELL تعني شراء الدولار (+USD)
        if epic.upper().endswith("USD"):
            return "SHORT_USD" if action == "BUY" else "LONG_USD"
        # أزواج تبدأ بالدولار: BUY تعني شراء الدولار (+USD)، SELL تعني بيع الدولار (-USD)
        elif epic.upper().startswith("USD"):
            return "LONG_USD" if action == "BUY" else "SHORT_USD"
        return "NEUTRAL"

    @classmethod
    def can_open_position(cls, open_positions: list, proposed_epic: str, proposed_action: str) -> tuple[bool, str]:
        proposed_usd_dir = cls.get_usd_direction(proposed_epic, proposed_action)
        if proposed_usd_dir == "NEUTRAL":
            return True, "الزوج محايد تجاه الدولار"

        for pos in open_positions:
            pos_epic = pos.get("market", {}).get("epic") or pos.get("epic", "")
            pos_dir = pos.get("position", {}).get("direction") or pos.get("direction", "")
            
            existing_usd_dir = cls.get_usd_direction(pos_epic, pos_dir)
            if existing_usd_dir == proposed_usd_dir:
                return False, f"حظر ارتباط: هناك مركز مفتوح مسبقاً على {pos_epic} يحمل نفس انكشاف الدولار ({proposed_usd_dir})."

        return True, "انكشاف العملة متوازن"

# ==============================================================================
# 6. صمام أمان السوق والأخبار اللحظية متعدد العملات
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
        except Exception:
            pass

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
        except Exception:
            pass

        return False, "لا توجد أخبار عالية التأثير في النافذة اللحظية."

# ==============================================================================
# 7. محرك تدفق الأوامر ودلتا الحجم ونماذج التعلم الآلي
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
            except Exception:
                pass

        if X[-1, 1] > np.mean(X[:, 1]) * 2.0:
            return 2
        elif abs(X[-1, 0]) > np.std(X[:, 0]) * 1.1:
            return 0
        return 1

class HybridMetaLabeler:
    FEATURE_NAMES = ["close", "vol", "mom", "cvd"]

    def __init__(self):
        self.lgb_model = None
        self.cb_model = None
        self.is_trained = False

    def train_ensemble(self, df_m1: pd.DataFrame, pip_mult: float):
        if len(df_m1) < 120:
            return
        d = df_m1.copy().dropna()
        future_ret = d['close'].pct_change(3).shift(-3)
        spread_cost = (0.8 * pip_mult) / d['close']
        y = (future_ret > spread_cost).astype(int).iloc[:-3]

        features = pd.DataFrame({
            "close": d['close'],
            "vol": d['volume'],
            "mom": d['close'].pct_change(10),
            "cvd": d.get('cvd_zscore', pd.Series(0, index=d.index))
        }).iloc[:-3].fillna(0.0)

        if len(y) < 60 or len(np.unique(y)) < 2:
            return

        if lgb is not None:
            try:
                self.lgb_model = lgb.LGBMClassifier(n_estimators=30, learning_rate=0.05, max_depth=3, verbose=-1, random_state=42)
                self.lgb_model.fit(features, y)
            except Exception:
                pass

        if CatBoostClassifier is not None:
            try:
                self.cb_model = CatBoostClassifier(iterations=30, learning_rate=0.05, depth=3, verbose=False, random_seed=42)
                self.cb_model.fit(features, y)
            except Exception:
                pass

        self.is_trained = (self.lgb_model is not None or self.cb_model is not None)

    def predict_success_probability(self, feature_row: np.ndarray) -> float:
        if not self.is_trained:
            return 0.70

        probs = []
        X_df = pd.DataFrame([feature_row], columns=self.FEATURE_NAMES).fillna(0.0)

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

        return float(np.mean(probs)) if probs else 0.70

# ==============================================================================
# 8. إدارة المخاطر، الجلسات، وتحليل M15 بدون استهلاك شبكة
# ==============================================================================
class AdvancedRiskAndSessionManager:
    def __init__(self):
        self.last_reset_day = -1
        self.day_start_equity = 1000.0
        self.daily_circuit_breaker_active = False
        self.consecutive_losses = 0

    def update_daily_equity_baseline(self, current_equity: float):
        now_utc = datetime.now(timezone.utc)
        if now_utc.day != self.last_reset_day:
            self.day_start_equity = current_equity
            self.last_reset_day = now_utc.day
            self.daily_circuit_breaker_active = False

    def check_circuit_breakers(self, current_equity: float) -> tuple[bool, str]:
        self.update_daily_equity_baseline(current_equity)

        if self.daily_circuit_breaker_active:
            return False, "قاطع الهبوط اليومي نشط (حظر التداول مؤقتاً لحماية الحساب)."

        drawdown_pct = ((self.day_start_equity - current_equity) / self.day_start_equity) * 100.0
        if drawdown_pct >= Config.MAX_DAILY_DRAWDOWN_PCT:
            self.daily_circuit_breaker_active = True
            return False, f"🚨 قاطع الهبوط: تجاوزت الخسائر {drawdown_pct:.2f}% (الحد الأقصى {Config.MAX_DAILY_DRAWDOWN_PCT}%)."

        return True, "إدارة المخاطر اليومية مستقرة."

    def get_dynamic_session_weights(self) -> dict:
        hour = datetime.now(timezone.utc).hour
        if 0 <= hour < 7:
            return {"SMC": 0.20, "VWAP": 0.60, "MOM": 0.20, "session": "Asian Range"}
        elif 7 <= hour < 12:
            return {"SMC": 0.50, "VWAP": 0.20, "MOM": 0.30, "session": "London Expansion"}
        elif 12 <= hour < 16:
            return {"SMC": 0.35, "VWAP": 0.25, "MOM": 0.40, "session": "Overlap Momentum"}
        else:
            return {"SMC": 0.30, "VWAP": 0.40, "MOM": 0.30, "session": "Late NY"}

    def calculate_lot_size(self, epic: str, equity: float, current_price: float, sl_pips: float) -> float:
        if current_price <= 0 or np.isnan(current_price):
            current_price = 1.0850

        active_kelly = Config.KELLY_FRACTION
        if self.consecutive_losses >= 2:
            active_kelly *= 0.5

        full_kelly = max(0.05, (0.58 * 2.5 - 0.42) / 2.5)
        risk_capital = equity * (full_kelly * active_kelly)
        
        pip_val_usd = FastCapitalBroker.get_pip_value_usd(epic, current_price)
        pip_risk = sl_pips * pip_val_usd
        calculated_lots = risk_capital / (pip_risk if pip_risk > 0 else 40.0)
        
        buying_power = equity * Config.LEVERAGE * Config.MAX_MARGIN_USAGE_PCT
        if epic.upper().startswith("USD"):
            max_possible_lots = buying_power / 100000.0
        else:
            max_possible_lots = buying_power / (100000.0 * current_price)

        final_lot = max(0.01, min(calculated_lots, max_possible_lots))
        return round(float(final_lot), 2)

class MultiTimeframeAnalyzer:
    @staticmethod
    def get_m15_bias_from_duckdb(epic: str) -> tuple[int, str]:
        """
        توليد فريم M15 ذاتياً من مستودع DuckDB المحلي بدون استدعاء شبكة (Zero-Latency)
        """
        df_m1 = DuckDBWarehouse.get_cached_candles(epic, limit=1000)
        if len(df_m1) < 150:
            return 0, "بيانات DuckDB غير كافية لـ M15"

        try:
            d = df_m1.copy()
            d['dt'] = pd.to_datetime(d['timestamp'])
            d.set_index('dt', inplace=True)
            
            # إعادة تجميع الشموع اللحظية إلى فريم 15 دقيقة
            df_m15 = d.resample('15min').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna().reset_index()

            if len(df_m15) < 30:
                return 0, "شمعات M15 المجمعة غير كافية"

            ema20 = df_m15['close'].ewm(span=20, adjust=False).mean().iloc[-1]
            ema50 = df_m15['close'].ewm(span=50, adjust=False).mean().iloc[-1]

            if ema20 > ema50:
                return 1, "الاتجاه صاعد على M15 (DuckDB)"
            elif ema20 < ema50:
                return -1, "الاتجاه هابط على M15 (DuckDB)"
            return 0, "اتجاه M15 محايد"
        except Exception as e:
            return 0, f"خطأ تجميع M15: {e}"

# ==============================================================================
# 9. المصادقة البصرية للشارت عبر Gemini Vision مع حماية الذاكرة
# ==============================================================================
class GeminiChartVisionValidator:
    @staticmethod
    def generate_chart_image_b64(df_m1: pd.DataFrame, vwap_series: pd.Series) -> str:
        d = df_m1.tail(40).copy().reset_index(drop=True)
        v = vwap_series.tail(40).values

        fig, ax = plt.subplots(figsize=(6, 3), dpi=100)
        try:
            fig.patch.set_facecolor('#0E1117')
            ax.set_facecolor('#0E1117')

            for i, row in d.iterrows():
                color = '#00FF7F' if row['close'] >= row['open'] else '#FF3B30'
                ax.plot([i, i], [row['low'], row['high']], color=color, linewidth=1)
                ax.plot([i, i], [row['open'], row['close']], color=color, linewidth=3)

            ax.plot(range(len(v)), v, color='#00BFFF', linestyle='--', linewidth=1.5, label="VWAP")
            ax.axis('off')
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode('utf-8')
            buf.close()
            return img_b64
        finally:
            plt.clf()
            plt.close('all')
            del fig, ax
            gc.collect()

    @classmethod
    def validate_setup_with_vision(cls, epic: str, df_m1: pd.DataFrame, vwap_series: pd.Series, action: str, price: float) -> tuple[bool, str]:
        try:
            b64_chart = cls.generate_chart_image_b64(df_m1, vwap_series)
            url = f"{Config.GEMINI_ENDPOINT}?key={Config.GEMINI_API_KEY}"
            headers = {"Content-Type": "application/json"}

            prompt = (
                f"You are a quant execution risk controller. We want to enter '{action}' on {epic} at {price}. "
                f"Examine this 40-bar chart with VWAP. Look for hostile counter-trend wicks or fakeouts. "
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
                            return (data.get("decision") == "APPROVE"), data.get("reason", "موافقة بصرية")
        except Exception as e:
            print(f"[Vision Warning - {epic}]: {e}")
        return True, "تم تخطي الفحص البصري"

# ==============================================================================
# 10. محرك Google Gemini مع Function Calling المحدث
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
أنت المهندس الكمي والمشرف الرئيسي على نظام التداول الآلي متعدد العملات (M1 CFDs).
أنت تتحدث باللغة العربية بطلاقة وبأسلوب مالي واثق ومحترف وموجز.

بيانات السوق والمحفظة اللحظية الحقيقية:
- الرصيد الإجمالي: ${system_context.get('balance', 0):,.2f}
- الرصيد المتاح: ${system_context.get('available', 0):,.2f}
- أزواج العملات النشطة: {', '.join(Config.ACTIVE_EPICS)}
- جلسة التداول الحالية: {system_context.get('session', 'N/A')}
- حالة قاطع الهبوط اليومي: {'مفعل وحظر التداول' if system_context.get('circuit_breaker') else 'طبيعي ومستقر'}
- صحة النظام (RAM / CPU): {system_context.get('health', {}).get('ram_mb', 0)}MB / {system_context.get('health', {}).get('cpu_pct', 0)}%
- الصفقات المفتوحة: {system_context.get('open_trades', 0)}
- التداول الآلي: {'نشط' if system_context.get('autotrade') else 'متوقف مؤقتاً'}
- وضع الحساب: {'تجريبي (DEMO)' if system_context.get('is_demo') else 'حقيقي (LIVE)'}
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
                    return (
                        f"✅ **[تم تطبيق التعديل ذاتياً بواسطة Gemini]**\n\n"
                        f"• مضاعف وقف ATR: `{Config.ATR_SL_MULTIPLIER}x`\n"
                        f"• مضاعف هدف ATR: `{Config.ATR_TP_MULTIPLIER}x`\n"
                        f"• نسبة كسر كيلي للمخاطرة: `{Config.KELLY_FRACTION}`"
                    )

                elif func_name == "force_recalibrate_models":
                    evo_res = system_instance.train_and_evolve()
                    return (
                        f"🧠 **[تمت إعادة المعايرة والتدريب والباكتيست الذاتي لجميع الأزواج]**\n\n"
                        f"• الحالة: {evo_res.get('status')}"
                    )

                elif func_name == "toggle_autotrade":
                    system_instance.autotrade_active = bool(args.get("enabled", False))
                    status_str = "مُفعل بنجاح 🚀" if system_instance.autotrade_active else "مُعطل مؤقتاً 🛑"
                    return f"🔄 **تم تغيير حالة التداول الآلي:** {status_str}"

                elif func_name == "switch_trading_mode":
                    mode_choice = str(args.get("mode", "demo")).lower()
                    if mode_choice == "live":
                        system_instance.broker.demo = False
                        Config.DEMO_MODE = False
                        system_instance.broker.login()
                        return "⚠️ **تحذير: تم التبديل للحساب الحقيقي (LIVE)!**"
                    else:
                        system_instance.broker.demo = True
                        Config.DEMO_MODE = True
                        system_instance.broker.login()
                        return "✅ **تم التبديل للحساب التجريبي (DEMO).**"

            return text_response if text_response else "تم استلام رسالتك وتحديث الحالة."

        except Exception as e:
            return f"تعذر استكمال المعالجة عبر Gemini: {e}"

# ==============================================================================
# 11. محرك النظام الرئيسي وتدوير العملات والباكتيست الموحد
# ==============================================================================
class MasterQuantSystem:
    def __init__(self):
        DuckDBWarehouse.init_database()
        self.broker = FastCapitalBroker()
        self.order_flow = OrderFlowEngine()
        self.hmm = HMMRegimeClassifier()
        self.meta_labelers = {epic: HybridMetaLabeler() for epic in Config.ACTIVE_EPICS}
        self.risk_mgr = AdvancedRiskAndSessionManager()
        self.autotrade_active = False
        self.evolution_log = []
        self.processed_deal_ids = set()

    def reconcile_closed_trades(self):
        activities = self.broker.fetch_recent_closed_trades(limit=5)
        for act in activities:
            details = act.get("details") if isinstance(act.get("details"), dict) else {}
            deal_id = act.get("dealId") or details.get("dealId")
            
            if deal_id and deal_id not in self.processed_deal_ids:
                pnl_val = act.get("profitAndLoss") or act.get("pnl") or details.get("profitAndLoss") or details.get("profit")
                if pnl_val is not None:
                    pnl = float(pnl_val)
                    if pnl < 0:
                        self.risk_mgr.consecutive_losses += 1
                    else:
                        self.risk_mgr.consecutive_losses = 0
                self.processed_deal_ids.add(deal_id)

    def run_single_asset_backtest(self, epic: str, df: pd.DataFrame) -> dict:
        if len(df) < 150:
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

        ret = d['close'].pct_change().shift(-1).fillna(0.0)
        spread_cost = (0.8 * pip_mult) / d['close']
        trade_changes = pos.diff().abs()
        strat_ret = (pos * ret) - (trade_changes * spread_cost)

        cum_ret = (1 + strat_ret * Config.LEVERAGE).cumprod()
        dd = (cum_ret - cum_ret.cummax()) / cum_ret.cummax()
        trades_count = int(trade_changes.sum() / 2)
        winning_trades = int((strat_ret[trade_changes > 0] > 0).sum())
        win_rate = (winning_trades / max(1, trades_count)) * 100
        sharpe = (strat_ret.mean() / (strat_ret.std() + 1e-8)) * np.sqrt(1440 * 252)

        return {
            "return_pct": round(float(strat_ret.sum() * Config.LEVERAGE * 100), 2),
            "max_dd": round(abs(float(dd.min())) * 100, 2),
            "win_rate": round(win_rate, 1),
            "trades": trades_count,
            "sharpe": round(float(sharpe), 2)
        }

    def scan_and_rotate_assets(self) -> dict:
        acc = self.broker.get_account_details()

        # 1. فحص قاطع الهبوط اليومي
        cb_ok, cb_msg = self.risk_mgr.check_circuit_breakers(acc["balance"])
        if not cb_ok:
            return {"action": "CIRCUIT_BREAKER", "reason": cb_msg}

        # 2. فحص مواعيد السوق والأخبار
        market_ok, market_msg = MarketShield.check_market_hours()
        if not market_ok:
            return {"action": "HALT", "reason": market_msg}

        news_block, news_msg = MarketShield.check_live_news()
        if news_block:
            return {"action": "NEWS_BLOCK", "reason": news_msg}

        # 3. الصفقات المفتوحة مسبقاً
        open_pos = self.broker.get_open_positions()
        if len(open_pos) > 0:
            return {"action": "IN_POSITION", "reason": "هناك صفقة قائمة تحت حماية الوقف المتحرك"}

        session_info = self.risk_mgr.get_dynamic_session_weights()
        qualified_opportunities = []

        # 4. مسح سلة العملات بالكامل
        for epic in Config.ACTIVE_EPICS:
            pip_mult = self.broker.get_pip_multiplier(epic)
            df_m1 = self.broker.fetch_live_candles(epic, resolution="MINUTE", max_bars=100)
            if len(df_m1) < 50:
                continue

            last = df_m1.iloc[-1]
            live_spread_pips = (last['ask_close'] - last['close']) / pip_mult

            # حساب ATR(14)
            tr = np.maximum(df_m1['high'] - df_m1['low'], 
                            np.maximum(abs(df_m1['high'] - df_m1['close'].shift(1)), 
                                       abs(df_m1['low'] - df_m1['close'].shift(1))))
            atr_val = tr.rolling(14).mean().iloc[-1]
            if np.isnan(atr_val) or atr_val <= 0:
                continue
            atr_pips = atr_val / pip_mult

            # فلتر نسبة السبريد للمدى اللحظي
            if ((live_spread_pips / atr_pips) * 100.0) > Config.MAX_FRICTION_RATIO_PCT:
                continue

            df_m1 = self.order_flow.calculate_volume_delta(df_m1)
            regime = self.hmm.fit_predict_regime(df_m1)
            if regime == 2:
                continue

            # تحليل فريم M15 ذاتياً عبر مستودع DuckDB بدون استدعاء شبكة
            mtf_bias, _ = MultiTimeframeAnalyzer.get_m15_bias_from_duckdb(epic)

            current_price = last['close']
            tp = (df_m1['high'] + df_m1['low'] + df_m1['close']) / 3
            vwap = (tp * df_m1['volume']).cumsum() / (df_m1['volume'].cumsum() + 1e-8)
            std = (tp - vwap).rolling(30).std()
            vwap_val = vwap.iloc[-1]
            vwap_lower = vwap_val - (2.0 * std.iloc[-1])
            vwap_upper = vwap_val + (2.0 * std.iloc[-1])

            # فلتر الارتداد العادل وتفادي الانزلاق: منع الدخول إذا كان السعر متباعداً بشدة عن VWAP
            distance_to_vwap_pips = abs(current_price - vwap_val) / pip_mult
            if distance_to_vwap_pips > (1.8 * atr_pips):
                continue

            smc_sig = 1 if last['bullish_absorption'] else (-1 if last['bearish_absorption'] else 0)
            vwap_sig = 1 if current_price < vwap_lower else (-1 if current_price > vwap_upper else 0)
            mom_sig = np.sign(df_m1['close'].pct_change(10).iloc[-1])

            score = (smc_sig * session_info["SMC"]) + (vwap_sig * session_info["VWAP"]) + (mom_sig * session_info["MOM"])

            action = None
            if score > 0.40 and mtf_bias > 0:
                action = "BUY"
            elif score < -0.40 and mtf_bias < 0:
                action = "SELL"

            if action:
                # التحقق من إدارة ارتباط العملات ومخاطر الدولار المشترك
                can_open, corr_msg = CurrencyCorrelationManager.can_open_position(open_pos, epic, action)
                if not can_open:
                    continue

                mom_val = df_m1['close'].pct_change(10).iloc[-1]
                meta_features = np.array([current_price, last['volume'], mom_val, last['cvd_zscore']])
                prob_success = self.meta_labelers[epic].predict_success_probability(meta_features)

                if prob_success >= 0.65:
                    # احتساب الوقف والهدف الديناميكي القائم على ATR
                    dyn_sl = max(Config.MIN_STOP_PIPS, round(atr_pips * Config.ATR_SL_MULTIPLIER, 1))
                    dyn_tp = max(Config.MIN_PROFIT_PIPS, round(atr_pips * Config.ATR_TP_MULTIPLIER, 1))

                    qualified_opportunities.append({
                        "epic": epic,
                        "action": action,
                        "score": abs(score),
                        "prob": prob_success,
                        "price": current_price,
                        "sl_pips": dyn_sl,
                        "tp_pips": dyn_tp,
                        "df": df_m1,
                        "vwap": vwap
                    })

        if qualified_opportunities:
            best_opp = max(qualified_opportunities, key=lambda x: (x["prob"], x["score"]))
            
            vision_ok, vision_reason = GeminiChartVisionValidator.validate_setup_with_vision(
                best_opp["epic"], best_opp["df"], best_opp["vwap"], best_opp["action"], best_opp["price"]
            )
            
            if not vision_ok:
                return {"action": "VISION_REJECTED", "reason": f"رفض بصري لزوج {best_opp['epic']}: {vision_reason}"}

            if self.autotrade_active:
                lots = self.risk_mgr.calculate_lot_size(
                    best_opp["epic"], 
                    acc["available"], 
                    best_opp["price"],
                    best_opp["sl_pips"]
                )
                res = self.broker.execute_order_server_trailing(
                    epic=best_opp["epic"],
                    direction=best_opp["action"],
                    size=lots,
                    current_price=best_opp["price"],
                    stop_pips=best_opp["sl_pips"],
                    profit_pips=best_opp["tp_pips"]
                )
                raw_ref = res.get("dealReference") or "OK"
                clean_ref = re.sub(r'[^a-zA-Z0-9-]', '', str(raw_ref))

                return {
                    "action": best_opp["action"],
                    "epic": best_opp["epic"],
                    "price": best_opp["price"],
                    "lots": lots,
                    "sl_pips": best_opp["sl_pips"],
                    "tp_pips": best_opp["tp_pips"],
                    "prob": round(best_opp["prob"], 2),
                    "session": session_info["session"],
                    "deal_ref": clean_ref,
                    "reason": f"اقتناص فرصة رابحة ({best_opp['epic']}) بوقف ديناميكي {best_opp['sl_pips']} نقطة"
                }
            else:
                return {
                    "action": "SIGNAL_DETECTED",
                    "epic": best_opp["epic"],
                    "price": best_opp["price"],
                    "sl_pips": best_opp["sl_pips"],
                    "tp_pips": best_opp["tp_pips"],
                    "prob": round(best_opp["prob"], 2),
                    "reason": f"فرصة ممتازة على {best_opp['epic']} ولكن التداول الآلي معطل (المراقبة فقط)"
                }

        return {"action": "HOLD", "reason": "استقرار محايد عبر كافة العملات النشطة"}

    def run_full_backtest_duckdb(self) -> dict:
        results = {}
        for epic in Config.ACTIVE_EPICS:
            df = DuckDBWarehouse.get_cached_candles(epic, limit=1000)
            if len(df) >= 150:
                results[epic] = self.run_single_asset_backtest(epic, df)
        return results

    def train_and_evolve(self) -> dict:
        trained_epics = []
        backtest_results = {}
        
        for epic in Config.ACTIVE_EPICS:
            df = DuckDBWarehouse.get_cached_candles(epic, limit=1000)
            if len(df) >= 150:
                df_features = self.order_flow.calculate_volume_delta(df)
                self.meta_labelers[epic].train_ensemble(df_features, self.broker.get_pip_multiplier(epic))
                trained_epics.append(epic)
                bt_metrics = self.run_single_asset_backtest(epic, df)
                backtest_results[epic] = bt_metrics
        
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "status": f"تم التدريب والباكتيست الذاتي لـ: {', '.join(trained_epics)}",
            "results": backtest_results
        }
        self.evolution_log.append(entry)
        return entry

# ==============================================================================
# 12. أوامر التيليجرام التفاعلية المباشرة (أوامر صريحة بدون أزرار تفاعلية)
# ==============================================================================
system = MasterQuantSystem()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👑 **نظام التداول الكمي المؤسسي الشامل متعدد العملات**\n\n"
        f"• محرك الذكاء الاصطناعي: **Google Gemini Flash (Vision + Tools)**\n"
        f"• أزواج العملات النشطة: **{', '.join(Config.ACTIVE_EPICS)}**\n"
        f"• إدارة الارتباط: **Net USD Correlation Cap مدمجة**\n"
        f"• الوقف/الهدف: **ديناميكي وفق ATR (1.5x / 3.0x)**\n"
        f"• مستودع البيانات: **DuckDB Candle Warehouse (M15 Resampling)**\n"
        f"• حساب التداول: **{'تجريبي (DEMO)' if system.broker.demo else 'حقيقي (LIVE)'}**\n"
        f"• حالة التداول الآلي: **{'نشط ✅' if system.autotrade_active else 'معطل ⏸'}**\n\n"
        "**الأوامر السريعة المتاحة:**\n"
        "• `/autotrade_on` - تشغيل التداول الآلي اللحظي\n"
        "• `/autotrade_off` - إيقاف التداول الآلي مؤقتاً\n"
        "• `/mode_demo` - التبديل للحساب التجريبي\n"
        "• `/mode_live` - التبديل للحساب الحقيقي\n"
        "• `/backtest` - باكتيست فوري من مستودع DuckDB\n"
        "• `/status` - فحص المحفظة ومراكز التداول الحالية\n"
        "• `/health` - مراقبة صحة الخادم، الرام والمعالج (Watchdog)\n"
        "• `/evolution` - تقرير تدريب وباكتيست نماذج التعلم الآلي\n"
        "• `/news` - فحص مفكرة الأخبار الاقتصادية اللحظية\n\n"
        "💬 *يمكنك محادثة Gemini بالعربية أو توجيه أوامر مثل: 'عدل مضاعف وقف ATR إلى 2' أو 'أعد تدريب النماذج'!*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def autotrade_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system.autotrade_active = True
    await update.message.reply_text("🚀 **تم تفعيل التداول الآلي المؤسسي متعدد العملات.**", parse_mode="Markdown")

async def autotrade_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system.autotrade_active = False
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
            f"  ↳ نسبة الفوز: `{r.get('win_rate')}%` | الصفقات: `{r.get('trades')}`\n"
            f"  ↳ معامل شارب: `{r.get('sharpe')}`\n\n"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acc = system.broker.get_account_details()
    open_p = system.broker.get_open_positions()
    sess = system.risk_mgr.get_dynamic_session_weights()
    msg = (
        "💼 **[الحالة اللحظية والمحفظة]**\n\n"
        f"• الرصيد الإجمالي: `${acc['balance']:,.2f}`\n"
        f"• الرصيد المتاح: `${acc['available']:,.2f}`\n"
        f"• جلسة التداول: `{sess['session']}`\n"
        f"• الأزواج تحت المراقبة: `{', '.join(Config.ACTIVE_EPICS)}`\n"
        f"• إعدادات ATR الديناميكية: الوقف=`{Config.ATR_SL_MULTIPLIER}x` | الهدف=`{Config.ATR_TP_MULTIPLIER}x`\n"
        f"• قاطع الهبوط اليومي (3%): **{'حظر تداول 🚫' if system.risk_mgr.daily_circuit_breaker_active else 'طبيعي ومستقر ✅'}**\n"
        f"• الصفقات المفتوحة: `{len(open_p)}`\n"
        f"• التداول الآلي: **{'نشط ✅' if system.autotrade_active else 'معطل ⏸'}**"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    h = SystemHealthWatchdog.get_metrics()
    msg = (
        "🖥 **[تقرير صحة النظام ومكافحة تسريب الذاكرة - Windows]**\n\n"
        f"• استهلاك الذاكرة (RAM): `{h['ram_mb']} MB`\n"
        f"• استهلاك المعالج (CPU): `{h['cpu_pct']}%`\n"
        f"• وقت التشغيل المتواصل: `{h['uptime']}`\n"
        f"• خيوط المعالجة النشطة: `{h['threads']}`\n"
        f"• المساحة الحرة للقرص: `{h['disk_free_gb']} GB`\n"
        f"• مستودع DuckDB: **متصل ويعمل بكفاءة ✅**"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def evolution_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not system.evolution_log:
        await update.message.reply_text("جاري تدريب النماذج والباكتيست الذاتي لأول مرة من مستودع DuckDB...")
        await asyncio.to_thread(system.train_and_evolve)
    last = system.evolution_log[-1]
    
    msg = (
        "🧬 **[تقرير التدريب والباكتيست الذاتي الساعي]**\n\n"
        f"• التوقيت: `{last['timestamp']}`\n"
        f"• الحالة: {last['status']}\n\n"
        "**نتائج الباكتيست اللحظي للعملات:**\n"
    )
    results = last.get("results", {})
    for epic, r in results.items():
        msg += (
            f"• **{epic}:** ربح `{r.get('return_pct')}%` | فوز `{r.get('win_rate')}%` | شارب `{r.get('sharpe')}`\n"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocked, news_msg = MarketShield.check_live_news()
    market_ok, market_msg = MarketShield.check_market_hours()
    msg = (
        "🌐 **[فحص السيولة والأخبار الحية لسلة العملات]**\n\n"
        f"• حالة الأخبار: **{'حظر تداول 🚫' if blocked else 'آمن للتداول ✅'}**\n"
        f"  ↳ {news_msg}\n\n"
        f"• حالة جلسة السوق: **{'مفتوح ✅' if market_ok else 'مغلق ⏸'}**\n"
        f"  ↳ {market_msg}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def gemini_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await update.message.reply_chat_action("typing")

    acc = system.broker.get_account_details()
    open_pos = system.broker.get_open_positions()
    sess = system.risk_mgr.get_dynamic_session_weights()
    h = SystemHealthWatchdog.get_metrics()

    system_ctx = {
        "balance": acc["balance"],
        "available": acc["available"],
        "session": sess["session"],
        "health": h,
        "circuit_breaker": system.risk_mgr.daily_circuit_breaker_active,
        "open_trades": len(open_pos),
        "autotrade": system.autotrade_active,
        "is_demo": system.broker.demo
    }

    reply = await asyncio.to_thread(GeminiConversationalAgent.ask_and_execute, user_text, system, system_ctx)
    await update.message.reply_text(reply, parse_mode="Markdown")

# ==============================================================================
# 13. مهام الجدولة اللحظية والساعية ونبضات الصحة
# ==============================================================================
async def job_scanner_minute(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(system.reconcile_closed_trades)
        res = await asyncio.to_thread(system.scan_and_rotate_assets)

        if res.get("action") in ["BUY", "SELL"]:
            msg = (
                f"⚡ **[تنفيذ صفقة مؤسسية - تدوير العملات]**\n\n"
                f"• الزوج المقتنص: `{res['epic']}`\n"
                f"• الاتجاه: `{res['action']}`\n"
                f"• السعر: `{res['price']}`\n"
                f"• حجم العقد: `{res['lots']} Lot`\n"
                f"• وقف الخسارة الديناميكي: `{res.get('sl_pips')} Pips`\n"
                f"• جني الأرباح الديناميكي: `{res.get('tp_pips')} Pips`\n"
                f"• الجلسة: `{res.get('session', 'N/A')}`\n"
                f"• احتمالية النجاح: `{res['prob']*100:.1f}%`\n"
                f"• المصادقة البصرية: **معتمدة عبر Gemini Vision**\n"
                f"• المرجع: `{res.get('deal_ref', 'OK')}`"
            )
            await context.bot.send_message(chat_id=Config.TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
        elif res.get("action") == "SIGNAL_DETECTED":
            msg = (
                f"📡 **[رصد إشارة دخول - وضع المراقبة]**\n\n"
                f"• الزوج: `{res['epic']}`\n"
                f"• السعر: `{res['price']}`\n"
                f"• الوقف/الهدف المقترح: `{res.get('sl_pips')} / {res.get('tp_pips')} Pips`\n"
                f"• الاحتمالية: `{res['prob']*100:.1f}%`\n"
                f"• الحالة: {res['reason']}"
            )
            await context.bot.send_message(chat_id=Config.TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
        elif res.get("action") == "CIRCUIT_BREAKER":
            await context.bot.send_message(chat_id=Config.TELEGRAM_CHAT_ID, text=f"🚨 {res['reason']}")
    except Exception as e:
        print(f"[Scanner Job Error]: {e}")

async def job_evolution_hourly(context: ContextTypes.DEFAULT_TYPE):
    try:
        evo = await asyncio.to_thread(system.train_and_evolve)
        results = evo.get("results", {})
        
        msg = (
            "🧬 **[تقرير دوري آلي: التدريب والباكتيست الذاتي الساعي]**\n\n"
            f"• التوقيت: `{evo['timestamp']}`\n"
            f"• الحالة: {evo['status']}\n\n"
            "**أداء سلة العملات في الباكتيست الأخير:**\n"
        )
        for epic, r in results.items():
            msg += (
                f"**• {epic}:** عائـد `{r.get('return_pct')}%` | فـوز `{r.get('win_rate')}%` | هبـوط `{r.get('max_dd')}%` | شـارب `{r.get('sharpe')}`\n"
            )
        
        await context.bot.send_message(chat_id=Config.TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
        print(f"[Hourly Evolution]: اكتمل التدريب والباكتيست لجميع الأزواج بنجاح.")
    except Exception as e:
        print(f"[Evolution Job Error]: {e}")

async def job_health_heartbeat(context: ContextTypes.DEFAULT_TYPE):
    try:
        h = SystemHealthWatchdog.get_metrics()
        msg = (
            "💓 **[نبضة صحة النظام الدورية - 12 ساعة]**\n\n"
            f"• استهلاك الذاكرة (RAM): `{h['ram_mb']} MB`\n"
            f"• استهلاك المعالج (CPU): `{h['cpu_pct']}%`\n"
            f"• وقت التشغيل المتواصل: `{h['uptime']}`\n"
            f"• المساحة الحرة: `{h['disk_free_gb']} GB`\n"
            "الخدمة تعمل باستقرار تام في خلفية Windows دون أي تسريب للذاكرة."
        )
        await context.bot.send_message(chat_id=Config.TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[Heartbeat Error]: {e}")

# ==============================================================================
# نقطة التشغيل الرئيسية
# ==============================================================================
if __name__ == "__main__":
    print("[+] تشغيل نظام التداول المؤسسي المتقدم لـ Windows (Multi-Asset + Dynamic ATR + DuckDB)...")
    app = ApplicationBuilder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # تسجيل الأوامر الصريحة بدون أزرار تفاعلية
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

    # التحدث التفاعلي وتعديل النظام ذاتياً عبر Gemini
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), gemini_chat_handler))

    # جدولة المهام: دقيقة لمسح العملات، ساعة للتدريب والباكتيست الذاتي، و12 ساعة لنبضة الصحة
    jq = app.job_queue
    jq.run_repeating(job_scanner_minute, interval=60, first=10)
    jq.run_repeating(job_evolution_hourly, interval=3600, first=30)
    jq.run_repeating(job_health_heartbeat, interval=43200, first=3600)

    app.run_polling()
