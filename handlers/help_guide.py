"""Interactive Arabic help guide for the one-time welcome button."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

HOME = "📖 <b>دليل 〆 𝑿𝑬𝑳𝑽𝑶𝑹 الكامل</b>\n\nاختار القسم اللي عايز تعرف عنه. بعض الخيارات بتظهر حسب نوع الرابط أو حسابك."
SECTIONS = {
"download": "📥 <b>طريقة تنزيل المحتوى</b>\n\n1. ابعت رابطًا عامًا من منصة يدعمها البوت مثل YouTube أو TikTok أو Instagram أو Facebook أو X.\n2. انتظر تحليل الرابط وظهور معلومات المحتوى.\n3. اختار أفضل جودة أو جودة فيديو محددة أو صوت MP3 أو صورة أو ألبوم حسب الخيارات المتاحة.\n4. انتظر تجهيز الملف وإرساله. الروابط الخاصة أو المحذوفة أو التي تتطلب تسجيل دخول قد لا تعمل.",
"quality": "🎯 <b>الجودة والحجم</b>\n\nمن الإعدادات ← الجودة والحجم تقدر تختار الوضع الذكي أو 360p أو 480p أو 720p أو 1080p أو أعلى جودة متاحة. تقدر تضبط جودة الصوت وحد الحجم؛ وتظل حدود البوت وتيليجرام سارية. لو الملف كبير جرّب جودة أقل أو MP3.",
"delivery": "📦 <b>طريقة الاستلام</b>\n\nمن الإعدادات ← طريقة الاستلام اختار إرسال كفيديو أو كملف أو استخراج الصوت. الخيارات تعتمد على نوع المحتوى والمنصة.",
"manager": "⚡ <b>مدير التحميلات</b>\n\nراجع الخيارات التي يعرضها البوت لإدارة التحميل. لو ظهر زر إلغاء في جلسة التحميل، استخدمه لإيقاف الطلب. قد ينتظر الطلب في الطابور عند انشغال السيرفر.",
"history": "📚 <b>سجل التحميلات</b>\n\nافتح الإعدادات ← سجل التحميلات لمراجعة السجل وإدارته وفق الخيارات المعروضة. حذف السجل لا يعني بالضرورة حذف نسخة مخزنة مؤقتًا على مستوى البوت.",
"privacy": "🔒 <b>الخصوصية والمساحة</b>\n\nراجع خيارات الاحتفاظ بالملفات المؤقتة والسجل المتاحة في هذا القسم. لا ترسل كلمات مرور أو رموز دخول. قد تُرفض الروابط الخاصة لأسباب أمنية أو بسبب قيود المنصة.",
"notifications": "🔔 <b>الإشعارات</b>\n\nمن الإعدادات ← الإشعارات راجع خيارات إشعار اكتمال التحميل والإشعارات الأخرى المتاحة لحسابك.",
"plans": "📊 <b>الخطط والحدود</b>\n\nقد تختلف حدود عدد الروابط في الدفعة الواحدة والمزايا حسب الخطة ونوع النسخة. راجع «الخطط والحدود» إذا ظهر لك الخيار. قد توجد حدود زمنية وحجمية وقيود من المنصة.",
"referral": "🎁 <b>الدعوات والمكافآت</b>\n\nإذا ظهر خيار «اكسب نسختك من البوت»، افتحه لعرض حالة الدعوات وشروط التأهل. الدعوة وحدها لا تضمن المكافأة؛ تطبق شروط النظام.",
"troubleshoot": "🛠️ <b>حل المشكلات</b>\n\n• رابط غير صالح: انسخ الرابط الأصلي كاملًا.\n• محتوى خاص أو محذوف: جرّب رابطًا عامًا.\n• الملف كبير: اختار جودة أقل أو MP3.\n• السيرفر مشغول: انتظر قليلًا.\n• فشل مؤقت: أعد المحاولة لاحقًا أو جرّب رابطًا آخر. تجنب تكرار الطلب بسرعة."
}
LABELS = [("📥 تنزيل المحتوى","download"),("🎯 الجودة والحجم","quality"),("📦 طريقة الاستلام","delivery"),("⚡ مدير التحميلات","manager"),("📚 سجل التحميلات","history"),("🔒 الخصوصية والمساحة","privacy"),("🔔 الإشعارات","notifications"),("📊 الخطط والحدود","plans"),("🎁 الدعوات والمكافآت","referral"),("🛠️ حل المشكلات","troubleshoot")]

def _home_markup(uid):
    rows = [[InlineKeyboardButton(label, callback_data=f"guide:section:{key}:{uid}")] for label,key in LABELS]
    rows.append([InlineKeyboardButton("✖️ إغلاق الدليل", callback_data=f"guide:close:{uid}")])
    return InlineKeyboardMarkup(rows)

def help_guide_markup(user_id: int):
    return InlineKeyboardMarkup([[InlineKeyboardButton("📖 شرح البوت بالكامل", callback_data=f"guide:home:{int(user_id)}")]])

async def help_guide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3 or not parts[-1].isdigit():
        await query.answer("انتهت صلاحية هذا الزر.", show_alert=True)
        return
    uid = int(parts[-1])
    if query.from_user.id != uid:
        await query.answer("الدليل ده اتفتح لحساب مستخدم آخر.", show_alert=True)
        return
    await query.answer()
    action = parts[1]
    if action == "close":
        await query.edit_message_text("تم إغلاق دليل الشرح. تقدر تستخدم أزرار البوت في أي وقت.")
    elif action == "section" and len(parts) == 4 and parts[2] in SECTIONS:
        await query.edit_message_text(SECTIONS[parts[2]], parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📚 كل أقسام الشرح", callback_data=f"guide:home:{uid}")],[InlineKeyboardButton("✖️ إغلاق الدليل", callback_data=f"guide:close:{uid}")]]), disable_web_page_preview=True)
    else:
        await query.edit_message_text(HOME, parse_mode="HTML", reply_markup=_home_markup(uid), disable_web_page_preview=True)
