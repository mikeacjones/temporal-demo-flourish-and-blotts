"""Customer HITL email — sent via SMTP to MailHog for the demo."""
import email.utils
from email.message import EmailMessage

import aiosmtplib
from temporalio import activity
from temporalio.exceptions import ApplicationError

from shared.models import SendConfirmationEmailInput
from worker.config import SMTP_HOST, SMTP_PORT, HITL_FROM_EMAIL


def _render_email(input: SendConfirmationEmailInput) -> tuple[str, str]:
    """Return (plain_text, html) bodies for the confirmation email."""
    plain = (
        f"Hello {input.customer_name},\n\n"
        f"Order {input.order_id} needs a quick decision from you:\n\n"
        f"{input.question}\n\n"
        f"{input.description}\n\n"
        f"Approve: {input.approve_url}\n"
        f"Deny (cancel order): {input.deny_url}\n\n"
        f"This link expires {input.expires_at_iso}.\n\n"
        f"— Flourish & Blotts\n"
    )

    html = f"""<!DOCTYPE html>
<html>
  <body style="font-family: Georgia, 'Times New Roman', serif; background:#f7f3e7; padding:32px; color:#1a1f3a;">
    <div style="max-width:560px; margin:0 auto; background:#fffdf5; border:1px solid #d4b24a; border-radius:8px; padding:32px;">
      <h1 style="color:#1a1f3a; margin-top:0;">Flourish &amp; Blotts</h1>
      <p style="color:#555; margin-bottom:24px;">The finest wizarding bookshop in Diagon Alley</p>
      <p>Hello <strong>{input.customer_name}</strong>,</p>
      <p>Your order <code>{input.order_id}</code> needs a quick decision from you:</p>
      <div style="background:#fff; border-left:4px solid #d4b24a; padding:16px 20px; margin:24px 0;">
        <p style="margin:0 0 8px 0; font-size:18px; font-weight:bold;">{input.question}</p>
        <p style="margin:0; color:#555;">{input.description}</p>
      </div>
      <div style="text-align:center; margin:32px 0;">
        <a href="{input.approve_url}"
           style="display:inline-block; background:#2d5a2d; color:#fff; padding:12px 28px; border-radius:4px; text-decoration:none; margin:0 8px; font-weight:bold;">
          Approve
        </a>
        <a href="{input.deny_url}"
           style="display:inline-block; background:#8b0000; color:#fff; padding:12px 28px; border-radius:4px; text-decoration:none; margin:0 8px; font-weight:bold;">
          Deny &amp; cancel my order
        </a>
      </div>
      <p style="color:#777; font-size:12px; margin-top:32px;">
        These links expire {input.expires_at_iso}. If you take no action, your order will be cancelled.
      </p>
    </div>
  </body>
</html>"""

    return plain, html


@activity.defn
async def send_customer_confirmation_email(input: SendConfirmationEmailInput) -> str:
    """Send the confirmation email via SMTP. Returns the Message-ID for audit."""
    plain, html = _render_email(input)

    msg = EmailMessage()
    msg["Subject"] = f"Action needed for your Flourish & Blotts order {input.order_id}"
    msg["From"] = HITL_FROM_EMAIL
    msg["To"] = input.to_email
    message_id = email.utils.make_msgid(domain="flourish-and-blotts.test")
    msg["Message-ID"] = message_id
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            timeout=15,
        )
    except aiosmtplib.SMTPResponseException as e:
        # 4xx SMTP errors are transient; 5xx are permanent per RFC 5321.
        if 500 <= (e.code or 0) < 600:
            raise ApplicationError(
                f"SMTP permanent failure ({e.code}): {e.message}",
                type="SMTPPermanentError",
                non_retryable=True,
            )
        raise ApplicationError(
            f"SMTP transient failure ({e.code}): {e.message}",
            type="SMTPTransientError",
        )
    except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPServerDisconnected) as e:
        raise ApplicationError(f"SMTP connection error: {e}", type="SMTPConnectionError")

    activity.logger.info(
        "Sent customer HITL email for order %s to %s (Message-ID %s)",
        input.order_id, input.to_email, message_id,
    )
    return message_id
