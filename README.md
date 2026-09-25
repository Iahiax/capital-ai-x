# capital-ai-x

pip install requests pandas numpy python-telegram-bot[job-queue] lightgbm catboost hmmlearn

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
