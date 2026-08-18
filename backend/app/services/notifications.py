"""
Notification services for SMS and Email with configurable providers.
"""
import httpx
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# ============ SMS Providers ============

class SMSProvider(ABC):
    """Base class for SMS providers"""
    
    @abstractmethod
    async def send_sms(self, to: str, message: str) -> bool:
        pass


class TwilioProvider(SMSProvider):
    """Twilio SMS provider"""
    
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    
    async def send_sms(self, to: str, message: str) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.base_url,
                    auth=(self.account_sid, self.auth_token),
                    data={
                        "To": to,
                        "From": self.from_number,
                        "Body": message
                    }
                )
                return response.status_code == 201
        except Exception as e:
            logger.warning(f"Twilio SMS error: {e}")
            return False


class GenericHTTPSMSProvider(SMSProvider):
    """Generic HTTP-based SMS provider for any API (supports Textbelt, etc.)"""
    
    def __init__(self, api_url: str, api_key: str, from_number: str):
        self.api_url = api_url
        self.api_key = api_key
        self.from_number = from_number
        # Detect if this is Textbelt based on URL
        self.is_textbelt = "textbelt" in api_url.lower()
    
    async def send_sms(self, to: str, message: str) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                if self.is_textbelt:
                    # Textbelt format: phone, message, key (no auth header)
                    response = await client.post(
                        self.api_url,
                        data={
                            "phone": to,
                            "message": message,
                            "key": self.api_key
                        }
                    )
                    # Textbelt returns JSON with "success": true/false
                    if response.is_success:
                        result = response.json()
                        logger.debug(f"[Textbelt SMS] Response: {result}")
                        if not result.get("success"):
                            logger.warning(f"[Textbelt SMS] Error: {result.get('error', 'Unknown error')}")
                        return result.get("success", False)
                    logger.warning(f"[Textbelt SMS] HTTP Error: {response.status_code}")
                    return False
                else:
                    # Standard format with Bearer auth
                    response = await client.post(
                        self.api_url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "to": to,
                            "from": self.from_number,
                            "message": message
                        }
                    )
                    return response.is_success
        except Exception as e:
            logger.warning(f"HTTP SMS error: {e}")
            return False


# ============ Email Provider ============

class EmailProvider:
    """SMTP Email provider"""
    
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        from_email: str,
        from_name: str = "Township 311",
        use_tls: bool = True
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.from_email = from_email
        self.from_name = from_name
        self.use_tls = use_tls
    
    def send_email(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        from_name: Optional[str] = None
    ) -> bool:
        try:
            sender_name = from_name or self.from_name
            logger.info(f"[Email] Sending email to {to} from '{sender_name}' with subject: {subject[:50]}...")
            
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{sender_name} <{self.from_email}>"
            msg["To"] = to
            
            if body_text:
                msg.attach(MIMEText(body_text, "plain"))
            msg.attach(MIMEText(body_html, "html"))
            
            if self.use_tls:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port) as server:
                    server.login(self.smtp_user, self.smtp_password)
                    server.send_message(msg)
            
            logger.info(f"[Email] Successfully sent email to {to}")
            return True
        except Exception as e:
            logger.error(f"[Email] Error sending to {to}: {e}")
            return False


# ============ Native cloud SMS providers ============

class SNSProvider(SMSProvider):
    """Amazon SNS SMS (boto3). For AWS GovCloud stacks. Credentials fall back to
    the instance role / default chain when explicit keys aren't given."""

    def __init__(self, region: str, sender_id: str = "",
                 access_key: str = "", secret_key: str = "", session_token: str = ""):
        self.region = region
        self.sender_id = sender_id
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token

    def _client(self):
        import boto3
        kwargs = {"region_name": self.region}
        if self.access_key and self.secret_key:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key
            if self.session_token:
                kwargs["aws_session_token"] = self.session_token
        return boto3.client("sns", **kwargs)

    async def send_sms(self, to: str, message: str) -> bool:
        if not self.region:
            return False
        import asyncio

        def _run():
            client = self._client()
            attrs = {}
            if self.sender_id:
                attrs["AWS.SNS.SMS.SenderID"] = {"DataType": "String", "StringValue": self.sender_id}
            resp = client.publish(PhoneNumber=to, Message=message, MessageAttributes=attrs or {})
            return bool(resp.get("MessageId"))

        try:
            return await asyncio.get_event_loop().run_in_executor(None, _run)
        except Exception as e:
            logger.warning(f"SNS SMS error: {e}")
            return False


def _acs_auth_headers(method: str, url: str, body_bytes: bytes, access_key: str) -> Dict[str, str]:
    """Azure Communication Services HMAC-SHA256 request signing (shared by ACS
    SMS + email). Signs date;host;content-hash with the base64 access key."""
    import base64
    import hashlib
    import hmac
    from email.utils import formatdate
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.netloc
    path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    content_hash = base64.b64encode(hashlib.sha256(body_bytes).digest()).decode("ascii")
    date = formatdate(usegmt=True)  # RFC1123 GMT
    string_to_sign = f"{method}\n{path_and_query}\n{date};{host};{content_hash}"
    signature = base64.b64encode(
        hmac.new(base64.b64decode(access_key), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    return {
        "x-ms-date": date,
        "x-ms-content-sha256": content_hash,
        "Authorization": (
            "HMAC-SHA256 SignedHeaders=x-ms-date;host;x-ms-content-sha256&"
            f"Signature={signature}"
        ),
        "Content-Type": "application/json",
    }


class ACSSMSProvider(SMSProvider):
    """Azure Communication Services SMS over REST + HMAC auth (no azure SDK)."""

    def __init__(self, endpoint: str, access_key: str, from_number: str):
        self.endpoint = (endpoint or "").rstrip("/")
        self.access_key = access_key
        self.from_number = from_number

    async def send_sms(self, to: str, message: str) -> bool:
        if not (self.endpoint and self.access_key and self.from_number):
            return False
        import json as _json
        url = f"{self.endpoint}/sms?api-version=2021-03-07"
        body = _json.dumps({
            "from": self.from_number,
            "smsRecipients": [{"to": to}],
            "message": message,
            "smsSendOptions": {"enableDeliveryReport": False},
        }).encode("utf-8")
        try:
            headers = _acs_auth_headers("POST", url, body, self.access_key)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, content=body, headers=headers)
                return resp.is_success
        except Exception as e:
            logger.warning(f"ACS SMS error: {e}")
            return False


# ============ Native cloud email providers ============

class SESEmailProvider:
    """Amazon SES email (boto3). Same send_email signature as EmailProvider so
    NotificationService can use either interchangeably."""

    def __init__(self, region: str, from_email: str, from_name: str = "Township 311",
                 access_key: str = "", secret_key: str = "", session_token: str = ""):
        self.region = region
        self.from_email = from_email
        self.from_name = from_name
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token

    def _client(self):
        import boto3
        kwargs = {"region_name": self.region}
        if self.access_key and self.secret_key:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key
            if self.session_token:
                kwargs["aws_session_token"] = self.session_token
        return boto3.client("ses", **kwargs)

    def send_email(self, to: str, subject: str, body_html: str,
                   body_text: Optional[str] = None, from_name: Optional[str] = None) -> bool:
        if not (self.region and self.from_email):
            logger.warning("[SES] region/from_email not configured")
            return False
        try:
            sender = f"{from_name or self.from_name} <{self.from_email}>"
            body: Dict[str, Any] = {"Html": {"Data": body_html, "Charset": "UTF-8"}}
            if body_text:
                body["Text"] = {"Data": body_text, "Charset": "UTF-8"}
            self._client().send_email(
                Source=sender,
                Destination={"ToAddresses": [to]},
                Message={"Subject": {"Data": subject, "Charset": "UTF-8"}, "Body": body},
            )
            logger.info(f"[SES] Sent email to {to}")
            return True
        except Exception as e:
            logger.error(f"[SES] Error sending to {to}: {e}")
            return False


class ACSEmailProvider:
    """Azure Communication Services Email over REST + HMAC auth (no azure SDK).
    Mirrors EmailProvider.send_email so the two are interchangeable."""

    def __init__(self, endpoint: str, access_key: str, from_email: str, from_name: str = "Township 311"):
        self.endpoint = (endpoint or "").rstrip("/")
        self.access_key = access_key
        self.from_email = from_email  # must be a verified ACS sender address
        self.from_name = from_name

    def send_email(self, to: str, subject: str, body_html: str,
                   body_text: Optional[str] = None, from_name: Optional[str] = None) -> bool:
        if not (self.endpoint and self.access_key and self.from_email):
            logger.warning("[ACS Email] endpoint/key/sender not configured")
            return False
        import json as _json
        url = f"{self.endpoint}/emails:send?api-version=2023-03-31"
        content: Dict[str, Any] = {"subject": subject, "html": body_html}
        if body_text:
            content["plainText"] = body_text
        payload = {
            "senderAddress": self.from_email,
            "content": content,
            "recipients": {"to": [{"address": to, "displayName": to}]},
        }
        body = _json.dumps(payload).encode("utf-8")
        try:
            headers = _acs_auth_headers("POST", url, body, self.access_key)
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, content=body, headers=headers)
                if resp.is_success:
                    logger.info(f"[ACS Email] Queued email to {to}")
                    return True
                logger.error(f"[ACS Email] HTTP {resp.status_code}: {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"[ACS Email] Error sending to {to}: {e}")
            return False


# ============ Notification Service ============

class NotificationService:
    """Unified notification service for SMS and Email"""
    
    _instance = None
    _sms_provider: Optional[SMSProvider] = None
    _email_provider: Optional[EmailProvider] = None
    # Which vendor each configured provider is, kept so a health row written by
    # a real send says whose verdict it is. Without it every send recorded
    # provider NULL, and a town that switched from one gateway to another kept
    # the previous vendor's answer on the card with nothing to say so.
    _sms_provider_name: Optional[str] = None
    _email_provider_name: Optional[str] = None

    @classmethod
    def get_instance(cls) -> "NotificationService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def configure_sms(self, provider_type: str, config: Dict[str, Any]):
        """Configure SMS provider dynamically"""
        self._sms_provider_name = provider_type
        if provider_type == "twilio":
            self._sms_provider = TwilioProvider(
                account_sid=config.get("account_sid", ""),
                auth_token=config.get("auth_token", ""),
                from_number=config.get("from_number", "")
            )
        elif provider_type == "http":
            self._sms_provider = GenericHTTPSMSProvider(
                api_url=config.get("api_url", ""),
                api_key=config.get("api_key", ""),
                from_number=config.get("from_number", "")
            )
        elif provider_type == "sns":
            self._sms_provider = SNSProvider(
                region=config.get("region", ""),
                sender_id=config.get("sender_id", ""),
                access_key=config.get("access_key", ""),
                secret_key=config.get("secret_key", ""),
                session_token=config.get("session_token", ""),
            )
        elif provider_type == "acs":
            self._sms_provider = ACSSMSProvider(
                endpoint=config.get("endpoint", ""),
                access_key=config.get("access_key", ""),
                from_number=config.get("from_number", ""),
            )

    def configure_email(self, config: Dict[str, Any], provider_type: str = "smtp"):
        """Configure the Email provider. Defaults to SMTP (works with any relay,
        including SES/ACS SMTP endpoints); 'ses' and 'acs' use the native APIs."""
        # "smtp" for anything that is not one of the two native APIs, matching
        # the branch below and the value EMAIL_PROVIDER stores.
        self._email_provider_name = provider_type if provider_type in ("ses", "acs") else "smtp"
        if provider_type == "ses":
            self._email_provider = SESEmailProvider(
                region=config.get("region", ""),
                from_email=config.get("from_email", ""),
                from_name=config.get("from_name", "Township 311"),
                access_key=config.get("access_key", ""),
                secret_key=config.get("secret_key", ""),
                session_token=config.get("session_token", ""),
            )
        elif provider_type == "acs":
            self._email_provider = ACSEmailProvider(
                endpoint=config.get("endpoint", ""),
                access_key=config.get("access_key", ""),
                from_email=config.get("from_email", ""),
                from_name=config.get("from_name", "Township 311"),
            )
        else:
            self._email_provider = EmailProvider(
                smtp_host=config.get("smtp_host", ""),
                smtp_port=config.get("smtp_port", 587),
                smtp_user=config.get("smtp_user", ""),
                smtp_password=config.get("smtp_password", ""),
                from_email=config.get("from_email", ""),
                from_name=config.get("from_name", "Township 311"),
                use_tls=config.get("use_tls", True)
            )
    
    def _provider_name(self, connector: str) -> Optional[str]:
        """The vendor behind this connector, for the health row.

        A verdict is only true of the provider that produced it. Recording
        without one leaves a card showing yesterday's answer about a vendor the
        town has since switched away from, and nothing on screen able to tell.
        """
        return self._sms_provider_name if connector == "sms" else self._email_provider_name

    async def _record_health(self, connector: str, success: bool) -> None:
        """Record a real send against connector health.

        Health used to be written only by the admin Test button, which meant a
        card claimed "Working" for a week on the strength of one manual click
        and never reflected whether residents were actually getting their
        email. Recording here is what makes the badge mean something: it moves
        with real traffic.

        Best-effort, and its own session -- a notification must never fail
        because bookkeeping did, and it must not join the caller's transaction
        where a rollback would erase the record of a send that really happened.
        """
        try:
            from app.db.session import SessionLocal
            from app.services import connector_health as ch
            provider = self._provider_name(connector)
            async with SessionLocal() as db:
                if success:
                    await ch.record_success(db, connector, provider=provider)
                else:
                    await ch.record_failure(
                        db, connector, "The provider did not accept the message",
                        provider=provider)
        except Exception as exc:
            logger.debug("Could not record %s health: %s", connector, exc)

    def _record_health_sync(self, connector: str, success: bool) -> None:
        """The same, from the synchronous email path.

        Scheduled onto a running loop when there is one and skipped otherwise:
        spinning up an event loop inside a worker thread to write one row is not
        worth the deadlock risk.
        """
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            loop.create_task(self._record_health(connector, success))
        except Exception as exc:
            logger.debug("Could not schedule %s health: %s", connector, exc)

    async def send_sms(self, to: str, message: str) -> bool:
        """Send SMS notification"""
        if not self._sms_provider:
            logger.warning("SMS provider not configured")
            return False
        success = await self._sms_provider.send_sms(to, message)
        await self._record_health("sms", success)

        # Track SMS usage if successful
        if success:
            try:
                from app.db.session import SessionLocal
                from app.services.api_usage import track_api_usage
                async with SessionLocal() as db:
                    await track_api_usage(
                        db,
                        service_name="sms",
                        operation="send_sms",
                        api_calls=1
                    )
            except Exception as e:
                logger.debug(f"Failed to track SMS usage: {e}")
        
        return success
    
    def send_email(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: Optional[str] = None,
        from_name: Optional[str] = None
    ) -> bool:
        """Send email notification. Optionally override sender name for branded emails.

        The text alternative is guaranteed here rather than trusted from the
        caller. All three providers below accept one -- SMTP as a second MIME
        part, SES as `Body.Text`, ACS as `content.plainText` -- and none of them
        invents one, so a caller that forgot sent an HTML-only message. That
        costs deliverability with every spam filter and leaves a text-only
        client, a smartwatch preview and a screen reader with nothing to read.
        Recovering the text from rendered HTML is worse than composing it from
        the content, which is why `email_layout.build_email` does the latter;
        this is the floor, not the plan.
        """
        if not self._email_provider:
            logger.warning("Email provider not configured")
            return False
        if not (body_text or "").strip():
            from app.services.email_layout import html_to_text
            body_text = html_to_text(body_html)
        success = self._email_provider.send_email(to, subject, body_html, body_text, from_name=from_name)
        self._record_health_sync("email", success)

        # Track email usage if successful (sync version)
        if success:
            try:
                import asyncio
                from app.db.session import SessionLocal
                from app.services.api_usage import track_api_usage
                
                async def _track():
                    async with SessionLocal() as db:
                        await track_api_usage(
                            db,
                            service_name="email",
                            operation="send_email",
                            api_calls=1
                        )
                
                # Run in new event loop if needed
                try:
                    asyncio.get_running_loop()  # Check if loop is running
                    asyncio.create_task(_track())
                except RuntimeError:
                    asyncio.run(_track())
            except Exception as e:
                logger.debug(f"Failed to track email usage: {e}")
        
        return success
    
    def send_request_confirmation_branded(
        self,
        request_id: str,
        service_name: str,
        description: str,
        address: Optional[str],
        email: str,
        phone: Optional[str],
        township_name: str,
        logo_url: Optional[str],
        primary_color: str,
        portal_url: str,
        language: str = "en"
    ):
        """Send branded confirmation for a new service request (sync - uses static translations only)"""
        from app.services.email_templates import build_confirmation_email
        
        # Build branded email with translation
        email_content = build_confirmation_email(
            township_name=township_name,
            logo_url=logo_url,
            primary_color=primary_color,
            request_id=request_id,
            service_name=service_name,
            description=description,
            address=address,
            portal_url=portal_url,
            language=language
        )
        
        # Send email with township name as sender
        if email:
            self.send_email(email, email_content["subject"], email_content["html"], email_content["text"],
                          from_name=f"{township_name} 311")
        
        # Send SMS - removed as not in scope for confirmation
    
    async def send_request_confirmation_branded_async(
        self,
        request_id: str,
        service_name: str,
        description: str,
        address: Optional[str],
        email: str,
        phone: Optional[str],
        township_name: str,
        logo_url: Optional[str],
        primary_color: str,
        portal_url: str,
        language: str = "en"
    ):
        """
        Send branded confirmation for a new service request (async - uses Google Translate API).
        Supports 130+ languages with automatic translation and caching.
        """
        from app.services.email_templates import build_confirmation_email_async
        
        # Build branded email with translation via Google Translate API
        email_content = await build_confirmation_email_async(
            township_name=township_name,
            logo_url=logo_url,
            primary_color=primary_color,
            request_id=request_id,
            service_name=service_name,
            description=description,
            address=address,
            portal_url=portal_url,
            language=language
        )
        
        # Send email with township name as sender
        if email:
            self.send_email(email, email_content["subject"], email_content["html"], email_content["text"],
                          from_name=f"{township_name} 311")
    
    
    def send_request_confirmation(self, request_id: str, email: str, phone: Optional[str] = None):
        """Legacy confirmation - now calls branded version with defaults"""
        # A fallback for legacy call sites that have no branding to hand. It
        # still goes through the one shared layout, so it is the same email in
        # a plainer suit rather than a second design.
        from app.services import email_layout as L

        message = L.build_email(
            subject=f"Request #{request_id} received",
            township_name="311",
            blocks=[
                L.heading("Your request has been received", 2),
                L.paragraph("Thank you for submitting a service request to your local township."),
                L.fields([("Request ID", f"#{request_id}")]),
                L.paragraph("You can track the status of your request using this ID."),
            ],
            footer_lines=["We appreciate your help in making our community better."],
        )
        if email:
            self.send_email(email, message["subject"], message["html"], message["text"])
    
    async def send_status_update_branded(
        self,
        request_id: str,
        service_name: str,
        old_status: str,
        new_status: str,
        completion_message: Optional[str],
        completion_photo_url: Optional[str],
        email: Optional[str],
        phone: Optional[str],
        township_name: str,
        logo_url: Optional[str],
        primary_color: str,
        portal_url: str,
        language: str = "en"
    ):
        """
        Send branded status update notification with completion photo support.
        Uses Google Translate API for 130+ languages with caching.
        """
        from app.services.email_templates import build_status_update_email_async, build_sms_status_update_async
        
        # Build branded email with translation
        email_content = await build_status_update_email_async(
            township_name=township_name,
            logo_url=logo_url,
            primary_color=primary_color,
            request_id=request_id,
            service_name=service_name,
            old_status=old_status,
            new_status=new_status,
            completion_message=completion_message,
            completion_photo_url=completion_photo_url,
            portal_url=portal_url,
            language=language
        )
        
        if email:
            self.send_email(email, email_content["subject"], email_content["html"], email_content["text"],
                          from_name=f"{township_name} 311")
        
        if phone:
            sms_message = await build_sms_status_update_async(
                request_id, new_status, township_name, portal_url, 
                completion_message or "", service_name, language
            )
            await self.send_sms(phone, sms_message)
    
    async def send_status_update(
        self,
        request_id: str,
        new_status: str,
        email: Optional[str] = None,
        phone: Optional[str] = None
    ):
        """Legacy status update - uses basic template"""
        status_text = {
            "open": "is now open and being reviewed",
            "in_progress": "is now being worked on",
            "closed": "has been resolved"
        }.get(new_status, f"status changed to {new_status}")
        
        from app.services import email_layout as L

        message = L.build_email(
            subject=f"Request #{request_id} status update",
            township_name="311",
            blocks=[
                L.heading("Request status update", 2),
                L.paragraph(f"Your service request #{request_id} {status_text}."),
            ],
            footer_lines=["Thank you for your patience."],
        )
        sms_message = f"Request #{request_id} {status_text}"

        if email:
            self.send_email(email, message["subject"], message["html"], message["text"])
        
        if phone:
            await self.send_sms(phone, sms_message)
    
    async def send_comment_notification_async(
        self,
        request_id: str,
        service_name: str,
        comment_author: str,
        comment_content: str,
        email: str,
        township_name: str,
        logo_url: Optional[str],
        primary_color: str,
        portal_url: str,
        language: str = "en"
    ):
        """
        Send notification when staff leaves a public comment.
        Uses Google Translate API for 130+ languages with caching.
        """
        from app.services.email_templates import build_comment_email_async
        
        email_content = await build_comment_email_async(
            township_name=township_name,
            logo_url=logo_url,
            primary_color=primary_color,
            request_id=request_id,
            service_name=service_name,
            comment_author=comment_author,
            comment_content=comment_content,
            portal_url=portal_url,
            language=language
        )
        
        if email:
            self.send_email(email, email_content["subject"], email_content["html"], email_content["text"],
                          from_name=f"{township_name} 311")
    
    def send_comment_notification(
        self,
        request_id: str,
        service_name: str,
        comment_author: str,
        comment_content: str,
        email: str,
        township_name: str,
        logo_url: Optional[str],
        primary_color: str,
        portal_url: str
    ):
        """Send notification when staff leaves a public comment (sync - static translations only)"""
        from app.services.email_templates import build_comment_email
        
        email_content = build_comment_email(
            township_name=township_name,
            logo_url=logo_url,
            primary_color=primary_color,
            request_id=request_id,
            service_name=service_name,
            comment_author=comment_author,
            comment_content=comment_content,
            portal_url=portal_url
        )
        
        if email:
            self.send_email(email, email_content["subject"], email_content["html"], email_content["text"],
                          from_name=f"{township_name} 311")


# Singleton instance
notification_service = NotificationService.get_instance()

