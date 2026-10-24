import io
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import models, transaction
from django.utils import timezone

from .models import Booking, BookingSeat, Payment, ShowSeat
from .views import _lock_booking_then_payment, _mark_payment_refunded

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5)
def send_booking_ticket(self, booking_id):
    with transaction.atomic():
        # show is nullable for legacy bookings. Lock just the Booking row so
        # PostgreSQL does not try to lock the nullable side of the outer join.
        booking = Booking.objects.select_for_update(of=("self",)).select_related(
            "user", "movie", "theater", "show__screen"
        ).get(pk=booking_id)
        if booking.status != Booking.CONFIRMED or booking.ticket_delivery_status == "sent": return "skipped"
        if booking.ticket_delivery_status == "sending" and booking.delivery_started_at and booking.delivery_started_at > timezone.now()-timedelta(minutes=10): return "already_sending"
        if not booking.user.email:
            booking.ticket_delivery_status = "no_email"
            booking.delivery_last_error = "User has no email address"
            booking.save(update_fields=["ticket_delivery_status", "delivery_last_error"])
            return "no_email"
        booking.ticket_delivery_status = "sending"
        booking.delivery_started_at = timezone.now()
        booking.delivery_attempts += 1
        booking.save(update_fields=["ticket_delivery_status", "delivery_started_at", "delivery_attempts"])
    booking = Booking.objects.select_related("user", "movie", "theater__city", "show__screen").prefetch_related("booking_seats__show_seat__seat", "payments").get(pk=booking_id)
    try:
        if booking.ticket_file:
            with booking.ticket_file.open("rb") as saved_pdf: pdf_bytes = saved_pdf.read()
        else:
            token = signing.dumps({"ref": str(booking.reference)}, salt="bookmyseat.ticket")
            pdf_bytes = _render_pdf(booking, token)
            booking.ticket_file.save(f"ticket-{booking.reference}.pdf", ContentFile(pdf_bytes), save=True)
        email = EmailMessage(subject=f"Your {booking.movie.name} tickets", body=f"Your booking {booking.reference} is confirmed. Your ticket is attached.", to=[booking.user.email])
        email.extra_headers = {"Message-ID": f"<ticket-{booking.reference}@bookmyseat.local>"}
        email.attach(f"ticket-{booking.reference}.pdf", pdf_bytes, "application/pdf")
        email.send(fail_silently=False)
        booking.ticket_delivery_status = "sent"
        booking.delivery_started_at = None
        booking.delivery_last_error = ""
        booking.delivery_sent_at = timezone.now()
        booking.save(update_fields=["ticket_delivery_status", "delivery_started_at", "delivery_last_error", "delivery_sent_at"])
        return "sent"
    except Exception as exc:
        Booking.objects.filter(pk=booking_id).update(ticket_delivery_status="failed", delivery_started_at=None, delivery_last_error=str(exc)[:1000])
        if self.request.retries >= self.max_retries:
            logger.exception("Ticket delivery exhausted retries for booking %s", booking_id)
            raise
        retry_countdown = min(60 * (2 ** self.request.retries), 3600)
        raise self.retry(exc=exc, countdown=retry_countdown)


def _render_pdf(booking, token):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    import qrcode
    from django.urls import reverse
    from urllib.parse import urljoin
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Ticket {booking.reference}")
    pdf.setFont("Helvetica-Bold", 20); pdf.drawString(20 * mm, height - 25 * mm, booking.movie.name)
    pdf.setFont("Helvetica", 11)
    venue = ", ".join(value for value in [booking.theater.name, booking.theater.city.name if booking.theater.city_id else "", booking.theater.address] if value)
    rows = [f"Theater: {venue}", f"Screen: {booking.show.screen.name if booking.show_id else 'Legacy booking'}", f"Show: {timezone.localtime(booking.show.starts_at).strftime('%d %b %Y, %H:%M %Z') if booking.show_id else 'See booking history'}", f"Seats: {', '.join(x.show_seat.seat.seat_number for x in booking.booking_seats.select_related('show_seat__seat')) or (booking.seat.seat_number if booking.seat_id else 'N/A')}", f"Booking: {booking.reference}", f"Payment: {booking.payments.filter(status=Payment.CAPTURED).first().transaction_id if booking.payments.filter(status=Payment.CAPTURED).exists() else 'N/A'}", f"Amount: INR {booking.total_amount}"]
    y = height - 42 * mm
    for row in rows: pdf.drawString(20 * mm, y, row); y -= 9 * mm
    qr = qrcode.make(f"{settings.SITE_URL}/movies/tickets/verify/{token}/")
    qr_buf = io.BytesIO(); qr.save(qr_buf, format="PNG"); qr_buf.seek(0)
    from reportlab.lib.utils import ImageReader
    pdf.drawImage(ImageReader(qr_buf), width - 55 * mm, 20 * mm, 35 * mm, 35 * mm)
    pdf.setFont("Helvetica", 8); pdf.drawString(20 * mm, 20 * mm, "Scan the QR code to check the ticket status.")
    pdf.showPage(); pdf.save()
    return buf.getvalue()


@shared_task
def release_expired_holds():
    now = timezone.now()
    booking_ids = list(ShowSeat.objects.filter(
        status=ShowSeat.HELD, hold_expires_at__lte=now,
        active_booking__status=Booking.PENDING).order_by("active_booking_id").values_list("active_booking_id", flat=True).distinct())
    with transaction.atomic():
        for booking in Booking.objects.select_for_update().filter(pk__in=booking_ids).order_by("pk"):
            rows = list(ShowSeat.objects.select_for_update().filter(
                active_booking=booking, status=ShowSeat.HELD,
                hold_expires_at__lte=now).order_by("seat_id"))
            for row in rows:
                row.status, row.held_by, row.hold_expires_at, row.active_booking = ShowSeat.AVAILABLE, None, None, None
                row.save(update_fields=["status", "held_by", "hold_expires_at", "active_booking"])
            if not booking.active_show_seats.filter(status=ShowSeat.HELD, held_by=booking.user, hold_expires_at__gt=now).exists():
                booking.status = Booking.FAILED
                booking.save(update_fields=["status"])
                Payment.objects.filter(booking=booking, status=Payment.CREATED).update(status=Payment.FAILED)
    return len(booking_ids)


@shared_task
def recover_ticket_deliveries():
    stale = timezone.now()-timedelta(minutes=10)
    ids = list(Booking.objects.filter(status=Booking.CONFIRMED).filter(
        models.Q(ticket_delivery_status__in=["pending", "failed", "no_email"]) |
        models.Q(ticket_delivery_status="sending", delivery_started_at__lt=stale)).exclude(user__email="").order_by("id").values_list("id", flat=True)[:100])
    for booking_id in ids: send_booking_ticket.delay(booking_id)
    return len(ids)


@shared_task
def refund_late_payment(payment_id):
    previous_failed_refund_id = ""
    with transaction.atomic():
        booking_id = Payment.objects.values_list("booking_id", flat=True).get(pk=payment_id)
        Booking.objects.select_for_update().get(pk=booking_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id, booking_id=booking_id)
        if payment.status == Payment.REFUNDED or payment.refund_status == "processed":
            return "already_refunded"
        if payment.refund_status == "initiating":
            payment.refund_status = "manual_required"
            payment.save(update_fields=["refund_status", "updated_at"])
            logger.error("Refund outcome is ambiguous; manual provider reconciliation required for payment %s", payment_id)
            return "manual_required"
        if payment.refund_id and payment.refund_status != "failed":
            return "awaiting_provider_confirmation"
        if payment.refund_status == "failed":
            # A retry is safe only because the previous provider attempt is
            # definitively failed. Retain its identifier until the new
            # response is reconciled; do not treat it as an uncertain ID.
            previous_failed_refund_id = payment.refund_id
        if payment.status != Payment.CAPTURED:
            return "not_captured"
        if payment.refund_status not in {"pending", "requested", "retrying", "failed", "none"}:
            return payment.refund_status
        payment.refund_status = "initiating"
        payment.save(update_fields=["refund_status", "updated_at"])
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET or not payment.transaction_id:
        Payment.objects.filter(pk=payment_id).update(refund_status="manual_required")
        return "manual_required"
    try:
        import razorpay
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        refund = client.payment.refund(payment.transaction_id, {"amount": int(payment.amount * 100)})
        refund_id, provider_status = refund.get("id"), refund.get("status")
        if not refund_id:
            raise ValueError("Provider refund response did not include an identifier")
        if provider_status == "processed":
            _mark_payment_refunded(payment_id, refund_id)
            return "refunded"
        if provider_status in {"created", "pending"}:
            return _record_provider_refund_response(
                payment_id, refund_id, "pending", previous_failed_refund_id
            )
        return _record_provider_refund_response(
            payment_id, refund_id, "failed", previous_failed_refund_id
        )
    except Exception:
        # A timeout may occur after the provider accepted the refund. Never
        # issue a blind second refund; reconcile by provider transaction first.
        Payment.objects.filter(pk=payment_id, refund_status="initiating").update(refund_status="manual_required")
        logger.exception("Refund provider outcome is ambiguous for payment %s", payment_id)
        return "manual_required"


def _record_provider_refund_response(payment_id, refund_id, refund_status, previous_failed_refund_id=""):
    """Persist a provider response without downgrading a concurrent webhook.

    The provider call is complete before this transaction begins. Any rows
    needed for the update are locked in the documented Booking -> Payment
    order, and terminal refund state wins over a stale response.
    """
    with transaction.atomic():
        payment, _booking = _lock_booking_then_payment(payment_id)
        if payment.status == Payment.REFUNDED or payment.refund_status == "processed":
            if payment.refund_id and payment.refund_id != refund_id:
                logger.error(
                    "Provider returned refund %s after payment %s was already processed as %s",
                    refund_id, payment_id, payment.refund_id,
                )
            return "already_refunded"
        if payment.status != Payment.CAPTURED:
            payment.refund_status = "manual_required"
            payment.save(update_fields=["refund_status", "updated_at"])
            return "manual_required"
        if (payment.refund_id and payment.refund_id != refund_id
                and payment.refund_id != previous_failed_refund_id):
            payment.refund_status = "manual_required"
            payment.save(update_fields=["refund_status", "updated_at"])
            logger.error("Conflicting provider refund identifiers for payment %s", payment_id)
            return "manual_required"
        payment.refund_id = refund_id
        payment.refund_status = refund_status
        payment.save(update_fields=["refund_id", "refund_status", "updated_at"])
        return "awaiting_provider_confirmation" if refund_status == "pending" else "provider_refund_failed"


@shared_task
def recover_late_payment_refunds():
    ids = list(Payment.objects.filter(status=Payment.CAPTURED, refund_status__in=["pending", "retrying"]).exclude(transaction_id="").order_by("id").values_list("id", flat=True)[:100])
    for payment_id in ids: refund_late_payment.delay(payment_id)
    return len(ids)


@shared_task
def reconcile_uncertain_payment_orders():
    """Resolve provider-order creations whose outcome was unknown (timeout/crash).

    Looks the order up at the provider by receipt (== Payment.idempotency_key).
    One matching order with the right amount is attached; no match after the
    grace period is a definite failure; anything else stays "uncertain" for
    manual review. NOTE: the provider-side receipt filter is unverified against
    live Razorpay in this repository.
    """
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        return 0
    import razorpay
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    grace = timezone.now() - timedelta(minutes=15)
    resolved = 0
    for payment_id in list(Payment.objects.filter(order_state=Payment.ORDER_UNCERTAIN, provider_order_id="").order_by("pk").values_list("pk", flat=True)[:100]):
        payment = Payment.objects.get(pk=payment_id)
        try:
            response = client.order.all({"receipt": payment.idempotency_key})
        except Exception:
            logger.exception("Order reconciliation lookup failed for payment %s", payment_id)
            continue
        # Malformed provider output is not evidence that no order exists.
        # Leave the attempt uncertain so recovery cannot create a duplicate.
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            logger.error("Order reconciliation returned malformed results for payment %s", payment_id)
            continue
        receipt_matches = [item for item in items if isinstance(item, dict) and item.get("receipt") == payment.idempotency_key]
        matches = []
        malformed_match = False
        for item in receipt_matches:
            try:
                amount = int(item.get("amount", -1))
            except (TypeError, ValueError, OverflowError):
                malformed_match = True
                continue
            if not item.get("id") or amount != int(payment.amount * 100):
                malformed_match = True
                continue
            matches.append(item)
        if malformed_match:
            logger.error("Order reconciliation found an incomplete or mismatched receipt for payment %s", payment_id)
            continue
        with transaction.atomic():
            locked, booking = _lock_booking_then_payment(payment_id)
            if locked.order_state != Payment.ORDER_UNCERTAIN or locked.provider_order_id:
                continue
            if len(matches) == 1:
                locked.provider_order_id, locked.order_state = matches[0]["id"], Payment.ORDER_CREATED
                if locked.status == Payment.CREATED and (booking.status != Booking.PENDING or locked.amount != booking.total_amount):
                    locked.status = Payment.FAILED
                locked.save(update_fields=["provider_order_id", "order_state", "status", "updated_at"])
                resolved += 1
            elif not matches and locked.updated_at < grace:
                locked.order_state = Payment.ORDER_FAILED
                if locked.status == Payment.CREATED:
                    locked.status = Payment.FAILED
                locked.save(update_fields=["order_state", "status", "updated_at"])
                resolved += 1
            else:
                logger.error("Payment order %s remains uncertain; manual review required", payment_id)
    return resolved
