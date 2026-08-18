"""
Branded Email Templates for Township 311 System

Every resident-facing message -- confirmation, status update, staff comment --
is described here as content and rendered by `email_layout`, which owns the
document, the palette and the plain-text half. Nothing in this module writes
markup, and nothing in it escapes its own arguments: the layout's block
helpers do that once, and escaping twice is how an apostrophe reaches an inbox
as `&amp;#39;`.

Translations for the strings live in EMAIL_I18N below, falling back to the
Translate API for anything not held statically.
"""
from typing import Optional, Dict

from app.services import email_layout as L


def _safe_url(value: Optional[str]) -> Optional[str]:
    """Only allow http(s) URLs in href/src attributes; drop anything else
    (javascript:, data:, etc.) to prevent attribute breakout."""
    if not value:
        return None
    v = str(value).strip()
    return v if v.lower().startswith(("http://", "https://")) else None


# Email template translations for common languages
EMAIL_I18N = {
    "en": {
        # Common
        "service_portal": "311 Service Portal",
        "request_id": "Request ID",
        "category": "Category",
        "description": "Description",
        "location": "Location",
        "submitted": "Submitted",
        "no_reply": "Please do not reply directly to this email.",

        # Confirmation email
        "request_received": "Request received",
        "track_request": "Track your request",
        "thank_you": "Thank you for helping make {township} a better place.",
        "subject_received": "Request #{id} received - {township}",

        # Status update email
        "current_status": "Current status",
        "resolution_notes": "Resolution notes",
        "completion_photo": "Completion photo",
        "view_details": "View request details",
        "subject_status": "Request #{id} status: {status} - {township}",
        "status_open": "Open",
        "status_in_progress": "In progress",
        "status_closed": "Resolved",

        # Comment email
        "message_from": "Message from {author}",
        "view_conversation": "View the full conversation",
        "receiving_because": "You are receiving this because you submitted a request to {township}.",
        "subject_comment": "New update on request #{id} - {township}",

        # SMS - confirmation
        "sms_received": "Your request has been received.",
        "sms_ref": "Ref",
        "sms_track": "Track",

        # SMS - status update
        "sms_being_reviewed": "Your request is being reviewed.",
        "sms_being_worked": "Your request is being worked on.",
        "sms_resolved": "Your request has been resolved.",
        "sms_details": "Details",
    },
    "es": {
        # Common
        "service_portal": "Portal de Servicios 311",
        "request_id": "ID de solicitud",
        "category": "Categoría",
        "description": "Descripción",
        "location": "Ubicación",
        "submitted": "Enviada",
        "no_reply": "Por favor no responda directamente a este correo.",

        # Confirmation email
        "request_received": "Solicitud recibida",
        "track_request": "Siga su solicitud",
        "thank_you": "Gracias por ayudar a hacer de {township} un lugar mejor.",
        "subject_received": "Solicitud #{id} recibida - {township}",

        # Status update email
        "current_status": "Estado actual",
        "resolution_notes": "Notas de resolución",
        "completion_photo": "Foto de finalización",
        "view_details": "Ver los detalles de la solicitud",
        "subject_status": "Solicitud #{id} estado: {status} - {township}",
        "status_open": "Abierta",
        "status_in_progress": "En progreso",
        "status_closed": "Resuelta",

        # Comment email
        "message_from": "Mensaje de {author}",
        "view_conversation": "Ver la conversación completa",
        "receiving_because": "Recibe esto porque envió una solicitud a {township}.",
        "subject_comment": "Nueva actualización en la solicitud #{id} - {township}",

        # SMS
        "sms_received": "Su solicitud ha sido recibida.",
        "sms_ref": "Ref",
        "sms_track": "Seguir",
        "sms_being_reviewed": "Su solicitud está siendo revisada.",
        "sms_being_worked": "Se está trabajando en su solicitud.",
        "sms_resolved": "Su solicitud ha sido resuelta.",
        "sms_details": "Detalles",
    },
    "zh": {
        "service_portal": "311服务门户",
        "request_received": "请求已收到。",
        "report_submitted": "您的报告已成功提交",
        "request_id": "请求编号",
        "category": "类别",
        "description": "描述",
        "location": "位置",
        "track_request": "追踪您的请求",
        "or_visit": "或访问",
        "thank_you": "感谢您帮助让{township}变得更好。",
        "no_reply": "请勿直接回复此邮件。",
        "subject_received": "请求 #{id} 已收到 - {township}",
        "subject_update": "请求 #{id} 更新 - {township}",
        "status_updated": "状态已更新",
        "your_request_status": "您的请求状态已更新",
        "new_status": "新状态",
        "message_from_staff": "工作人员留言",
        "request_details": "请求详情",
    },
    "hi": {
        # Common
        "service_portal": "311 सेवा पोर्टल",
        "request_id": "अनुरोध आईडी",
        "category": "श्रेणी",
        "description": "विवरण",
        "location": "स्थान",
        "or_visit": "या देखें",
        "no_reply": "कृपया इस ईमेल का सीधे जवाब न दें।",
        
        # Confirmation email
        "request_received": "अनुरोध प्राप्त",
        "report_submitted": "आपकी रिपोर्ट सफलतापूर्वक जमा की गई है",
        "track_request": "अपना अनुरोध ट्रैक करें",
        "thank_you": "{township} को बेहतर बनाने में मदद के लिए धन्यवाद",
        "subject_received": "अनुरोध #{id} प्राप्त - {township}",
        
        # Status update email
        "status_update": "स्थिति अपडेट",
        "your_request_status": "आपके अनुरोध की स्थिति अपडेट की गई है",
        "current_status": "वर्तमान स्थिति",
        "resolution_notes": "समाधान नोट्स",
        "completion_photo": "पूर्णता फोटो",
        "view_details": "अनुरोध विवरण देखें",
        "subject_status": "अनुरोध #{id} स्थिति: {status} - {township}",
        "status_open": "खुला",
        "status_in_progress": "कार्य प्रगति पर है",
        "status_closed": "समाधित",
        
        # Comment email
        "new_update": "आपके अनुरोध पर नया अपडेट",
        "staff_member": "स्टाफ सदस्य",
        "view_conversation": "पूर्ण वार्तालाप देखें",
        "receiving_because": "आप यह प्राप्त कर रहे हैं क्योंकि आपने {township} को एक अनुरोध प्रस्तुत किया था।",
        "subject_comment": "अनुरोध #{id} पर नया अपडेट - {township}",
        
        # SMS
        "sms_received": "आपका अनुरोध प्राप्त हुआ",
        "sms_ref": "संदर्भ",
        "sms_track": "ट्रैक करें",
        "sms_being_reviewed": "समीक्षा की जा रही है",
        "sms_being_worked": "काम किया जा रहा है",
        "sms_resolved": "समाधित हो गया है",
        "sms_details": "विवरण",
    },
    "ko": {
        "service_portal": "311 서비스 포털",
        "request_received": "요청이 접수되었습니다",
        "report_submitted": "귀하의 신고가 성공적으로 제출되었습니다",
        "request_id": "요청 ID",
        "category": "카테고리",
        "description": "설명",
        "location": "위치",
        "track_request": "요청 추적",
        "or_visit": "또는 방문",
        "thank_you": "{township}를 더 나은 곳으로 만드는 데 도움을 주셔서 감사합니다",
        "no_reply": "이 이메일에 직접 회신하지 마십시오.",
        "subject_received": "요청 #{id} 접수 - {township}",
        "subject_update": "요청 #{id} 업데이트 - {township}",
        "status_updated": "상태 업데이트",
        "your_request_status": "귀하의 요청 상태가 업데이트되었습니다",
        "new_status": "새 상태",
        "message_from_staff": "직원 메시지",
        "request_details": "요청 세부정보",
    },
    "ar": {
        "service_portal": "بوابة خدمة 311",
        "request_received": "تم استلام الطلب",
        "report_submitted": "تم إرسال تقريرك بنجاح",
        "request_id": "رقم الطلب",
        "category": "الفئة",
        "description": "الوصف",
        "location": "الموقع",
        "track_request": "تتبع طلبك",
        "or_visit": "أو قم بزيارة",
        "thank_you": "شكرًا لمساعدتك في جعل {township} مكانًا أفضل",
        "no_reply": "يرجى عدم الرد مباشرة على هذا البريد الإلكتروني.",
        "subject_received": "تم استلام الطلب #{id} - {township}",
        "subject_update": "تحديث على الطلب #{id} - {township}",
        "status_updated": "تم تحديث الحالة",
        "your_request_status": "تم تحديث حالة طلبك",
        "new_status": "الحالة الجديدة",
        "message_from_staff": "رسالة من الموظفين",
        "request_details": "تفاصيل الطلب",
    },
    "fr": {
        "service_portal": "Portail de Service 311",
        "request_received": "Demande Reçue",
        "report_submitted": "Votre signalement a été soumis avec succès",
        "request_id": "Numéro de Demande",
        "category": "Catégorie",
        "description": "Description",
        "location": "Emplacement",
        "track_request": "Suivre Votre Demande",
        "or_visit": "ou visitez",
        "thank_you": "Merci de contribuer à améliorer {township}",
        "no_reply": "Veuillez ne pas répondre directement à cet email.",
        "subject_received": "Demande #{id} Reçue - {township}",
        "subject_update": "Mise à jour de la Demande #{id} - {township}",
        "status_updated": "Statut Mis à Jour",
        "your_request_status": "Le statut de votre demande a été mis à jour",
        "new_status": "Nouveau Statut",
        "message_from_staff": "Message du personnel",
        "request_details": "Détails de la Demande",
    },
    "pt": {
        "service_portal": "Portal de Serviços 311",
        "request_received": "Solicitação Recebida",
        "report_submitted": "Seu relato foi enviado com sucesso",
        "request_id": "ID da Solicitação",
        "category": "Categoria",
        "description": "Descrição",
        "location": "Localização",
        "track_request": "Acompanhe Sua Solicitação",
        "or_visit": "ou visite",
        "thank_you": "Obrigado por ajudar a tornar {township} um lugar melhor",
        "no_reply": "Por favor, não responda diretamente a este email.",
        "subject_received": "Solicitação #{id} Recebida - {township}",
        "subject_update": "Atualização da Solicitação #{id} - {township}",
        "status_updated": "Status Atualizado",
        "your_request_status": "O status da sua solicitação foi atualizado",
        "new_status": "Novo Status",
        "message_from_staff": "Mensagem da equipe",
        "request_details": "Detalhes da Solicitação",
    },
    "ja": {
        "service_portal": "311サービスポータル",
        "request_received": "リクエストを受け付けました。",
        "report_submitted": "レポートは正常に送信されました",
        "request_id": "リクエストID",
        "category": "カテゴリ",
        "description": "説明",
        "location": "場所",
        "track_request": "リクエストを追跡",
        "or_visit": "または訪問",
        "thank_you": "{township}をより良い場所にするためにご協力いただきありがとうございます。",
        "no_reply": "このメールに直接返信しないでください。",
        "subject_received": "リクエスト #{id} 受付 - {township}",
        "subject_update": "リクエスト #{id} 更新 - {township}",
        "status_updated": "ステータス更新",
        "your_request_status": "リクエストのステータスが更新されました",
        "new_status": "新しいステータス",
        "message_from_staff": "スタッフからのメッセージ",
        "request_details": "リクエスト詳細",
    },
    "vi": {
        "service_portal": "Cổng Dịch vụ 311",
        "request_received": "Yêu Cầu Đã Nhận",
        "report_submitted": "Báo cáo của bạn đã được gửi thành công",
        "request_id": "Mã Yêu Cầu",
        "category": "Danh Mục",
        "description": "Mô Tả",
        "location": "Địa Điểm",
        "track_request": "Theo Dõi Yêu Cầu",
        "or_visit": "hoặc truy cập",
        "thank_you": "Cảm ơn bạn đã giúp {township} trở nên tốt đẹp hơn",
        "no_reply": "Vui lòng không trả lời trực tiếp email này.",
        "subject_received": "Yêu cầu #{id} Đã Nhận - {township}",
        "subject_update": "Cập nhật Yêu cầu #{id} - {township}",
        "status_updated": "Trạng Thái Đã Cập Nhật",
        "your_request_status": "Trạng thái yêu cầu của bạn đã được cập nhật",
        "new_status": "Trạng Thái Mới",
        "message_from_staff": "Tin nhắn từ nhân viên",
        "request_details": "Chi Tiết Yêu Cầu",
    },
}

# Cache for translated email strings: {(key, lang): translated_value}
_email_translation_cache: Dict[tuple, str] = {}


async def get_i18n_async(lang: str) -> Dict[str, str]:
    """
    Get i18n strings for a language.
    1. First checks static dictionary for pre-translated common languages
    2. Falls back to Google Translate API with caching for all other 130+ languages
    """
    import re
    
    # English - use as-is
    if lang == "en":
        return dict(EMAIL_I18N["en"])

    # A hand-written dictionary is authoritative for the keys it holds, but
    # several of them were written before half these strings existed. Falling
    # back to English for the rest is how a Korean resident got a Korean
    # heading over an English button; the missing keys are translated below
    # and cached like any other language.
    static = EMAIL_I18N.get(lang, {})
    missing = [k for k in EMAIL_I18N["en"] if k not in static]
    if lang in EMAIL_I18N and not missing:
        return dict(static)
    
    # For other languages, translate using Google Translate API with caching
    from app.services.translation import translate_text
    
    # Helper function to protect placeholders from translation
    def protect_placeholders(text: str) -> tuple:
        """Replace {placeholder} with numbered tokens to protect from translation."""
        placeholders = re.findall(r'\{[a-zA-Z_]+\}', text)
        protected = text
        for i, ph in enumerate(placeholders):
            protected = protected.replace(ph, f'[[PH{i}]]', 1)
        return protected, placeholders
    
    def restore_placeholders(text: str, placeholders: list) -> str:
        """Restore placeholders after translation."""
        restored = text
        for i, ph in enumerate(placeholders):
            restored = restored.replace(f'[[PH{i}]]', ph)
            # Also handle cases where Google adds spaces: [[ PH0 ]]
            restored = restored.replace(f'[[ PH{i} ]]', ph)
            restored = restored.replace(f'[[PH{i} ]]', ph)
            restored = restored.replace(f'[[ PH{i}]]', ph)
        return restored
    
    translated = dict(static)

    for key in (missing if static else list(EMAIL_I18N["en"])):
        english_value = EMAIL_I18N["en"][key]
        cache_key = (key, lang)
        
        # Check cache first
        if cache_key in _email_translation_cache:
            translated[key] = _email_translation_cache[cache_key]
            continue
        
        # Translate via Google Translate API
        try:
            # Protect placeholders before translation
            protected_text, placeholders = protect_placeholders(english_value)
            
            result = await translate_text(protected_text, "en", lang)
            if result:
                # Restore placeholders after translation
                final_result = restore_placeholders(result, placeholders)
                translated[key] = final_result
                _email_translation_cache[cache_key] = final_result
            else:
                # Fallback to English if translation fails
                translated[key] = english_value
        except Exception:
            translated[key] = english_value
    
    return translated


def get_i18n(lang: str) -> Dict[str, str]:
    """
    Synchronous version - returns static translations only.
    For full translation support, use get_i18n_async().

    English sits underneath so a partial dictionary yields a partly-translated
    message rather than a KeyError; the async path fills the same gaps properly.
    """
    return {**EMAIL_I18N["en"], **EMAIL_I18N.get(lang, {})}


STATUS_TONES = {
    "open": "warning",
    "in_progress": "info",
    "closed": "success",
}


def _status_label(new_status: str, i18n: Dict[str, str]) -> str:
    return {
        "open": i18n.get("status_open", "Open"),
        "in_progress": i18n.get("status_in_progress", "In Progress"),
        "closed": i18n.get("status_closed", "Resolved"),
    }.get(new_status, str(new_status).replace("_", " ").title())


def _tracking_url(portal_url: str, request_id: str) -> str:
    return f"{(portal_url or '').rstrip('/')}/#track/{request_id}"


def _absolute(url: Optional[str], portal_url: str) -> Optional[str]:
    """Make a stored media path absolute. A relative src is a broken image in
    every mail client -- there is no page for it to be relative to."""
    if not url:
        return None
    if url.startswith("/"):
        return f"{(portal_url or '').rstrip('/')}{url}"
    return _safe_url(url)


# --- Content, described once per email and rendered twice by email_layout ---
#
# Note that nothing below escapes its own arguments any more: the block helpers
# in email_layout escape everything they are given, and escaping twice is how
# a resident's apostrophe becomes "&amp;#39;" in the message they receive.

def _confirmation_parts(i18n, township_name, request_id, service_name,
                        description, address, portal_url):
    """What the resident is told, and nothing beside it.

    There used to be a "Your report has been submitted successfully" line
    directly under a heading that already said the request was received, and a
    heading with an exclamation mark. A restatement is not information. What
    is left is the reference number, what was reported, where, and the link
    that tracks it.
    """
    tracking_url = _tracking_url(portal_url, request_id)
    rows = [
        (i18n.get("request_id", "Request ID"), f"#{request_id}"),
        (i18n.get("category", "Category"), service_name),
        (i18n.get("description", "Description"), (description or "")[:200]),
    ]
    if address:
        rows.append((i18n.get("location", "Location"), address))
    blocks = [
        L.heading(i18n.get("request_received", "Request received"), 2),
        L.fields(rows),
        L.button(i18n.get("track_request", "Track your request"), tracking_url),
    ]
    footer = [i18n.get("thank_you", "Thank you for helping make {township} a better place.")
              .format(township=township_name)]
    subject = i18n.get("subject_received", "Request #{id} received - {township}").format(
        id=request_id, township=township_name)
    return subject, blocks, footer


def _status_update_parts(i18n, township_name, request_id, service_name, new_status,
                         completion_message, completion_photo_url, portal_url):
    """The change leads.

    The old shape opened with a "Status Update" heading and a sentence saying
    the status had been updated -- the subject line and the panel underneath
    both already said so. The panel is now the first thing in the message, at
    24px, so what changed is legible before anything is read.
    """
    tracking_url = _tracking_url(portal_url, request_id)
    label = _status_label(new_status, i18n)
    blocks = [
        L.status_panel(i18n.get("current_status", "Current status"), label,
                       STATUS_TONES.get(new_status, "neutral")),
        L.fields([
            (i18n.get("request_id", "Request ID"), f"#{request_id}"),
            (i18n.get("category", "Category"), service_name),
        ]),
    ]
    if completion_message and new_status == "closed":
        blocks.append(L.callout(completion_message, "success",
                                title=i18n.get("resolution_notes", "Resolution notes")))
    photo = _absolute(completion_photo_url, portal_url) if new_status == "closed" else None
    if photo:
        blocks.append(L.image(photo, i18n.get("completion_photo", "Completion photo")))
    blocks.append(L.button(i18n.get("view_details", "View request details"), tracking_url))
    subject = i18n.get("subject_status", "Request #{id} status: {status} - {township}").format(
        id=request_id, status=label, township=township_name)
    footer = [i18n.get("receiving_because",
                       "You are receiving this because you submitted a request to {township}.")
              .format(township=township_name)]
    return subject, blocks, footer


def _comment_parts(i18n, township_name, request_id, service_name, comment_author,
                   comment_content, portal_url):
    """The staff member's words lead; the reference sits under them.

    The heading ("New Update on Your Request") repeated the subject line and
    has gone. The panel title used to be built by gluing the author's name to
    the words "Staff Member" with a hyphen, which assumes English word order;
    it is now one translated sentence with a named placeholder.
    """
    tracking_url = _tracking_url(portal_url, request_id)
    blocks = [
        L.callout(comment_content, "info",
                  title=i18n.get("message_from", "Message from {author}")
                  .format(author=comment_author)),
        L.fields([
            (i18n.get("request_id", "Request ID"), f"#{request_id}"),
            (i18n.get("category", "Category"), service_name),
        ]),
        L.button(i18n.get("view_conversation", "View the full conversation"), tracking_url),
    ]
    subject = i18n.get("subject_comment", "New update on request #{id} - {township}").format(
        id=request_id, township=township_name)
    footer = [i18n.get("receiving_because",
                       "You are receiving this because you submitted a request to {township}.")
              .format(township=township_name)]
    return subject, blocks, footer


def build_confirmation_email(
    township_name: str,
    logo_url: Optional[str],
    primary_color: str,
    request_id: str,
    service_name: str,
    description: str,
    address: Optional[str],
    portal_url: str,
    language: str = "en"
) -> Dict[str, str]:
    """New-request confirmation to the resident. {'subject','html','text'}."""
    i18n = get_i18n(language)
    subject, blocks, footer = _confirmation_parts(
        i18n, township_name, request_id, service_name, description, address, portal_url)
    return L.build_email(
        subject=subject, township_name=township_name, blocks=blocks,
        logo_url=logo_url, primary_color=primary_color, footer_lines=footer,
        language=language,
        preheader=f"{i18n.get('request_id', 'Request ID')} #{request_id}",
        tagline=i18n.get("service_portal", L.DEFAULT_TAGLINE),
        no_reply_note=i18n.get("no_reply", "Please do not reply directly to this email."),
    )


async def build_confirmation_email_async(
    township_name: str,
    logo_url: Optional[str],
    primary_color: str,
    request_id: str,
    service_name: str,
    description: str,
    address: Optional[str],
    portal_url: str,
    language: str = "en"
) -> Dict[str, str]:
    """As above, but translating through the Translate API for any language not
    in the static dictionary."""
    i18n = await get_i18n_async(language)
    subject, blocks, footer = _confirmation_parts(
        i18n, township_name, request_id, service_name, description, address, portal_url)
    return L.build_email(
        subject=subject, township_name=township_name, blocks=blocks,
        logo_url=logo_url, primary_color=primary_color, footer_lines=footer,
        language=language,
        preheader=f"{i18n.get('request_id', 'Request ID')} #{request_id}",
        tagline=i18n.get("service_portal", L.DEFAULT_TAGLINE),
        no_reply_note=i18n.get("no_reply", "Please do not reply directly to this email."),
    )


def build_status_update_email(
    township_name: str,
    logo_url: Optional[str],
    primary_color: str,
    request_id: str,
    service_name: str,
    old_status: str,
    new_status: str,
    completion_message: Optional[str],
    completion_photo_url: Optional[str],
    portal_url: str,
    language: str = "en"
) -> Dict[str, str]:
    """Status change notification to the resident, with the completion photo
    when the request was closed with one."""
    i18n = get_i18n(language)
    subject, blocks, footer = _status_update_parts(
        i18n, township_name, request_id, service_name, new_status,
        completion_message, completion_photo_url, portal_url)
    return L.build_email(
        subject=subject, township_name=township_name, blocks=blocks,
        logo_url=logo_url, primary_color=primary_color, footer_lines=footer,
        language=language, preheader=subject,
        tagline=i18n.get("service_portal", L.DEFAULT_TAGLINE),
        no_reply_note=i18n.get("no_reply", "Please do not reply directly to this email."),
    )


async def build_status_update_email_async(
    township_name: str,
    logo_url: Optional[str],
    primary_color: str,
    request_id: str,
    service_name: str,
    old_status: str,
    new_status: str,
    completion_message: Optional[str],
    completion_photo_url: Optional[str],
    portal_url: str,
    language: str = "en"
) -> Dict[str, str]:
    """As above, translated."""
    i18n = await get_i18n_async(language)
    subject, blocks, footer = _status_update_parts(
        i18n, township_name, request_id, service_name, new_status,
        completion_message, completion_photo_url, portal_url)
    return L.build_email(
        subject=subject, township_name=township_name, blocks=blocks,
        logo_url=logo_url, primary_color=primary_color, footer_lines=footer,
        language=language, preheader=subject,
        tagline=i18n.get("service_portal", L.DEFAULT_TAGLINE),
        no_reply_note=i18n.get("no_reply", "Please do not reply directly to this email."),
    )


def build_comment_email(
    township_name: str,
    logo_url: Optional[str],
    primary_color: str,
    request_id: str,
    service_name: str,
    comment_author: str,
    comment_content: str,
    portal_url: str,
    language: str = "en"
) -> Dict[str, str]:
    """A staff member's public comment, sent to the resident who filed."""
    i18n = get_i18n(language)
    subject, blocks, footer = _comment_parts(
        i18n, township_name, request_id, service_name, comment_author,
        comment_content, portal_url)
    return L.build_email(
        subject=subject, township_name=township_name, blocks=blocks,
        logo_url=logo_url, primary_color=primary_color, footer_lines=footer,
        language=language, preheader=subject,
        tagline=i18n.get("service_portal", L.DEFAULT_TAGLINE),
        no_reply_note=i18n.get("no_reply", "Please do not reply directly to this email."),
    )


async def build_comment_email_async(
    township_name: str,
    logo_url: Optional[str],
    primary_color: str,
    request_id: str,
    service_name: str,
    comment_author: str,
    comment_content: str,
    portal_url: str,
    language: str = "en"
) -> Dict[str, str]:
    """As above, translated."""
    i18n = await get_i18n_async(language)
    subject, blocks, footer = _comment_parts(
        i18n, township_name, request_id, service_name, comment_author,
        comment_content, portal_url)
    return L.build_email(
        subject=subject, township_name=township_name, blocks=blocks,
        logo_url=logo_url, primary_color=primary_color, footer_lines=footer,
        language=language, preheader=subject,
        tagline=i18n.get("service_portal", L.DEFAULT_TAGLINE),
        no_reply_note=i18n.get("no_reply", "Please do not reply directly to this email."),
    )



def build_sms_confirmation(request_id: str, township_name: str, portal_url: str = "", service_name: str = "", description: str = "", address: str = "") -> str:
    """Build SMS message for request confirmation."""
    tracking_link = f"{portal_url}/#track/{request_id}" if portal_url else ""
    
    # Truncate description for SMS
    short_desc = description[:60] + "..." if len(description) > 60 else description
    
    message = f"""{township_name} 311
Your request has been received.

{service_name}"""
    
    if short_desc:
        message += f"\n\"{short_desc}\""
    
    if address:
        message += f"\n{address}"
    
    message += f"\n\nRef: {request_id}"

    if tracking_link:
        message += f"\nTrack: {tracking_link}"
    
    return message


def build_sms_status_update(request_id: str, new_status: str, township_name: str, portal_url: str = "", completion_message: str = "", service_name: str = "") -> str:
    """Build SMS message for status update."""
    status_text = {
        "open": "Your request is being reviewed.",
        "in_progress": "Your request is being worked on.",
        "closed": "Your request has been resolved.",
    }.get(new_status, f"Status: {new_status}")

    tracking_link = f"{portal_url}/#track/{request_id}" if portal_url else ""

    message = f"""{township_name} 311
{status_text}"""

    if service_name:
        message += f"\n\n{service_name}"
    
    if new_status == "closed" and completion_message:
        # Truncate long completion messages for SMS
        short_msg = completion_message[:80] + "..." if len(completion_message) > 80 else completion_message
        message += f"\n{short_msg}"
    
    message += f"\n\nRef: {request_id}"
    
    if tracking_link:
        message += f"\nDetails: {tracking_link}"
    
    return message


# ==================== ASYNC VERSIONS WITH GOOGLE TRANSLATE ====================

async def build_sms_confirmation_async(
    request_id: str,
    township_name: str,
    portal_url: str = "",
    service_name: str = "",
    description: str = "",
    address: str = "",
    language: str = "en"
) -> str:
    """
    Async version - Build SMS message for request confirmation.
    Uses Google Translate API for any language not in the static dictionary.
    """
    i18n = await get_i18n_async(language)
    tracking_link = f"{portal_url}/#track/{request_id}" if portal_url else ""
    
    # Truncate description for SMS
    short_desc = description[:60] + "..." if len(description) > 60 else description
    
    message = f"""{township_name} 311
{i18n.get("sms_received", "Your request has been received.")}

{service_name}"""
    
    if short_desc:
        message += f'\n"{short_desc}"'
    
    if address:
        message += f"\n{address}"
    
    message += f"\n\n{i18n.get('sms_ref', 'Ref')}: {request_id}"
    
    if tracking_link:
        message += f"\n{i18n.get('sms_track', 'Track')}: {tracking_link}"
    
    return message


async def build_sms_status_update_async(
    request_id: str,
    new_status: str,
    township_name: str,
    portal_url: str = "",
    completion_message: str = "",
    service_name: str = "",
    language: str = "en"
) -> str:
    """
    Async version - Build SMS message for status update.
    Uses Google Translate API for any language not in the static dictionary.
    """
    i18n = await get_i18n_async(language)
    
    # Whole sentences, keyed by status. The old version glued the *label*
    # "Request ID" to the fragment "is being reviewed" and shipped "Request ID
    # is being reviewed" -- which is what building a sentence out of parts in
    # English word order does to every other language as well.
    status_text = {
        "open": i18n.get("sms_being_reviewed", "Your request is being reviewed."),
        "in_progress": i18n.get("sms_being_worked", "Your request is being worked on."),
        "closed": i18n.get("sms_resolved", "Your request has been resolved."),
    }.get(new_status, f"{i18n.get('current_status', 'Current status')}: {new_status}")

    tracking_link = f"{portal_url}/#track/{request_id}" if portal_url else ""

    message = f"""{township_name} 311
{status_text}"""

    if service_name:
        message += f"\n\n{service_name}"
    
    if new_status == "closed" and completion_message:
        # Truncate long completion messages for SMS
        short_msg = completion_message[:80] + "..." if len(completion_message) > 80 else completion_message
        message += f"\n{short_msg}"
    
    message += f"\n\n{i18n.get('sms_ref', 'Ref')}: {request_id}"
    
    if tracking_link:
        message += f"\n{i18n.get('sms_details', 'Details')}: {tracking_link}"
    
    return message
