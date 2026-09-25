# capital-ai-x

pip install requests pandas numpy python-telegram-bot[job-queue] lightgbm catboost hmmlearn

pip install requests urllib3 pandas numpy matplotlib python-telegram-bot[job-queue] lightgbm catboost hmmlearn

pip install duckdb psutil

pip install requests urllib3 numpy pandas scipy scikit-learn matplotlib duckdb psutil "python-telegram-bot[job-queue]" lightgbm catboost hmmlearn


الطريقة الأولى (الأفضل والأسهل): استخدام NSSM (Non-Sucking Service Manager)
هذه الأداة تحول أي سكربت بايثون إلى خدمة رسمية في ويندوز (Windows Service).

1- قم بتحميل أداة NSSM (ملف مضغوط بحجم صغير جداً) من موقعها الرسمي وافتحه.

2- انقل ملف ⁠nssm.exe⁠ إلى مجلد مشروع البوت.

3- افتح موجه الأوامر (CMD) كمسؤول (Run as Administrator) في مجلد المشروع، ثم اكتب :
nssm install QuantTraderBot

4- ستظهر لك نافذة إعدادات مرئية:
• Path: مسار مفسر البايثون في جهازك (مثلاً: ⁠C:\Users\YourUser\AppData\Local\Programs\Python\Python311\python.exe⁠).
• Startup directory: مجلد المشروع الذي يحتوي على الكود.
Arguments: اسم ملف الكود x.py

5- اضغط على Install service.
لتشغيل البوت كخدمة خلفية مستمرة:
nssm start QuantTraderBot
ستعمل الخدمة حتى لو سجلت الخروج من ويندوز، وستعيد تشغيل السكربت فوراً إذا حدث أي انهيار (Crash).



جميع النماذج الـ 22 الرياضية وإجراءات إدارة المخاطر موجودة وتعمل بنفس المعادلات:
 نماذج التقلب والتنبؤ: ⁠GARCH(1,1)⁠, ⁠Kalman Filter⁠, ⁠Rolling Hurst⁠, ⁠Ornstein-Uhlenbeck Half-Life⁠.
 خوارزميات التعلم الآلي: ⁠CatBoost⁠, ⁠LightGBM⁠, ⁠HMM Regimes⁠, ⁠Conformal Prediction⁠, ⁠Triple-Barrier Labeling⁠.
 تدفق الأوامر والسيولة: ⁠CVD Divergence⁠, ⁠EQH/EQL Sweeps⁠, ⁠Asian Range⁠, ⁠FVG⁠, ⁠Net Liquidity Void Ratio⁠, ⁠Glosten-Harris⁠.
 الأمان والأنظمة: ⁠Windows Affinity⁠, ⁠Crash Dump⁠, ⁠Watchdog⁠, ⁠Risk Parity⁠, ⁠CVaR 99%⁠, ⁠Dynamic Beta⁠.
الكود الآن أكثر إحكاماً وسرعة في زمن التنفيذ اللحظي (Execution Latency)، ويستهلك قدراً أقل من الذاكرة في Windows.


كل سطر وفكرة برمجتها اليوم موجودة وتعمل 100%:
1. صمامات الأمان والسيولة الكلية (Market Shields & Macro)
 حظر إغلاق عطلة نهاية الأسبوع: (⁠MarketShield.check_market_hours⁠) — حظر التداول يوم الجمعة بعد 20:00 UTC، وطوال السبت، وحتى 21:00 UTC من مساء الأحد.
 حظر تثبيت لندن 4PM Fix: (⁠MarketShield.check_london_fix_window⁠) — تجميد التداول بين 15:55 و 16:05 UTC لتفادي إعادة موازنة المؤسسات الكبرى.
 حماية رسوم التبييت (Overnight Swaps): (⁠MarketShield.check_overnight_swap_window⁠) — منع فتح صفقات جديدة بين 21:30 و 22:15 UTC.
 إغلاق صفقات التبييت الرابحة: (⁠enforce_swap_closure_if_needed⁠) — إغلاق أي مركز رابح تلقائياً عند الساعة 21:50 UTC لتفادي اقتطاع رسوم الفائدة الليلية.
 المفكرة الاقتصادية المزدوجة (Live News Filter): (⁠MarketShield.check_live_news⁠) — ربط حي مع كل من FMP API و Finnhub API لحظر التداول قبل وبعد الأخبار القوية بـ 15 دقيقة لعملات: USD, EUR, GBP, JPY, AUD.
 صدمة الارتباط الجماعي للسلة (Cross-Asset Macro Shock): (⁠CrossAssetMacroShockFilter.detect_macro_shock⁠) — قياس مصفوفة الارتباط اللحظي، وإذا تجاوز متوسط ارتباط العملات 0.85 يتم تعليق التداول فوراً.
 انتقال التقلب العابر للأصول (Volatility Spillover): (⁠MarketShield.check_volatility_spillover⁠) — رصد انفجار التقلب في أي زوج (تجاوز متوسطه بـ 2.2 ضعف) وتجميد باقي السلة استباقياً.
2. بنية السوق الدقيقة، تدفق الأوامر، والسيولة (Microstructure & SMC)
 عجز السيولة الصافي (Net Liquidity Void Ratio - NLVR): (⁠MarketMicrostructureEngine.calculate_net_liquidity_void_ratio⁠) — حظر الدخول عند شمعات الاندفاع الماروبوزو حتى يتم ملء 50% من فراغ السيولة.
 مرشح السبريد المحقق (Glosten-Harris): (⁠MarketMicrostructureEngine.check_glosten_harris_adverse_selection⁠) — حظر الصفقات إذا تضخم السبريد بنسبة تفوق 70% نتيجة ضغط صانع السوق.
 طفرات الحجم اللحظية (Micro-Volume Bursts): (⁠MarketMicrostructureEngine.detect_micro_volume_burst⁠) — مضاعفة الزخم فوراً إذا تدفق حجم أكبر من 2.5 ضعف المتوسط.
 الانزلاق غير المتماثل (Asymmetric Slippage Cap): (⁠MarketMicrostructureEngine.get_asymmetric_slippage_cap⁠) — سقف انزلاق مخصص ومشدد لزوج GBPUSD مقارنة بباقي الأزواج.
 حماية ما قبل التنفيذ (Pre-Trade Slippage): فصل أسعار العرض والطلب بدقة (⁠Ask vs Ask⁠ و ⁠Bid vs Bid⁠) لرفض أي صفقة يتحرك سعرها قبل وصول الأمر للوسيط.
 دلتا الحجم التراكمي (CVD & CLV): (⁠OrderFlowEngine.calculate_volume_delta⁠) — احتساب أحجام الشراء والبيع الحقيقية وتوليد مؤشر Z-score اللحظي.
 امتصاص الذيول السعرية (Absorption Wicks): رصد ذيول الامتصاص الشرائي والبيعي المتزامنة مع تدفق أحجام تفوق 1.4 ضعف المتوسط.
 دايفرجنس دلتا الحجم (CVD Divergence): (⁠OrderFlowEngine.detect_cvd_divergence⁠) — رصد انعكاس الاتجاه عندما يصنع السعر قاعاً جديداً بينما تسجل الدلتا قاعاً أعلى.
 سمية تدفق الأوامر (Order Flow Toxicity): (⁠OrderFlowEngine.check_order_flow_toxicity⁠) — منع الدخول إذا كانت السيولة المتدفقة في الشمعتين السابقتين بنسبة 70% في الاتجاه المعاكس لصفقتك.
 مصيدة القمم والقيعان المتساوية (EQH / EQL Sweep): (⁠OrderFlowEngine.detect_eqh_eql_sweep⁠) — كشف اصطياد السيولة فوق القمم المتطابقة مع شرط وجود 3 شمعات فاصلة على الأقل.
 مصيدة سيولة الجلسة الآسيوية (Asian Sweep): (⁠OrderFlowEngine.detect_asian_liquidity_sweep⁠) — استخراج أعلى وأدنى سعر لمدى آسيا من DuckDB ورصد كسره الكاذب بين 07:00 و 16:00 UTC.
 فجوات القيمة العادلة (FVG Confluence): (⁠OrderFlowEngine.detect_fvg_confluence⁠) — كشف مناطق عدم التوازن السعري (Imbalance) وإعطاء وزن تفضيلي للصفقة عند إعادة اختبارها.
3. النماذج الرياضية والإحصائية المتقدمة (Advanced Quant Math)
 تنقية المويجات الرياضية (Haar Wavelet Denoising): (⁠AdvancedQuantMath.haar_wavelet_denoise⁠) — إزالة ضوضاء فريم الدقيقة عالية التردد قبل تقديم الشارت للرؤية البصرية.
 التفاضل الكسري (Fractional Differentiation d=0.4): (⁠AdvancedQuantMath.fractional_differentiation⁠) — تحقيق استقرار السلاسل الزمنية مع الاحتفاظ الكامل بذاكرة الأسعار التاريخية.
 مرشح كالمان لتقدير السرعة والاتجاه (Kalman Filter): (⁠AdvancedQuantMath.kalman_filter_price_velocity⁠) — تقدير زخم السعر بدون أي تأخير زمني كلاسيكي (Zero-Lag Velocity).
 مؤشر هيرست ونسبة التباين (Rolling Hurst Exponent): (⁠AdvancedQuantMath.calculate_rolling_hurst⁠) — فحص السوق واستبعاد الضوضاء البراونية العشوائية (0.47 \le H \le 0.53).
 عمر نصف أورنشتاين-أولينبيك (Ornstein-Uhlenbeck Half-Life): (⁠AdvancedQuantMath.calculate_ou_half_life⁠) — حساب الزمن المتوقع لعودة السعر إلى خط الـ VWAP وفرض خروج زمني إجباري إذا عجز عن الارتداد.
 التنبؤ بالتقلب المسبق (Recursive GARCH(1,1)): (⁠AdvancedQuantMath.predict_garch_volatility⁠) — استهداف التباين (Variance Targeting) والتنبؤ بمدى تقلب الشمعة القادمة لضبط أبعاد الوقف.
 التحليل متعدد الأطر الزمنية (M15 Bias from DuckDB): (⁠MultiTimeframeAnalyzer.get_m15_bias_from_duckdb⁠) — تجميع شمعات M1 لحظياً لتحديد اتجاه EMA20/EMA50 على فريم M15.
4. الذكاء الاصطناعي، التعلم الآلي، والمصادقة (ML & AI)
 تصنيف بيئات السوق عبر نماذج ماركوف المخفية (Gaussian HMM): (⁠HMMRegimeClassifier.fit_predict_regime⁠) — تصنيف السوق إلى 3 حالات، وحظر التداول عند رصد بيئة الاضطراب الحاد (State 2).
 طريقة الحواجز الثلاثية للوسم (Triple-Barrier Labeling): (⁠AdvancedQuantMath.triple_barrier_labeling⁠) — تدريب النماذج على ملامسة الأهداف الحقيقية رأسياً وأفقياً.
 التدريب النظيف والعزل الزمني (Purged CV + Embargo): (⁠HybridMetaLabeler.train_ensemble_with_purged_cv⁠) — تدريب مشترك لكل من LightGBM و CatBoost مع منع تسريب البيانات المستقبلية.
 مرشح استقرار التوزيع الإحصائي (PSI Drift): (⁠PopulationStabilityIndex.calculate_psi⁠) — تجميد تحديث الأوزان إذا تغير التوزيع الإحصائي للسوق بأكثر من 0.25 لمنع فرط التخصيص.
 التنبؤ الإحصائي المطابق (Conformal Prediction Sets): (⁠HybridMetaLabeler.predict_conformal_probability⁠) — رفض أي صفقة لا تحظى بنسبة ثقة إحصائية مؤكدة بنسبة 90%.
 نظام التداول الخفي للمصادقة المسبقة (Canary Shadow Trading): (⁠CanaryShadowTester⁠) — اختبار أداء النماذج في الذاكرة بوضع الظل لمدة 30 دقيقة قبل اعتمادها الفعلي.
 التكامل مع Kyma AI (Chat & Vision):
 محادثة تفاعلية واستدعاء أدوات التحكم في المخاطر وحالة النظام باللغة العربية (⁠KymaConversationalAgent⁠).
 مصادقة بصرية بالذكاء الاصطناعي لـ 40 شمعة مع خط الـ VWAP عبر الرؤية الحاسوبية (⁠KymaChartVisionValidator⁠).
5. إدارة المحفظة، المخاطر، والتنفيذ (Risk & Execution)
 مؤشر الدولار التجميعي اللحظي (Synthetic DXY): (⁠SyntheticDXYEngine.get_synthetic_dxy_trend⁠) — دمج أزواج EUR, JPY, GBP, AUD لحظياً بأوزانها الهندسية لاشتراط توافق سيولة الدولار.
 مصفوفة القوة النسبية للعملات: (⁠CurrencyStrengthMatrix.evaluate_currency_strength⁠) — فحص تمايز القوة بين طرفي الزوج لمنع التداول العكسي.
 تحييد بيتا الدولار الفعلي (Dynamic USD Beta): (⁠CurrencyCorrelationManager.calculate_usd_beta⁠) — قياس حساسية الزوج مقابل الدولار واستبعاد الحساسيات المتطرفة (> 2.5).
 قاطع الارتباط الاتجاهي للدولار: (⁠CurrencyCorrelationManager.can_open_position⁠) — منع فتح صفقتين متزامنتين تعتمدان على نفس اتجاه الدولار.
 سقف أزواج العملات المنفردة: قصر التداول على زوجين مختلفين في نفس الوقت.
 قاطع الهبوط اليومي (Daily Circuit Breaker 3%): إيقاف التداول آلياً إذا بلغت خسائر اليوم 3% من رصيد البداية.
 التحكم في مدة التراجع (Time-Under-Water): تجميد كسر كيلي إذا بقي الرصيد أدنى من أعلى قمة مسجلة لأكثر من 4 ساعات.
 فترة التهدئة الإلزامية (Cooldown): حظر التداول لمدة 20 دقيقة عند تسجيل خسارتين متتاليتين.
 أوزان الجلسات المتغيرة: ضبط مضاعفات الوقف والأهداف ديناميكياً وفق الجلسة (آسيا: ارتدادي، لندن: اختراق توسعي، نيويورك: استمراري).
 تكافؤ المخاطر للسلة (Risk Parity): موازنة أحجام العقود بحيث لا يطغى الزوج الأعلى تذبذباً على حسابك.
 القيمة المعرضة للخطر المشروطة (CVaR 99% / Expected Shortfall): احتساب الخسارة الذيلية المتطرفة للحساب.
 صيغة كيلي البايزية المتدحرجة (Bayesian Rolling Kelly): تعديل حجم العقود لحظياً بناءً على نسبة الفوز التاريخية ومعدل الربح للخسارة، مع تقليص الحجم عند بطء اتصال السيرفر.
 عقود التجزئة المؤسسية (Child Orders Execution): فتح عقدين عند الدخول لضمان إغلاق نصف العقد عند 1.5 ATR بدقة، وترك النصف الآخر لتحقيق 3.0 ATR بالوقف المتحرك.
 تأمين الدخول (Risk-Free Breakeven): نقل الوقف لنقطة الدخول فور وصول الأرباح إلى 1.0 ATR مع احتساب تكلفة السبريد.
 الوقف الهيكلي المتحرك (Structural Swing-Trail): زحف الوقف خلف قاع أو قمة الشمعة السابقة مع اشتراط عدم التراجع خلف نقطة الدخول أبداً.
6. استقرار بيئة Windows وقاعدة البيانات (Infrastructure & Database)
 تخصيص أنوية المعالج ورفع الأولوية: (⁠tune_windows_process_affinity_and_priority⁠) — ضبط أولوية العملية إلى ⁠HIGH_PRIORITY_CLASS⁠ في Windows وتوزيعها على كافة مسارات المعالج الفيزيائية.
 مؤقت اليقظة والتعافي الذاتي الداخلي: (⁠InternalSystemWatchdog⁠) — فحص دورة الأحداث كل 10 ثوانٍ وإعادة إقلاع الخدمة عبر NSSM إذا تجمدت 180 ثانية.
 التفريغ الذاتي للذاكرة عند الانهيار (Crash Dump): إنشاء تقرير ⁠crash_dump.json⁠ فور وقوع أي استثناء حرج لتحديد سببه فوراً.
 قفل الأوامر المتزامن (Execution Mutex): فرض فاصل زمني (1.5 ثانية) بين الأوامر لتفادي مخالفة حدود طلبات الوسيط (Rate Limits).
 مستودع البيانات DuckDB المقفل متزامناً: حفظ الشموع، الصفقات المغلقة، ومقاييس TCA مع قفل خيوط المعالجة ⁠RLock⁠، وصيانة تلقائية (Checkpoint & Vacuum) عند منتصف الليل.
 شاشة مراقبة حية ملونة في التيرمينال (Live Terminal Logger): تصنيف الأحداث فورا بالألوان (⁠[SCAN]⁠, ⁠[FILTER]⁠, ⁠[SUCCESS]⁠, ⁠[ERROR]⁠).
 موجه تيليجرام الموحد (Universal Router): الاستجابة لجميع الأوامر والمحادثات في الخاص والمجموعات والقنوات مع حماية ضد انهيار التنسيق ومعالجة تعارض الاتصال المزدوج.
كل هذه المنظومات مدمجة ومترابطة في الكود الأخير، ولا يوجد أي تفصيل تم حذفه أو التنازل عنه. المنظومة جاهزة للعمل في السوق الحقيقي.


الذكاء الاصطناعي المرتبط kyma api نموذج : llama-3.3-70b
