import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

class EmailService:
    def __init__(self):
        self.smtp_host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 587))
        self.smtp_user = os.environ.get('SMTP_USER')
        self.smtp_password = os.environ.get('SMTP_PASSWORD')
        self.from_email = os.environ.get('FROM_EMAIL', self.smtp_user)
    
    def send_email(self, to_email, subject, html_content, text_content=None):
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_email
            msg['To'] = to_email
            
            # Plain text version
            if text_content:
                part1 = MIMEText(text_content, 'plain')
                msg.attach(part1)
            
            # HTML version
            part2 = MIMEText(html_content, 'html')
            msg.attach(part2)
            
            # Send email
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
            return True
        except Exception as e:
            print(f"Email error: {e}")
            return False
    
    def send_welcome_email(self, user):
        subject = "Welcome to InvestBook!"
        html = f"""
        <h1>Welcome {user.username}!</h1>
        <p>Thank you for joining InvestBook. Start investing today!</p>
        <p>Your trust score: {user.trust_score}</p>
        """
        return self.send_email(user.email, subject, html)
    
    def send_investment_confirmation(self, user, group, amount):
        subject = f"Investment Confirmed - {group.name}"
        html = f"""
        <h1>Investment Confirmed</h1>
        <p>Dear {user.username},</p>
        <p>Your investment of ${amount} in {group.name} has been confirmed.</p>
        <p>Total committed: ${group.total_committed}</p>
        """
        return self.send_email(user.email, subject, html)
