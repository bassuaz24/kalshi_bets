import os

SEND_EMAIL_TURN_ON = os.getenv("SEND_EMAIL_TURN_ON", "NO").strip().upper() == "YES"
EMAIL_RECIPIENT = ""
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
EMAIL_INTERVAL_SECS = int(os.getenv("EMAIL_INTERVAL_SECS", "3600"))