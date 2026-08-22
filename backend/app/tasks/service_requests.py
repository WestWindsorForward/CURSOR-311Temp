from app.core.celery_app import celery_app
from app.db.session import SessionLocal
from app.models import ServiceRequest
from app.services.notifications import notification_service
from app.services import email_layout as L
from app.services.town_time import format_town
from sqlalchemy import select
import asyncio
import os


def run_async(coro):
    """Helper to run async functions in sync context.

    The loop this creates is closed when the task ends, so nothing that outlives
    it may still hold a database connection. Keeping that true is the worker
    engine's job rather than this helper's: `app.db.session.use_null_pool`,
    wired to Celery's worker start-up signals, gives the worker an engine that
    pools nothing, so every connection is closed inside the loop that opened it.

    The dispose below used to be the only defence, and it could not be -- half
    the task modules (`road_data`, `connector_checks`) and two services call
    `asyncio.run` without coming through here, and one such call site is enough
    to leave a dead-loop connection in the shared pool for the next task to trip
    over. It stays as the belt to that braces: it is close to free against a
    NullPool, and it still covers a process where the signal never fired.
    """
    from app.db.session import engine

    async def _runner():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_runner())


async def get_secret(db, key_name: str) -> str:
    """Get a secret value from Secret Manager (checks GCP first, then DB)"""
    try:
        from app.services.secret_manager import get_secret as sm_get_secret
        value = await sm_get_secret(key_name)
        return value if value else ""
    except Exception:
        return ""


# Whether this process has already said that SMS is not configured. See the
# `else` branch of the provider dispatch below for why it is a latch and not a
# plain warning.
_SMS_UNCONFIGURED_LOGGED = False


async def configure_notifications(db):
    """Configure notification service from database secrets"""
    import logging
    logger = logging.getLogger(__name__)

    global _SMS_UNCONFIGURED_LOGGED

    # Configure SMS provider
    sms_provider = await get_secret(db, "SMS_PROVIDER")
    logger.info(f"[SMS Config] SMS_PROVIDER: {'set' if sms_provider else 'empty'}")

    # One switch, asked of the one module that owns it.
    #
    # This read `SMS_ENABLED` directly, and two other places read
    # `modules.sms_alerts`, and both had to be on. Three sources for one answer
    # is how a town ended up able to switch texting "off" in the admin console
    # and have it stay on -- capability_switches.py has the reconciliation.
    from app.services import capability_switches
    if not await capability_switches.enabled("sms"):
        notification_service._sms_provider = None
        notification_service._sms_provider_name = None
        logger.info("[SMS Config] text messages are switched off for this town")
        sms_provider = "none"

    if sms_provider in ("twilio", "http", "sns", "acs"):
        # Configured now, so a later un-configuration is news again rather than
        # something the once-per-process latch has already swallowed.
        _SMS_UNCONFIGURED_LOGGED = False

    if sms_provider == "twilio":
        notification_service.configure_sms("twilio", {
            "account_sid": await get_secret(db, "TWILIO_ACCOUNT_SID"),
            "auth_token": await get_secret(db, "TWILIO_AUTH_TOKEN"),
            "from_number": await get_secret(db, "TWILIO_PHONE_NUMBER")
        })
        logger.info("[SMS Config] Configured Twilio provider")
    elif sms_provider == "http":
        api_url = await get_secret(db, "SMS_HTTP_API_URL")
        api_key = await get_secret(db, "SMS_HTTP_API_KEY")
        logger.info(f"[SMS Config] Configuring HTTP provider with URL: {'set' if api_url else 'EMPTY'}")
        notification_service.configure_sms("http", {
            "api_url": api_url,
            "api_key": api_key,
            "from_number": await get_secret(db, "SMS_FROM_NUMBER")
        })
        logger.info("[SMS Config] Configured HTTP/Textbelt provider")
    elif sms_provider == "sns":
        notification_service.configure_sms("sns", {
            "region": await get_secret(db, "AWS_REGION"),
            "sender_id": await get_secret(db, "SMS_SENDER_ID"),
            "access_key": await get_secret(db, "AWS_ACCESS_KEY_ID"),
            "secret_key": await get_secret(db, "AWS_SECRET_ACCESS_KEY"),
            "session_token": await get_secret(db, "AWS_SESSION_TOKEN"),
        })
        logger.info("[SMS Config] Configured Amazon SNS provider")
    elif sms_provider == "acs":
        notification_service.configure_sms("acs", {
            "endpoint": await get_secret(db, "ACS_ENDPOINT"),
            "access_key": await get_secret(db, "ACS_ACCESS_KEY"),
            "from_number": await get_secret(db, "SMS_FROM_NUMBER"),
        })
        logger.info("[SMS Config] Configured Azure Communication Services provider")
    else:
        # Cleared, not just logged. `notification_service` is a singleton that
        # outlives this call, so a previously built sender stays on it and keeps
        # sending -- switching from Twilio to a blank or mistyped provider left
        # Twilio doing the work, and the log line said the opposite.
        notification_service._sms_provider = None
        notification_service._sms_provider_name = None
        # Once per process, not once per scheduled task.
        #
        # `configure_notifications` runs at the top of every notification path,
        # including the fifteen-minute health scan, so a town that has simply
        # not set up texting was writing this warning ~100 times a day. A
        # warning that is always there is not a warning, it is background, and
        # it buries the ones that mean something.
        #
        # Not configured is a *state*, and it is already reported as one: the
        # SMS capability shows unconfigured on the Setup page. The honest
        # warning is the one at the point of use -- `send_sms` still warns
        # every time something actually tries to text with no provider, which
        # is a real event with a real victim.
        if _SMS_UNCONFIGURED_LOGGED:
            logger.debug("[SMS Config] Unknown or empty SMS_PROVIDER - SMS will not work")
        else:
            logger.warning("[SMS Config] Unknown or empty SMS_PROVIDER - SMS will not work")
            _SMS_UNCONFIGURED_LOGGED = True

    # Configure Email provider
    if not await capability_switches.enabled("email"):
        # Cleared, not just skipped. Skipping left the sender built by the
        # previous call in place, so a town that switched resident email off
        # carried on emailing residents until the worker happened to restart.
        notification_service._email_provider = None
        notification_service._email_provider_name = None
        logger.info("[Email Config] email is switched off for this town")
    else:
        email_provider = (await get_secret(db, "EMAIL_PROVIDER") or "smtp").strip().lower()
        from_name = await get_secret(db, "SMTP_FROM_NAME") or "Township 311"
        if email_provider == "ses":
            notification_service.configure_email({
                "region": await get_secret(db, "AWS_REGION"),
                "from_email": await get_secret(db, "SES_FROM_EMAIL") or await get_secret(db, "SMTP_FROM_EMAIL"),
                "from_name": from_name,
                "access_key": await get_secret(db, "AWS_ACCESS_KEY_ID"),
                "secret_key": await get_secret(db, "AWS_SECRET_ACCESS_KEY"),
                "session_token": await get_secret(db, "AWS_SESSION_TOKEN"),
            }, provider_type="ses")
            logger.info("[Email Config] Configured Amazon SES provider")
        elif email_provider == "acs":
            notification_service.configure_email({
                "endpoint": await get_secret(db, "ACS_ENDPOINT"),
                "access_key": await get_secret(db, "ACS_ACCESS_KEY"),
                "from_email": await get_secret(db, "ACS_FROM_EMAIL") or await get_secret(db, "SMTP_FROM_EMAIL"),
                "from_name": from_name,
            }, provider_type="acs")
            logger.info("[Email Config] Configured Azure Communication Services email provider")
        else:
            smtp_port_str = await get_secret(db, "SMTP_PORT")
            use_tls_str = await get_secret(db, "SMTP_USE_TLS")
            notification_service.configure_email({
                "smtp_host": await get_secret(db, "SMTP_HOST"),
                "smtp_port": int(smtp_port_str) if smtp_port_str else 587,
                "smtp_user": await get_secret(db, "SMTP_USER"),
                "smtp_password": await get_secret(db, "SMTP_PASSWORD"),
                "from_email": await get_secret(db, "SMTP_FROM_EMAIL"),
                "from_name": from_name,
                "use_tls": use_tls_str.lower() != "false" if use_tls_str else True
            }, provider_type="smtp")


@celery_app.task(bind=True, max_retries=3)
def analyze_request(self, request_id: int):
    """Analyze service request with Vertex AI (if enabled)"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[AI Analysis] Starting analysis for request {request_id}")
    
    async def _analyze():
        from app.models import SystemSettings
        from app.services.vertex_ai_service import (
            get_historical_context,
            get_spatial_context,
            build_analysis_prompt,
            analyze_with_gemini,
            strip_pii
        )
        from datetime import datetime, timezone
        
        async with SessionLocal() as db:
            settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_result.scalar_one_or_none()

            # Fetch the request first — enrichment below runs for it regardless of
            # whether AI is available.
            result = await db.execute(
                select(ServiceRequest).where(ServiceRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            if not request:
                return {"error": "Request not found"}

            # AI powers ONLY the qualitative summary + suggested priority. The
            # non-AI enrichment (nearby history, weather, proximity, similar
            # reports) always runs, so those facts populate even when AI is off or
            # not configured. This is what every intake path — resident portal,
            # manual/call-taker, email, and webhook — shares.
            from app.services import capability_switches
            ai_module_on = await capability_switches.enabled("ai")
            ai_provider = None
            if ai_module_on:
                from app.services.ai import get_ai_provider
                ai_provider = await get_ai_provider(db)
            logger.info(f"[Analysis] request {request_id}: ai_module_on={ai_module_on} "
                        f"provider={getattr(ai_provider, 'provider', None)}")

            # Phone/email/manual intake often carries an address but no map pin.
            # Geocode so spatial + weather enrichment can run for it too. Skipped
            # cleanly when there's no address to work with.
            if (request.lat is None or request.long is None) and request.address:
                try:
                    # Through the dispatcher, like /gis/geocode. Reading
                    # GOOGLE_MAPS_API_KEY directly meant an Esri or Azure town's
                    # background geocoding ran on Google-or-OSM with no town
                    # bias, while the interactive path used the provider the
                    # town actually chose.
                    from app.services import geocode_dispatch
                    _geo = await geocode_dispatch.geocode(db, request.address)
                    if _geo:
                        request.lat, request.long = _geo.lat, _geo.lng
                except Exception:
                    logger.debug("[Analysis] geocode fallback failed", exc_info=True)
            
            # Build request data with PII stripped
            request_data = {
                "service_name": request.service_name,
                "service_code": request.service_code,
                "description": strip_pii(request.description or ""),
                "address": request.address,  # Keep address for context
                "submitted_date": request.requested_datetime.isoformat() if request.requested_datetime else None,
                "matched_asset": request.matched_asset,
                "custom_fields": request.custom_fields,
            }
            
            from app.services.weather_service import get_weather_for_location
            
            # Record time of analysis
            from zoneinfo import ZoneInfo
            analysis_time = datetime.now(ZoneInfo("US/Eastern"))
            request_data["analysis_time"] = analysis_time.strftime("%Y-%m-%d %H:%M:%S %Z")

            # ---- Enrichment: ALWAYS runs. Each step guards its own inputs and
            # never fails the task, so a request with partial data (e.g. no
            # coordinates) still yields whatever facts are computable. ----
            historical_context, spatial_context, weather = {}, {}, None
            try:
                historical_context = await get_historical_context(
                    db, request.address, request.service_code, request.lat, request.long,
                    exclude_id=request.id, description=request.description or "",
                ) or {}
            except Exception:
                logger.warning("[Analysis] historical context failed", exc_info=True)
            try:
                spatial_context = await get_spatial_context(
                    db, request.lat, request.long, request.service_code,
                ) or {}
            except Exception:
                logger.warning("[Analysis] spatial context failed", exc_info=True)
            try:
                weather = await get_weather_for_location(request.lat, request.long)
            except Exception:
                logger.debug("[Analysis] weather lookup failed", exc_info=True)
            request_data["current_weather"] = weather or "Unknown"

            # The non-AI, computed facts the triage panel shows regardless of AI.
            analysis = dict(request.ai_analysis) if isinstance(request.ai_analysis, dict) else {}
            analysis["context"] = {
                "similar_reports": historical_context.get("similar_reports", []),
                "nearby_similar": historical_context.get("nearby_similar", 0),
                "recurrence_count": historical_context.get("recurrence_count", 0),
                "past_resolution_quality": historical_context.get("past_resolution_quality"),
                "weather_at_report": weather,
                "critical_infrastructure": spatial_context.get("critical_infrastructure", []),
                "is_school_zone": spatial_context.get("is_school_zone", False),
                "nearby_outages": spatial_context.get("nearby_outages", 0),
                "vulnerable_pop_impact": spatial_context.get("vulnerable_pop_impact"),
                "computed_at": analysis_time.isoformat(),
            }
            if historical_context.get("similar_reports"):
                analysis["similar_reports"] = historical_context["similar_reports"]

            # ---- AI summary + suggested priority: only when AI is available. ----
            ai_ran = False
            if ai_module_on and ai_provider and (request.description or request.media_urls):
                try:
                    prompt = build_analysis_prompt(
                        request_data, historical_context=historical_context, spatial_context=spatial_context,
                    )
                    image_data = request.media_urls[:3] if request.media_urls else None
                    logger.info(f"[Analysis] Calling {ai_provider.provider} for request {request_id}...")
                    ai_result = await ai_provider.complete_json(prompt, image_data)
                    if isinstance(ai_result, dict):
                        if "_error" in ai_result:
                            logger.error(f"[Analysis] AI error: {ai_result.get('_error')}")
                            analysis["_error"] = ai_result["_error"]
                            # The adapters never raise -- a quota failure
                            # (Vertex 429/RESOURCE_EXHAUSTED, Azure 429,
                            # Bedrock ThrottlingException) arrives as this
                            # fallback dict and the report still processes
                            # with a manual-review default. That graceful
                            # part stays; the health note is what stops it
                            # being *invisible* to the administrator.
                            from app.services import connector_health
                            await connector_health.note_quota_failure(
                                "ai", ai_result["_error"], provider=ai_provider.provider)
                        else:
                            analysis.update(ai_result)
                            ai_ran = True
                            request.ai_summary = ai_result.get("qualitative_analysis", "")
                            request.ai_analyzed_at = datetime.now(timezone.utc)
                    # Keep the computed similar_reports even if the AI result omitted them.
                    if historical_context.get("similar_reports"):
                        analysis["similar_reports"] = historical_context["similar_reports"]
                    try:
                        from app.services.api_usage import track_api_usage
                        await track_api_usage(
                            db=db, service_name=ai_provider.provider, operation="analyze_request",
                            tokens_input=len(prompt) // 4, tokens_output=len(str(ai_result)) // 4,
                            request_id=request.service_request_id,
                        )
                    except Exception as e:
                        logger.warning(f"[Analysis] usage tracking failed: {e}")
                except Exception as ai_exc:
                    logger.warning("[Analysis] AI summary failed", exc_info=True)
                    # Adapters shouldn't raise, but a quota error surfacing as
                    # an exception still deserves the same flag as one arriving
                    # in the fallback dict above. note_quota_failure never
                    # raises, so this cannot re-break the swallowed branch.
                    from app.services import connector_health
                    await connector_health.note_quota_failure(
                        "ai", ai_exc, provider=ai_provider.provider)

            # Fold the AI photo/text assessment into the moderation flag (image
            # moderation lives here — it needs the vision model). Graceful no-op
            # when AI didn't run; the deterministic text scan already ran at
            # intake, so we only ever raise the flag, never clear it.
            try:
                from app.services.content_moderation import flags_from_ai_assessment
                ai_mod = flags_from_ai_assessment(analysis)
                if ai_mod.flagged and not request.flagged:
                    request.flagged = True
                    request.flag_reason = (request.flag_reason or ai_mod.reason())[:255]
            except Exception:
                logger.warning("[Moderation] AI assessment fold-in failed", exc_info=True)

            # Priority is never auto-applied — staff must accept the AI suggestion,
            # which is stored in ai_analysis['priority_score'] for reference.
            request.ai_analysis = analysis
            await db.commit()
            logger.info(f"[Analysis] Saved request {request_id} (ai_ran={ai_ran})")
            return {"status": "success", "ai_ran": ai_ran}
    
    try:
        result = run_async(_analyze())
        logger.info(f"[AI Analysis] Task completed: {result}")
        return result
    except Exception as exc:
        logger.error(f"[AI Analysis] Task failed with error: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(bind=True)
def geocode_address(self, request_id: int):
    """Geocode address to lat/long using configured service"""
    async def _geocode():
        async with SessionLocal() as db:
            result = await db.execute(
                select(ServiceRequest).where(ServiceRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            if not request or not request.address:
                return {"error": "Request or address not found"}
            
            # The town's chosen provider, via the same dispatcher /gis/geocode
            # uses -- not a hardcoded Google key.
            from app.services import geocode_dispatch
            geo_result = await geocode_dispatch.geocode(db, request.address)
            
            if geo_result:
                request.lat = geo_result.lat
                request.long = geo_result.lng
                await db.commit()
                return {
                    "status": "success",
                    "lat": geo_result.lat,
                    "lng": geo_result.lng,
                    "formatted_address": geo_result.formatted_address
                }
            else:
                return {"status": "geocoding_failed", "address": request.address}
    
    try:
        return run_async(_geocode())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)


@celery_app.task
def send_notification(request_id: int, notification_type: str):
    """Send notification (email/SMS) for request updates"""
    async def _notify():
        async with SessionLocal() as db:
            # Configure notification providers
            await configure_notifications(db)
            
            # Get the request
            result = await db.execute(
                select(ServiceRequest).where(ServiceRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            if not request:
                return {"error": "Request not found"}
            
            if notification_type == "confirmation":
                # Send confirmation for new request
                notification_service.send_request_confirmation(
                    request_id=str(request.service_request_id),
                    email=request.email,
                    phone=request.phone
                )
            elif notification_type == "status_update":
                # Send status update
                await notification_service.send_status_update(
                    request_id=str(request.service_request_id),
                    new_status=request.status,
                    email=request.email,
                    phone=request.phone
                )
            
            return {"status": "sent", "type": notification_type}
    
    try:
        return run_async(_notify())
    except Exception as e:
        return {"status": "error", "error": str(e)}


@celery_app.task
def send_branded_notification(request_id: int, notification_type: str, old_status: str = None, completion_message: str = None):
    """Send branded notification (email/SMS) using township branding from SystemSettings"""
    import logging
    logger = logging.getLogger(__name__)
    
    async def _notify():
        from app.models import SystemSettings
        
        async with SessionLocal() as db:
            # Configure notification providers
            await configure_notifications(db)
            
            # Get system settings for branding
            settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_result.scalar_one_or_none()
            
            township_name = settings.township_name if settings else "Your Township"
            logo_url = settings.logo_url if settings else None
            primary_color = settings.primary_color if settings else "#6366f1"
            
            # The same switch `configure_notifications` above consulted, rather
            # than a second copy of the question read from `modules`.
            from app.services import capability_switches
            email_enabled = await capability_switches.enabled("email")
            sms_enabled = await capability_switches.enabled("sms")

            if not email_enabled and not sms_enabled:
                logger.info(f"[Notification] Skipping - email and text messages are both switched off")
                return {"status": "skipped", "reason": "notifications disabled"}
            
            # Get custom domain for portal URL
            custom_domain = settings.custom_domain if settings else None
            if not custom_domain:
                custom_domain = os.environ.get('DOMAIN', '')
            portal_url = f"https://{custom_domain}" if custom_domain and custom_domain != 'localhost' else "http://localhost:5173"
            
            # Get the request
            result = await db.execute(
                select(ServiceRequest).where(ServiceRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            if not request:
                return {"error": "Request not found"}
            
            logger.info(f"[Notification] Sending {notification_type} for request {request.service_request_id} (email={email_enabled}, sms={sms_enabled})")
            
            # Get user's preferred language for translation
            preferred_lang = request.preferred_language or "en"
            logger.info(f"[Notification] User language: {preferred_lang}")
            
            # Translate content if needed (not English)
            async def translate_if_needed(text: str) -> str:
                """Translate text to user's preferred language if not English"""
                if not text or preferred_lang == "en":
                    return text
                
                try:
                    from app.services.translate import translate_text
                    return await translate_text(text, preferred_lang, "en")
                except Exception as e:
                    logger.warning(f"[Notification] Translation failed: {e}, using original text")
                    return text
            
            if notification_type == "confirmation":
                # Translate service name and description for confirmation
                service_name_translated = await translate_if_needed(request.service_name)
                description_translated = await translate_if_needed(request.description or "")
                
                # Send branded confirmation email if enabled
                if email_enabled and request.email:
                    await notification_service.send_request_confirmation_branded_async(
                        request_id=str(request.service_request_id),
                        service_name=service_name_translated,
                        description=description_translated,
                        address=request.address,
                        email=request.email,
                        phone=request.phone,
                        township_name=township_name,
                        logo_url=logo_url,
                        primary_color=primary_color,
                        portal_url=portal_url,
                        language=preferred_lang
                    )
                
                # Also send SMS if enabled and phone provided
                if sms_enabled and request.phone:
                    from app.services.email_templates import build_sms_confirmation
                    sms_message = build_sms_confirmation(
                        request.service_request_id, 
                        township_name, 
                        portal_url,
                        service_name=service_name_translated,
                        description=description_translated,
                        address=request.address or ""
                    )
                    # Translate SMS message as well
                    sms_translated = await translate_if_needed(sms_message)
                    await notification_service.send_sms(request.phone, sms_translated)
                    
            elif notification_type == "status_update":
                # Translate service name and completion message
                service_name_translated = await translate_if_needed(request.service_name)
                completion_msg_translated = await translate_if_needed(completion_message or request.completion_message or "")
                
                # Send branded status update (checks internally for email/phone)
                await notification_service.send_status_update_branded(
                    request_id=str(request.service_request_id),
                    service_name=service_name_translated,
                    old_status=old_status or "open",
                    new_status=request.status,
                    completion_message=completion_msg_translated if completion_msg_translated else None,
                    completion_photo_url=request.completion_photo_url,
                    email=request.email if email_enabled else None,
                    phone=request.phone if sms_enabled else None,
                    township_name=township_name,
                    logo_url=logo_url,
                    primary_color=primary_color,
                    portal_url=portal_url,
                    language=preferred_lang
                )
            
            return {
                "status": "sent", 
                "type": notification_type, 
                "email": email_enabled, 
                "sms": sms_enabled,
                "language": preferred_lang
            }
    
    try:
        return run_async(_notify())
    except Exception as e:
        logger.error(f"[Notification] Failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task
def send_comment_notification_task(request_id: int, comment_author: str, comment_content: str):
    """Send notification when staff leaves a public comment on a request"""
    import logging
    logger = logging.getLogger(__name__)
    
    async def _notify():
        from app.models import SystemSettings
        
        async with SessionLocal() as db:
            # Configure notification providers
            await configure_notifications(db)
            
            # Get system settings for branding
            settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_result.scalar_one_or_none()
            
            township_name = settings.township_name if settings else "Your Township"
            logo_url = settings.logo_url if settings else None
            primary_color = settings.primary_color if settings else "#6366f1"
            custom_domain = settings.custom_domain if settings else None
            if not custom_domain:
                custom_domain = os.environ.get('DOMAIN', '')
            portal_url = f"https://{custom_domain}" if custom_domain and custom_domain != 'localhost' else "http://localhost:5173"
            
            # Get the request
            result = await db.execute(
                select(ServiceRequest).where(ServiceRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            if not request:
                return {"error": "Request not found"}
            
            logger.info(f"[Notification] Sending comment notification for request {request.service_request_id} to {request.email}")
            
            # Get user's preferred language for translation
            preferred_lang = request.preferred_language or "en"
            
            # Translate content if needed
            async def translate_if_needed(text: str) -> str:
                """Translate text to user's preferred language if not English"""
                if not text or preferred_lang == "en":
                    return text
                
                try:
                    from app.services.translate import translate_text
                    return await translate_text(text, preferred_lang, "en")
                except Exception as e:
                    logger.warning(f"[Notification] Translation failed: {e}, using original text")
                    return text
            
            # Translate service name and comment content
            service_name_translated = await translate_if_needed(request.service_name)
            comment_content_translated = await translate_if_needed(comment_content)
            
            # Send comment notification using async version with language support
            await notification_service.send_comment_notification_async(
                request_id=str(request.service_request_id),
                service_name=service_name_translated,
                comment_author=comment_author,
                comment_content=comment_content_translated,
                email=request.email,
                township_name=township_name,
                logo_url=logo_url,
                primary_color=primary_color,
                portal_url=portal_url,
                language=preferred_lang
            )
            
            return {"status": "sent", "type": "comment", "language": preferred_lang}
    
    try:
        return run_async(_notify())
    except Exception as e:
        logger.error(f"[Notification] Comment notification failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task
def send_department_notification(request_id: int, department_email: str = None):
    """Notify staff of a new request, honoring each user's notification preferences.

    `department_email` is optional: it is the routed department's routing_email
    when one is configured, used only as the no-recipients archive fallback and
    as a legacy lookup for messages queued by older API code. Recipients come
    from the request itself — the assignee, else the routed department's staff,
    else the town's active admins (small towns often have no department
    memberships at all, and their preference toggles must still mean something).
    """
    import logging
    logger = logging.getLogger(__name__)
    
    async def _notify():
        from app.models import SystemSettings, Department
        
        async with SessionLocal() as db:
            await configure_notifications(db)
            
            # Get system settings for branding and module checks
            settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_result.scalar_one_or_none()
            
            from app.services import capability_switches
            sms_enabled_globally = await capability_switches.enabled("sms")
            
            township_name = settings.township_name if settings else "Your Township"
            logo_url = settings.logo_url if settings else None
            primary_color = settings.primary_color if settings else "#6366f1"
            custom_domain = settings.custom_domain if settings else None
            if not custom_domain:
                custom_domain = os.environ.get('DOMAIN', '')
            portal_url = f"https://{custom_domain}" if custom_domain and custom_domain != 'localhost' else "http://localhost:5173"
            
            # Get the request
            result = await db.execute(
                select(ServiceRequest).where(ServiceRequest.id == request_id)
            )
            request = result.scalar_one_or_none()
            if not request:
                return {"error": "Request not found"}
            
            # Resolve the routed department from the request itself; the
            # routing_email lookup remains only for messages queued by older
            # API code that passed the email without the request being routed.
            department = None
            if request.assigned_department_id:
                dept_result = await db.execute(
                    select(Department).where(Department.id == request.assigned_department_id)
                )
                department = dept_result.scalar_one_or_none()
            if department is None and department_email:
                dept_result = await db.execute(
                    select(Department).where(Department.routing_email == department_email)
                )
                department = dept_result.scalar_one_or_none()

            notified_staff = []

            # Recipient tiers: assignee → department staff → active admins.
            # The admin tier is what keeps the Notification Settings toggles
            # alive in a town with no department memberships configured.
            from app.models import User, user_departments
            from app.services.notification_rules import pick_recipient_group

            assignee = None
            if request.assigned_to:
                assignee_result = await db.execute(
                    select(User)
                    .where(User.username == request.assigned_to)
                    .where(User.is_active == True)
                )
                assignee = assignee_result.scalar_one_or_none()

            department_staff = []
            if department:
                staff_result = await db.execute(
                    select(User)
                    .join(user_departments)
                    .where(user_departments.c.department_id == department.id)
                    .where(User.is_active == True)
                )
                department_staff = list(staff_result.scalars().all())

            admin_result = await db.execute(
                select(User).where(User.role == "admin").where(User.is_active == True)
            )
            admins = list(admin_result.scalars().all())

            staff_members = pick_recipient_group(assignee, department_staff, admins)

            if staff_members:
                from app.services.notification_rules import should_notify_staff
                for staff in staff_members:
                    prefs = staff.notification_preferences or {}

                    # Enforce Assigned Only + the new-request email/SMS toggles.
                    is_assigned_to_me = bool(request.assigned_to) and staff.username == request.assigned_to
                    send_email, send_sms = should_notify_staff(
                        prefs, "new_requests",
                        is_assigned_to_me=is_assigned_to_me,
                        sms_enabled_globally=sms_enabled_globally,
                    )
                    if not send_email and not send_sms:
                        continue
                    
                    # Build notification content
                    subject = f"New request: {request.service_name}"
                    staff_link = f"{portal_url}/staff#request/{request.service_request_id}"

                    message = L.build_email(
                        subject=subject,
                        township_name=township_name,
                        logo_url=logo_url,
                        primary_color=primary_color,
                        preheader=f"{request.service_request_id} - {request.service_name}",
                        # No greeting and no "a new service request has been
                        # submitted to your department": the heading says that,
                        # and a line that restates the heading is a line the
                        # reader has to skip before reaching the address.
                        blocks=[
                            L.heading("New request assigned", 2),
                            L.fields([
                                ("Request ID", request.service_request_id),
                                ("Category", request.service_name),
                                ("Address", request.address or "Not provided"),
                                ("Description", (request.description or "")[:200]),
                            ]),
                            L.button("View request in the staff dashboard", staff_link),
                        ],
                        footer_lines=[
                            f"You're receiving this because you're staff at {township_name}.",
                        ],
                    )

                    # Send email if enabled
                    if send_email and staff.email:
                        notification_service.send_email(
                            to=staff.email,
                            subject=message["subject"],
                            body_html=message["html"],
                            body_text=message["text"],
                            from_name=f"{township_name} 311"
                        )
                        notified_staff.append({"email": staff.email, "type": "email"})

                    # Send SMS if enabled globally and by user preference
                    if send_sms and staff.phone:
                        short_desc = (request.description or "")[:50]
                        sms_message = f"""{township_name} 311
New request: {request.service_name}
"{short_desc}..."
{request.address or 'No address'}

{staff_link}"""
                        await notification_service.send_sms(staff.phone, sms_message)
                        notified_staff.append({"phone": staff.phone, "type": "sms"})
            
            # Also send to department email as fallback/archive
            if department_email and not notified_staff:
                subject = f"New service request: #{request.service_request_id} - {request.service_name}"
                message = L.build_email(
                    subject=subject,
                    township_name=township_name,
                    logo_url=logo_url,
                    primary_color=primary_color,
                    preheader=f"{request.service_request_id} - {request.service_name}",
                    blocks=[
                        L.heading("New service request received", 2),
                        L.fields([
                            ("Request ID", request.service_request_id),
                            ("Category", request.service_name),
                            ("Description", request.description or ""),
                            ("Address", request.address or "Not provided"),
                            # The town's wall clock, not the stored UTC. A raw
                            # `2026-08-18 02:14:00+00:00` in a field list is
                            # both unreadable and, for a clerk in New Jersey,
                            # the wrong evening.
                            ("Submitted", format_town(
                                request.requested_datetime,
                                getattr(settings, "timezone", None))),
                        ]),
                        L.button("Open the staff dashboard", f"{portal_url}/staff"),
                    ],
                    footer_lines=[
                        f"This address is the routing address for a department at {township_name}.",
                    ],
                )
                notification_service.send_email(
                    to=department_email,
                    subject=message["subject"],
                    body_html=message["html"],
                    body_text=message["text"],
                    from_name=f"{township_name} 311"
                )
                notified_staff.append({"email": department_email, "type": "fallback"})
            
            logger.info(f"[Dept Notification] Sent to {len(notified_staff)} recipients for request {request.service_request_id}")
            return {"status": "sent", "recipients": notified_staff}

    try:
        return run_async(_notify())
    except Exception as e:
        return {"status": "error", "error": str(e)}


@celery_app.task
def notify_staff_of_activity(request_id: int, event: str, actor: str = None):
    """Notify assigned staff / department about activity on an existing request,
    honoring each user's notification preferences.

    event: 'status_changes' or 'comments' — maps to the email_/sms_ preference
    keys and the copy. `actor` is the username who triggered it and is skipped,
    so a staffer isn't emailed about their own action. Recipients are the routed
    department's staff plus the specifically-assigned user; anyone with
    "Assigned Only" set is included only when the request is assigned to them.
    """
    import logging
    logger = logging.getLogger(__name__)

    label = "Status updated" if event == "status_changes" else "New comment"

    async def _notify():
        from app.models import SystemSettings, User, user_departments

        async with SessionLocal() as db:
            await configure_notifications(db)

            settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_result.scalar_one_or_none()
            from app.services import capability_switches
            sms_enabled_globally = await capability_switches.enabled("sms")
            township_name = settings.township_name if settings else "Your Township"
            logo_url = settings.logo_url if settings else None
            primary_color = settings.primary_color if settings else "#6366f1"
            custom_domain = (settings.custom_domain if settings else None) or os.environ.get('DOMAIN', '')
            portal_url = f"https://{custom_domain}" if custom_domain and custom_domain != 'localhost' else "http://localhost:5173"

            result = await db.execute(select(ServiceRequest).where(ServiceRequest.id == request_id))
            request = result.scalar_one_or_none()
            if not request:
                return {"error": "Request not found"}

            # Recipient default: the specifically-assigned person. If nobody is
            # assigned, notify whoever last worked the request — the most recent
            # staff member (other than the current actor) to change its status or
            # comment on it — so the person handling it stays in the loop. Only if
            # there's no prior toucher either do we fall back to the department.
            recipients = {}
            if request.assigned_to:
                assignee_result = await db.execute(
                    select(User)
                    .where(User.username == request.assigned_to)
                    .where(User.is_active == True)
                )
                assignee = assignee_result.scalar_one_or_none()
                if assignee:
                    recipients[assignee.id] = assignee
            else:
                from app.models import RequestComment, RequestAuditLog
                last_username, last_ts = None, None

                # Most recent staff status-change / edit from the audit trail.
                audit_rows = await db.execute(
                    select(RequestAuditLog.actor_name, RequestAuditLog.created_at)
                    .where(RequestAuditLog.service_request_id == request.id)
                    .where(RequestAuditLog.actor_type.in_(["staff", "admin"]))
                    .where(RequestAuditLog.actor_name.isnot(None))
                    .order_by(RequestAuditLog.created_at.desc())
                )
                for name, ts in audit_rows.all():
                    if actor and name == actor:
                        continue
                    last_username, last_ts = name, ts
                    break

                # Most recent comment author (comments are staff-authored).
                comment_rows = await db.execute(
                    select(RequestComment.username, RequestComment.created_at)
                    .where(RequestComment.service_request_id == request.id)
                    .order_by(RequestComment.created_at.desc())
                )
                for name, ts in comment_rows.all():
                    if actor and name == actor:
                        continue
                    if last_ts is None or (ts and ts > last_ts):
                        last_username, last_ts = name, ts
                    break

                if last_username:
                    u_result = await db.execute(
                        select(User)
                        .where(User.username == last_username)
                        .where(User.is_active == True)
                    )
                    u = u_result.scalar_one_or_none()
                    if u:
                        recipients[u.id] = u

                # No assignee and no prior toucher — fall back to the department.
                if not recipients and request.assigned_department_id:
                    staff_result = await db.execute(
                        select(User)
                        .join(user_departments)
                        .where(user_departments.c.department_id == request.assigned_department_id)
                        .where(User.is_active == True)
                    )
                    for s in staff_result.scalars().all():
                        recipients[s.id] = s

            # Last tier: nobody assigned, nobody has touched it, and the routed
            # department has no members (or the assignee was deactivated) — the
            # normal shape for a small town with no department memberships.
            # Route to the active admins so the event is not silently dropped;
            # should_notify_staff below still skips the actor and applies each
            # admin's own preference toggles.
            if not recipients:
                admin_result = await db.execute(
                    select(User).where(User.role == "admin").where(User.is_active == True)
                )
                for a in admin_result.scalars().all():
                    recipients[a.id] = a

            staff_link = f"{portal_url}/staff#request/{request.service_request_id}"
            status_text = getattr(request.status, 'value', request.status) or ''
            subject = f"{label}: {request.service_request_id} — {request.service_name}"

            from app.services.notification_rules import should_notify_staff
            notified = []
            for staff in recipients.values():
                prefs = staff.notification_preferences or {}
                is_assigned_to_me = bool(request.assigned_to) and staff.username == request.assigned_to
                send_email, send_sms = should_notify_staff(
                    prefs, event,
                    is_assigned_to_me=is_assigned_to_me,
                    is_actor=bool(actor) and staff.username == actor,
                    sms_enabled_globally=sms_enabled_globally,
                )

                if send_email and staff.email:
                    message = L.build_email(
                        subject=subject,
                        township_name=township_name,
                        logo_url=logo_url,
                        primary_color=primary_color,
                        preheader=f"{label} - {request.service_request_id}",
                        # The greeting carried nothing, and the sentence under
                        # it was the heading lower-cased with four words after
                        # it -- a fragment assembled in English word order that
                        # said no more than the heading already had.
                        blocks=[
                            L.heading(label, 2),
                            L.fields([
                                ("Request", f"{request.service_request_id} - {request.service_name}"),
                                ("Status", status_text),
                                ("Address", request.address or "Not provided"),
                            ]),
                            L.button("View request in the staff dashboard", staff_link),
                        ],
                        footer_lines=[
                            f"You're receiving this because you're staff at {township_name}.",
                        ],
                    )
                    notification_service.send_email(
                        to=staff.email, subject=message["subject"],
                        body_html=message["html"], body_text=message["text"],
                        from_name=f"{township_name} 311"
                    )
                    notified.append(staff.email)

                if send_sms and staff.phone:
                    await notification_service.send_sms(
                        staff.phone,
                        f"{township_name} 311 — {label} on {request.service_request_id}: {status_text}\n{staff_link}"
                    )
                    notified.append(staff.phone)

            logger.info(f"[Staff Activity:{event}] notified {len(notified)} for request {request.service_request_id}")
            return {"status": "sent", "recipients": notified}

    try:
        return run_async(_notify())
    except Exception as e:
        return {"status": "error", "error": str(e)}


@celery_app.task
def reap_expired_photo_handles():
    """Delete photos screened at pick time that no report ever claimed.

    The resident portal screens a photo when it is chosen and hands the browser
    a handle; the handle is spent -- and the row deleted -- when the report is
    submitted. Residents abandon forms, so a fraction of those rows are never
    spent, and every one of them is a full-size image in a table an
    unauthenticated endpoint can write to.

    Hourly, because the handles only live an hour. Anything past `expires_at`
    is already unredeemable, so this deletes nothing a submission could still
    have used.
    """
    import logging
    logger = logging.getLogger(__name__)

    async def _reap():
        from app.services import photo_handles

        async with SessionLocal() as db:
            gone = await photo_handles.reap(db)
            await db.commit()
            if gone:
                logger.info("[Photo handles] Reaped %d unclaimed screened photo(s)", gone)
            return {"status": "success", "reaped": gone}

    try:
        return run_async(_reap())
    except Exception as e:
        logger.error(f"[Photo handles] Reap failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task
def purge_old_ip_addresses():
    """Null out IP addresses older than 90 days.

    The Privacy Impact Assessment commits to a 90-day retention for IP
    addresses while audit logs themselves are kept for 7 years, so we scrub
    the IP field rather than deleting the log entry.
    """
    import logging
    logger = logging.getLogger(__name__)

    async def _purge():
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import update
        from app.models import AuditLog, DisclaimerAcknowledgment

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        async with SessionLocal() as db:
            r1 = await db.execute(
                update(AuditLog)
                .where(AuditLog.timestamp < cutoff, AuditLog.ip_address.isnot(None))
                .values(ip_address=None, user_agent=None)
            )
            r2 = await db.execute(
                update(DisclaimerAcknowledgment)
                .where(DisclaimerAcknowledgment.acknowledged_at < cutoff,
                       DisclaimerAcknowledgment.ip_address.isnot(None))
                .values(ip_address=None)
            )
            await db.commit()
            purged = (r1.rowcount or 0) + (r2.rowcount or 0)
            logger.info(f"[Retention] Purged IP addresses from {purged} record(s) older than 90 days")
            return {"status": "success", "purged": purged}

    try:
        return run_async(_purge())
    except Exception as e:
        logger.error(f"[Retention] IP purge failed: {e}")
        return {"status": "error", "error": str(e)}


# How many records one query pulls, and how many such queries one run makes.
#
# The batch keeps transactions short; the ceiling is a runaway guard rather than
# a policy, and at 200,000 records it is far above any town's real backlog. A
# run that reaches it says so in its result instead of reporting success.
BATCH_SIZE = 200
MAX_BATCHES = 1000


@celery_app.task
def enforce_retention_policy():
    """
    Enforce document retention policy by archiving expired records.

    Should be scheduled to run daily via Celery Beat.
    Respects legal holds (flagged records are never archived).
    """
    import logging
    logger = logging.getLogger(__name__)

    async def _enforce():
        from app.models import SystemSettings
        from app.services.retention_config import read_retention_config
        from app.services.retention_service import (
            get_records_for_archival,
            archive_record,
        )

        async with SessionLocal() as db:
            # Get retention settings
            settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_result.scalar_one_or_none()

            # Instance-wide legal hold: freeze ALL purging until it is lifted.
            if settings is not None and getattr(settings, "legal_hold", False):
                logger.info("[Retention] Legal hold is active — purge suspended, nothing archived")
                return {"status": "skipped_legal_hold", "archived": 0}

            # No period, or nothing chosen to remove, and the run stops here.
            #
            # There is no fallback left to take. This used to reach for a table
            # of per-state periods the product had invented, so a town in Texas
            # quietly anonymised records on a seven-year clock nobody had
            # checked, with a nightly job reporting success. Guessing is the one
            # thing this must not do: a wrong guess destroys resident data that
            # cannot be restored, while stopping costs a town some records kept
            # too long and leaves a warning on the console saying exactly why.
            config = read_retention_config(settings)
            if not config.configured:
                # The console learns this from the proactive health check,
                # which reads the same settings row live — nothing to record
                # here, and nothing that can go stale between runs.
                logger.warning("[Retention] Not configured (%s) — nothing archived. %s",
                               config.reason, config.detail)
                return {
                    "status": "skipped_unconfigured",
                    "archived": 0,
                    "reason": config.reason,
                    "message": config.detail,
                }

            retention_days = config.retention_days
            archive_mode = config.mode
            scrub_fields = config.scrub_fields

            logger.info("[Retention] Enforcing this town's schedule: %s days, %s, clearing %s",
                        retention_days, archive_mode, ", ".join(scrub_fields) or "nothing")
            
            # Everything eligible, in batches.
            #
            # This used to take the first hundred and stop, with no sign on
            # screen that it had. A town with five thousand expired records
            # needed fifty presses of a button that looked like it had finished,
            # or fifty nights -- and the retention policy it publishes says the
            # records are gone, so the gap between the claim and the database
            # widened every day.
            #
            # Batched rather than one query, because the whole point is that
            # this set can be large: a single transaction over five thousand
            # rows holds locks for the duration and fails all-or-nothing.
            #
            # `skipped` is what stops this looping forever. Records under legal
            # hold stay eligible by design -- they are past their date and must
            # not be touched -- so a batch of nothing but held records would be
            # fetched again for ever. Once a pass archives none of what it was
            # given, there is nothing left this run can act on.
            archived_count = 0
            skipped_count = 0
            errors = []
            batches = 0

            while True:
                records = await get_records_for_archival(db, retention_days, limit=BATCH_SIZE)
                if not records:
                    break
                batches += 1
                archived_this_batch = 0
                for record in records:
                    try:
                        result = await archive_record(db, record.id, archive_mode, scrub_fields)
                        if result["status"] in ["anonymized", "deleted"]:
                            archived_count += 1
                            archived_this_batch += 1
                        else:
                            skipped_count += 1
                            logger.info(
                                "[Retention] Skipped %s: %s",
                                record.service_request_id, result.get("message"),
                            )
                    except Exception as e:
                        errors.append({"record_id": record.id, "error": str(e)})
                        logger.error("[Retention] Error archiving %s: %s", record.id, e)
                if archived_this_batch == 0:
                    # Nothing in that batch could be acted on, and the query is
                    # deterministic, so the next one would return the same rows.
                    break
                if batches >= MAX_BATCHES:
                    logger.warning(
                        "[Retention] Stopped after %s batches with records still eligible. "
                        "Run again to continue.", batches,
                    )
                    break

            logger.info(
                "[Retention] %s archived, %s skipped, %s errors across %s batches",
                archived_count, skipped_count, len(errors), batches,
            )
            return {
                "status": "success",
                "retention_days": retention_days,
                "mode": archive_mode,
                "archived": archived_count,
                "skipped": skipped_count,
                "errors": len(errors),
                "batches": batches,
                # True when the cap stopped it early, so a caller can say so
                # rather than reporting a finished job that is not finished.
                "more_remaining": batches >= MAX_BATCHES,
            }
    
    try:
        return run_async(_enforce())
    except Exception as e:
        logger.error(f"[Retention] Task failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task
def backup_database():
    """
    Create an encrypted database backup and upload to S3.
    
    Should be scheduled to run daily via Celery Beat.
    Backups are encrypted with AES-256 using the BACKUP_ENCRYPTION_KEY secret.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    async def _backup():
        # "Automatic backups" is a tick on the setup page, and unticking it used
        # to change nothing -- beat kept firing this and backups kept being
        # written and shipped to S3. A town that said no to off-site copies of
        # its database meant it.
        from app.services import capability_switches
        if not await capability_switches.enabled("backups"):
            logger.info("[Backup] Skipped: the town has switched automatic backups off")
            return {"status": "skipped", "message": "automatic backups are switched off"}
        from app.services.backup_service import create_backup
        return await create_backup()

    try:
        logger.info("[Backup] Starting scheduled database backup...")
        result = run_async(_backup())
        if result.get("status") == "skipped":
            return result
        
        if result["status"] == "success":
            logger.info(f"[Backup] Completed successfully: {result['backup_name']} ({result['size_bytes']} bytes)")
        else:
            logger.error(f"[Backup] Failed: {result.get('message', 'Unknown error')}")
        
        return result
    except Exception as e:
        logger.error(f"[Backup] Task failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task
def cleanup_expired_backups():
    """
    Delete database backups older than the retention period.

    Should be scheduled to run weekly via Celery Beat.
    Uses the retention period this town configured. If it has not configured
    one, nothing is deleted — see cleanup_old_backups.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    async def _cleanup():
        from app.services.backup_service import cleanup_old_backups
        return await cleanup_old_backups()
    
    try:
        logger.info("[Backup Cleanup] Starting expired backup cleanup...")
        result = run_async(_cleanup())
        
        if result["status"] == "success":
            logger.info(f"[Backup Cleanup] Deleted {result['deleted_count']} expired backups")
        else:
            logger.error(f"[Backup Cleanup] Failed: {result.get('message', 'Unknown error')}")
        
        return result
    except Exception as e:
        logger.error(f"[Backup Cleanup] Task failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task
def send_weekly_digest():
    """
    Send weekly digest email to staff with summary of open requests.
    
    Should be scheduled to run weekly via Celery Beat (Monday mornings).
    Respects individual staff notification preferences.
    """
    import logging
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func, and_
    logger = logging.getLogger(__name__)
    
    async def _send_digest():
        from app.models import User, SystemSettings, Department, user_departments
        
        async with SessionLocal() as db:
            await configure_notifications(db)
            
            # Get system settings for branding
            settings_result = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_result.scalar_one_or_none()
            
            township_name = settings.township_name if settings else "Your Township"
            logo_url = settings.logo_url if settings else None
            primary_color = settings.primary_color if settings else "#6366f1"
            custom_domain = settings.custom_domain if settings else None
            if not custom_domain:
                custom_domain = os.environ.get('DOMAIN', '')
            portal_url = f"https://{custom_domain}" if custom_domain and custom_domain != 'localhost' else "http://localhost:5173"
            
            from app.services import capability_switches
            if not await capability_switches.enabled("email"):
                logger.info("[Weekly Digest] Skipping - email notifications disabled")
                return {"status": "skipped", "reason": "email notifications disabled"}
            
            # Get all active staff members
            staff_result = await db.execute(
                select(User).where(
                    and_(
                        User.is_active == True,
                        User.role.in_(["staff", "admin"])
                    )
                )
            )
            staff_members = staff_result.scalars().all()
            
            sent_count = 0
            skipped_count = 0
            
            for staff in staff_members:
                # Check notification preferences
                prefs = staff.notification_preferences or {}
                # Weekly digest is controlled by email_new_requests (or add dedicated pref later)
                if not prefs.get('email_new_requests', True):
                    skipped_count += 1
                    continue
                
                if not staff.email:
                    skipped_count += 1
                    continue
                
                # Get staff's departments
                dept_query = await db.execute(
                    select(Department)
                    .join(user_departments)
                    .where(user_departments.c.user_id == staff.id)
                )
                departments = dept_query.scalars().all()
                dept_ids = [d.id for d in departments]
                
                # Get request statistics for staff's departments (or all if admin)
                from sqlalchemy import case
                if staff.role == "admin" or not dept_ids:
                    # Admins see all departments
                    stats_query = select(
                        func.count(ServiceRequest.id).label('total'),
                        func.sum(case((ServiceRequest.status == 'open', 1), else_=0)).label('open_count'),
                        func.sum(case((ServiceRequest.status == 'in_progress', 1), else_=0)).label('in_progress'),
                        # Deliberately not called "overdue". Seven days is a
                        # threshold written here and nowhere else -- no town
                        # configured it, and it has no relation to the opt-in
                        # per-category `sla_hours` that is the only deadline
                        # this system actually knows about. Counting requests
                        # by age is a fact; calling them late is a judgement
                        # made on the town's behalf that nobody agreed to.
                        func.sum(case((and_(
                            ServiceRequest.status.in_(['open', 'in_progress']),
                            ServiceRequest.requested_datetime < datetime.now(timezone.utc) - timedelta(days=7)
                        ), 1), else_=0)).label('open_over_7_days')
                    ).where(
                        and_(
                            ServiceRequest.deleted_at.is_(None),
                            ServiceRequest.status.in_(['open', 'in_progress'])
                        )
                    )
                else:
                    stats_query = select(
                        func.count(ServiceRequest.id).label('total'),
                        func.sum(case((ServiceRequest.status == 'open', 1), else_=0)).label('open_count'),
                        func.sum(case((ServiceRequest.status == 'in_progress', 1), else_=0)).label('in_progress'),
                        # Deliberately not called "overdue". Seven days is a
                        # threshold written here and nowhere else -- no town
                        # configured it, and it has no relation to the opt-in
                        # per-category `sla_hours` that is the only deadline
                        # this system actually knows about. Counting requests
                        # by age is a fact; calling them late is a judgement
                        # made on the town's behalf that nobody agreed to.
                        func.sum(case((and_(
                            ServiceRequest.status.in_(['open', 'in_progress']),
                            ServiceRequest.requested_datetime < datetime.now(timezone.utc) - timedelta(days=7)
                        ), 1), else_=0)).label('open_over_7_days')
                    ).where(
                        and_(
                            ServiceRequest.deleted_at.is_(None),
                            ServiceRequest.status.in_(['open', 'in_progress']),
                            ServiceRequest.assigned_department_id.in_(dept_ids)
                        )
                    )
                
                stats_result = await db.execute(stats_query)
                stats = stats_result.first()
                
                total = stats.total or 0
                open_count = int(stats.open_count or 0)
                in_progress = int(stats.in_progress or 0)
                open_over_7_days = int(stats.open_over_7_days or 0)
                
                # Skip if no open requests
                if total == 0:
                    skipped_count += 1
                    continue
                
                # Get top 5 oldest open requests
                oldest_query = select(ServiceRequest).where(
                    and_(
                        ServiceRequest.deleted_at.is_(None),
                        ServiceRequest.status.in_(['open', 'in_progress'])
                    )
                )
                if dept_ids and staff.role != "admin":
                    oldest_query = oldest_query.where(ServiceRequest.assigned_department_id.in_(dept_ids))
                oldest_query = oldest_query.order_by(ServiceRequest.requested_datetime.asc()).limit(5)
                
                oldest_result = await db.execute(oldest_query)
                oldest_requests = oldest_result.scalars().all()
                
                # The oldest open requests, as rows of a real table -- the
                # digest's old three-column "flex" summary was the clearest
                # example of the whole problem: it stacked into a single
                # unreadable column in Outlook, because Word has no flexbox.
                rows = []
                for req in oldest_requests:
                    age_days = (datetime.now(timezone.utc) - req.requested_datetime).days if req.requested_datetime else 0
                    age_str = f"{age_days}d" if age_days > 0 else "Today"
                    rows.append([
                        req.service_request_id,
                        req.service_name,
                        str(getattr(req.status, "value", req.status) or "").replace("_", " "),
                        age_str,
                    ])

                subject = f"Weekly digest: {total} open requests - {township_name} 311"
                message = L.build_email(
                    subject=subject,
                    township_name=township_name,
                    logo_url=logo_url,
                    primary_color=primary_color,
                    preheader=(f"{open_count} open, {in_progress} in progress, "
                               f"{open_over_7_days} open over 7 days"),
                    blocks=[
                        L.heading("Weekly digest", 2),
                        L.stats([
                            ("Open", open_count),
                            ("In progress", in_progress),
                            ("Open 7+ days", open_over_7_days),
                        ]),
                        L.heading("Oldest open requests", 3),
                        L.table(["ID", "Category", "Status", "Age"], rows) if rows
                        else L.paragraph("Nothing has been waiting -- the queue is clear."),
                        L.button("View all requests", f"{portal_url}/staff"),
                    ],
                    footer_lines=[
                        f"You're receiving this because you're staff at {township_name}.",
                        f"Manage notification preferences: {portal_url}/staff/settings",
                    ],
                )

                # Send email
                notification_service.send_email(
                    to=staff.email,
                    subject=message["subject"],
                    body_html=message["html"],
                    body_text=message["text"],
                    from_name=f"{township_name} 311",
                )
                sent_count += 1
                logger.info(f"[Weekly Digest] Sent to {staff.email}")
            
            return {
                "status": "success",
                "sent": sent_count,
                "skipped": skipped_count
            }
    
    try:
        logger.info("[Weekly Digest] Starting weekly digest emails...")
        result = run_async(_send_digest())
        logger.info(f"[Weekly Digest] Completed: {result}")
        return result
    except Exception as e:
        logger.error(f"[Weekly Digest] Task failed: {e}")
        return {"status": "error", "error": str(e)}



@celery_app.task(bind=True)
def anchor_audit_chain(self):
    """Record the request-audit hash-chain head to the append-only AuditAnchor
    table AND emit it to the application log. Runs daily via Celery Beat.

    This puts the chain head outside the mutable audit table (and, in hosted
    mode, outside the instance entirely via log shipping), so a full-history
    rewrite can't pass silently — the anchors and external logs would disagree.
    """
    from app.models import RequestAuditLog, AuditAnchor
    from sqlalchemy import func as _func

    async def _anchor():
        async with SessionLocal() as db:
            head = (await db.execute(
                select(RequestAuditLog.entry_hash)
                .where(RequestAuditLog.entry_hash.isnot(None))
                .order_by(RequestAuditLog.id.desc()).limit(1)
            )).scalar()
            if not head:
                return {"status": "skipped", "reason": "no hashed audit entries yet"}
            count = (await db.execute(
                select(_func.count()).select_from(RequestAuditLog)
                .where(RequestAuditLog.entry_hash.isnot(None))
            )).scalar() or 0
            last = (await db.execute(
                select(AuditAnchor).order_by(AuditAnchor.id.desc()).limit(1)
            )).scalar_one_or_none()
            if last and last.head_hash == head:
                return {"status": "unchanged", "head": head, "count": count}
            db.add(AuditAnchor(head_hash=head, entry_count=count))
            await db.commit()
            # External-visible line (shipped to central log aggregation in hosted mode)
            logger.info(f"[AUDIT ANCHOR] head={head} count={count}")
            return {"status": "anchored", "head": head, "count": count}

    try:
        return run_async(_anchor())
    except Exception as e:
        logger.error(f"[AUDIT ANCHOR] task failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task(name="app.tasks.service_requests.refresh_ai_models")
def refresh_ai_models():
    """Daily: live-discover each configured AI provider's current models into the
    shared cache, so the model picker stays current even when no admin is in the
    UI. Logs a warning when the configured model is no longer offered (the exact
    situation where a retired preview id would otherwise fail silently)."""
    import logging as _logging
    _log = _logging.getLogger(__name__)

    async def _refresh():
        from app.services.ai.registry import AI_CATALOG, AI_PROVIDER_KEY
        from app.services.ai import model_discovery as md
        from app.services.secret_manager import get_secret as _get
        results = {}
        async with SessionLocal() as db:
            current_provider = (await _get(AI_PROVIDER_KEY)) or "vertex"
            for provider in AI_CATALOG.keys():
                creds = await md.provider_creds(provider)
                # Skip providers with no credentials configured — nothing to list.
                if not creds:
                    continue
                try:
                    entry = await md.refresh_provider(db, provider)
                except Exception as e:  # never let one provider break the sweep
                    _log.info(f"[AI models] refresh failed for {provider}: {e}")
                    continue
                results[provider] = entry.get("source")
                if provider == current_provider and entry.get("current_model") \
                        and not entry.get("current_model_available"):
                    _log.warning(
                        f"[AI models] configured model '{entry.get('current_model')}' for "
                        f"provider '{provider}' is no longer offered — an admin should pick a "
                        f"current model in Setup → AI Provider."
                    )
        return {"status": "ok", "refreshed": results}

    try:
        return run_async(_refresh())
    except Exception as e:
        logger.error(f"[AI models] refresh task failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task
def proactive_health_scan():
    """Evaluate proactive (leading-indicator) health and email admins when a
    check crosses into a worse state — so problems surface *before* an outage,
    not after. De-duped against the last-seen state so a persistently-degraded
    check doesn't email every run. Scheduled via Celery Beat."""
    import logging
    logger = logging.getLogger(__name__)

    async def _scan():
        from app.models import SystemSettings, User
        from app.services.proactive_health import (
            evaluate,
            escalations as find_escalations,
            next_alert_state,
        )
        from app.services.notifications import notification_service

        async with SessionLocal() as db:
            await configure_notifications(db)

            result = await evaluate(db)
            checks = result["checks"]

            settings_res = await db.execute(select(SystemSettings).order_by(SystemSettings.id).limit(1))
            settings = settings_res.scalar_one_or_none()
            if not settings:
                return {"status": "no_settings"}
            prev = dict(settings.health_alert_state or {})

            # Which checks just got worse (ok/unknown -> warning/critical, or
            # warning -> critical)? Those are the ones worth an alert.
            # Both rules -- muted checks do not page, and a mute defers rather
            # than cancels -- are pure and live next to the checks themselves,
            # where they are unit-tested without a database or a mail server.
            escalations = find_escalations(checks, prev)
            settings.health_alert_state = next_alert_state(checks, prev)
            await db.commit()

            if not escalations:
                return {"status": "ok", "overall": result["overall_status"], "alerts": 0}

            # Email active admins.
            admins_res = await db.execute(
                select(User).where(User.role == "admin", User.is_active == True)
            )
            admins = [a for a in admins_res.scalars().all() if a.email]
            if not admins:
                logger.warning("[proactive] escalations found but no admin emails to notify")
                return {"status": "no_admins", "alerts": len(escalations)}

            township = settings.township_name or "Your 311"
            crit = [c for c in escalations if c["status"] == "critical"]
            worst = "Critical" if crit else "Warning"
            # One callout per check rather than a colour-coded table row. The
            # severity is the callout's title -- the word "Critical", not a red
            # cell -- because an administrator reading this on a phone in dark
            # mode, or with a screen reader, has to get the same answer.
            blocks = [
                L.heading(f"{worst}: system needs attention", 2),
                L.paragraph("Each check below has crossed its alert threshold. "
                            "The condition observed and the action for it are "
                            "stated on each one."),
            ]
            for c in escalations:
                tone = "critical" if c["status"] == "critical" else "warning"
                blocks.append(L.callout(
                    f"{c['message']} {c['action']}",
                    tone,
                    title=f"{c['status'].upper()} - {c['label']}",
                ))
            blocks.append(L.paragraph(
                "Full detail, and the restart and maintenance actions, are in "
                "Admin Console under System Health.", muted=True))
            subject = f"[{worst}] {township} - {len(escalations)} system check(s) need attention"
            message = L.build_email(
                subject=subject,
                township_name=township,
                logo_url=settings.logo_url,
                primary_color=settings.primary_color or "#6366f1",
                preheader=", ".join(c["label"] for c in escalations)[:120],
                blocks=blocks,
                footer_lines=["Proactive health alert. Sent to administrators only."],
            )
            for admin in admins:
                try:
                    notification_service.send_email(
                        to=admin.email, subject=message["subject"],
                        body_html=message["html"], body_text=message["text"],
                        from_name=f"{township} System Monitor",
                    )
                except Exception as e:
                    logger.warning(f"[proactive] failed to email {admin.email}: {e}")

            return {"status": "alerted", "overall": result["overall_status"], "alerts": len(escalations)}

    try:
        return run_async(_scan())
    except Exception as e:
        logger.error(f"[proactive] scan failed: {e}")
        return {"status": "error", "error": str(e)}
