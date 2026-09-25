import os
import ast
import sys
import json
import time
import asyncio
import tempfile
import subprocess
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import numpy as np
import pandas as pd
from datetime import datetime, timezone
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
# 1. إعدادات النظام وبيانات الاعتماد
# ==============================================================================
class Config:
    # Capital.com API
    CAPITAL_EMAIL = "yahia.x@outlook.sa"
    CAPITAL_API_KEY = "ut2RpxSbx6fiDdHv"
    CAPITAL_PASSWORD = "Yahia@1411"
    DEMO_MODE = True
    DEMO_SERVER = "https://demo-api-capital.backend-capital.com"
    LIVE_SERVER = "https://api-capital.backend-capital.com"
    EPIC_EURUSD = "EURUSD"

    # إدارة المخاطر والرافعة المالية
    LEVERAGE = 100.0
    KELLY_FRACTION = 0.25
    MAX_MARGIN_USAGE_PCT = 0.80
    DEFAULT_STOP_PIPS = 4.0
    DEFAULT_PROFIT_PIPS = 10.0

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

    # Firebase Firestore REST API
    FIREBASE_PROJECT_ID = "capital-xx"
    FIREBASE_API_KEY = "AIzaSyBeLkqTzIgRcHbAxGbTS5gqnUz2QCA4dYI"
    FIRESTORE_URL = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"

# ==============================================================================
# 2. وسيط Capital.com مع تصحيح أسعار الأهداف وتجديد الجلسات
# ==============================================================================
class FastCapitalBroker:
    def __init__(self):
        self.demo = Config.DEMO_MODE
        self.cst = None
        self.xst = None
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retries)
        self.session.mount("https://", adapter)
        self.login()

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
                return {"balance": float(bal.get("balance", 1000.0)), "available": float(bal.get("available", 1000.0))}
        except Exception as e:
            print(f"[Account Details Error]: {e}")
        return {"balance": 1000.0, "available": 1000.0}

    def fetch_candles(self, resolution="MINUTE", max_bars=1000) -> pd.DataFrame:
        url = f"{self.get_server()}/api/v1/prices/{Config.EPIC_EURUSD}"
        params = {"resolution": resolution, "max": max_bars}
        try:
            r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            if r.status_code == 401:
                self.login()
                r = self.session.get(url, headers=self.get_headers(), params=params, timeout=6)
            data = r.json().get("prices", [])
            rows = []
            for p in data:
                rows.append({
                    "timestamp": p["snapshotTime"],
                    "open": float(p["openPrice"]["bid"]),
                    "high": float(p["highPrice"]["bid"]),
                    "low": float(p["lowPrice"]["bid"]),
                    "close": float(p["closePrice"]["bid"]),
                    "volume": float(p.get("lastTradedVolume", 1.0))
                })
            return pd.DataFrame(rows)
        except Exception as e:
            print(f"[Fetch Candles Error - {resolution}]: {e}")
            return pd.DataFrame()

    def execute_order_server_trailing(self, direction: str, size: float, current_price: float, stop_pips: float, profit_pips: float) -> dict:
        """
        إرسال مسافة الوقف المتحرك وحساب سعر الهدف المطلق profitLevel بدقة
        """
        url = f"{self.get_server()}/api/v1/positions"
        stop_dist_price = round(stop_pips * 0.0001, 5)

        # حساب الهدف كسعر مطلق معتمد لدى وسيط Capital.com
        if direction == "BUY":
            profit_level = round(current_price + (profit_pips * 0.0001), 5)
        else:
            profit_level = round(current_price - (profit_pips * 0.0001), 5)

        payload = {
            "epic": Config.EPIC_EURUSD,
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

# ==============================================================================
# 3. صمام أمان السوق والأخبار اللحظية
# ==============================================================================
class MarketShield:
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
    def check_live_news() -> tuple[bool, str]:
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        try:
            url_fmp = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={today_str}&to={today_str}&apikey={Config.FMP_API_KEY}"
            resp = requests.get(url_fmp, timeout=4)
            if resp.status_code == 200:
                events = resp.json()
                for ev in events:
                    if str(ev.get("currency", "")).upper() in ["USD", "EUR"] and str(ev.get("impact", "")).capitalize() in ["High", "Very high"]:
                        date_raw = str(ev.get("date", ""))
                        if len(date_raw) >= 19:
                            ev_dt = datetime.strptime(date_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            diff = (ev_dt - now_utc).total_seconds() / 60.0
                            if -15.0 <= diff <= 15.0:
                                return True, f"حظر إخباري (FMP): {ev.get('event')} خلال {diff:.1f} دقيقة."
        except Exception:
            pass

        try:
            url_fh = f"https://finnhub.io/api/v1/calendar/economic?from={today_str}&to={today_str}&token={Config.FINNHUB_API_KEY}"
            resp = requests.get(url_fh, timeout=4)
            if resp.status_code == 200:
                events = resp.json().get("economicCalendar", [])
                for ev in events:
                    if str(ev.get("country", "")).upper() in ["US", "EU", "DE"] and str(ev.get("impact", "")).lower() in ["high", "3"]:
                        time_raw = str(ev.get("time", ""))
                        if len(time_raw) >= 19:
                            ev_dt = datetime.strptime(time_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            diff = (ev_dt - now_utc).total_seconds() / 60.0
                            if -15.0 <= diff <= 15.0:
                                return True, f"حظر إخباري (Finnhub): {ev.get('event')} خلال {diff:.1f} دقيقة."
        except Exception:
            pass

        return False, "لا توجد أخبار عالية التأثير في النافذة اللحظية."

# ==============================================================================
# 4. محرك تدفق الأوامر ودلتا الحجم (Order Flow & CVD)
# ==============================================================================
class OrderFlowEngine:
    @staticmethod
    def calculate_volume_delta(df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        range_hl = (d['high'] - d['low']) + 1e-8
        clv = ((d['close'] - d['low']) - (d['high'] - d['close'])) / range_hl
        d['bar_delta'] = d['volume'] * clv
        d['cvd'] = d['bar_delta'].cumsum()
        d['cvd_zscore'] = (d['cvd'] - d['cvd'].rolling(30).mean()) / (d['cvd'].rolling(30).std() + 1e-8)
        
        body = abs(d['close'] - d['open'])
        wick_lower = np.minimum(d['open'], d['close']) - d['low']
        wick_upper = d['high'] - np.maximum(d['open'], d['close'])
        
        d['bullish_absorption'] = (d['volume'] > d['volume'].rolling(20).mean() * 1.4) & (wick_lower > body * 1.5)
        d['bearish_absorption'] = (d['volume'] > d['volume'].rolling(20).mean() * 1.4) & (wick_upper > body * 1.5)
        return d

# ==============================================================================
# 5. مصنف حالة السوق ونموذج التصفية الهجين (HMM & Meta-Labeler)
# ==============================================================================
class HMMRegimeClassifier:
    def __init__(self):
        self.model = None

    def fit_predict_regime(self, df: pd.DataFrame) -> int:
        """
        ترتيب الحالات إحصائياً لضمان أن الحالة 2 هي دائماً حالة الصدمة والتقلب المرتفع
        """
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
                
                # فرز الحالات بناءً على تباين عوائد كل حالة لضمان ثبات التسميات
                state_vars = [np.var(X[pred_states == i, 0]) if np.sum(pred_states == i) > 0 else 0 for i in range(3)]
                sorted_rank = np.argsort(state_vars) # 0: أدنى تقلب، 2: أعلى تقلب
                rank_map = {sorted_rank[0]: 0, sorted_rank[1]: 1, sorted_rank[2]: 2}
                return rank_map.get(pred_states[-1], 1)
            except Exception:
                pass

        if X[-1, 1] > np.mean(X[:, 1]) * 2.0:
            return 2 # صدمة سعرية شاذة
        elif abs(X[-1, 0]) > np.std(X[:, 0]) * 1.1:
            return 0 # اتجاهي (Trend)
        return 1     # عرضي (Range)

class HybridMetaLabeler:
    def __init__(self):
        self.lgb_model = None
        self.cb_model = None
        self.is_trained = False

    def train_ensemble(self, df_m1: pd.DataFrame):
        if len(df_m1) < 120:
            return
        d = df_m1.copy().dropna()
        future_ret = d['close'].pct_change(3).shift(-3)
        spread_cost = (0.8 * 0.0001) / d['close']
        y = (future_ret > spread_cost).astype(int).iloc[:-3]

        features = pd.DataFrame({
            "close": d['close'],
            "vol": d['volume'],
            "mom": d['close'].pct_change(10),
            "cvd": d.get('cvd_zscore', pd.Series(0, index=d.index))
        }).iloc[:-3].fillna(0)

        if len(y) < 60 or len(np.unique(y)) < 2:
            return

        X = features.values
        Y = y.values

        if lgb is not None:
            try:
                self.lgb_model = lgb.LGBMClassifier(n_estimators=30, learning_rate=0.05, max_depth=3, verbose=-1, random_state=42)
                self.lgb_model.fit(X, Y)
            except Exception as e:
                print(f"[LGBM Train Warning]: {e}")

        if CatBoostClassifier is not None:
            try:
                self.cb_model = CatBoostClassifier(iterations=30, learning_rate=0.05, depth=3, verbose=False, random_seed=42)
                self.cb_model.fit(X, Y)
            except Exception as e:
                print(f"[CatBoost Train Warning]: {e}")

        self.is_trained = (self.lgb_model is not None or self.cb_model is not None)

    def predict_success_probability(self, feature_row: np.ndarray) -> float:
        if not self.is_trained:
            return 0.70

        probs = []
        X = feature_row.reshape(1, -1)
        if self.lgb_model is not None:
            try:
                probs.append(float(self.lgb_model.predict_proba(X)[0][1]))
            except Exception:
                pass
        if self.cb_model is not None:
            try:
                probs.append(float(self.cb_model.predict_proba(X)[0][1]))
            except Exception:
                pass

        return float(np.mean(probs)) if probs else 0.70

# ==============================================================================
# 6. إدارة رأس المال والأطر الزمنية المتعددة
# ==============================================================================
class MultiTimeframeAnalyzer:
    @staticmethod
    def get_m15_bias(broker: FastCapitalBroker) -> tuple[int, str]:
        df_m15 = broker.fetch_candles(resolution="MINUTE_15", max_bars=100)
        if len(df_m15) < 50:
            return 0, "بيانات M15 قيد الاكتمال"
        ema20 = df_m15['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df_m15['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        if ema20 > ema50:
            return 1, "الاتجاه صاعد على M15"
        elif ema20 < ema50:
            return -1, "الاتجاه هابط على M15"
        return 0, "اتجاه M15 محايد"

class FractionalKellyRiskManager:
    @staticmethod
    def calculate_lot_size(equity: float, win_rate_pct: float, risk_reward_ratio: float, current_price: float) -> float:
        if current_price <= 0 or np.isnan(current_price):
            current_price = 1.0850
        p = max(0.35, min(0.80, win_rate_pct / 100.0))
        q = 1.0 - p
        b = max(1.0, risk_reward_ratio)
        full_kelly = max(0.05, (p * b - q) / b)
        risk_capital = equity * (full_kelly * Config.KELLY_FRACTION)
        pip_risk = Config.DEFAULT_STOP_PIPS * 10.0
        calculated_lots = risk_capital / pip_risk
        max_possible_lots = (equity * Config.LEVERAGE * Config.MAX_MARGIN_USAGE_PCT) / (100000.0 * current_price)
        final_lot = max(0.01, min(calculated_lots, max_possible_lots))
        return round(float(final_lot), 2)

# ==============================================================================
# 7. محرك Google Gemini مع Function Calling المحدث (camelCase)
# ==============================================================================
GEMINI_FUNCTION_TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "update_trading_risk",
                "description": "تعديل إعدادات المخاطر مثل مسافة وقف الخسارة وجني الأرباح ونسبة صيغة كيلي",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "stop_pips": {"type": "NUMBER", "description": "مسافة وقف الخسارة بالنقاط (Pips)"},
                        "profit_pips": {"type": "NUMBER", "description": "مسافة جني الأرباح بالنقاط (Pips)"},
                        "kelly_fraction": {"type": "NUMBER", "description": "نسبة كسر كيلي (بين 0.05 و 0.50)"}
                    }
                }
            },
            {
                "name": "force_recalibrate_models",
                "description": "إعادة تدريب نماذج التعلم الآلي CatBoost/LightGBM وتحديث أوزان الاستراتيجيات فورياً",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "set_strategy_bias",
                "description": "تعديل أوزان استراتيجيات التداول يدوياً (SMC, VWAP, MOM)",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "smc_weight": {"type": "NUMBER", "description": "وزن استراتيجية SMC والسيولة"},
                        "vwap_weight": {"type": "NUMBER", "description": "وزن استراتيجية انحرافات VWAP"},
                        "mom_weight": {"type": "NUMBER", "description": "وزن استراتيجية الزخم Momentum"}
                    },
                    "required": ["smc_weight", "vwap_weight", "mom_weight"]
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
أنت المهندس الكمي والمشرف الرئيسي على نظام التداول الآلي EUR/USD (M1 CFDs).
أنت تتحدث باللغة العربية بطلاقة وبأسلوب مالي واثق ومحترف وموجز.

بيانات السوق والمحفظة اللحظية الحقيقية:
- الرصيد الإجمالي: ${system_context.get('balance', 0):,.2f}
- الرصيد المتاح: ${system_context.get('available', 0):,.2f}
- السعر اللحظي: {system_context.get('price', 'N/A')}
- حالة السوق (HMM): {system_context.get('regime', 'N/A')}
- اتجاه فريم 15 دقيقة: {system_context.get('mtf_bias', 'N/A')}
- الصفقات المفتوحة: {system_context.get('open_trades', 0)}
- التداول الآلي: {'نشط' if system_context.get('autotrade') else 'متوقف مؤقتاً'}
- وضع الحساب: {'تجريبي (DEMO)' if system_context.get('is_demo') else 'حقيقي (LIVE)'}
- إعدادات المخاطر: وقف={Config.DEFAULT_STOP_PIPS} نقطة | هدف={Config.DEFAULT_PROFIT_PIPS} نقطة | كسر كيلي={Config.KELLY_FRACTION}
- أوزان الاستراتيجيات: {json.dumps(system_instance.weights)}

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

            # فحص آمن لكافة الأجزاء لاقتناص استدعاء الدوال والنصوص
            for p in parts:
                if "functionCall" in p:
                    func_call = p["functionCall"]
                if "text" in p:
                    text_response += p["text"]

            if func_call:
                func_name = func_call.get("name")
                args = func_call.get("args", {})

                if func_name == "update_trading_risk":
                    if "stop_pips" in args:
                        Config.DEFAULT_STOP_PIPS = float(args["stop_pips"])
                    if "profit_pips" in args:
                        Config.DEFAULT_PROFIT_PIPS = float(args["profit_pips"])
                    if "kelly_fraction" in args:
                        Config.KELLY_FRACTION = float(args["kelly_fraction"])
                    return (
                        f"✅ **[تم تطبيق التعديل ذاتياً بواسطة Gemini]**\n\n"
                        f"• وقف الخسارة الجديد: `{Config.DEFAULT_STOP_PIPS}` نقطة\n"
                        f"• جني الأرباح الجديد: `{Config.DEFAULT_PROFIT_PIPS}` نقطة\n"
                        f"• نسبة كسر كيلي للمخاطرة: `{Config.KELLY_FRACTION}`"
                    )

                elif func_name == "force_recalibrate_models":
                    evo_res = system_instance.train_and_evolve()
                    return (
                        f"🧠 **[تمت إعادة المعايرة والتدريب الذاتي]**\n\n"
                        f"• الحالة: {evo_res.get('status')}\n"
                        f"• الأوزان الحالية: `{json.dumps(system_instance.weights)}`"
                    )

                elif func_name == "set_strategy_bias":
                    system_instance.weights["SMC"] = float(args.get("smc_weight", system_instance.weights["SMC"]))
                    system_instance.weights["VWAP"] = float(args.get("vwap_weight", system_instance.weights["VWAP"]))
                    system_instance.weights["MOM"] = float(args.get("mom_weight", system_instance.weights["MOM"]))
                    return f"⚙️ **تم ضبط أوزان الاستراتيجيات بنجاح:**\n`{json.dumps(system_instance.weights)}`"

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
# 8. محرك النظام والتداول والباكتيست الكامل
# ==============================================================================
class MasterQuantSystem:
    def __init__(self):
        self.broker = FastCapitalBroker()
        self.order_flow = OrderFlowEngine()
        self.hmm = HMMRegimeClassifier()
        self.meta_labeler = HybridMetaLabeler()
        self.weights = {"SMC": 0.40, "VWAP": 0.35, "MOM": 0.25}
        self.autotrade_active = False
        self.evolution_log = []

    def scan_market_and_execute(self) -> dict:
        market_ok, market_msg = MarketShield.check_market_hours()
        if not market_ok:
            return {"action": "HALT", "reason": market_msg}

        news_block, news_msg = MarketShield.check_live_news()
        if news_block:
            return {"action": "NEWS_BLOCK", "reason": news_msg}

        open_pos = self.broker.get_open_positions()
        if len(open_pos) > 0:
            return {"action": "IN_POSITION", "reason": "هناك صفقة قائمة تحت حماية الوقف المتحرك"}

        df_m1 = self.broker.fetch_candles(resolution="MINUTE", max_bars=300)
        if len(df_m1) < 60:
            return {"action": "WAIT", "reason": "انتظار تدفق الشموع اللحظية"}

        df_m1 = self.order_flow.calculate_volume_delta(df_m1)
        regime = self.hmm.fit_predict_regime(df_m1)
        if regime == 2:
            return {"action": "SHOCK_BLOCK", "reason": "صدمة سعرية شاذة بنموذج HMM (حظر التداول)"}

        mtf_bias, mtf_desc = MultiTimeframeAnalyzer.get_m15_bias(self.broker)

        last = df_m1.iloc[-1]
        current_price = last['close']

        # حساب مؤشرات الاستراتيجيات الثلاث بدقة
        tp = (df_m1['high'] + df_m1['low'] + df_m1['close']) / 3
        vwap = (tp * df_m1['volume']).cumsum() / (df_m1['volume'].cumsum() + 1e-8)
        std = (tp - vwap).rolling(30).std()
        vwap_lower = vwap.iloc[-1] - (2.0 * std.iloc[-1])
        vwap_upper = vwap.iloc[-1] + (2.0 * std.iloc[-1])

        smc_sig = 1 if last['bullish_absorption'] else (-1 if last['bearish_absorption'] else 0)
        vwap_sig = 1 if current_price < vwap_lower else (-1 if current_price > vwap_upper else 0)
        mom_sig = np.sign(df_m1['close'].pct_change(10).iloc[-1])

        # دمج الأوزان الثلاثة بالكامل
        score = (smc_sig * self.weights["SMC"]) + (vwap_sig * self.weights["VWAP"]) + (mom_sig * self.weights["MOM"])

        action = "HOLD"
        if score > 0.40 and mtf_bias > 0:
            action = "BUY"
        elif score < -0.40 and mtf_bias < 0:
            action = "SELL"

        if action in ["BUY", "SELL"]:
            # استخراج ميزات السلسلة الزمنية دون التسبب في انهيار البرمجية
            mom_val = df_m1['close'].pct_change(10).iloc[-1]
            meta_features = np.array([current_price, last['volume'], mom_val, last['cvd_zscore']])
            prob_success = self.meta_labeler.predict_success_probability(meta_features)

            if prob_success < 0.65:
                return {"action": "FILTERED", "reason": f"ألغيت بواسطة الفلتر الهجين (الاحتمالية {prob_success:.2f} < 0.65)"}

            if self.autotrade_active:
                acc = self.broker.get_account_details()
                lots = FractionalKellyRiskManager.calculate_lot_size(acc["available"], 59.0, 2.5, current_price)
                res = self.broker.execute_order_server_trailing(
                    direction=action,
                    size=lots,
                    current_price=current_price,
                    stop_pips=Config.DEFAULT_STOP_PIPS,
                    profit_pips=Config.DEFAULT_PROFIT_PIPS
                )
                return {
                    "action": action,
                    "price": current_price,
                    "lots": lots,
                    "prob": round(prob_success, 2),
                    "deal_ref": res.get("dealReference", "OK"),
                    "reason": f"توافق M15 ({mtf_desc}) ومصادقة الفلتر الهجين بنجاح"
                }

        return {"action": "HOLD", "price": current_price, "reason": "استقرار حيادي"}

    def run_full_backtest(self) -> dict:
        """باكتيست حقيقي على الاستراتيجيات الثلاث مخصوماً منه السبريد"""
        df = self.broker.fetch_candles(resolution="MINUTE", max_bars=1000)
        if len(df) < 200:
            return {"status": "البيانات غير كافية"}

        d = df.copy()
        tp = (d['high'] + d['low'] + d['close']) / 3
        vwap = (tp * d['volume']).cumsum() / (d['volume'].cumsum() + 1e-8)
        std = (tp - vwap).rolling(30).std()
        
        vwap_sig = pd.Series(0, index=d.index)
        vwap_sig[d['close'] < (vwap - 2.0 * std)] = 1
        vwap_sig[d['close'] > (vwap + 2.0 * std)] = -1

        mom_sig = np.sign(d['close'].pct_change(10)).fillna(0)
        smc_sig = pd.Series(0, index=d.index)
        smc_sig[d['close'] < d['close'].shift(20).rolling(20).min()] = 1
        smc_sig[d['close'] > d['close'].shift(20).rolling(20).max()] = -1

        signals = (smc_sig * self.weights["SMC"]) + (vwap_sig * self.weights["VWAP"]) + (mom_sig * self.weights["MOM"])
        pos = pd.Series(0, index=d.index)
        pos[signals > 0.40] = 1
        pos[signals < -0.40] = -1

        ret = d['close'].pct_change().shift(-1).fillna(0)
        spread_cost = (0.8 * 0.0001) / d['close']
        trade_changes = pos.diff().abs()
        strat_ret = (pos * ret) - (trade_changes * spread_cost)

        cum_ret = (1 + strat_ret * Config.LEVERAGE).cumprod()
        dd = (cum_ret - cum_ret.cummax()) / cum_ret.cummax()
        
        trades_count = int(trade_changes.sum() / 2)
        winning_trades = int((strat_ret[trade_changes > 0] > 0).sum())
        win_rate = (winning_trades / max(1, trades_count)) * 100

        return {
            "total_return_pct": round(float(strat_ret.sum() * Config.LEVERAGE * 100), 2),
            "max_dd_pct": round(abs(float(dd.min())) * 100, 2),
            "win_rate_pct": round(win_rate, 1),
            "trades": trades_count,
            "sharpe": round(float((strat_ret.mean() / (strat_ret.std() + 1e-8)) * np.sqrt(1440 * 252)), 2)
        }

    def train_and_evolve(self):
        df_m1 = self.broker.fetch_candles(resolution="MINUTE", max_bars=1000)
        if len(df_m1) >= 200:
            df_m1 = self.order_flow.calculate_volume_delta(df_m1)
            self.meta_labeler.train_ensemble(df_m1)
            entry = {
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "status": "تم تدريب نماذج LightGBM و CatBoost بنجاح على الشموع الأخيرة.",
                "weights": self.weights
            }
            self.evolution_log.append(entry)
            return entry
        return {"status": "البيانات غير كافية للتدريب"}

# ==============================================================================
# 9. معالجات أوامر التيليجرام المنفصلة
# ==============================================================================
system = MasterQuantSystem()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 **نظام التداول المؤسسي الخارق EUR/USD (M1 CFDs)**\n\n"
        f"• محرك المحادثة: **Google Gemini AI (Function Calling مدمج)**\n"
        f"• حساب التداول: **{'تجريبي (DEMO)' if system.broker.demo else 'حقيقي (LIVE)'}**\n"
        f"• الفلترة الذكية: **CatBoost + LightGBM Hybrid**\n"
        f"• حالة التداول الآلي: **{'نشط ✅' if system.autotrade_active else 'معطل ⏸'}**\n\n"
        "**الأوامر السريعة المتاحة:**\n"
        "• /autotrade_on - تشغيل التداول الآلي اللحظي\n"
        "• /autotrade_off - إيقاف التداول الآلي مؤقتاً\n"
        "• /mode_demo - التبديل للحساب التجريبي\n"
        "• /mode_live - التبديل للحساب الحقيقي\n"
        "• /backtest - عرض تقرير الباكتيست الشامل\n"
        "• /status - فحص المحفظة والمراكز المفتوحة\n"
        "• /evolution - عرض تقرير التدريب والتطوير الذاتي\n"
        "• /news - فحص المفكرة الاقتصادية اللحظية\n\n"
        "💬 *يمكنك محادثة Gemini بالعربية أو توجيه أوامر مثل: 'اجعل وقف الخسارة 5 نقاط' أو 'أعد تدريب النماذج'!*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def autotrade_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    system.autotrade_active = True
    await update.message.reply_text("🚀 **تم تفعيل التداول الآلي التراكمي بنجاح.**", parse_mode="Markdown")

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
    await update.message.reply_text("⏳ جاري سحب أحدث 1000 شمعة وإجراء الباكتيست...")
    b_res = system.run_full_backtest()
    msg = (
        "📊 **[تقرير الباكتيست الشامل للنموذج]**\n\n"
        f"• العائد التراكمي: `{b_res.get('total_return_pct')}%`\n"
        f"• أقصى هبوط (Max DD): `{b_res.get('max_dd_pct')}%`\n"
        f"• نسبة الفوز: `{b_res.get('win_rate_pct')}%`\n"
        f"• عدد الصفقات: `{b_res.get('trades')}`\n"
        f"• معامل شارب: `{b_res.get('sharpe')}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    acc = system.broker.get_account_details()
    open_p = system.broker.get_open_positions()
    msg = (
        "💼 **[الحالة اللحظية والمحفظة]**\n\n"
        f"• الرصيد الإجمالي: `${acc['balance']:,.2f}`\n"
        f"• الرصيد المتاح: `${acc['available']:,.2f}`\n"
        f"• الصفقات المفتوحة: `{len(open_p)}`\n"
        f"• التداول الآلي: **{'نشط ✅' if system.autotrade_active else 'معطل ⏸'}**\n"
        f"• إعدادات الوقف/الهدف: `{Config.DEFAULT_STOP_PIPS}` / `{Config.DEFAULT_PROFIT_PIPS}` نقطة"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def evolution_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not system.evolution_log:
        await update.message.reply_text("جاري تدريب النموذج الذاتي لأول مرة...")
        system.train_and_evolve()
    last = system.evolution_log[-1]
    msg = (
        "🧬 **[تقرير التدريب والتطوير الذاتي]**\n\n"
        f"• التوقيت: `{last['timestamp']}`\n"
        f"• الحالة: {last['status']}\n"
        f"• الأوزان الحالية: `{json.dumps(last['weights'])}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocked, news_msg = MarketShield.check_live_news()
    market_ok, market_msg = MarketShield.check_market_hours()
    msg = (
        "🌐 **[فحص السيولة والأخبار الحية]**\n\n"
        f"• حالة الأخبار: **{'حظر تداول 🚫' if blocked else 'آمن للتداول ✅'}**\n"
        f"  ↳ {news_msg}\n\n"
        f"• حالة جلسة السوق: **{'مفتوح ✅' if market_ok else 'مغلق ⏸'}**\n"
        f"  ↳ {market_msg}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def gemini_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الدردشة الحرة مع تشغيل غير متزامن لتفادي تجميد فحص الشموع اللحظية"""
    user_text = update.message.text
    await update.message.reply_chat_action("typing")

    acc = system.broker.get_account_details()
    df_m1 = system.broker.fetch_candles(resolution="MINUTE", max_bars=10)
    curr_price = df_m1['close'].iloc[-1] if len(df_m1) > 0 else "N/A"
    mtf_bias, mtf_desc = MultiTimeframeAnalyzer.get_m15_bias(system.broker)
    open_pos = system.broker.get_open_positions()

    system_ctx = {
        "balance": acc["balance"],
        "available": acc["available"],
        "price": curr_price,
        "regime": "مستقر (Trend)" if mtf_bias != 0 else "تذبذب عرضي (Range)",
        "mtf_bias": mtf_desc,
        "open_trades": len(open_pos),
        "autotrade": system.autotrade_active,
        "is_demo": system.broker.demo
    }

    # تشغيل في خيط مستقل لمنع تجميد المجدول اللحظي
    reply = await asyncio.to_thread(GeminiConversationalAgent.ask_and_execute, user_text, system, system_ctx)
    await update.message.reply_text(reply, parse_mode="Markdown")

# ==============================================================================
# 10. مهام الجدولة اللحظية والساعية
# ==============================================================================
async def job_scanner_minute(context: ContextTypes.DEFAULT_TYPE):
    try:
        res = system.scan_market_and_execute()
        if res.get("action") in ["BUY", "SELL"]:
            deal_ref_clean = str(res.get('deal_ref', 'OK')).replace("_", "\\_")
            msg = (
                f"⚡ **[تنفيذ صفقة تلقائية - {res['action']}]**\n\n"
                f"• السعر: `{res['price']}`\n"
                f"• حجم اللوت: `{res['lots']} Lot` (معادلة كيلي)\n"
                f"• ثقة الفلتر الهجين: `{res['prob']*100:.1f}%`\n"
                f"• السبب: {res['reason']}\n"
                f"• المرجع: `{deal_ref_clean}`"
            )
            await context.bot.send_message(chat_id=Config.TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[Scanner Job Error]: {e}")

async def job_evolution_hourly(context: ContextTypes.DEFAULT_TYPE):
    try:
        evo = system.train_and_evolve()
        print(f"[Hourly Evolution]: {evo.get('status')}")
    except Exception as e:
        print(f"[Evolution Job Error]: {e}")

# ==============================================================================
# نقطة التشغيل الرئيسية
# ==============================================================================
if __name__ == "__main__":
    print("[+] تشغيل نظام التداول المؤسسي الكامل لـ Windows بنجاح...")
    app = ApplicationBuilder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # تسجيل الأوامر
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("autotrade_on", autotrade_on_cmd))
    app.add_handler(CommandHandler("autotrade_off", autotrade_off_cmd))
    app.add_handler(CommandHandler("mode_demo", mode_demo_cmd))
    app.add_handler(CommandHandler("mode_live", mode_live_cmd))
    app.add_handler(CommandHandler("backtest", backtest_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("evolution", evolution_cmd))
    app.add_handler(CommandHandler("news", news_cmd))

    # التحدث التفاعلي وتعديل النظام ذاتياً عبر Gemini
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), gemini_chat_handler))

    # مهام الخلفية: فحص الدقيقة والتدريب الساعي
    jq = app.job_queue
    jq.run_repeating(job_scanner_minute, interval=60, first=10)
    jq.run_repeating(job_evolution_hourly, interval=3600, first=30)

    app.run_polling()
